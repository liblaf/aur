<div align="center" markdown>

![Mihomo Smart for Arch Linux](https://socialify.git.ci/liblaf/aur/image?description=1&forks=1&issues=1&language=1&name=1&owner=1&pattern=Transparent&pulls=1&stargazers=1&theme=Auto)

[![Validate and update Mihomo Smart](https://github.com/liblaf/aur/actions/workflows/packages.yml/badge.svg)](https://github.com/liblaf/aur/actions/workflows/packages.yml)
[![AUR version](https://img.shields.io/aur/version/mihomo-smart-bin)](https://aur.archlinux.org/packages/mihomo-smart-bin)

[AUR package](https://aur.archlinux.org/packages/mihomo-smart-bin) · [Upstream releases](https://github.com/vernesong/mihomo/releases) · [Workflow runs](https://github.com/liblaf/aur/actions/workflows/packages.yml)

</div>

---

Arch Linux packaging and automatic release maintenance for
[Mihomo Smart](https://github.com/vernesong/mihomo), the Mihomo fork with Smart
proxy groups. This repository maintains the existing `mihomo-smart-bin` AUR
package. Original packaging is credited to `neko_ayaya`; `liblaf` is a co-maintainer.

The package reuses the upstream **Linux amd64 v3** pacman archive. It requires an
x86-64-v3 CPU. It includes the upstream binary, default configuration, systemd
services, and license, plus the large LightGBM model. The wrapper keeps the
`mihomo-smart-bin` package identity and conflicts with the standard `mihomo`
package. It generates its own metadata with the relative backup path
`etc/mihomo/config.yaml` so pacman preserves local configuration edits.

## Install

Using an AUR helper:

```bash
paru -S mihomo-smart-bin
```

Or build as your normal user:

```bash
git clone https://github.com/liblaf/aur.git
cd aur/packages/mihomo-smart-bin
sudo pacman -S --needed base-devel
makepkg --syncdeps --install
```

Package installation does not switch a running proxy automatically. Review
`/etc/mihomo/config.yaml` before starting or restarting `mihomo.service`.

## Automatic maintenance

GitHub Actions checks the upstream rolling `Prerelease-Alpha` release and
`LightGBM-Model` release every six hours, at **02:37, 08:37, 14:37, and 20:37
Asia/Shanghai**. GitHub may delay scheduled runs. Unchanged releases skip package
builds, except for a compatibility check every 28 days.

For each update, the workflow:

1. Selects the exact amd64-v3 Smart `.pkg.tar.zst` asset and `Model-large.bin`.
2. Verifies GitHub's SHA256 digests, download sizes, archive contents, and ELF header;
   checks again that upstream metadata stayed stable during the download.
3. Reads the upstream Mihomo version from the package metadata, verifies the
   complete Git history and revision count, and regenerates `.SRCINFO` with `makepkg`.
4. Builds and installs the package in a disposable Arch Linux container, checks
   that local config edits survive reinstallation, validates a Smart configuration,
   and queries a running Smart group through its controller.
5. Publishes only validated packaging files to AUR in a separate job, then records
   the new metadata and AUR commit on GitHub with a verified signature.

Package versions follow the upstream Mihomo version and Git revision, for example
`1.19.34.alpha.smart.r4831.g4624551`. The base version comes from the verified
upstream package's `.PKGINFO`; `4831` is the number of commits reachable from the
release commit in its complete Git history. The revision count precedes the hash
so successive builds sort in commit order. The executable still reports its
upstream `alpha-smart-<commit>` version. Model and packaging updates increment
`pkgrel`; a new core version resets it to 1.

Unexpected URLs, missing digests, changed archive bytes for the same core commit,
and intervening AUR co-maintainer edits stop publication. New core commits must
descend from the previous commit, increase the revision count, and retain or
increase the upstream base version. The publisher checks the AUR commit and
complete packaging tree against
[aur-state.json](https://github.com/liblaf/aur/blob/main/packages/mihomo-smart-bin/aur-state.json).
After reviewing an intentional co-maintainer update, reconcile the recipe and
tracked state before resuming automation. Ordinary pushes never overwrite AUR
history. If AUR publication succeeds but recording the GitHub commit fails, a
rerun of the failed publishing job can recover that exact update:

```bash
gh run rerun RUN_ID --failed --repo liblaf/aur
```

Use the failed run's retained artifact for recovery. A fresh run may select a
newer upstream build and will stop on the unresolved AUR difference.

This automation updates the **AUR recipe**. Local machines receive it through
their usual AUR upgrade process; installing and restarting a proxy remains a
separate operation. Upstream deletes older rolling release assets, so old pinned
recipes may stop downloading once a newer build replaces them.

## Shared publishing

The [repository README](https://github.com/liblaf/aur#shared-aur-identity)
documents the shared AUR key and `aur-publish` environment. One publication job
processes the validated packages and records their metadata in one signed GitHub
commit. AUR export includes only `PKGBUILD`, `.SRCINFO`,
`mihomo-smart-bin.install`, and `LICENSE`.

The package is maintained through the central
[packages workflow](https://github.com/liblaf/aur/actions/workflows/packages.yml).
Set `AUR_PUBLISH_ENABLED=false` in `liblaf/aur` to pause AUR writes.

## Maintain locally

The updater and tests use Python's standard library, Git, and `bsdtar` from
`libarchive`. Check upstream without changing files, or prepare a verified update:

Run these commands from `packages/mihomo-smart-bin`:

```bash
python3 scripts/update.py --check
python3 scripts/update.py --update
python3 -m unittest discover -s tests -v
shellcheck PKGBUILD scripts/validate.sh
git diff -- PKGBUILD .SRCINFO
```

Updating metadata requires Arch's `makepkg`. GitHub API requests can use `GH_TOKEN`
or `GITHUB_TOKEN`; an authenticated local `gh` CLI is also supported. Without
credentials, GitHub's lower anonymous rate limit applies.

`--check` reads release metadata without downloading package assets or Git history.
When it finds a new core commit, it reports `changed=true` with `pkgver=null` until
`--update` verifies the archive and derives the version. New core updates fetch
complete Git history with blob filtering, without checking out or running source.

For a packaging change, increment `pkgrel` and regenerate `.SRCINFO`:

```bash
makepkg --printsrcinfo > .SRCINFO
```

## License

New maintenance code is licensed under
[MIT](https://github.com/liblaf/aur/blob/main/LICENSE).
The exported packaging license retains its original
[0BSD](https://github.com/liblaf/aur/blob/main/packages/mihomo-smart-bin/LICENSE) notice.
Mihomo Smart is licensed under
[GPL-3.0-or-later](https://github.com/vernesong/mihomo/blob/Alpha/LICENSE).
