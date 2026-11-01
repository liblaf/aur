"""Offline protocol tests: real nvchecker cmd source and executable pkgctl fixtures."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("versions", REPO / "scripts/versions.py")
versions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(versions)


class VersionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="versions-test-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root = self.directory / "staged package"
        self.root.mkdir()
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        (self.root / "PKGBUILD").write_text("pkgname=fixture\npkgver=1\npkgrel=1\n")
        (self.root / ".SRCINFO").write_text("original srcinfo\n")
        self.before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        self.environment = patch.dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                                      GH_TOKEN="test-only-secret")
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def executable(self, name, source):
        path = self.bin / name
        path.write_text("#!/usr/bin/env python3\n" + source)
        path.chmod(0o755)
        return path

    def hook(self, fields):
        # Lives outside the staging root to exercise explicit trusted hook paths.
        path = self.directory / "fixture hook.py"
        path.write_text("import os, sys\nfrom pathlib import Path\n"
                        "assert Path.cwd() == Path(os.environ['FIXTURE_ROOT'])\n"
                        "assert os.environ['GH_TOKEN'] == 'test-only-secret'\n"
                        "counter = Path(os.environ['FIXTURE_COUNTER'])\n"
                        "counter.write_text(counter.read_text() + 'check\\n' if counter.exists() else 'check\\n')\n"
                        "if '--check' in sys.argv:\n"
                        "    Path(sys.argv[sys.argv.index('--github-output') + 1]).write_text("
                        + repr("".join(f"{k}={v}\n" for k, v in fields.items())) + ")\n"
                        "print('noisy helper output test-only-secret')\n")
        os.environ["FIXTURE_ROOT"] = str(self.root)
        os.environ["FIXTURE_COUNTER"] = str(self.directory / "calls")
        return path

    def real_nvchecker(self):
        uvx = shutil.which("uvx")
        if not uvx:
            self.skipTest("uvx is unavailable")
        ready = subprocess.run([uvx, "--offline", "--from", "nvchecker", "nvchecker", "--version"],
                               capture_output=True)
        if ready.returncode:
            self.skipTest("nvchecker is not cached for offline uvx")
        self.executable("nvchecker", "import os, sys\nos.execv(" + repr(uvx) + ", "
                        + repr([uvx, "--offline", "--from", "nvchecker", "nvchecker"])
                        + " + sys.argv[1:])\n")

    def assert_unchanged(self):
        self.assertEqual(self.before, {p.name: p.read_bytes() for p in self.root.iterdir()})

    def test_real_nvchecker_one_discovery_staged_root_and_no_writes(self):
        self.real_nvchecker()
        fields = {"changed": "true", "latest": "2.3.4"}
        result = versions.check("manggo", self.root, hook=self.hook(fields))
        self.assertEqual(result, fields)
        self.assertEqual((self.directory / "calls").read_text(), "check\n")
        self.assert_unchanged()
        self.assertNotIn("test-only-secret", (REPO / ".config/nvchecker/manggo.toml").read_text())

    def test_real_nvchecker_model_only_identity_and_original_fields(self):
        self.real_nvchecker()
        fields = {"changed": "true", "pkgver": "1.alpha.smart.r1.gabcdef0", "pkgrel": "2",
                  "commit": "abcdef0", "binary_sha256": "a" * 64, "model_sha256": "b" * 64}
        self.assertEqual(versions.check("mihomo-smart-bin", self.root, hook=self.hook(fields)), fields)
        first = versions._identity("mihomo-smart-bin", fields)
        fields["model_sha256"] = "c" * 64
        self.assertNotEqual(first, versions._identity("mihomo-smart-bin", fields))
        self.assertNotEqual(first, fields["pkgver"])
        self.assertEqual(versions.check("mihomo-smart-bin", self.root, hook=self.hook(fields)), fields)
        self.assertEqual((self.directory / "calls").read_text(), "check\ncheck\n")
        self.assert_unchanged()

    def test_new_core_discovery_keeps_pkgver_unresolved(self):
        self.real_nvchecker()
        fields = {"changed": "true", "pkgrel": "1", "commit": "abcdef0",
                  "binary_sha256": "a" * 64, "model_sha256": "b" * 64}
        self.assertEqual(versions.check("mihomo-smart-bin", self.root, hook=self.hook(fields)), fields)
        self.assertNotIn("pkgver", fields)
        self.assertEqual((self.directory / "calls").read_text(), "check\n")
        self.assert_unchanged()

    def test_real_nvchecker_failing_hook_does_not_leak_output(self):
        self.real_nvchecker()
        hook = self.directory / "broken.py"
        hook.write_text("import sys\nprint('test-only-secret raw HTTP', file=sys.stderr)\nsys.exit(1)\n")
        with self.assertRaises(versions.VersionError) as caught:
            versions.check("manggo", self.root, hook=hook)
        self.assertNotIn("test-only-secret", str(caught.exception))
        self.assert_unchanged()

    def test_nvchecker_exit_zero_requires_matching_success(self):
        hook = self.hook({"changed": "false", "latest": "2.3.4"})
        for records in ([], [{"name": "other", "event": "updated", "version": "2.3.4"}],
                        [{"name": "manggo", "event": "updated", "version": "9.9.9"}],
                        [{"name": "manggo", "event": "no-result", "level": "error"}],
                        [{"name": "manggo", "version": "2.3.4"}]):
            with self.subTest(records=records):
                self.executable("nvchecker", "import os, subprocess, sys\n"
                                "subprocess.run([sys.executable, os.environ['AUR_VERSION_ADAPTER'], 'hook', "
                                "'--package', 'manggo'], check=True, capture_output=True)\n"
                                + "print(" + repr("\n".join(json.dumps(r) for r in records)) + ")\n")
                with self.assertRaises(versions.VersionError):
                    versions.check("manggo", self.root, hook=hook)
        self.assert_unchanged()

    def test_nvchecker_success_without_side_report_is_rejected(self):
        self.executable("nvchecker", "print(" + repr(json.dumps(
            {"name": "manggo", "event": "updated", "version": "2.3.4"})) + ")\n")
        with self.assertRaises(versions.VersionError):
            versions.check("manggo", self.root)
        self.assert_unchanged()

    def test_missing_and_invalid_hook_reports_fail_closed(self):
        self.real_nvchecker()
        for fields in ({}, {"changed": "yes", "latest": "2.3.4"},
                       {"changed": "true"}, {"changed": "false", "latest": "raw HTTP"}):
            with self.subTest(fields=fields), self.assertRaises(versions.VersionError):
                versions.check("manggo", self.root, hook=self.hook(fields))
        self.assert_unchanged()

    def native(self, records, code=0):
        (self.root / ".nvchecker.toml").write_text('[fixture]\nsource="manual"\nmanual="2"\n')
        self.executable("pkgctl", "import json, os, sys\n"
                        + "assert os.getcwd() == " + repr(str(self.root)) + "\n"
                        + "assert sys.argv[1:] == ['version', 'check', '--json']\n"
                        + "print(" + repr(json.dumps(records)) + ")\n"
                        + f"sys.exit({code})\n")

    def record(self, **overrides):
        return {"pkgbase": "fixture", "status": "success", "message": None,
                "out_of_date": True, "local_version": "1", "upstream_version": "2", **overrides}

    def test_pkgctl_valid_outdated_for_both_exit_conventions(self):
        for code in (0, 2):
            self.native([self.record()], code)
            before = {p.name: p.read_bytes() for p in self.root.iterdir()}
            self.assertEqual(versions.check("fixture", self.root),
                             {"changed": "true", "latest": "2", "pkgver": "1"})
            self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})
        self.native([self.record(status="warning", out_of_date=False, local_version="3")])
        self.assertEqual(versions.check("fixture", self.root)["changed"], "false")

    def test_pkgctl_zero_exit_does_not_hide_failure_or_missing_record(self):
        for records in ([], {}, [self.record(pkgbase="wrong")],
                        [self.record(status="failure")], [self.record(error="test-only-secret")],
                        [self.record(upstream_version=None)], [self.record(out_of_date="false")],
                        [self.record(), self.record()], [self.record(status="unknown")]):
            with self.subTest(records=records):
                self.native(records)
                with self.assertRaises(versions.VersionError) as caught:
                    versions.check("fixture", self.root)
                self.assertNotIn("test-only-secret", str(caught.exception))

    def test_pkgctl_malformed_json_and_error_exit(self):
        self.native([self.record()], 1)
        with self.assertRaises(versions.VersionError):
            versions.check("fixture", self.root)
        self.executable("pkgctl", "print('raw HTTP test-only-secret')\n")
        with self.assertRaises(versions.VersionError):
            versions.check("fixture", self.root)

    def test_native_update_orders_upgrade_before_srcinfo_and_uses_root(self):
        (self.root / ".nvchecker.toml").write_text('[fixture]\nsource="manual"\nmanual="2"\n')
        self.executable("pkgctl", "import sys\nfrom pathlib import Path\n"
                        "assert sys.argv[1:] == ['version', 'upgrade']\n"
                        "Path('PKGBUILD').write_text('upgraded recipe')\n")
        self.executable("makepkg", "import sys\nfrom pathlib import Path\n"
                        "assert sys.argv[1:] == ['--printsrcinfo']\n"
                        "assert Path('PKGBUILD').read_text() == 'upgraded recipe'\n"
                        "print('pkgbase = fixture\\npkgver = 2')\n")
        versions.update("fixture", self.root)
        self.assertEqual((self.root / ".SRCINFO").read_text(), "pkgbase = fixture\npkgver = 2\n")

    def test_custom_update_only_invokes_update_hook(self):
        hook = self.directory / "updater.py"
        hook.write_text("import sys\nfrom pathlib import Path\n"
                        "assert sys.argv[1:] == ['--update']\nPath('PKGBUILD').write_text('updated')\n")
        versions.update("manggo", self.root, hook=hook)
        self.assertEqual((self.root / "PKGBUILD").read_text(), "updated")

    def test_failed_native_upgrade_preserves_srcinfo(self):
        (self.root / ".nvchecker.toml").touch()
        self.executable("pkgctl", "import sys\nsys.exit(1)\n")
        with self.assertRaises(versions.VersionError):
            versions.update("fixture", self.root)
        self.assertEqual((self.root / ".SRCINFO").read_text(), "original srcinfo\n")


if __name__ == "__main__":
    unittest.main()
