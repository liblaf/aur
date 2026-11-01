#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Run as root in a fresh, unprivileged Arch container after package installation.
# Arguments: project directory, built package archive, verified source-cache directory.
# The shared runner supplies dependencies; this hook never installs dependencies.
set -euo pipefail

gui_pid=
xvfb_pid=
cleanup_gui() {
  if [[ -n "$gui_pid" ]]; then
    kill -- "-$gui_pid" 2>/dev/null || true
    wait "$gui_pid" 2>/dev/null || true
    gui_pid=
  fi
  if [[ -n "$xvfb_pid" ]]; then
    kill "$xvfb_pid" 2>/dev/null || true
    wait "$xvfb_pid" 2>/dev/null || true
    xvfb_pid=
  fi
}

if (( $# != 3 )); then
  printf 'Usage: %s ABS_PROJECT ABS_PACKAGE ABS_SOURCE_CACHE\n' "$0" >&2
  exit 2
fi
project=$1
package=$2
source_cache=$3
for path in "$project" "$package" "$source_cache"; do
  [[ "$path" == /* ]] || { printf 'Expected absolute path: %s\n' "$path" >&2; exit 2; }
done
[[ -d "$project" && -f "$project/PKGBUILD" && -f "$package" && -d "$source_cache" ]]
[[ $EUID == 0 ]]
id builder > /dev/null
cd -- "$project"

# Accept only a literal version assignment; never source the recipe as root.
pkgver=$(python3 - <<'PY_VERSION'
import re
from pathlib import Path
values = re.findall(r"^pkgver=(?:'([A-Za-z0-9._+:-]+)'|\"([A-Za-z0-9._+:-]+)\"|([A-Za-z0-9._+:-]+))$",
                    Path('PKGBUILD').read_text(), re.MULTILINE)
if len(values) != 1:
    raise SystemExit('Expected exactly one literal pkgver assignment')
print(next(value for value in values[0] if value))
PY_VERSION
)

pacman -Qk manggo
desktop-file-validate /usr/share/applications/com.pylogmon.Manggo.desktop
update-mime-database /usr/share/mime
while IFS= read -r binary; do
  result=$(ldd "$binary" 2>&1 || true)
  if [[ "$result" == *'not found'* ]]; then
    printf '%s\n%s\n' "$binary" "$result"
    exit 1
  fi
done < <(find /usr/lib/manggo -type f \( -name '*.so*' -o -name Manggo -o -name ManggoAsrWorker \))

# Exercise an installed desktop app without accessing a user's desktop or files.
trap 'cleanup_gui' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
Xvfb :99 -screen 0 1440x1000x24 -nolisten tcp -ac > /tmp/xvfb.log 2>&1 &
xvfb_pid=$!
for ((attempt = 0; attempt < 20; attempt++)); do
  if DISPLAY=:99 xwininfo -root > /dev/null 2>&1; then
    break
  fi
  sleep 1
done
DISPLAY=:99 xwininfo -root > /dev/null
install -d -o builder -g builder -m 700 /tmp/manggo-runtime
setsid runuser -u builder -- env DISPLAY=:99 XDG_RUNTIME_DIR=/tmp/manggo-runtime \
  QT_QPA_PLATFORM=xcb dbus-run-session -- Manggo > /tmp/manggo.log 2>&1 &
gui_pid=$!
window_ready=false
for ((attempt = 0; attempt < 20; attempt++)); do
  if DISPLAY=:99 xwininfo -root -tree > /tmp/windows.txt 2>/dev/null; then
    if python3 - <<'PY'
import re
from pathlib import Path
windows = Path('/tmp/windows.txt').read_text()
matches = re.findall(r'"[^"\n]*Manggo[^"\n]*".*?\s(\d+)x(\d+)\+', windows)
raise SystemExit(0 if any(int(w) > 100 and int(h) > 100 for w, h in matches) else 1)
PY
    then
      window_ready=true
      break
    fi
  fi
  sleep 1
done
if [[ "$window_ready" != true ]]; then
  cat /tmp/manggo.log /tmp/windows.txt
  exit 1
fi
actual_version=$(runuser -u builder -- env DISPLAY=:99 XDG_RUNTIME_DIR=/tmp/manggo-runtime \
  QT_QPA_PLATFORM=xcb timeout 10 manggo --version)
[[ "$actual_version" == "Manggo $pkgver" ]]
# cleanup_gui is defined above and also serves as the EXIT handler.
# shellcheck disable=SC2218
cleanup_gui

# ARM64 repackaging is architecture-independent; this is not a native runtime test.
printf '%s\n' 'Coverage: ARM64 repackaging on x86_64; no native ARM64 runtime test.'
arm=$(mktemp -d /home/builder/manggo-arm.XXXXXX)
install -d -o builder -g builder "$arm"
cp PKGBUILD "$arm/"
cp /etc/makepkg.conf "$arm/makepkg.conf"
printf '\nCARCH=aarch64\nCHOST=aarch64-linux-gnu\n' >> "$arm/makepkg.conf"
# Reuse a verified ARM64 source if the runner cached it; otherwise makepkg
# downloads the recipe URL and verifies its declared checksum normally.
arm_source="manggo_${pkgver}_arm64.deb"
if [[ -f "$source_cache/$arm_source" ]]; then
  cp -- "$source_cache/$arm_source" "$arm/$arm_source"
  chown builder:builder "$arm/$arm_source"
fi
chown builder:builder "$arm/PKGBUILD" "$arm/makepkg.conf"
# $1 is expanded by the builder shell.
# shellcheck disable=SC2016
runuser -u builder -- bash -c 'cd -- "$1" && exec makepkg --config ./makepkg.conf --nodeps --ignorearch --cleanbuild --force --noconfirm' bash "$arm" \
  > /tmp/build-arm.log 2>&1 || {
  tail -80 /tmp/build-arm.log
  exit 1
}

printf 'Validated Manggo %s: installed files, GUI/version, and ARM64 repackaging.\n' "$pkgver"
