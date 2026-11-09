#!/usr/bin/env bash
set -euo pipefail
export TZ="Asia/Shanghai"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
NODE_BIN="${NODE_BIN:-}"
NODE_BIN="${NODE_BIN:-$(command -v node || true)}"
PYTHON_BIN="${PYTHON_BIN:-}"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || true)}"
[[ -n "$NODE_BIN" && -n "$PYTHON_BIN" ]]
exec "$PYTHON_BIN" "$(cd "$(dirname "$0")" && pwd)/dev/mobile_fast.py" serve
