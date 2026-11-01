#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Run the package-specific AUR maintenance tools from one repository."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "liblaf/aur"


def run(*args: str, cwd: Path = ROOT, capture: bool = False) -> str:
    result = subprocess.run(args, cwd=cwd, check=True, text=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else ""


def packages() -> list[str]:
    return sorted(path.name for path in (ROOT / "packages").iterdir()
                  if path.is_dir() and not path.is_symlink()
                  and re.fullmatch(r"[a-z0-9][a-z0-9+_.-]*", path.name)
                  and (path / "PKGBUILD").is_file())


def package_path(name: str) -> Path:
    if name not in packages():
        raise ValueError(f"Unknown package: {name}")
    return ROOT / "packages" / name


def publisher(name: str):
    path = package_path(name) / "scripts/publish.py"
    spec = importlib.util.spec_from_file_location("publish_" + name.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def output(path: Path | None, values: dict[str, str]) -> None:
    if path is not None:
        with path.open("a") as stream:
            for key, value in values.items():
                if "\n" in value:
                    stream.write(f"{key}<<AUR_OUTPUT_END\n{value}\nAUR_OUTPUT_END\n")
                else:
                    stream.write(f"{key}={value}\n")
    print(json.dumps(values, sort_keys=True))


def check(name: str, force: bool = False) -> dict[str, str]:
    root = package_path(name)
    with tempfile.TemporaryDirectory(prefix="aur-check-") as temporary:
        directory = Path(temporary)
        release = directory / "release"
        run(sys.executable, str(root / "scripts/update.py"), "--check",
            "--github-output", str(release))
        fields = dict(line.split("=", 1) for line in release.read_text().splitlines())
        aur = directory / "aur"
        run("git", "-c", "core.hooksPath=/dev/null", "clone", "--quiet",
            "--depth", "1", f"https://aur.archlinux.org/{name}.git", str(aur))
        sync = any(not (aur / filename).is_file()
                   or (root / filename).read_bytes() != (aur / filename).read_bytes()
                   for filename in publisher(name).FILES)
    report = root / "automation-validation.json"
    due = True
    if report.exists():
        validated = datetime.fromisoformat(json.loads(report.read_text())["validated_at"])
        due = datetime.now(timezone.utc) - validated >= timedelta(days=28)
    return {key: str(value).lower() for key, value in {
        "changed": fields["changed"] == "true", "aur_sync": sync,
        "maintenance_due": due,
        "needed": force or fields["changed"] == "true" or sync or due,
    }.items()}


def validate(name: str, candidate: Path) -> None:
    root = package_path(name)
    candidate = candidate.resolve()
    candidate.mkdir(parents=True, exist_ok=True)
    base = os.environ.get("GITHUB_SHA") or run("git", "rev-parse", "HEAD", capture=True)
    args = ["docker", "run", "--rm", "--volume", f"{root}:/workspace:ro",
            "--volume", f"{candidate}:/candidate", "--env", f"BASE_COMMIT={base}"]
    if name == "mihomo-smart-bin":
        args += ["--env", "GH_TOKEN"]
    if os.environ.get("SKIP_UPDATE") == "1":
        args += ["--env", "SKIP_UPDATE=1"]
    run(*args, "archlinux:base", "bash", "/workspace/scripts/validate.sh")


def publish(candidates: Path, aur_root: Path) -> str:
    """Prepare every validated package, then let the workflow commit them together."""
    if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
        raise ValueError("Publication requires the trusted GitHub repository")
    if run("git", "status", "--porcelain", "--untracked-files=all", capture=True):
        raise ValueError("Publication requires a clean repository")
    if not os.environ.get("GIT_SSH_COMMAND"):
        raise ValueError("An explicit AUR SSH identity and host configuration are required")
    available = {"candidate-" + name: name for name in packages()}
    entries = sorted(candidates.iterdir())
    if any(entry.is_symlink() or not entry.is_dir() or entry.name not in available for entry in entries):
        raise ValueError("Unexpected candidate artifact")
    selected = [(available[entry.name], entry) for entry in entries]
    # Validate every candidate before the first external write. Individual publishers
    # additionally verify the remote baseline and support interrupted-publication retries.
    for name, candidate in selected:
        publisher(name).validate(candidate)
    aur_root.mkdir(parents=True, exist_ok=True)
    files = []
    for name, candidate in selected:
        module = publisher(name)
        aur = aur_root / name
        run("git", "-c", "core.hooksPath=/dev/null", "clone", "--quiet", "--branch", "master",
            f"ssh://aur@aur.archlinux.org/{name}.git", str(aur))
        if name == "manggo":
            module.publish(candidate, aur, "main", record_validation=True)
        else:
            module.publish(candidate, aur, "main")
        names = [*module.FILES, "automation-validation.json"]
        if (package_path(name) / "aur-state.json").is_file():
            names.append("aur-state.json")
        files.extend(f"packages/{name}/{filename}" for filename in names)
    return "\n".join(files)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    matrix = commands.add_parser("matrix", help="Discover the GitHub Actions package matrix")
    matrix.add_argument("--github-output", type=Path)
    test = commands.add_parser("test", help="Run a package's existing regression checks")
    test.add_argument("package", nargs="?", choices=packages())
    upstream = commands.add_parser("check", help="Check upstream, AUR synchronization, and validation age")
    upstream.add_argument("package", choices=packages())
    upstream.add_argument("--force", action="store_true")
    upstream.add_argument("--github-output", type=Path)
    build = commands.add_parser("validate", help="Build and exercise a package in disposable Arch Linux")
    build.add_argument("package", choices=packages())
    build.add_argument("--candidate", required=True, type=Path)
    release = commands.add_parser("publish", help="Publish validated candidates with the shared AUR key")
    release.add_argument("--candidates", required=True, type=Path)
    release.add_argument("--aur-root", required=True, type=Path)
    release.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "matrix":
            output(args.github_output, {"matrix": json.dumps({"package": packages()})})
        elif args.command == "test":
            if args.package is None:
                run(sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v")
            for name in [args.package] if args.package else packages():
                run(sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v",
                    cwd=package_path(name))
        elif args.command == "check":
            output(args.github_output, check(args.package, args.force))
        elif args.command == "validate":
            validate(args.package, args.candidate)
        elif args.command == "publish":
            output(args.github_output, {"files": publish(args.candidates.resolve(), args.aur_root.resolve())})
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"aur: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
