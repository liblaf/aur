#!/usr/bin/env bash
# Quick checks for an installed package. Run as root after `pacman -U`.
set -euo pipefail

name=$1
pkgver=$(sed -n 's/^\tpkgver = //p' "packages/${name}/.SRCINFO")
as_user() { runuser -u nobody -- env -i PATH=/usr/bin HOME=/tmp LANG=C.UTF-8 "$@"; }

no_missing_libraries() {
  local file
  while IFS= read -r -d '' file; do
    if ldd "${file}" 2>/dev/null | grep 'not found'; then
      echo "Missing libraries for ${file}" >&2
      return 1
    fi
  done < <(find "$@" -type f \( -perm -u+x -o -name '*.so*' \) -print0)
}

case "${name}" in
  faceform-wrap)
    output=$(cd /tmp && as_user QT_QPA_PLATFORM=offscreen WRAPCMD_NO_OPENGL=1 faceform-wrap-cmd --version 2>&1)
    echo "${output}"
    [[ "${output}" == *"Wrap ${pkgver}"* ]]
    [[ "${output}" != *"Could not find platform"* ]]
    # Plugins resolve bundled libraries through the executables' $ORIGIN/lib RPATH.
    LD_LIBRARY_PATH=/usr/lib/faceform-wrap/lib \
      no_missing_libraries /usr/lib/faceform-wrap/{Wrap,WrapCmd,WrapServer,plugins}
    ;;
  manggo)
    no_missing_libraries /usr/lib/manggo
    ;;
  mihomo-smart-bin)
    commit=$(sed -n 's/^\tpkgver = .*\.g//p' "packages/${name}/.SRCINFO")
    output=$(as_user mihomo -v)
    echo "${output}"
    [[ "${output}" == *"alpha-smart-${commit}"* ]]
    [[ -s /etc/mihomo/Model.bin ]]
    ;;
  *)
    echo "No smoke test for ${name}" >&2
    exit 1
    ;;
esac
echo "${name} ${pkgver}: smoke test passed"
