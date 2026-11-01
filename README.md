<div align="center" markdown>

![AUR packages](https://socialify.git.ci/liblaf/aur/image?description=1&forks=1&issues=1&language=1&name=1&owner=1&pattern=Transparent&pulls=1&stargazers=1&theme=Auto)

[![Validate and update AUR packages](https://github.com/liblaf/aur/actions/workflows/packages.yml/badge.svg)](https://github.com/liblaf/aur/actions/workflows/packages.yml)

[Packages](https://github.com/liblaf/aur/tree/main/packages) · [Workflow runs](https://github.com/liblaf/aur/actions/workflows/packages.yml) · [Migration notes](https://github.com/liblaf/aur/blob/main/docs/migration.md)

---

</div>

Arch Linux packages maintained by `liblaf`, with shared release checks, build
validation, and AUR publication.

| Package | Description | Architectures |
| --- | --- | --- |
| [manggo](https://github.com/liblaf/aur/tree/main/packages/manggo) | Desktop translation and screenshot OCR | x86_64, aarch64 |
| [mihomo-smart-bin](https://github.com/liblaf/aur/tree/main/packages/mihomo-smart-bin) | Mihomo Smart with the large LightGBM model | x86_64, requires x86-64-v3 |

Install through your AUR helper:

```bash
paru -S manggo mihomo-smart-bin
```

## Maintain packages

The helpers use Python's standard library and Docker. A shared Arch image provides
devtools, pkgctl, nvchecker, namcap, ShellCheck, and actionlint. Task commands live in
[.config/mise.toml](https://github.com/liblaf/aur/blob/main/.config/mise.toml)
and run with `mise`:

```bash
mise run test
mise run check manggo
mise run update mihomo-smart-bin
mise run build mihomo-smart-bin
mise run lint
mise run smoke mihomo-smart-bin
mise run validate mihomo-smart-bin
mise run publish
```

`check` discovers releases; `update` prepares changes in staging and copies them
back after verification. `build` builds the current recipe with devtools in a clean
Arch chroot and saves a manifest, exact package archives, recipe, and verified
sources under `.artifacts/<package>/`. `lint` checks recipes, shell hooks, and
workflows without building. `smoke` verifies the saved artifact hashes, installs
those archives, runs namcap, and exercises the installed package.

`validate` runs update, source verification, clean build, and smoke checks together,
then writes only validated AUR metadata under `.candidates/`. `SKIP_UPDATE=1`
validates the current pinned recipe. `publish` dispatches the GitHub workflow.
See each package's README for runtime checks and upstream details.

The shared image is cached by configuration and UTC day; no host package installation
is required. Clean builds require Docker support for privileged containers and
nested systemd-nspawn. Runtime checks run in separate unprivileged containers.
GitHub credentials are supplied only to the metadata preparation process, which
exits before build and runtime stages begin.

## Automation

[GitHub Actions](https://github.com/liblaf/aur/blob/main/.github/workflows/packages.yml)
discovers package directories and checks upstream every six hours. It builds new
releases, AUR differences, and compatibility checks due after 28 days. Pushes and
pull requests also build packages. A manual run validates both current packages.

Each package retains its upstream validation rules and regression tests. Builds
run in parallel with devtools; GUI and Smart-controller hooks consume the resulting
archives. Manggo also checks ARM64 repackaging on x86_64; native ARM64 runtime is
not covered. One publication job checks the candidate
manifests, source commit, allowed recipe changes, and current AUR state, then
publishes each selected package. It records all results together in one signed
GitHub commit and advances `main` only if the validated source commit is still
current. Concurrent workflow runs are serialized.

Only explicit packaging file lists reach AUR. Package scripts, tests, reports,
and documentation stay in this repository. Existing AUR histories and
co-maintainer edits are checked before publication. Retained candidate artifacts
allow a failed publication job to retry an interrupted update.

Declare each package in
[.config/packages.toml](https://github.com/liblaf/aur/blob/main/.config/packages.toml).
For an ordinary release package, add `packages/<pkgbase>/PKGBUILD`, `.SRCINFO`,
and a native `.nvchecker.toml` beside the recipe, then declare its AUR files:

```toml
[packages.example]
export_files = ["PKGBUILD", ".SRCINFO"]
```

The default checker is `pkgctl version check`; the default updater is
`pkgctl version upgrade`, followed by `.SRCINFO` generation. Package-local
`.nvchecker.toml` is the native pkgctl convention. Shared configuration stays in
`.config/`. Manggo and Mihomo declare specialized discovery/update hooks through
nvchecker, retaining their download, archive, digest, and history verification.
Optional smoke hooks exercise installed applications after the common checks.

The workflow discovers declared package directories automatically. Conventional
candidates can be validated and recorded on GitHub without custom scripts. AUR
publication additionally requires a configured publisher with a tracked AUR
baseline. Existing publishers' file lists must match the catalog declarations.

## Shared AUR identity

The `liblaf` AUR account uses one Ed25519 key for both packages. The public key is
tracked in [.config/aur-actions-key.pub](https://github.com/liblaf/aur/blob/main/.config/aur-actions-key.pub):

```text
SHA256:j/9lcQVcxP71115YrdkfNEnD6F2g7IfA1N6VaIIt+nk
```

The private key is stored in the `aur-publish` environment as
`AUR_SSH_PRIVATE_KEY`. Its local copy is `~/.ssh/id_ed25519_aur`.
The environment permits deployments only from `main`. Set up a replacement key
by registering its public half on AUR, updating the tracked public key and the
environment secret, and verifying publication before retiring the old key:

```bash
gh secret set AUR_SSH_PRIVATE_KEY --repo liblaf/aur \
  --env aur-publish < ~/.ssh/id_ed25519_aur
gh variable set AUR_PUBLISH_ENABLED --repo liblaf/aur --body true
mise run publish
```

Set `AUR_PUBLISH_ENABLED=false` to pause AUR writes while checks continue. The
workflow verifies the key against the tracked public half and pins AUR's host key
using [.config/aur-known-hosts](https://github.com/liblaf/aur/blob/main/.config/aur-known-hosts).
The key is available only in the publication job. GitHub commits use the temporary
`GITHUB_TOKEN` and the pinned
[commit-action](https://github.com/suzuki-shunsuke/commit-action).

Publication is currently **disabled** while this repository is stabilized. Run a
complete validation and record its results on GitHub without writing to AUR:

```bash
gh workflow run packages.yml --repo liblaf/aur \
  -f validate_current=true -f record_validation=true
```

`main` requires verified signatures and blocks deletion, creation, and force
pushes. It accepts signed commits directly so automatic updates can run without
mandatory pull request approval.

## License

Copyright © 2026 [liblaf](https://github.com/liblaf). <br />
Shared maintenance code is [MIT](https://github.com/liblaf/aur/blob/main/LICENSE)
licensed. Imported files retain their license notices; the Mihomo package's
exported packaging license remains
[0BSD](https://github.com/liblaf/aur/blob/main/packages/mihomo-smart-bin/LICENSE).
Upstream applications retain their own licenses.
