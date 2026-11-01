#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Shared Arch toolchain, clean builds, and artifact-consuming runtime checks."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def command(*args: str, cwd: Path | None = None, capture: bool = False) -> str:
    result = subprocess.run(args, cwd=cwd, check=True, text=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else ""


def safe_name(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(p in (".", "..") for p in path.parts):
        raise ValueError(f"Unsafe relative filename: {name}")
    return name


def spec(name: str, repo: Path = ROOT) -> dict:
    catalog = tomllib.loads((repo / ".config/packages.toml").read_text())["packages"]
    if name not in catalog:
        raise ValueError(f"Declare {name} in .config/packages.toml")
    result = catalog[name]
    files = result.get("export_files", ["PKGBUILD", ".SRCINFO"])
    if (not isinstance(files, list) or len(set(files)) != len(files)
            or not {"PKGBUILD", ".SRCINFO"}.issubset(files)):
        raise ValueError(f"Invalid export_files for {name}")
    for filename in files:
        safe_name(filename)
    for key in ("update_hook", "smoke_hook", "nvchecker"):
        if key in result:
            safe_name(result[key])
    dependencies = result.get("smoke_dependencies", [])
    if not isinstance(dependencies, list) or any(not re.fullmatch(r"[a-z0-9][a-z0-9+_.-]*", p) for p in dependencies):
        raise ValueError(f"Invalid smoke dependencies for {name}")
    return result | {"export_files": files, "smoke_dependencies": dependencies}


def copy_recipe(name: str, destination: Path, repo: Path = ROOT) -> None:
    config = spec(name, repo)
    source = repo / "packages" / name
    files = [*config["export_files"]]
    if "update_hook" in config:
        files.append(config["update_hook"])
    if (source / ".nvchecker.toml").is_file():
        files.append(".nvchecker.toml")
    destination.mkdir(parents=True)
    for filename in files:
        origin = source / filename
        if not origin.is_file() or origin.is_symlink() or not origin.resolve().is_relative_to(source.resolve()):
            raise ValueError(f"Missing or unsafe package file: {name}/{filename}")
        target = destination / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, target)


def tool_image(repo: Path = ROOT) -> str:
    override = os.environ.get("AUR_TOOL_IMAGE")
    if override:
        return override
    dockerfile = repo / ".config/arch/Dockerfile"
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    digest = hashlib.sha256(dockerfile.read_bytes()).hexdigest()[:12]
    tag = f"liblaf/aur-tools:{day}-{digest}"
    found = subprocess.run(("docker", "image", "inspect", tag), capture_output=True)
    if found.returncode:
        command("docker", "build", "--pull", "--build-arg", f"BUILD_DATE={day}",
                "-f", str(dockerfile), "-t", tag, str(dockerfile.parent))
    return tag


def container(image: str, mode: str, *args: str, work: Path | None = None,
              artifact: Path | None = None, privileged: bool = False,
              metadata_token: bool = False, capture: bool = False) -> str:
    options = ["docker", "run", "--rm", "--volume", f"{ROOT}:/repo:ro"]
    if privileged:
        options += ["--privileged", "--tmpfs", "/run"]
    if work is not None:
        options += ["--volume", f"{work.resolve()}:/work",
                    "--env", f"AUR_HOST_UID={os.getuid()}", "--env", f"AUR_HOST_GID={os.getgid()}"]
    if artifact is not None:
        options += ["--volume", f"{artifact.resolve()}:/artifacts:ro"]
    environment = os.environ.copy()
    if metadata_token:
        if not environment.get("GH_TOKEN") and shutil.which("gh"):
            token = subprocess.run(("gh", "auth", "token"), text=True, capture_output=True)
            if token.returncode == 0:
                environment["GH_TOKEN"] = token.stdout.strip()
        options += ["--env", "GH_TOKEN"]
    result = subprocess.run((*options, image, "bash", "/repo/scripts/arch.sh", mode, *args),
                            env=environment, check=True, text=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else ""


def version(name: str, operation: str, repo: Path = ROOT) -> dict | None:
    config = spec(name, repo)
    image = tool_image(repo)
    destination = repo / "packages" / name
    before = {f: (destination / f).read_bytes() for f in ("PKGBUILD", ".SRCINFO")}
    with tempfile.TemporaryDirectory(prefix="aur-version-work-") as temporary:
        work = Path(temporary)
        copy_recipe(name, work / "project", repo)
        args = [name, config.get("update_hook", "-"), config.get("nvchecker", "-")]
        result = container(image, f"version-{operation}", *args, work=work,
                           metadata_token=True, capture=operation == "check")
        if operation == "check":
            return json.loads(result)
        # Tools mutate only the disposable staging copy. Roll back both files if
        # copying an updated pair into the checkout fails.
        if any((destination / f).read_bytes() != data for f, data in before.items()):
            raise ValueError("Package changed while version tools were running")
        try:
            for filename in before:
                shutil.copyfile(work / "project" / filename, destination / filename)
        except BaseException:
            for filename, data in before.items():
                (destination / filename).write_bytes(data)
            raise
    return None


def hashes(directory: Path) -> dict[str, str]:
    values = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("Artifact bundle contains a non-regular entry")
        if path.is_file():
            values[path.relative_to(directory).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return values


def verify_bundle(directory: Path, name: str) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("format") != "aur-build-v1" or manifest.get("package") != name:
        raise ValueError("Unexpected build artifact identity")
    actual = hashes(directory)
    actual.pop("manifest.json")
    if manifest.get("sha256") != actual:
        raise ValueError("Build artifacts changed after validation")
    archives = manifest.get("archives", [])
    if not archives or len(set(archives)) != len(archives):
        raise ValueError("Missing or duplicate package archives")
    info = read_srcinfo((directory / "recipe/.SRCINFO").read_text())
    prefixes = archive_prefixes(info)
    for filename in archives:
        if not safe_name(filename).startswith("packages/") or filename not in actual:
            raise ValueError("Package archive is outside the package output directory")
        if not matches_archive(Path(filename).name, prefixes):
            raise ValueError("Package archive filename differs from the recipe outputs")
    for package in info["pkgname"]:
        if not any(matches_archive(Path(f).name, archive_prefixes(info, {package})) for f in archives):
            raise ValueError("Missing required recipe archive in build bundle")
    return manifest


def build(name: str, output: Path, *, update: bool = False, base: str | None = None) -> None:
    config = spec(name)
    if output.is_symlink():
        raise ValueError("Build output must not be a symlink")
    output = output.resolve()
    if output.exists():
        if output.is_symlink() or not output.is_dir():
            raise ValueError("Build output must be a directory")
        if any(output.iterdir()):
            verify_bundle(output, name)
    image = tool_image()
    base = base or os.environ.get("GITHUB_SHA") or command("git", "rev-parse", "HEAD", cwd=ROOT, capture=True)
    with tempfile.TemporaryDirectory(prefix="aur-build-work-") as temporary:
        work = Path(temporary)
        copy_recipe(name, work / "project")
        container(image, "prepare", name, config.get("update_hook", "-"),
                  config.get("nvchecker", "-"), str(int(update)), work=work, metadata_token=update)
        # This process has no metadata token, host home, SSH agent, or Docker socket.
        container(image, "build", name, work=work, privileged=True)
        bundle = work / "bundle"
        bundle.mkdir()
        for filename in config["export_files"]:
            target = bundle / "recipe" / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(work / "project" / filename, target)
        for dirname in ("packages", "sources"):
            shutil.copytree(work / dirname, bundle / dirname)
        archives = json.loads((work / "build-report.json").read_text())
        manifest = {"format": "aur-build-v1", "package": name, "base_commit": base,
                    "image": command("docker", "image", "inspect", image, "--format", "{{.Id}}", capture=True),
                    "archives": ["packages/" + f for f in archives], "sha256": hashes(bundle)}
        (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        verify_bundle(bundle, name)
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            shutil.rmtree(output)
        shutil.copytree(bundle, output)
    print(f"Clean-chroot build artifacts: {output}")


def smoke(name: str, artifact: Path, candidate: Path | None = None) -> None:
    artifact = artifact.resolve()
    manifest = verify_bundle(artifact, name)
    container(tool_image(), "smoke", name, artifact=artifact)
    verify_bundle(artifact, name)
    if candidate is None:
        return
    config = spec(name)
    if candidate.is_symlink():
        raise ValueError("Candidate output must not be a symlink")
    candidate = candidate.resolve()
    files = config["export_files"]
    if candidate.exists() and any(candidate.iterdir()):
        allowed = {*files, "manifest.json"}
        if not set(hashes(candidate)).issubset(allowed):
            raise ValueError("Candidate output contains unrelated files")
        for path in candidate.rglob("*"):
            if path.is_dir() and not any(f.startswith(path.relative_to(candidate).as_posix() + "/") for f in files):
                raise ValueError("Candidate output contains an unrelated directory")
        shutil.rmtree(candidate)
    candidate.mkdir(parents=True, exist_ok=True)
    for filename in files:
        target = candidate / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(artifact / "recipe" / filename, target)
    info = read_srcinfo((candidate / ".SRCINFO").read_text())
    report = {"base_commit": manifest["base_commit"], "version": info["pkgver"][0],
              "sha256": {f: hashlib.sha256((candidate / f).read_bytes()).hexdigest() for f in files}}
    if name != "manggo":
        report["pkgrel"] = info["pkgrel"][0]
    (candidate / "manifest.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def read_srcinfo(text: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for line in text.splitlines():
        if " = " in line:
            key, value = line.strip().split(" = ", 1)
            result.setdefault(key, []).append(value)
    return result


def implicit_debug_name(info: dict[str, list[str]]) -> str | None:
    if {"!debug", "!strip"}.intersection(info.get("options", [])):
        return None
    name = info["pkgbase"][0] + "-debug"
    return name if name not in info["pkgname"] else None


def archive_prefixes(info: dict[str, list[str]], names: set[str] | None = None) -> set[str]:
    if names is None:
        names = set(info["pkgname"])
        debug = implicit_debug_name(info)
        if debug:
            names.add(debug)
    return {f"{package}-{info['pkgver'][0]}-{info['pkgrel'][0]}-{arch}.pkg.tar"
            for package in names for arch in info["arch"]}


def matches_archive(filename: str, prefixes: set[str]) -> bool:
    return any(filename == prefix or re.fullmatch(re.escape(prefix) + r"\.[A-Za-z0-9]+", filename)
               for prefix in prefixes)


class CandidatePolicy:
    """Validate conventional version updates for GitHub recording, without AUR writes."""

    def __init__(self, repo: Path, name: str, files: list[str]):
        self.repo, self.name, self.FILES = repo, name, tuple(files)

    @staticmethod
    def recipe_shape(text: str) -> str:
        for key, pattern in (("pkgver", r"[A-Za-z0-9_.+~]+"), ("pkgrel", r"[0-9]+(?:\.[0-9]+)?")):
            text, count = re.subn(rf"^{key}=['\"]?({pattern})['\"]?$", key + "=<release>", text,
                                 flags=re.MULTILINE)
            if count != 1:
                raise ValueError(f"Expected a literal {key} in conventional recipe")
        def checksum(match):
            values = re.findall(r"['\"]([^'\"]+)['\"]", match[2])
            if not values or not re.fullmatch(r"[\s'\"0-9a-fASKIP]+", match[2]):
                raise ValueError("Conventional checksums must be literal hashes")
            if any(v != "SKIP" and not re.fullmatch(r"[0-9a-f]{32,128}", v) for v in values):
                raise ValueError("Invalid conventional checksum")
            return match[1] + "(" + " ".join("SKIP" if v == "SKIP" else "<hash>" for v in values) + ")"
        return re.sub(r"^((?:sha[0-9]+|md5|b2)sums(?:_[A-Za-z0-9_]+)?=)\(([^)]*)\)", checksum, text,
                      flags=re.MULTILINE)

    @staticmethod
    def info_shape(text: str, version: str) -> str:
        lines = []
        for line in text.splitlines():
            if re.match(r"\s*(pkgver|pkgrel) = ", line):
                line = line.split(" = ", 1)[0] + " = <release>"
            elif re.match(r"\s*(source|noextract)(?:_[A-Za-z0-9_]+)? = ", line):
                line = line.replace(version, "<version>")
            elif re.match(r"\s*(?:sha[0-9]+|md5|b2)sums(?:_[A-Za-z0-9_]+)? = ", line):
                key, value = line.split(" = ", 1)
                if value != "SKIP" and not re.fullmatch(r"[0-9a-f]{32,128}", value):
                    raise ValueError("Invalid .SRCINFO checksum")
                line = key + " = " + ("SKIP" if value == "SKIP" else "<hash>")
            lines.append(line)
        return "\n".join(lines)

    def validate(self, candidate: Path) -> dict:
        manifest = json.loads((candidate / "manifest.json").read_text())
        if set(manifest) != {"base_commit", "version", "pkgrel", "sha256"}:
            raise ValueError("Unexpected conventional manifest fields")
        head = command("git", "rev-parse", "HEAD", cwd=self.repo, capture=True)
        if manifest["base_commit"] != head or set(manifest["sha256"]) != set(self.FILES):
            raise ValueError("Conventional candidate has an unexpected source or file list")
        actual = hashes(candidate)
        actual.pop("manifest.json")
        if actual != manifest["sha256"]:
            raise ValueError("Conventional candidate digest mismatch")
        trusted = self.repo / "packages" / self.name
        before, after = [(directory / "PKGBUILD").read_text() for directory in (trusted, candidate)]
        if self.recipe_shape(before) != self.recipe_shape(after):
            raise ValueError("Conventional candidate changes code outside release fields")
        old_info, new_info = [(directory / ".SRCINFO").read_text() for directory in (trusted, candidate)]
        old, new = read_srcinfo(old_info), read_srcinfo(new_info)
        if (new.get("pkgbase") != [self.name] or new.get("pkgver") != [manifest["version"]]
                or new.get("pkgrel") != [manifest["pkgrel"]]):
            raise ValueError("Conventional candidate version identity mismatch")
        for key in ("pkgver", "pkgrel"):
            if not re.search(rf"^{key}=['\"]?{re.escape(new[key][0])}['\"]?$", after, flags=re.MULTILINE):
                raise ValueError("Conventional PKGBUILD and .SRCINFO disagree")
        if self.info_shape(old_info, old["pkgver"][0]) != self.info_shape(new_info, new["pkgver"][0]):
            raise ValueError("Conventional candidate changes non-release metadata")
        arrays = re.findall(r"^((?:sha[0-9]+|md5|b2)sums(?:_[A-Za-z0-9_]+)?)=\(([^)]*)\)",
                            after, flags=re.MULTILINE)
        for key, body in arrays:
            if new.get(key) != re.findall(r"['\"]([^'\"]+)['\"]", body):
                raise ValueError("Conventional recipe and .SRCINFO checksums disagree")
        for filename in self.FILES:
            if filename not in ("PKGBUILD", ".SRCINFO") and (trusted / filename).read_bytes() != (candidate / filename).read_bytes():
                raise ValueError("Conventional candidate changes a non-release file")
        return manifest


def namcap(*paths: Path) -> None:
    for path in paths:
        result = subprocess.run(("namcap", str(path)), text=True, capture_output=True)
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        if result.returncode or re.search(r"\bE: ", result.stdout + result.stderr):
            raise RuntimeError(f"namcap rejected {path.name}")


def inside_build_report(name: str, work: Path = Path("/work")) -> None:
    info = read_srcinfo((work / "project/.SRCINFO").read_text())
    expected_version = info["pkgver"][0] + "-" + info["pkgrel"][0]
    if info.get("epoch", ["0"])[0] != "0":
        expected_version = info["epoch"][0] + ":" + expected_version
    archives = []
    seen = set()
    debug = implicit_debug_name(info)
    expected_names = set(info["pkgname"]) | ({debug} if debug else set())
    optional_debug = archive_prefixes(info, {debug}) if debug else set()
    for line in (work / "package-files").read_text().splitlines():
        path = Path(line)
        if path.is_symlink() or path.parent != work / "packages":
            raise ValueError("Unexpected devtools package output")
        # makepkg lists the optional debug package even when there were no ELF
        # debug symbols to package. Required recipe outputs must always exist.
        if not path.exists() and matches_archive(path.name, optional_debug):
            continue
        if not path.is_file():
            raise ValueError("Missing required devtools package output")
        metadata = read_srcinfo(command("bsdtar", "-xOf", str(path), ".PKGINFO", capture=True))
        pkgname = metadata.get("pkgname", [None])[0]
        if pkgname not in expected_names or metadata.get("pkgver") != [expected_version]:
            raise ValueError("Built package identity differs from .SRCINFO")
        seen.add(pkgname)
        archives.append(path.name)
    if not set(info["pkgname"]).issubset(seen):
        raise ValueError("devtools did not produce every required package")
    (work / "build-report.json").write_text(json.dumps(archives) + "\n")


def inside_smoke(name: str) -> None:
    if os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"):
        raise ValueError("Runtime checks must not receive a GitHub token")
    config = spec(name)
    artifact = Path("/artifacts")
    manifest = verify_bundle(artifact, name)
    if config["smoke_dependencies"]:
        command("pacman", "-S", "--needed", "--noconfirm", *config["smoke_dependencies"])
    archives = [artifact / name for name in manifest["archives"]]
    command("pacman", "-U", "--noconfirm", *map(str, archives))
    # Resolve dependencies against the same installed environment used for smoke checks.
    namcap(*archives)
    if config.get("smoke_hook"):
        project = Path("/home/builder/project")
        shutil.copytree(artifact / "recipe", project)
        command("chown", "-R", "builder:builder", str(project))
        command("bash", str(ROOT / "packages" / name / config["smoke_hook"]),
                str(project), str(archives[0]), str(artifact / "sources"))
    else:
        for archive in archives:
            metadata = read_srcinfo(command("bsdtar", "-xOf", str(archive), ".PKGINFO", capture=True))
            command("pacman", "-Qk", metadata["pkgname"][0])


def inside_lint(names: list[str]) -> None:
    sources = sorted((ROOT / "scripts").glob("*.sh")) + sorted((ROOT / ".config").rglob("*.sh"))
    for name in names:
        root = ROOT / "packages" / name
        spec(name)
        sources += [root / "PKGBUILD", *sorted(root.glob("*.install")), *sorted((root / "scripts").glob("*.sh"))]
        namcap(root / "PKGBUILD")
    command("shellcheck", "--shell=bash", *map(str, sources))
    command("actionlint", *map(str, sorted((ROOT / ".github/workflows").glob("*.yml"))))


if __name__ == "__main__":
    try:
        if sys.argv[1] == "build-report":
            inside_build_report(sys.argv[2])
        elif sys.argv[1] == "smoke":
            inside_smoke(sys.argv[2])
        elif sys.argv[1] == "lint":
            inside_lint(sys.argv[2:])
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"pipeline: {error}", file=sys.stderr)
        sys.exit(1)
