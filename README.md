<div align="center" markdown>

![AUR packages](https://socialify.git.ci/liblaf/aur/image?description=1&forks=1&issues=1&language=1&name=1&owner=1&pattern=Transparent&pulls=1&stargazers=1&theme=Auto)

[![AUR](https://github.com/liblaf/aur/actions/workflows/aur.yml/badge.svg)](https://github.com/liblaf/aur/actions/workflows/aur.yml)

</div>

AUR packages maintained by `liblaf`. Every day, GitHub Actions checks each
upstream for a new release, updates the `PKGBUILD`, builds it in a clean Arch
Linux container, smoke-tests the installed package, and publishes it to the AUR.

| Package | Description | Architectures |
| --- | --- | --- |
| [faceform-wrap](https://aur.archlinux.org/packages/faceform-wrap) | Topology transfer tool that fits a base mesh to 3D scans | x86_64 |
| [manggo](https://aur.archlinux.org/packages/manggo) | Desktop translation and screenshot OCR assistant | x86_64, aarch64 |
| [mihomo-smart-bin](https://aur.archlinux.org/packages/mihomo-smart-bin) | Mihomo with the Smart policy group and LightGBM model | x86_64 (x86-64-v3) |

```bash
paru -S faceform-wrap manggo mihomo-smart-bin
```

Faceform Wrap and Manggo are closed source, so their names have no `-bin`
suffix, following the [non-free package guidelines]. Mihomo is open source and
these are prebuilt binaries, so the package takes the `-bin` suffix.

[non-free package guidelines]: https://wiki.archlinux.org/title/Nonfree_applications_package_guidelines

Command names follow upstream, including capitalization and suffixes. Faceform
Wrap provides `Wrap.sh` and `WrapCmd.sh` on `PATH`; its desktop entry uses `Wrap.sh`.
Manggo's command is `Manggo`.

## Layout

- `packages/<name>/`: exactly the files published to the AUR, with the package
  sources licensed under [0BSD](https://wiki.archlinux.org/title/AUR_submission_guidelines).
- `scripts/update.py`: finds the latest upstream release, then rewrites
  `pkgver` (and any release-specific variables), checksums, and `.SRCINFO`.
- `scripts/smoke.sh`: quick checks for an installed package.
- `scripts/publish.sh`: pushes a package to the AUR if it differs. It refuses
  to overwrite a newer version pushed by a co-maintainer.
- `.github/workflows/aur.yml`: runs daily, on pushes, and on pull requests.

## Workflow

Each package builds in its own job. Scheduled and manual runs check upstream
first; pushes and pull requests build the recipe as committed. On `main`,
every package that builds and passes its tests is pushed to the AUR. Updated
recipes are then committed back to this repository. One failing package does
not block the others.

To pause all automation (upstream checks, builds, AUR publishing, and
commits back), disable the workflow; enable it to resume:

```bash
gh workflow disable aur.yml --repo liblaf/aur
gh workflow enable aur.yml --repo liblaf/aur
```

While it is disabled, nothing runs, including builds for pushes and pull
requests. After re-enabling, the next run on `main` publishes any changes made
in the meantime.

Publishing uses the `AUR_SSH_PRIVATE_KEY` secret in the `aur-publish`
environment. The matching public key is
[`.config/aur-actions-key.pub`](.config/aur-actions-key.pub).

## Local tasks

Tasks are defined in [`.config/mise.toml`](.config/mise.toml). Updates and lint
need Arch Linux (`pacman-contrib`, `namcap`); builds run in Docker.

```bash
mise run update [package]   # check upstream and update recipes
mise run lint               # .SRCINFO, namcap, ShellCheck, actionlint
mise run build <package>    # clean build, namcap, install, smoke test
mise run publish            # trigger the GitHub workflow
```

To change a recipe by hand, edit `PKGBUILD`, bump `pkgrel` if users need
to rebuild, run `mise run srcinfo`, and push to `main`.

## License

Package sources are 0BSD (see each package's `LICENSE`); the repository
tooling is MIT ([LICENSE](LICENSE)).
