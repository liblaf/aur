"""Execute publication workflow shell safeguards against an offline GitHub API."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/packages.yml"


def step_script(name):
    """Read a named literal run block without requiring a YAML dependency."""
    lines = WORKFLOW.read_text().splitlines()
    start = lines.index(f"      - name: {name}") + 1
    for index in range(start, len(lines)):
        line = lines[index]
        if line.startswith("      - "):
            break
        if line == "        run: |":
            body = []
            for command in lines[index + 1:]:
                if command and not command.startswith("          "):
                    break
                body.append(command[10:])
            return "\n".join(body)
    raise AssertionError(f"No literal shell script for {name}")


class WorkflowPublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.calls = self.directory / "calls.jsonl"
        gh = self.directory / "gh"
        gh.write_text("#!/usr/bin/env python3\n" + textwrap.dedent('''\
            import json
            import os
            import sys
            args = sys.argv[1:]
            with open(os.environ["API_CALLS"], "a") as output:
                output.write(json.dumps(args) + "\\n")
            if "--method" not in args:
                path = next(arg for arg in args if arg.startswith("repos/"))
                if "/git/commits/" in path:
                    print(os.environ["VERIFIED"])
                elif path.endswith("/git/ref/heads/main"):
                    print(os.environ["MAIN_SHA"])
                elif "/actions/runs/" in path:
                    print(os.environ.get("ARTIFACT_NAMES", ""), end="")
                else:
                    sys.exit("Unexpected GET")
            elif args[args.index("--method") + 1] == "PATCH":
                # A sibling main commit arrives after the preceding GET.
                if os.environ.get("RACE") == "1":
                    sys.exit("Update is not a fast forward")
                if args[-2:] != ["-F", "force=false"]:
                    sys.exit("Expected a non-forced update")
            '''))
        gh.chmod(0o755)
        self.env = os.environ | {"PATH": f"{self.directory}:{os.environ['PATH']}",
                                 "API_CALLS": str(self.calls), "GITHUB_REPOSITORY": "liblaf/aur",
                                 "GITHUB_SHA": "a" * 40, "COMMIT_SHA": "b" * 40,
                                 "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2",
                                 "GITHUB_WORKSPACE": str(self.directory),
                                 "RUNNER_TEMP": str(self.directory),
                                 "GITHUB_OUTPUT": str(self.directory / "output"),
                                 "VERIFIED": "true", "MAIN_SHA": "a" * 40}

    def execute(self, name):
        return subprocess.run(("bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", step_script(name)),
                              cwd=self.directory, env=self.env, text=True, capture_output=True)

    def api_calls(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def test_verified_commit_updates_stable_main_without_force(self):
        result = self.execute("Publish the verified commit to main")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.api_calls()
        self.assertEqual(len(calls), 3)
        self.assertIn("/git/commits/" + self.env["COMMIT_SHA"], calls[0][1])
        self.assertTrue(calls[1][1].endswith("/git/ref/heads/main"))
        self.assertIn("PATCH", calls[2])
        self.assertIn("sha=" + self.env["COMMIT_SHA"], calls[2])
        self.assertIn("force=false", calls[2])

    def test_unverified_commit_or_moved_main_blocks_update(self):
        for changes in ({"VERIFIED": "false"}, {"MAIN_SHA": "c" * 40}):
            with self.subTest(changes=changes):
                self.env.update(VERIFIED="true", MAIN_SHA="a" * 40)
                self.env.update(changes)
                self.calls.unlink(missing_ok=True)
                result = self.execute("Publish the verified commit to main")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any("PATCH" in call for call in self.api_calls()))

    def test_api_race_rejection_fails_publication_and_cleanup_can_run(self):
        self.env["RACE"] = "1"
        result = self.execute("Publish the verified commit to main")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a fast forward", result.stderr)
        cleanup = self.execute("Remove GitHub publication branch")
        self.assertEqual(cleanup.returncode, 0, cleanup.stderr)
        self.assertIn("DELETE", self.api_calls()[-1])
        self.assertTrue(self.api_calls()[-1][-1].endswith("/git/refs/heads/aur-automation/123-2"))

    def test_branch_creation_uses_validated_event_sha_and_attempt(self):
        result = self.execute("Prepare GitHub publication branch")
        self.assertEqual(result.returncode, 0, result.stderr)
        call = self.api_calls()[0]
        self.assertIn("POST", call)
        self.assertIn("ref=refs/heads/aur-automation/123-2", call)
        self.assertIn("sha=" + self.env["GITHUB_SHA"], call)

    def test_candidate_discovery_reports_empty_and_available_runs(self):
        for names, expected in (("", "false"), ("candidate-manggo\ncandidate-mihomo-smart-bin\n", "true")):
            with self.subTest(names=names):
                output = Path(self.env["GITHUB_OUTPUT"])
                output.unlink(missing_ok=True)
                self.env["ARTIFACT_NAMES"] = names
                result = self.execute("Find validated candidates")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output.read_text(), f"found={expected}\n")

    def test_publication_rejects_untracked_and_staged_changes(self):
        # The fake API and its output live outside the checkout for this test.
        checkout = self.directory / "checkout"
        checkout.mkdir()
        subprocess.run(("git", "init", "--quiet", str(checkout)), check=True)
        script = step_script("Require a clean publication checkout")

        def clean_check():
            return subprocess.run(("bash", "-e", "-o", "pipefail", "-c", script),
                                  cwd=checkout, env=self.env, capture_output=True)

        self.assertEqual(clean_check().returncode, 0)
        (checkout / "unexpected").write_text("changed\n")
        self.assertNotEqual(clean_check().returncode, 0)
        subprocess.run(("git", "add", "unexpected"), cwd=checkout, check=True)
        self.assertNotEqual(clean_check().returncode, 0)

    def test_publication_rejects_failed_git_status(self):
        result = self.execute("Require a clean publication checkout")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a git repository", result.stderr)

    def test_dedicated_private_key_must_match_pinned_public_key(self):
        config = self.directory / "config"
        config.mkdir()
        source_key = self.directory / "source-key"
        subprocess.run(("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(source_key)),
                       check=True, capture_output=True)
        expected_key = config / "aur-actions-key.pub"
        expected_key.write_text(source_key.with_suffix(".pub").read_text())
        self.env["AUR_SSH_PRIVATE_KEY"] = source_key.read_text()
        result = self.execute("Prepare dedicated AUR authentication")
        self.assertEqual(result.returncode, 0, result.stderr)
        temporary_key = self.directory / "aur-key"
        self.assertEqual(temporary_key.stat().st_mode & 0o777, 0o600)
        expected_key.write_text("ssh-ed25519 unexpected-key different-comment\n")
        result = self.execute("Prepare dedicated AUR authentication")
        self.assertNotEqual(result.returncode, 0)
        cleanup = self.execute("Remove temporary key")
        self.assertEqual(cleanup.returncode, 0, cleanup.stderr)
        self.assertFalse(temporary_key.exists())

    def test_missing_private_key_stops_authentication(self):
        self.env["AUR_SSH_PRIVATE_KEY"] = ""
        result = self.execute("Prepare dedicated AUR authentication")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Configure AUR_SSH_PRIVATE_KEY", result.stderr)
        self.assertFalse((self.directory / "aur-key").exists())

    def stub_driver(self):
        commands = self.directory / "driver-commands.jsonl"
        python = self.directory / "python3"
        python.write_text(f"#!{sys.executable}\n" + textwrap.dedent('''\
            import json
            import os
            import sys
            with open(os.environ["DRIVER_COMMANDS"], "a") as output:
                output.write(json.dumps(sys.argv[1:]) + "\\n")
            if sys.argv[2] == "publish" and os.environ["AUR_PUBLISH_ENABLED"] != "true":
                sys.exit("AUR publishing is disabled")
            sys.exit(int(os.environ.get("DRIVER_STATUS", "0")))
            '''))
        python.chmod(0o755)
        self.env["DRIVER_COMMANDS"] = str(commands)
        return commands

    def test_record_only_mode_records_metadata_with_aur_publishing_disabled(self):
        commands = self.stub_driver()
        self.env.update(RECORD_ONLY="true", AUR_PUBLISH_ENABLED="false")
        result = self.execute("Publish validated metadata to AUR and prepare GitHub changes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([json.loads(line) for line in commands.read_text().splitlines()], [[
            "scripts/aur.py", "record", "--candidates", str(self.directory / "candidates"),
            "--github-output", self.env["GITHUB_OUTPUT"],
        ]])
        self.assertFalse((self.directory / "aur-key").exists())

    def test_record_failure_stops_without_falling_back_to_aur_publication(self):
        commands = self.stub_driver()
        self.env.update(RECORD_ONLY="true", AUR_PUBLISH_ENABLED="false", DRIVER_STATUS="1")
        result = self.execute("Publish validated metadata to AUR and prepare GitHub changes")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([json.loads(line)[1] for line in commands.read_text().splitlines()], ["record"])

    def test_normal_publication_mode_uses_aur_driver(self):
        commands = self.stub_driver()
        self.env.update(RECORD_ONLY="false", AUR_PUBLISH_ENABLED="true")
        result = self.execute("Publish validated metadata to AUR and prepare GitHub changes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([json.loads(line) for line in commands.read_text().splitlines()], [[
            "scripts/aur.py", "publish", "--candidates", str(self.directory / "candidates"),
            "--aur-root", str(self.directory / "aur"), "--github-output", self.env["GITHUB_OUTPUT"],
        ]])


if __name__ == "__main__":
    unittest.main()
