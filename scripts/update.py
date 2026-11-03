#!/usr/bin/env python3
"""Check upstream releases and update PKGBUILD and .SRCINFO in place.

Run on Arch Linux (needs makepkg, updpkgsums, vercmp, bsdtar, and git).
Prints "changed" or "unchanged" for each package.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = "liblaf-aur-updater/1 (+https://github.com/liblaf/aur)"


def request(url: str, method: str = "GET") -> urllib.request.Request:
    headers = {"User-Agent": USER_AGENT}
    if url.startswith("https://api.github.com/") and os.environ.get("GH_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
    return urllib.request.Request(url, method=method, headers=headers)


def get_json(url: str):
    with urllib.request.urlopen(request(url), timeout=60) as response:
        return json.load(response)


def run(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(args, cwd=cwd, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()


def manggo(current: dict[str, str]) -> dict[str, str]:
    data = get_json("https://manggo.pylogmon.cn/api/v1/public/homepage?locale=en")
    items = next(s["download"]["items"] for s in data["data"]["sections"] if s.get("type") == "download")
    versions = {item["version"].removeprefix("v") for item in items
                if item.get("platform") == "Linux" and item.get("systemRequirements") == "deb"
                and item.get("architecture") in ("x64", "arm64")}
    if len(versions) != 1:
        raise RuntimeError(f"expected one Manggo Linux version, found {sorted(versions)}")
    return {"pkgver": versions.pop()}


def mihomo_smart_bin(current: dict[str, str]) -> dict[str, str]:
    api = "https://api.github.com/repos/vernesong/mihomo/releases/tags/"
    names = [a["name"] for a in get_json(api + "Prerelease-Alpha")["assets"]]
    commits = [m[1] for name in names
               if (m := re.fullmatch(r"mihomo-linux-amd64-v3-alpha-smart-([0-9a-f]{7,40})\.pkg\.tar\.zst", name))]
    if len(commits) != 1:
        raise RuntimeError("expected one amd64-v3 Smart package in Prerelease-Alpha")
    commit = commits[0]
    if commit == current["_commit"]:
        model = next(a for a in get_json(api + "LightGBM-Model")["assets"] if a["name"] == "Model-large.bin")
        sums = re.search(r"^sha256sums=\(([^)]*)\)", current["__text__"], re.MULTILINE)[1].split()
        # A new model without a new binary is a package release of the same version.
        if model["digest"].removeprefix("sha256:") != sums[1].strip("'\""):
            return {"pkgrel": str(int(current["pkgrel"]) + 1)}
        return {}
    with tempfile.TemporaryDirectory() as directory:
        archive = Path(directory) / "mihomo.pkg.tar.zst"
        url = ("https://github.com/vernesong/mihomo/releases/download/Prerelease-Alpha/"
               f"mihomo-linux-amd64-v3-alpha-smart-{commit}.pkg.tar.zst")
        with urllib.request.urlopen(request(url), timeout=300) as response:
            archive.write_bytes(response.read())
        info = run("bsdtar", "-xOf", str(archive), ".PKGINFO")
        base = re.search(r"^pkgver = ([0-9]+\.[0-9]+\.[0-9]+)\.alpha", info, re.MULTILINE)[1]
        # The upstream commit count keeps snapshots of one base version ordered.
        repo = Path(directory) / "mihomo.git"
        run("git", "init", "--quiet", "--bare", str(repo))
        run("git", "-C", str(repo), "fetch", "--quiet", "--filter=blob:none", "--no-tags",
            "https://github.com/vernesong/mihomo.git", "refs/tags/Prerelease-Alpha:refs/tags/upstream")
        head = run("git", "-C", str(repo), "rev-parse", "refs/tags/upstream^{commit}")
        if not head.startswith(commit):
            raise RuntimeError("Prerelease-Alpha tag does not match its release asset")
        count = run("git", "-C", str(repo), "rev-list", "--count", head)
    return {"pkgver": f"{base}.alpha.smart.r{count}.g{commit}", "_commit": commit}


def faceform_wrap(current: dict[str, str]) -> dict[str, str]:
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    try:
        opener.open(request("https://customer.faceform.com/customer/dl/Wrap/Linux", "HEAD"), timeout=60)
        raise RuntimeError("Faceform download link did not redirect")
    except urllib.error.HTTPError as error:
        location = error.headers.get("Location", "")
    match = re.fullmatch(r"https://downloads\.faceform\.com/file/faceform/Wrap/([0-9.]+)/([0-9a-f]+)/"
                         r"Faceform_Wrap_\1_Linux\.tar\.xz", location)
    if not match:
        raise RuntimeError(f"unexpected Faceform download location: {location!r}")
    return {"pkgver": match[1], "_download_id": match[2]}


CHECKS = {"faceform-wrap": faceform_wrap, "manggo": manggo, "mihomo-smart-bin": mihomo_smart_bin}


def update(name: str) -> bool:
    root = ROOT / "packages" / name
    recipe = root / "PKGBUILD"
    text = recipe.read_text()
    current = dict(re.findall(r"^(\w+)=['\"]?([^'\"\n(]*)['\"]?$", text, re.MULTILINE))
    changes = CHECKS[name](current | {"__text__": text})
    changes = {key: value for key, value in changes.items() if current.get(key) != value}
    if not changes:
        return False
    if "pkgver" in changes:
        if int(run("vercmp", changes["pkgver"], current["pkgver"])) <= 0:
            raise RuntimeError(f"{name}: upstream {changes['pkgver']} is not newer than {current['pkgver']}")
        changes.setdefault("pkgrel", "1")
    for key, value in changes.items():
        text, count = re.subn(rf"^{key}=.*$", f"{key}={value}", text, count=1, flags=re.MULTILINE)
        if count != 1:
            raise RuntimeError(f"{name}: PKGBUILD has no {key} assignment")
    original = recipe.read_text()
    recipe.write_text(text)
    try:
        with tempfile.TemporaryDirectory() as sources:
            subprocess.run(["updpkgsums"], cwd=root, check=True, env=os.environ | {"SRCDEST": sources})
        (root / ".SRCINFO").write_text(run("makepkg", "--printsrcinfo", cwd=root) + "\n")
    except BaseException:
        recipe.write_text(original)
        raise
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("packages", nargs="*", metavar="package", help=f"default: all of {', '.join(CHECKS)}")
    names = parser.parse_args().packages or list(CHECKS)
    if unknown := set(names) - set(CHECKS):
        parser.error(f"unknown package: {', '.join(sorted(unknown))}")
    failed = False
    for name in names:
        try:
            print(f"{name}: {'changed' if update(name) else 'unchanged'}")
        except Exception as error:
            print(f"{name}: update failed: {error}", file=sys.stderr)
            failed = True
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
