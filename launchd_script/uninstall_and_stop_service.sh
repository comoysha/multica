#!/usr/bin/env bash
set -euo pipefail

# Copy this script into the target project and set a narrow PROCESS_MATCH if needed.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

SERVICE_LABEL="${SERVICE_LABEL:-com.multica.mobile-metro}"
PLIST_BASENAME="${PLIST_BASENAME:-${SERVICE_LABEL}.plist}"
PLIST_DEST="${PLIST_DEST:-${HOME}/Library/LaunchAgents/${PLIST_BASENAME}}"
PID_FILE="${PID_FILE:-}"
PROCESS_MATCH="${PROCESS_MATCH:-}"
REMOVE_LOGS="${REMOVE_LOGS:-0}"
LOG_DIR="${LOG_DIR:-${PROJECT_ROOT}/data/logs}"

export TZ="Asia/Shanghai"

launchctl bootout "gui/$(id -u)/${SERVICE_LABEL}" >/dev/null 2>&1 || true
launchctl disable "gui/$(id -u)/${SERVICE_LABEL}" >/dev/null 2>&1 || true
rm -f "${PLIST_DEST}"

echo "Stopped ${SERVICE_LABEL}; logs and source preserved."
