"""Exercise publication ordering and artifact boundaries in a real Git checkout."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("aur", Path(__file__).resolve().parents[1] / "scripts/aur.py")
aur = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(aur)


class SharedPublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.repository = self.directory / "repository"
        self.repository.mkdir()
        for name in ("manggo", "mihomo-smart-bin"):
            package = self.repository / "packages" / name
            package.mkdir(parents=True)
            (package / "PKGBUILD").write_text("trusted recipe\n")
        self.git("init", "--initial-branch=main")
        self.git("add", ".")
        self.git("-c", "user.name=test", "-c", "user.email=test@example.test",
                 "-c", "commit.gpgsign=false", "commit", "-m", "base")
        self.candidates = self.directory / "candidates"
        self.candidates.mkdir()
        for name in ("manggo", "mihomo-smart-bin"):
            (self.candidates / ("candidate-" + name)).mkdir()
        self.aur_root = self.directory / "aur"
        self.events = []
        self.invalid = None
        self.addCleanup(patch.stopall)
        patch.object(aur, "ROOT", self.repository).start()
        # run's default cwd was bound at definition; route to this checkout.
        original_run = aur.run
        def run(*args, **kwargs):
            if "clone" in args:
                self.events.append(("clone", args[-1]))
                return ""
            return original_run(*args, cwd=self.repository, **kwargs)
        patch.object(aur, "run", side_effect=run).start()
        patch.object(aur, "publisher", side_effect=self.publisher).start()
        patch.dict(os.environ, GITHUB_REPOSITORY=aur.REPOSITORY, AUR_PUBLISH_ENABLED="true",
                   GIT_SSH_COMMAND="ssh -i /dedicated-key -oIdentitiesOnly=yes").start()

    def git(self, *args):
        return subprocess.run(("git", "-c", "core.hooksPath=/dev/null", *args), cwd=self.repository,
                              capture_output=True, text=True, check=True).stdout.strip()

    def publisher(self, name):
        def validate(candidate):
            self.events.append(("validate", name))
            if name == self.invalid:
                raise ValueError("invalid candidate")
        def publish(candidate, checkout, branch, **kwargs):
            self.events.append(("publish", name))
            (self.repository / "packages" / name / "PKGBUILD").write_text("validated recipe\n")
        return SimpleNamespace(FILES=("PKGBUILD", ".SRCINFO"), validate=validate, publish=publish)

    def test_all_candidates_validated_before_first_external_write(self):
        files = aur.publish(self.candidates, self.aur_root).splitlines()
        self.assertEqual(self.events[:2], [("validate", "manggo"), ("validate", "mihomo-smart-bin")])
        self.assertEqual([event for event in self.events if event[0] == "publish"],
                         [("publish", "manggo"), ("publish", "mihomo-smart-bin")])
        self.assertEqual(files, [f"packages/{name}/{file}" for name in ("manggo", "mihomo-smart-bin")
                                 for file in ("PKGBUILD", ".SRCINFO", "automation-validation.json")])

    def test_invalid_second_candidate_prevents_all_publication(self):
        self.invalid = "mihomo-smart-bin"
        with self.assertRaisesRegex(ValueError, "invalid candidate"):
            aur.publish(self.candidates, self.aur_root)
        self.assertTrue(all(event[0] == "validate" for event in self.events))

    def test_unknown_or_symlink_artifacts_are_rejected_before_publication(self):
        for symlink in (False, True):
            unexpected = self.candidates / "candidate-unexpected"
            if symlink:
                unexpected.symlink_to(self.directory, target_is_directory=True)
            else:
                unexpected.mkdir()
            with self.assertRaisesRegex(ValueError, "Unexpected candidate"):
                aur.publish(self.candidates, self.aur_root)
            self.assertEqual(self.events, [])
            if symlink:
                unexpected.unlink()
            else:
                unexpected.rmdir()

    def test_dirty_repository_prevents_all_publication(self):
        (self.repository / "unreviewed").write_text("pending work\n")
        with self.assertRaisesRegex(ValueError, "clean repository"):
            aur.publish(self.candidates, self.aur_root)
        self.assertEqual(self.events, [])

    def test_disabled_publication_prevents_even_candidate_processing(self):
        with patch.dict(os.environ, AUR_PUBLISH_ENABLED="false"):
            with self.assertRaisesRegex(ValueError, "publication is disabled"):
                aur.publish(self.candidates, self.aur_root)
        self.assertEqual(self.events, [])

    def test_record_only_validates_and_copies_without_contacting_aur(self):
        for name in ("manggo", "mihomo-smart-bin"):
            candidate = self.candidates / ("candidate-" + name)
            (candidate / "manifest.json").write_text(json.dumps(
                {"version": "1.0.0", "base_commit": self.git("rev-parse", "HEAD")}))
            for filename in ("PKGBUILD", ".SRCINFO"):
                (candidate / filename).write_text("validated metadata\n")
        with patch.dict(os.environ, AUR_PUBLISH_ENABLED="false", GITHUB_RUN_ID="123"):
            files = aur.record(self.candidates).splitlines()
        self.assertEqual(self.events, [("validate", "manggo"), ("validate", "mihomo-smart-bin")])
        for name in ("manggo", "mihomo-smart-bin"):
            root = self.repository / "packages" / name
            self.assertEqual((root / "PKGBUILD").read_text(), "validated metadata\n")
            self.assertEqual(json.loads((root / "automation-validation.json").read_text())["version"], "1.0.0")
            self.assertNotIn(f"packages/{name}/aur-state.json", files)


if __name__ == "__main__":
    unittest.main()
