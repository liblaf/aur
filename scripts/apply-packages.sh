#!/usr/bin/env bash
# Apply only complete tested recipes from the merged artifact download.
set -euo pipefail
shopt -s nullglob

artifacts=("${RUNNER_TEMP:?}"/packages/package-*)
if (( ${#artifacts[@]} == 0 )); then
  echo "No tested package artifacts found in $RUNNER_TEMP/packages" >&2
  exit 1
fi

names=()
for artifact in "${artifacts[@]}"; do
  name=${artifact##*/package-}
  if [[ ! -d "$artifact" || ! -f "$artifact/PKGBUILD" || ! -f "$artifact/.SRCINFO" ]]; then
    echo "$artifact: expected PKGBUILD and .SRCINFO" >&2
    exit 1
  fi
  if [[ -z "$name" || "$name" == . || "$name" == .. || ! -d "packages/$name" ]]; then
    echo "$name: no package directory in the checkout" >&2
    exit 1
  fi
  names+=("$name")
done

for name in "${names[@]}"; do
  cp -a "$RUNNER_TEMP/packages/package-$name/." "packages/$name/"
done

{
  printf 'names=%s\n' "${names[*]}"
  echo 'files<<EOF'
  git status --porcelain --untracked-files=all -- packages | cut -c4-
  echo EOF
} >> "${GITHUB_OUTPUT:?}"
