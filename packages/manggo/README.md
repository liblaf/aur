# Manggo for Arch Linux

[![Validate and update AUR packages](https://github.com/liblaf/aur/actions/workflows/packages.yml/badge.svg)](https://github.com/liblaf/aur/actions/workflows/packages.yml)

[Manggo](https://manggo.pylogmon.cn/) is a desktop translation and screenshot OCR
assistant. This directory packages its official Debian downloads for Arch Linux.
It contains the recipe, release maintenance tools, and validation notes.

Architectures: **x86_64** and **aarch64**. See the AUR listing for the current version.
Published as [manggo on the AUR](https://aur.archlinux.org/packages/manggo),
maintained by `liblaf`.

Source: [liblaf/aur, packages/manggo](https://github.com/liblaf/aur/tree/main/packages/manggo).
Shared automation and authentication: [repository README](../../README.md).

---

## Install locally

Install Arch's build tools, then run these commands from the repository root as
your normal user. The remaining local commands on this page use this package directory.

```bash
sudo pacman -S --needed base-devel
cd packages/manggo
makepkg --syncdeps --install
```

Launch **Manggo** from the application menu, or run `manggo`. The upstream `Manggo`
command, desktop actions, icon, and `.mplugin` file association are preserved.

An AUR helper can install it with `yay -S manggo`
and update it during normal AUR upgrades. Use the package manager for updates;
upstream's Debian/RPM updater does not manage the Arch package database.

## Package choices

- The name is `manggo`: Arch's [nonfree package naming guidance](https://wiki.archlinux.org/title/Nonfree_applications_package_guidelines#Package_naming)
  omits `-bin` when source code is unavailable.
- Each architecture downloads the corresponding official Gitcode Debian package
  using a versioned URL and a pinned SHA-256 checksum.
- Bundled Qt, FFmpeg, ICU, OCR models, and speech components stay together under
  `/usr/lib/manggo`. Replacing these with system libraries risks ABI mismatches.
- The bundled sherpa-onnx library's CI build directory is removed from its RUNPATH;
  its relative paths remain intact. Application executables are kept unstripped.
- ARM64 has additional GStreamer, Brotli, ICE, and SM runtime dependencies.
- Wayland integration can require `wl-clipboard`, `xdg-desktop-portal`, and the
  portal backend for your desktop. Global shortcuts, selection, and capture depend
  on compositor support. Install optional packages according to the features used.

## Maintain a release

The helper uses Python's standard library. Updating metadata also requires Arch's
`makepkg` and `bsdtar`. Check releases without changing files:

```bash
python3 scripts/update.py --check
```

To prepare the latest stable release:

```bash
python3 scripts/update.py --update
git diff -- PKGBUILD .SRCINFO
makepkg --cleanbuild --syncdeps --force
```

The updater checks the vendor website's download metadata, requires matching x64
and ARM64 versions, downloads both artifacts, and checks their Debian package
names, architectures, and versions. It updates both checksums, resets `pkgrel=1`,
and regenerates `.SRCINFO`. It refuses downgrades, changed same-version downloads,
unexpected release URLs, and unsupported version formats. A changed upstream
layout requires maintainer review. Downloads are verified against the pinned
hashes by `makepkg`; the vendor download page currently provides no signed checksum.

For packaging changes to an existing release, increment `pkgrel` and run:

```bash
makepkg --printsrcinfo > .SRCINFO
```

Before publishing, validate the package in a clean Arch environment:

```bash
sudo pacman -S --needed devtools namcap shellcheck desktop-file-utils
shellcheck PKGBUILD
extra-x86_64-build
namcap PKGBUILD
namcap manggo-*.pkg.tar.zst
```

Review lint findings rather than treating exit status alone as success. The
validation notes record the upstream warnings observed for this release. Run GUI
checks on the intended desktop; validate ARM64 on ARM64 hardware before claiming
runtime support there.

## Automated updates

The shared [package workflow](https://github.com/liblaf/aur/actions/workflows/packages.yml)
checks upstream releases and tracks compatibility checks in this directory's
`automation-validation.json`. See the [repository README](../../README.md) for
scheduling, manual runs, and the shared AUR publishing key.

Manggo validation performs these steps:

1. Require matching x86_64/ARM64 Debian releases and verify their control metadata.
2. Update version/checksums and regenerate `.SRCINFO`.
3. Run updater and publisher regression tests in a disposable Arch container.
4. Build and install x86_64, check dependencies and package metadata, and launch
   the application in Xvfb. Also validate ARM64 repackaging.
5. Upload the validated `PKGBUILD`, `.SRCINFO`, and integrity manifest.

The integration job verifies each candidate against its trusted recipe, publishes
its metadata to the corresponding AUR repository, and prepares its validation
record. One verified GitHub commit records the selected packages' changes.
Downloaded vendor code runs in a separate job without publishing credentials.

The Manggo publisher rejects changes outside release fields, divergent AUR
metadata, and a moved GitHub `main`. Its working tree check covers this package
directory so the shared integration job can prepare other packages before the
GitHub commit. If AUR publication succeeds and GitHub publication fails, a later
run can finish recording the update without publishing it to AUR again.

`aur-state.json` records the last known AUR commit and both file hashes. The
publisher verifies that complete baseline before updating an older AUR recipe.
Recovery accepts one direct child with exactly the candidate's files; other
co-maintainer changes require updating the baseline after review. Successful
publication records the resulting AUR commit and hashes for the next run.

Run the shared workflow manually from any directory:

```bash
gh workflow run packages.yml --repo liblaf/aur -f validate_current=true
```

Pushes and pull requests validate packages. Once shared publication is activated,
scheduled and manual runs on `main` can publish them. The validation record
contains the version, source commit, timestamp, and Actions run. Historical
package checks remain in [VALIDATION.md](VALIDATION.md).

## Publish updates to the AUR

The package is owned by the `liblaf` [AUR account](https://aur.archlinux.org/account/liblaf).
Use that account's registered SSH key. Follow the
[AUR submission guidelines](https://wiki.archlinux.org/title/AUR_submission_guidelines).

From this directory, copy only the two AUR metadata files into an AUR checkout:

```bash
aur_dir="$(mktemp -d)"
git clone ssh://aur@aur.archlinux.org/manggo.git "$aur_dir"
cp PKGBUILD .SRCINFO "$aur_dir/"
git -C "$aur_dir" add PKGBUILD .SRCINFO
git -C "$aur_dir" commit -m 'Update Manggo packaging'
GIT_LFS_SKIP_PUSH=1 git -C "$aur_dir" push origin HEAD:master
```

Reuse a dedicated AUR checkout when maintaining releases locally. The push skips
Git LFS because the AUR repository contains only text metadata. Keep downloaded
Debian files, built Arch packages, and build directories out of Git.

## License

The packaging code is licensed under **MIT**; see the included `LICENSE`.
Manggo itself is **proprietary**, governed by its [user agreement](https://manggo.pylogmon.cn/terms).
The package installs the exact vendor license and agreement under
`/usr/share/licenses/manggo`; bundled third-party notices are preserved.

Upstream's license restricts redistribution. This recipe retrieves binaries
directly from the vendor; it does not grant permission to mirror them or publish
built packages. Confirm the intended public packaging arrangement with upstream
before submission if their permission is unclear. Their listed contact is
`support@pylogmon.cn`.
