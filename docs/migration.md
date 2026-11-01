# AUR repository migration

The package maintenance histories were imported with `git subtree` on
2026-11-01. The original repositories and local working files remain available.

| Package | Source repository | Imported commit | AUR baseline |
| --- | --- | --- | --- |
| manggo | `liblaf/aur-manggo` | `fb644ccfecef594370b1d402b10bd251f1f15849` | `cab2221f7a8526bdb3c384b72c051c616f4717e0` |
| mihomo-smart-bin | `liblaf/aur-mihomo-smart-bin` | `d5478c90b3804db7867f2d987dd16cb5975ae8a2` | `f397a4f2135001e39d25aca4764e366e3f56997c` |

Each package retains its updater, tests, validation script, packaging metadata,
and historical validation records. Shared workflow and SSH configuration now live
at the repository root. The shared publisher accepts only declared packaging
files and retains each AUR repository's independent history.

The Mihomo import uses the latest remote version,
`1.19.34.alpha.smart.r4842.g77af480-1`, rather than the older local checkout.
The local MIT notices for maintenance scripts were carried over. The pending MIT
license change applies to shared maintenance code; the exported package `LICENSE`
remains its published 0BSD version. The original local edits remain in the source
checkout. Its untracked binary-repository proposal is preserved as
`docs/arch-repository-design.md`; that proposal describes future work.

Manggo remains `1.2.2-1`. Its old GitHub workflow was failing because the recipe's
MIT comment differed from the published AUR recipe's 0BSD comment. The migrated
recipe retains the published comment, so its initial AUR bytes match. Its new
`aur-state.json` records the complete published baseline, enabling checked updates
and recovery after an interrupted GitHub record commit.

## Cutover and activation

Both original `update.yml` workflows are disabled. Their repositories remain
available as history. The central workflow checks and builds packages while
`AUR_PUBLISH_ENABLED=false`. No AUR commit or push is part of this migration.

The shared key is registered to AUR and stored as the central repository's
`aur-publish` environment secret. The environment allows only `main`.
`verify-aur-key.yml` checks the key fingerprint, account package access, and
remote refs through read-only SSH commands. It can run while publication is
disabled.

The destination's inherited mandatory pull request review rule was removed to
support automatic signed updates. Its verified-signature, deletion, creation,
and force-push protections remain, including the existing administrator
exception. This policy applies to all writers of this repository. The shared
workflow also checks the signature and source SHA before advancing `main`.

After the repository is stable, enable automatic AUR publication only with the
maintainer's confirmation:

```bash
gh variable set AUR_PUBLISH_ENABLED --repo liblaf/aur --body true
gh workflow run packages.yml --repo liblaf/aur -f validate_current=true
```

Automatic updates then run without a human approval step. The publisher still
stops on source changes, invalid candidates, unknown AUR edits, or remote races.
