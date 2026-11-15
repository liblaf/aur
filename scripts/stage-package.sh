#!/usr/bin/env bash
# Keep the package wrapper inside the archive, including for a single download.
set -euo pipefail

name=${1:?package name required}
source_dir="packages/$name"
artifact_dir="${RUNNER_TEMP:?}/artifacts/package-$name"

if [[ ! -f "$source_dir/PKGBUILD" || ! -f "$source_dir/.SRCINFO" ]]; then
  echo "$name: expected PKGBUILD and .SRCINFO in $source_dir" >&2
  exit 1
fi

mkdir -p "$artifact_dir"
cp -a "$source_dir/." "$artifact_dir/"
