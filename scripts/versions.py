#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Version adapter for trusted package roots and custom discovery hooks.

Custom discovery runs once through nvchecker source=cmd; its private side report
preserves the updater's fields without treating an opaque identity as pkgver.
Native pkgctl records follow devtools src/lib/version/check.sh. In particular,
JSON mode can report failure with exit status zero, and pkgctl disallows cmd.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

CUSTOM = frozenset(("manggo", "mihomo-smart-bin"))
ADAPTER = Path(__file__).resolve()


class VersionError(RuntimeError):
    """A sanitized version discovery or update failure."""


@contextmanager
def _native_environment():
    """Give pkgctl a temporary GitHub keyfile without adding it to packaging."""
    with tempfile.TemporaryDirectory(prefix="aur-pkgctl-keys-") as temporary:
        env = dict(os.environ, TERM="xterm")
        token = env.get("GH_TOKEN") or env.get("GITHUB_TOKEN")
        if token:
            directory = Path(temporary) / "nvchecker"
            directory.mkdir(mode=0o700)
            keyfile = directory / "keyfile.toml"
            keyfile.write_text("[keys]\ngithub = " + json.dumps(token) + "\n")
            keyfile.chmod(0o600)
            env["XDG_CONFIG_HOME"] = temporary
        yield env


def _run(args: list[str], root: Path, *, env: dict[str, str] | None = None,
         codes: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(args, cwd=root, env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError:
        raise VersionError("Could not start version tool") from None
    if result.returncode not in codes:
        # Neither helper output nor subprocess exception text is safe to relay.
        raise VersionError("Version tool failed")
    return result


def _path(root: Path, value: Path) -> Path:
    return (root / value).resolve()


def _identity(name: str, fields: dict[str, str]) -> str:
    if fields.get("changed") not in ("true", "false"):
        raise VersionError("Invalid hook change flag")
    if name == "manggo":
        latest = fields.get("latest", "")
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", latest):
            raise VersionError("Invalid Manggo hook version")
        return latest
    if name == "mihomo-smart-bin":
        # A new core has no derived pkgver until archive/history verification.
        if not fields.get("pkgrel"):
            raise VersionError("Missing Mihomo hook release field")
        commit = fields.get("commit", "")
        binary = fields.get("binary_sha256", "")
        model = fields.get("model_sha256", "")
        if (not re.fullmatch(r"[0-9a-f]{7,40}", commit)
                or not all(re.fullmatch(r"[0-9a-f]{64}", v) for v in (binary, model))):
            raise VersionError("Invalid Mihomo hook identity")
        return f"{commit}+{binary}+{model}"
    raise VersionError("Unknown custom package")


def _fields(path: Path) -> dict[str, str]:
    try:
        fields: dict[str, str] = {}
        for line in path.read_text().splitlines():
            key, value = line.split("=", 1)
            if not key or not value or key in fields:
                raise ValueError
            fields[key] = value
        return fields
    except (OSError, ValueError):
        raise VersionError("Invalid or missing hook report") from None


def _hook(name: str) -> None:
    """Internal nvchecker command; stdout contains only the version identity."""
    root = Path(os.environ["AUR_PACKAGE_ROOT"]).resolve()
    hook = Path(os.environ["AUR_VERSION_HOOK"]).resolve()
    report = Path(os.environ["AUR_CHECK_REPORT"])
    # Exclusive creation also prevents accidental second discovery in this run.
    with report.open("x") as stream:
        with tempfile.TemporaryDirectory(prefix="aur-hook-") as temporary:
            output = Path(temporary) / "outputs"
            _run([sys.executable, str(hook), "--check", "--github-output", str(output)], root)
            fields = _fields(output)
            identity = _identity(name, fields)
        json.dump({"fields": fields, "identity": identity}, stream)
    print(identity)


def check(name: str, root: Path, *, hook: Path | None = None,
          config: Path | None = None) -> dict[str, str]:
    root = root.resolve()
    if name in CUSTOM:
        hook = _path(root, hook or Path("scripts/update.py"))
        config = (_path(root, config) if config is not None else
                  ADAPTER.parents[1] / ".config" / "nvchecker" / f"{name}.toml")
        with tempfile.TemporaryDirectory(prefix="aur-version-") as temporary:
            report = Path(temporary) / "report.json"
            env = dict(os.environ, AUR_PACKAGE_ROOT=str(root), AUR_VERSION_HOOK=str(hook),
                       AUR_VERSION_ADAPTER=str(ADAPTER), AUR_CHECK_REPORT=str(report))
            result = _run(["nvchecker", "--failures", "--logger", "json", "-c", str(config)],
                          root, env=env)
            try:
                saved = json.loads(report.read_text())
                fields = saved["fields"]
                if not isinstance(fields, dict) or not all(
                        isinstance(k, str) and isinstance(v, str) for k, v in fields.items()):
                    raise ValueError
                identity = _identity(name, fields)
                records = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
                if any(not isinstance(r, dict) or r.get("level") in ("error", "critical")
                       for r in records):
                    raise ValueError
                successes = [r for r in records if r.get("name") == name
                             and r.get("event") in ("updated", "up-to-date")
                             and r.get("version") == identity]
                if len(successes) != 1 or saved["identity"] != identity:
                    raise ValueError
            except (OSError, ValueError, KeyError, TypeError):
                raise VersionError("Invalid or missing nvchecker success record") from None
        return fields
    if hook is not None or config is not None:
        raise VersionError("Native packages require .nvchecker.toml beside PKGBUILD")
    if not (root / ".nvchecker.toml").is_file():
        raise VersionError("Missing native .nvchecker.toml")
    with _native_environment() as env:
        result = _run(["pkgctl", "version", "check", "--json"], root, env=env, codes=(0, 2))
    try:
        records = json.loads(result.stdout)
        if not isinstance(records, list) or len(records) != 1:
            raise ValueError
        record = records[0]
        if (record.get("pkgbase") != name or record.get("status") not in ("success", "warning")
                or type(record.get("out_of_date")) is not bool
                or any(record.get(k) for k in ("error", "failure", "failed"))
                or record.get("status") == "warning" and record["out_of_date"]):
            raise ValueError
        current, latest = record["local_version"], record["upstream_version"]
        if not all(isinstance(v, str) and v.strip() and "\n" not in v for v in (current, latest)):
            raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError):
        raise VersionError("Invalid or failed pkgctl version record") from None
    return {"changed": str(record["out_of_date"]).lower(), "latest": latest, "pkgver": current}


def update(name: str, root: Path, *, hook: Path | None = None) -> None:
    root = root.resolve()
    if name in CUSTOM:
        _run([sys.executable, str(_path(root, hook or Path("scripts/update.py"))), "--update"], root)
    else:
        if hook is not None or not (root / ".nvchecker.toml").is_file():
            raise VersionError("Native packages require .nvchecker.toml beside PKGBUILD")
        with _native_environment() as env:
            _run(["pkgctl", "version", "upgrade"], root, env=env)
        generated = _run(["makepkg", "--printsrcinfo"], root).stdout
        if not generated.strip():
            raise VersionError("Empty generated .SRCINFO")
        (root / ".SRCINFO").write_text(generated)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("check", "update"):
        sub = commands.add_parser(command)
        sub.add_argument("--package", required=True)
        sub.add_argument("--root", type=Path, required=True)
        sub.add_argument("--hook", type=Path)
        if command == "check":
            sub.add_argument("--config", type=Path)
    internal = commands.add_parser("hook")
    internal.add_argument("--package", required=True, choices=sorted(CUSTOM))
    args = parser.parse_args(argv)
    try:
        if args.command == "hook":
            _hook(args.package)
        elif args.command == "check":
            print(json.dumps(check(args.package, args.root, hook=args.hook, config=args.config), sort_keys=True))
        else:
            update(args.package, args.root, hook=args.hook)
    except (VersionError, OSError, KeyError, ValueError):
        print("Version operation failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
