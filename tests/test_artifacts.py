"""Exercise recipe artifacts with offline staging, ZIP extraction and application."""

import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from zipfile import ZipFile

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class PackageArtifactTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.checkout = self.root / "checkout"
        for name in ("faceform-wrap", "manggo", "mihomo-smart-bin"):
            self.write_recipe(self.checkout, name, "old")
        self.git("init", "--quiet")
        self.git("add", "packages")
        self.git(
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "--quiet",
            "--message=Initial recipes",
        )
        self.runner_temp = self.root / "release-temp"
        self.download = self.runner_temp / "packages"
        self.output = self.root / "github-output"

    def git(self, *arguments):
        return subprocess.run(
            ["git", *arguments],
            cwd=self.checkout,
            check=True,
            capture_output=True,
            text=True,
        )

    def write_recipe(self, checkout, name, version):
        recipe = checkout / "packages" / name
        recipe.mkdir(parents=True)
        contents = {
            "PKGBUILD": f"pkgname={name}\npkgver={version}\n",
            ".SRCINFO": f"pkgbase = {name}\n\tpkgver = {version}\n",
            "launcher.sh": f"#!/bin/sh\necho {name}-{version}\n",
        }
        for filename, content in contents.items():
            (recipe / filename).write_text(content)
        return contents

    def stage_and_download(self, name):
        build = self.root / f"build-{name}"
        contents = self.write_recipe(build, name, "tested")
        build_temp = self.root / f"temp-{name}"
        subprocess.run(
            ["bash", str(SCRIPTS / "stage-package.sh"), name],
            cwd=build,
            env={**os.environ, "RUNNER_TEMP": str(build_temp)},
            check=True,
            capture_output=True,
            text=True,
        )
        staging = build_temp / "artifacts"
        archive_path = self.root / f"package-{name}.zip"
        # upload-artifact stores paths relative to its selected upload directory.
        with ZipFile(archive_path, "w") as archive:
            for path in staging.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(staging))
        with ZipFile(archive_path) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {f"package-{name}/{filename}" for filename in contents},
            )
            # merge-multiple extracts every archive into this same directory.
            archive.extractall(self.download)
        return contents

    def apply(self):
        return subprocess.run(
            ["bash", str(SCRIPTS / "apply-packages.sh")],
            cwd=self.checkout,
            env={
                **os.environ,
                "RUNNER_TEMP": str(self.runner_temp),
                "GITHUB_OUTPUT": str(self.output),
            },
            capture_output=True,
            text=True,
        )

    def assert_applied(self, recipes):
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        names = sorted(recipes)
        files = []
        for name in names:
            for filename, content in sorted(recipes[name].items()):
                path = f"packages/{name}/{filename}"
                files.append(path)
                self.assertEqual((self.checkout / path).read_text(), content)
        self.assertEqual(
            self.output.read_text(),
            f"names={' '.join(names)}\nfiles<<EOF\n" + "\n".join(files) + "\nEOF\n",
        )

    def test_single_faceform_archive_preserves_wrapper_and_srcinfo(self):
        self.assert_applied({"faceform-wrap": self.stage_and_download("faceform-wrap")})
        self.assertIn(
            "pkgver=old", (self.checkout / "packages/manggo/PKGBUILD").read_text()
        )

    def test_two_archives_merge_and_apply_their_own_recipes(self):
        # Download order does not determine the package names output order.
        recipes = {
            name: self.stage_and_download(name) for name in ("manggo", "faceform-wrap")
        }
        self.assert_applied(recipes)

    def test_empty_artifact_set_fails_instead_of_succeeding_without_packages(self):
        self.download.mkdir(parents=True)
        result = self.apply()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No tested package artifacts found", result.stderr)
        self.assertFalse(self.output.exists())

    def test_incomplete_recipe_fails_before_any_package_is_applied(self):
        self.stage_and_download("faceform-wrap")
        self.stage_and_download("manggo")
        (self.download / "package-manggo/.SRCINFO").unlink()
        result = self.apply()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expected PKGBUILD and .SRCINFO", result.stderr)
        self.assertIn(
            "pkgver=old",
            (self.checkout / "packages/faceform-wrap/PKGBUILD").read_text(),
        )
        self.assertFalse(self.output.exists())

    def test_unknown_package_fails(self):
        self.stage_and_download("unknown-package")
        result = self.apply()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "unknown-package: no package directory in the checkout", result.stderr
        )
        self.assertFalse((self.checkout / "packages/unknown-package").exists())
        self.assertFalse(self.output.exists())
