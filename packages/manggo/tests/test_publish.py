"""Integration tests for the guarded AUR publisher."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "publish.py"
SPEC = importlib.util.spec_from_file_location("publish", SCRIPT)
publish = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(publish)


PKG = """pkgname=manggo
pkgver=1.0.0
pkgrel=1
sha256sums_x86_64=('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')
sha256sums_aarch64=('bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb')
"""
SRC = """pkgbase = manggo
\tpkgver = 1.0.0
\tpkgrel = 1
\tsha256sums_x86_64 = aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
\tsha256sums_aarch64 = bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb

pkgname = manggo
"""


def run(directory: Path, *args: str) -> str:
    result = subprocess.run(("git", *args), cwd=directory, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class PublishTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.remote = self.base / "github.git"
        self.aur_remote = self.base / "aur.git"
        for remote, branch in ((self.remote, "main"), (self.aur_remote, "master")):
            subprocess.run(("git", "init", "--bare", f"--initial-branch={branch}", str(remote)), check=True,
                           stdout=subprocess.DEVNULL)
        self.root = self.base / "github"
        subprocess.run(("git", "clone", str(self.remote), str(self.root)), check=True,
                       stdout=subprocess.DEVNULL)
        run(self.root, "config", "core.hooksPath", "/dev/null")
        self.write_pair(self.root, PKG, SRC)
        run(self.root, "add", "PKGBUILD", ".SRCINFO")
        run(self.root, "-c", "user.name=test", "-c", "user.email=test@example.invalid",
            "commit", "-m", "base")
        run(self.root, "branch", "-M", "main")
        run(self.root, "push", "origin", "main")
        self.aur_seed = self.base / "aur-seed"
        subprocess.run(("git", "clone", str(self.aur_remote), str(self.aur_seed)), check=True,
                       stdout=subprocess.DEVNULL)
        run(self.aur_seed, "config", "core.hooksPath", "/dev/null")
        self.write_pair(self.aur_seed, PKG, SRC)
        run(self.aur_seed, "add", "PKGBUILD", ".SRCINFO")
        run(self.aur_seed, "-c", "user.name=test", "-c", "user.email=test@example.invalid",
            "commit", "-m", "base")
        run(self.aur_seed, "branch", "-M", "master")
        run(self.aur_seed, "push", "origin", "master")
        self.aur = self.base / "aur"
        subprocess.run(("git", "clone", str(self.aur_remote), str(self.aur)), check=True,
                       stdout=subprocess.DEVNULL)
        run(self.aur, "config", "core.hooksPath", "/dev/null")
        run(self.aur, "checkout", "-B", "master", "origin/master")
        state = {"commit": run(self.aur, "rev-parse", "HEAD"),
                 "sha256": {name: publish.sha256(self.aur / name) for name in publish.FILES}}
        (self.root / "aur-state.json").write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
        run(self.root, "add", "aur-state.json")
        run(self.root, "-c", "user.name=test", "-c", "user.email=test@example.invalid",
            "commit", "-m", "record AUR baseline")
        run(self.root, "push", "origin", "main")
        self.candidate = self.base / "candidate"
        self.make_candidate()
        self.old_root = publish.ROOT
        publish.ROOT = self.root

    def tearDown(self) -> None:
        publish.ROOT = self.old_root
        self.temp.cleanup()

    @staticmethod
    def write_pair(directory: Path, pkg: str, src: str) -> None:
        (directory / "PKGBUILD").write_text(pkg)
        (directory / ".SRCINFO").write_text(src)

    def make_candidate(self) -> None:
        self.candidate.mkdir()
        pkg = PKG.replace("1.0.0", "1.0.1").replace("aaaaaaaa", "cccccccc").replace("bbbbbbbb", "dddddddd")
        src = SRC.replace("1.0.0", "1.0.1").replace("aaaaaaaa", "cccccccc").replace("bbbbbbbb", "dddddddd")
        self.write_pair(self.candidate, pkg, src)
        hashes = {name: hashlib.sha256((self.candidate / name).read_bytes()).hexdigest()
                  for name in publish.FILES}
        (self.candidate / "manifest.json").write_text(json.dumps({
            "base_commit": run(self.root, "rev-parse", "HEAD"), "version": "1.0.1", "sha256": hashes}))

    def refresh_manifest(self) -> None:
        manifest = json.loads((self.candidate / "manifest.json").read_text())
        manifest["sha256"] = {name: hashlib.sha256((self.candidate / name).read_bytes()).hexdigest()
                              for name in publish.FILES}
        (self.candidate / "manifest.json").write_text(json.dumps(manifest))

    def assert_github_uncommitted(self, head: str) -> None:
        self.assertEqual(run(self.root, "rev-parse", "HEAD"), head)
        self.assertEqual(run(self.remote, "rev-parse", "refs/heads/main"), head)
        self.assertEqual(run(self.root, "diff", "--cached", "--name-only"), "")
        for name in publish.FILES:
            self.assertEqual((self.root / name).read_bytes(), (self.candidate / name).read_bytes())
        self.assertFalse((self.root / "manifest.json").exists())

    def commit_prepared_files(self) -> None:
        run(self.root, "add", *publish.FILES, "automation-validation.json", "aur-state.json")
        run(self.root, "-c", "user.name=test", "-c", "user.email=test@example.invalid",
            "commit", "-m", "emulated commit-action")
        run(self.root, "push", "origin", "main")

    def test_publishes_aur_and_prepares_github_metadata(self) -> None:
        head = run(self.root, "rev-parse", "HEAD")
        publish.publish(self.candidate, self.aur, "main")
        self.assert_github_uncommitted(head)
        self.assertFalse((self.root / "automation-validation.json").exists())
        self.assertEqual(run(self.aur, "log", "-1", "--format=%s"), "Update manggo to 1.0.1")
        self.assert_aur_state_current()
        for name in publish.FILES:
            self.assertEqual(run(self.aur_remote, "show", f"master:{name}"),
                             (self.candidate / name).read_text().strip())

    def test_publishes_aur_before_preparing_files_and_report(self) -> None:
        head = run(self.root, "rev-parse", "HEAD")
        original = publish.pair(self.root)
        real_git = publish.git
        pushed = False

        def inspect_git(directory: Path, *args: str, **kwargs: object) -> str:
            nonlocal pushed
            if directory == self.aur and args[0] == "push":
                self.assertEqual(publish.pair(self.root), original)
                self.assertFalse((self.root / "automation-validation.json").exists())
                result = real_git(directory, *args, **kwargs)
                pushed = True
                return result
            return real_git(directory, *args, **kwargs)

        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/repo", "GITHUB_RUN_ID": "42"}), \
                mock.patch.object(publish, "git", side_effect=inspect_git):
            publish.publish(self.candidate, self.aur, "main", record_validation=True)
        self.assertTrue(pushed)
        self.assert_github_uncommitted(head)
        report = json.loads((self.root / "automation-validation.json").read_bytes())
        self.assertEqual(report["base_commit"], head)
        self.assertEqual(report["run_url"], "https://github.com/owner/repo/actions/runs/42")
        self.assertEqual(report["version"], "1.0.1")
        self.assertRegex(report["validated_at"], r"^\d{4}-\d\d-\d\dT.*Z$")
        self.assertEqual(set(report), {"base_commit", "run_url", "validated_at", "version"})

    def test_replays_after_successful_github_commit(self) -> None:
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/repo", "GITHUB_RUN_ID": "42"}):
            publish.publish(self.candidate, self.aur, "main", record_validation=True)
            self.commit_prepared_files()
            head = run(self.root, "rev-parse", "HEAD")
            aur_head = run(self.aur_remote, "rev-parse", "master")
            publish.publish(self.candidate, self.aur, "main", record_validation=True)
        self.assert_github_uncommitted(head)
        self.assertEqual(run(self.aur_remote, "rev-parse", "master"), aur_head)
        self.assert_aur_state_current()
        report = json.loads((self.root / "automation-validation.json").read_text())
        self.assertEqual(report["base_commit"], head)

    def test_recovers_when_aur_was_already_published(self) -> None:
        head = run(self.root, "rev-parse", "HEAD")
        self.write_pair(self.aur, *((self.candidate / name).read_text() for name in publish.FILES))
        run(self.aur, "add", "PKGBUILD", ".SRCINFO")
        run(self.aur, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-m", "prior")
        run(self.aur, "push", "origin", "HEAD:master")
        aur_head = run(self.aur_remote, "rev-parse", "master")
        publish.publish(self.candidate, self.aur, "main")
        self.assert_github_uncommitted(head)
        self.assertEqual(run(self.aur_remote, "rev-parse", "master"), aur_head)
        self.assert_aur_state_current()

    def test_same_version_prepares_only_validation_report(self) -> None:
        self.write_pair(self.candidate, PKG, SRC)
        manifest = json.loads((self.candidate / "manifest.json").read_text())
        manifest["version"] = "1.0.0"
        (self.candidate / "manifest.json").write_text(json.dumps(manifest))
        self.refresh_manifest()
        head = run(self.root, "rev-parse", "HEAD")
        aur_head = run(self.aur_remote, "rev-parse", "master")
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/repo", "GITHUB_RUN_ID": "42"}):
            publish.publish(self.candidate, self.aur, "main", record_validation=True)
        self.assert_github_uncommitted(head)
        self.assertEqual(run(self.root, "diff", "--name-only"), "")
        self.assertEqual(run(self.root, "ls-files", "--others", "--exclude-standard"), "automation-validation.json")
        self.assertEqual(run(self.aur_remote, "rev-parse", "master"), aur_head)
        report = json.loads((self.root / "automation-validation.json").read_text())
        self.assertEqual(report["version"], "1.0.0")

    def test_missing_actions_context_fails_before_aur_mutation(self) -> None:
        aur_head = run(self.aur, "rev-parse", "HEAD")
        original = publish.pair(self.root)
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(publish.PublishError, "GITHUB_REPOSITORY and GITHUB_RUN_ID"):
                publish.publish(self.candidate, self.aur, "main", record_validation=True)
        self.assertEqual(run(self.aur, "rev-parse", "HEAD"), aur_head)
        self.assertEqual(run(self.aur_remote, "rev-parse", "master"), aur_head)
        self.assertEqual(publish.pair(self.aur), original)
        self.assertEqual(publish.pair(self.root), original)
        self.assertFalse((self.root / "automation-validation.json").exists())

    def test_rejects_a_candidate_that_changes_the_baseline(self) -> None:
        path = self.candidate / "PKGBUILD"
        path.write_text(path.read_text() + "depends=('surprise')\n")
        self.refresh_manifest()
        with self.assertRaisesRegex(publish.PublishError, "release metadata"):
            publish.publish(self.candidate, self.aur, "main")

    def test_rejects_divergent_aur_checkout(self) -> None:
        (self.aur / "PKGBUILD").write_text("pkgname=other\n")
        run(self.aur, "add", "PKGBUILD")
        run(self.aur, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-m", "manual")
        run(self.aur, "push", "origin", "master")
        with self.assertRaisesRegex(publish.PublishError, "co-maintainer"):
            publish.publish(self.candidate, self.aur, "main")

    def assert_aur_state_current(self) -> None:
        state = json.loads((self.root / "aur-state.json").read_text())
        self.assertEqual(state["commit"], run(self.aur_remote, "rev-parse", "master"))
        self.assertEqual(state["sha256"], {name: publish.sha256(self.candidate / name)
                                           for name in publish.FILES})

    def test_publishes_merged_github_candidate_from_older_aur_baseline(self) -> None:
        publish.copy_pair(self.candidate, self.root)
        run(self.root, "add", *publish.FILES)
        run(self.root, "-c", "user.name=test", "-c", "user.email=test@example.invalid",
            "commit", "-m", "accept reviewed release")
        run(self.root, "push", "origin", "main")
        manifest = json.loads((self.candidate / "manifest.json").read_text())
        manifest["base_commit"] = run(self.root, "rev-parse", "HEAD")
        (self.candidate / "manifest.json").write_text(json.dumps(manifest))
        self.assertNotEqual(publish.pair(self.root), publish.pair(self.aur))
        publish.publish(self.candidate, self.aur, "main")
        self.assertEqual(publish.pair(self.aur), publish.pair(self.candidate))
        self.assert_aur_state_current()

    def test_rejects_candidate_after_multiple_unrecorded_aur_commits(self) -> None:
        run(self.aur, "-c", "user.name=test", "-c", "user.email=test@example.invalid",
            "commit", "--allow-empty", "-m", "co-maintainer change")
        publish.copy_pair(self.candidate, self.aur)
        run(self.aur, "add", *publish.FILES)
        run(self.aur, "-c", "user.name=test", "-c", "user.email=test@example.invalid",
            "commit", "-m", "matching release")
        run(self.aur, "push", "origin", "master")
        original = publish.pair(self.root)
        with self.assertRaisesRegex(publish.PublishError, "co-maintainer"):
            publish.publish(self.candidate, self.aur, "main")
        self.assertEqual(publish.pair(self.root), original)

    def test_rejects_untracked_or_extra_committed_aur_files(self) -> None:
        (self.aur / "surprise").write_text("unexpected\n")
        with self.assertRaisesRegex(publish.PublishError, "dirty"):
            publish.publish(self.candidate, self.aur, "main")
        run(self.aur, "add", "surprise")
        run(self.aur, "-c", "user.name=test", "-c", "user.email=test@example.invalid",
            "commit", "-m", "extra file")
        run(self.aur, "push", "origin", "master")
        with self.assertRaisesRegex(publish.PublishError, "unexpected"):
            publish.publish(self.candidate, self.aur, "main")

    def test_rejects_aur_remote_moving_after_clone(self) -> None:
        (self.aur_seed / "PKGBUILD").write_text("changed\n")
        run(self.aur_seed, "add", "PKGBUILD")
        run(self.aur_seed, "-c", "user.name=test", "-c", "user.email=test@example.invalid",
            "commit", "-m", "concurrent change")
        run(self.aur_seed, "push", "origin", "master")
        with self.assertRaisesRegex(publish.PublishError, "remote moved"):
            publish.publish(self.candidate, self.aur, "main")

    def test_rejects_tracked_aur_baseline_digest_mismatch(self) -> None:
        state_path = self.root / "aur-state.json"
        state = json.loads(state_path.read_text())
        state["sha256"]["PKGBUILD"] = "0" * 64
        state_path.write_text(json.dumps(state))
        with self.assertRaisesRegex(publish.PublishError, "baseline differs"):
            publish.check_aur(self.aur, {name: publish.sha256(self.candidate / name)
                                         for name in publish.FILES})

    def test_rejects_nonregular_aur_tree_entry(self) -> None:
        (self.aur / "PKGBUILD").unlink()
        (self.aur / "PKGBUILD").symlink_to(".SRCINFO")
        run(self.aur, "add", "PKGBUILD")
        run(self.aur, "-c", "user.name=test", "-c", "user.email=test@example.invalid",
            "commit", "-m", "symlink packaging file")
        run(self.aur, "push", "origin", "master")
        with self.assertRaisesRegex(publish.PublishError, "non-regular"):
            publish.publish(self.candidate, self.aur, "main")

    def test_rejects_moved_github_main(self) -> None:
        other = self.base / "other"
        subprocess.run(("git", "clone", str(self.remote), str(other)), check=True,
                       stdout=subprocess.DEVNULL)
        run(other, "config", "core.hooksPath", "/dev/null")
        (other / "note").write_text("moved\n")
        run(other, "add", "note")
        run(other, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-m", "moved")
        run(other, "push", "origin", "main")
        with self.assertRaisesRegex(publish.PublishError, "moved"):
            publish.publish(self.candidate, self.aur, "main")

    def nest_package(self) -> Path:
        repository = self.root
        package = repository / "packages" / "manggo"
        package.mkdir(parents=True)
        for name in (*publish.FILES, "aur-state.json"):
            run(repository, "mv", name, str(package.relative_to(repository) / name))
        (package / "README.md").write_text("Manggo packaging\n")
        sibling = repository / "packages" / "other" / "PKGBUILD"
        sibling.parent.mkdir()
        sibling.write_text("pkgver=1.0.0\n")
        run(repository, "add", "packages")
        run(repository, "-c", "user.name=test", "-c", "user.email=test@example.invalid",
            "commit", "-m", "centralize packages")
        run(repository, "push", "origin", "main")
        manifest = json.loads((self.candidate / "manifest.json").read_text())
        manifest["base_commit"] = run(repository, "rev-parse", "HEAD")
        (self.candidate / "manifest.json").write_text(json.dumps(manifest))
        self.root = package
        publish.ROOT = package
        return sibling

    def test_nested_package_preserves_staged_and_unstaged_sibling_changes(self) -> None:
        sibling = self.nest_package()
        sibling.write_text("pkgver=1.0.1\n")
        run(sibling.parent, "add", "PKGBUILD")
        sibling.write_text("pkgver=1.0.2\n")
        head = run(self.root, "rev-parse", "HEAD")
        publish.publish(self.candidate, self.aur, "main")
        self.assertEqual(run(self.root, "rev-parse", "HEAD"), head)
        self.assertEqual(sibling.read_text(), "pkgver=1.0.2\n")
        self.assertEqual(run(sibling.parent, "show", ":packages/other/PKGBUILD"), "pkgver=1.0.1")
        self.assertEqual(publish.pair(self.root), publish.pair(self.candidate))
        self.assertEqual(publish.pair(self.aur), publish.pair(self.candidate))

    def test_nested_package_rejects_own_unstaged_changes(self) -> None:
        self.assert_nested_package_rejects_own_changes(staged=False)

    def test_nested_package_rejects_own_staged_changes(self) -> None:
        self.assert_nested_package_rejects_own_changes(staged=True)

    def assert_nested_package_rejects_own_changes(self, *, staged: bool) -> None:
        self.nest_package()
        original = publish.pair(self.root)
        aur_head = run(self.aur, "rev-parse", "HEAD")
        (self.root / "README.md").write_text("Unreviewed packaging changes\n")
        if staged:
            run(self.root, "add", "README.md")
        with self.assertRaisesRegex(publish.PublishError, "git diff"):
            publish.publish(self.candidate, self.aur, "main")
        self.assertEqual(publish.pair(self.root), original)
        self.assertEqual(run(self.aur_remote, "rev-parse", "master"), aur_head)
        self.assertEqual(run(self.aur, "rev-parse", "HEAD"), aur_head)


if __name__ == "__main__":
    unittest.main()
