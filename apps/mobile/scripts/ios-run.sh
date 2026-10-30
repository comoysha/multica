#!/usr/bin/env bash
# Managed device builds dispatch to M2. Local simulator work must be explicit.
set -euo pipefail

if [[ "${1:-}" == "--target" && "${2:-}" == "simulator" ]]; then
  echo "No reviewed remote simulator build recipe; use shared ios_debug smoke with an existing compatible app. Local compilation is disabled." >&2
  exit 2
fi
if [[ "${1:-}" == "--build-device" ]]; then
  shift
fi
[[ $# -eq 0 ]] || { echo "Use the managed build and ios_debug smoke entry; local device compilation is disabled" >&2; exit 2; }
[[ "${APP_ENV:-staging}" != "production" ]] || { echo "Production requires its own reviewed remote recipe; no local compilation fallback" >&2; exit 2; }
exec python3 "$(dirname "$0")/../../../scripts/dev/ios_device_build.py"
