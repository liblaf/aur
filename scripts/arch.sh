#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Shared tool entry point inside the disposable Arch image.
set -euo pipefail
export TERM=xterm

mode=$1
shift
project=/work/project
if [[ -d /work ]]; then
  trap 'chown -R "$AUR_HOST_UID:$AUR_HOST_GID" /work' EXIT
  chown -R builder:builder /work
fi
as_builder() {
  # shellcheck disable=SC2016
  runuser -u builder -- bash -c 'cd "$1"; shift; exec "$@"' bash "$project" "$@"
}
version_args() {
  version_options=(--package "$1" --root "$project")
  if [[ $2 != - ]]; then version_options+=(--hook "$project/$2"); fi
  if [[ $mode == version-check && $3 != - ]]; then version_options+=(--config "/repo/$3"); fi
}

case "$mode" in
  version-check|version-update)
    version_args "$1" "$2" "$3"
    as_builder python3 /repo/scripts/versions.py "${mode#version-}" "${version_options[@]}"
    ;;
  prepare)
    version_args "$1" "$2" "$3"
    if [[ $4 == 1 ]]; then
      as_builder python3 /repo/scripts/versions.py update "${version_options[@]}"
    fi
    unset GH_TOKEN GITHUB_TOKEN
    export SRCDEST=/work/sources PKGDEST=/work/packages
    install -d -o builder -g builder "$SRCDEST" "$PKGDEST"
    as_builder makepkg --printsrcinfo > /work/generated.SRCINFO
    cmp "$project/.SRCINFO" /work/generated.SRCINFO
    as_builder makepkg --verifysource --noconfirm
    ;;
  build)
    [[ -z ${GH_TOKEN:-} && -z ${GITHUB_TOKEN:-} ]]
    export SRCDEST=/work/sources PKGDEST=/work/packages
    install -d -o builder -g builder "$SRCDEST" "$PKGDEST"
    install -d /var/lib/aur-chroot
    systemd-machine-id-setup
    install -m 755 /repo/.config/arch/nspawn.sh /usr/local/bin/systemd-nspawn
    # Use devtools' config, rather than the Docker image's NoExtract settings.
    mkarchroot -C /usr/share/devtools/pacman.conf.d/extra.conf \
      -M /usr/share/devtools/makepkg.conf.d/x86_64.conf \
      /var/lib/aur-chroot/root base-devel > /work/chroot.log 2>&1 || {
      tail -80 /work/chroot.log
      exit 1
    }
    cd "$project"
    makechrootpkg -c -r /var/lib/aur-chroot -U builder -- --noconfirm > /work/build.log 2>&1 || {
      tail -100 /work/build.log
      exit 1
    }
    as_builder makepkg --packagelist > /work/package-files
    python3 /repo/scripts/pipeline.py build-report "$1"
    ;;
  smoke|lint)
    python3 /repo/scripts/pipeline.py "$mode" "$@"
    ;;
  *)
    printf 'Unknown Arch tooling stage: %s\n' "$mode" >&2
    exit 2
    ;;
esac
