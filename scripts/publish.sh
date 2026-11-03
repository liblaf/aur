#!/usr/bin/env bash
# Push packages/<name> to the AUR when its files differ from the AUR repository.
# Needs git, ssh, and vercmp; authentication comes from GIT_SSH_COMMAND.
set -euo pipefail

name=$1
source_dir="packages/${name}"
work=$(mktemp -d)
trap 'rm -rf -- "${work}"' EXIT

version() {
  local pkgver pkgrel
  pkgver=$(sed -n 's/^\tpkgver = //p' "$1")
  pkgrel=$(sed -n 's/^\tpkgrel = //p' "$1")
  echo "${pkgver}-${pkgrel}"
}

git -c init.defaultBranch=master clone --quiet "ssh://aur@aur.archlinux.org/${name}.git" "${work}"
local_version=$(version "${source_dir}/.SRCINFO")
if [[ -f "${work}/.SRCINFO" ]]; then
  aur_version=$(version "${work}/.SRCINFO")
  # Never overwrite a newer release pushed by a co-maintainer.
  if (( $(vercmp "${aur_version}" "${local_version}") > 0 )); then
    echo "${name}: AUR has ${aur_version}, newer than ${local_version}; import it first" >&2
    exit 1
  fi
fi

git -C "${work}" rm -rq --ignore-unmatch .
git ls-files -z --cached --others --exclude-standard -- "${source_dir}" | xargs -0 cp -t "${work}"
git -C "${work}" add -A
if git -C "${work}" diff --cached --quiet; then
  echo "${name}: AUR is up to date at ${local_version}"
  exit 0
fi

if [[ "${aur_version:-}" == "${local_version}" ]]; then
  message="Update packaging (${GITHUB_SERVER_URL:-https://github.com}/liblaf/aur/commit/$(git rev-parse --short HEAD))"
else
  message="Update to ${local_version}"
fi
git -C "${work}" -c user.name=liblaf -c user.email=30631553+liblaf@users.noreply.github.com \
  commit --quiet --message "${message}"
git -C "${work}" push --quiet origin HEAD:master
echo "${name}: published ${local_version} to the AUR"
