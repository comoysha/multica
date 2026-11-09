#!/usr/bin/env bash
set -euo pipefail

# Copy this script into the target project and only edit the config block below.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

SERVICE_LABEL="${SERVICE_LABEL:-com.multica.mobile-metro}"
PLIST_BASENAME="${PLIST_BASENAME:-${SERVICE_LABEL}.plist}"
PLIST_SOURCE="${PLIST_SOURCE:-${PROJECT_ROOT}/launchd/${PLIST_BASENAME}}"
PLIST_DEST="${PLIST_DEST:-${HOME}/Library/LaunchAgents/${PLIST_BASENAME}}"
LOG_DIR="${LOG_DIR:-${PROJECT_ROOT}/data/logs}"

export TZ="Asia/Shanghai"

mkdir -p "${HOME}/Library/LaunchAgents"
mkdir -p "${LOG_DIR}"

python3 - "$PROJECT_ROOT" <<'PYGEN'
import pathlib, plistlib, sys
root = pathlib.Path(sys.argv[1])
source = root / 'launchd/com.multica.mobile-metro.plist.template'
s = source.read_text()
for key, value in {'__RUNNER_SCRIPT__': str(root / 'scripts/run_mobile_metro_foreground.sh'), '__WORKING_DIRECTORY__': str(root), '__STDOUT_LOG__': str(root / 'data/logs/com.multica.mobile-metro.stdout.log'), '__STDERR_LOG__': str(root / 'data/logs/com.multica.mobile-metro.stderr.log'), '__SERVICE_LABEL__': 'com.multica.mobile-metro'}.items():
    s = s.replace(key, value.replace('&', '&amp;').replace('<', '&lt;'))
plistlib.loads(s.encode())
(root / 'launchd/com.multica.mobile-metro.plist').write_text(s)
PYGEN

if [[ ! -f "${PLIST_SOURCE}" ]]; then
  echo "LaunchAgent plist not found: ${PLIST_SOURCE}" >&2
  exit 1
fi

launchctl bootout "gui/$(id -u)/${SERVICE_LABEL}" >/dev/null 2>&1 || true
cp "${PLIST_SOURCE}" "${PLIST_DEST}"
chmod 644 "${PLIST_DEST}"
launchctl bootstrap "gui/$(id -u)" "${PLIST_DEST}"
launchctl enable "gui/$(id -u)/${SERVICE_LABEL}" >/dev/null 2>&1 || true
launchctl kickstart -k "gui/$(id -u)/${SERVICE_LABEL}"

echo "Installed and started ${SERVICE_LABEL} at $(TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S %Z')"
launchctl print "gui/$(id -u)/${SERVICE_LABEL}" | sed -n '1,60p'
