#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Run as root in a fresh, unprivileged Arch container after package installation.
# Arguments: project directory, built package archive, verified source-cache directory.
# The shared runner supplies dependencies; this hook never installs dependencies.
set -euo pipefail

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

pacman -Qk mihomo-smart-bin
test -f /usr/lib/systemd/system/mihomo@.service
bsdtar -xOf "$package" .PKGINFO > /tmp/wrapper.PKGINFO
python3 - "$source_cache" <<'PY'
from pathlib import Path
import re
import subprocess
import hashlib
import sys

metadata = Path('/tmp/wrapper.PKGINFO').read_text().splitlines()
assert 'pkgname = mihomo-smart-bin' in metadata
assert [line for line in metadata if line.startswith('backup = ')] == [
    'backup = etc/mihomo/config.yaml'
]
recipe = Path('PKGBUILD').read_text()
def literal(name, pattern):
    values = re.findall(r'^' + re.escape(name) + r"=(?:'(" + pattern + r")'|\"(" + pattern + r")\"|(" + pattern + r"))$",
                        recipe, re.MULTILINE)
    if len(values) != 1:
        raise SystemExit(f'Expected exactly one literal {name} assignment')
    return next(value for value in values[0] if value)
commit = literal('_commit', '[0-9a-f]+')
archive = Path(sys.argv[1]) / f'mihomo-{commit}.pkg.tar.zst'
assert hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest() == literal('_binary_sha256', '[0-9a-f]{64}')
upstream = subprocess.check_output(
    ['bsdtar', '-xOf', str(archive), '.PKGINFO'], text=True
).splitlines()
assert 'pkgname = mihomo' in upstream, 'Source archive is not the upstream package'
for key in ('pkgdesc', 'url', 'arch', 'license', 'group', 'depend'):
    prefix = key + ' = '
    assert [line for line in metadata if line.startswith(prefix)] == [
        line for line in upstream if line.startswith(prefix)
    ], key
print('Validated descriptive metadata against the upstream package.')
PY
cp /etc/mihomo/config.yaml /tmp/original-config.yaml
printf '\n# Preserve this local edit during package upgrades.\n' >> /etc/mihomo/config.yaml
cp /etc/mihomo/config.yaml /tmp/edited-config.yaml
pacman -U --noconfirm "$package" > /tmp/reinstall.log 2>&1
cmp /etc/mihomo/config.yaml /tmp/edited-config.yaml
cp /tmp/original-config.yaml /etc/mihomo/config.yaml
printf '%s\n' 'Validated package identity and preservation of local config edits.'
runuser -u builder -- mihomo -v > /tmp/version.txt
cat /tmp/version.txt
install -d -o builder -g builder /home/builder/smoke
cp /etc/mihomo/Model.bin /home/builder/smoke/Model.bin
cat > /home/builder/smoke/config.yaml <<'EOF'
mixed-port: 0
external-controller: 127.0.0.1:18090
allow-lan: false
mode: rule
log-level: warning
proxies: []
proxy-groups:
  - name: SMART
    type: smart
    proxies: [DIRECT]
    uselightgbm: true
    collectdata: false
rules:
  - MATCH,SMART
EOF
chown -R builder:builder /home/builder/smoke
runuser -u builder -- mihomo -d /home/builder/smoke -f /home/builder/smoke/config.yaml -t
runuser -u builder -- python3 - "$project/PKGBUILD" <<'PY'
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

recipe = Path(sys.argv[1]).read_text()
commit = re.search(r"^_commit=['\"]?([0-9a-f]+)", recipe, re.MULTILINE)[1]
binary_version = Path('/tmp/version.txt').read_text()
assert re.search(r'(?<![\w-])alpha-smart-' + re.escape(commit) + r'(?![\w-])', binary_version), binary_version
log = Path('/home/builder/smoke/runtime.log')
with log.open('w') as output:
    process = subprocess.Popen(['mihomo', '-d', '/home/builder/smoke', '-f', '/home/builder/smoke/config.yaml'],
                               stdout=output, stderr=subprocess.STDOUT)
    try:
        for attempt in range(50):
            if process.poll() is not None:
                raise RuntimeError('Mihomo exited during startup: ' + log.read_text())
            try:
                with urllib.request.urlopen('http://127.0.0.1:18090/version', timeout=1) as response:
                    version = json.load(response)
                # The controller serves /version before proxy configuration is ready.
                with urllib.request.urlopen('http://127.0.0.1:18090/proxies/SMART', timeout=1) as response:
                    group = json.load(response)
                break
            except (OSError, urllib.error.URLError):
                time.sleep(0.1)
        else:
            raise RuntimeError('Mihomo Smart group did not become ready: ' + log.read_text())
        assert version['version'] == 'alpha-smart-' + commit, version
        assert group['type'].lower() == 'smart', group
        print('Validated Smart group through the installed core controller.')
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
PY
