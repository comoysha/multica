#!/usr/bin/env bash
# Wrapper regressions never run Expo, a native compiler, or a device.
set -euo pipefail
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TEST_DIR=$(mktemp -d "${TMPDIR:-/tmp}/multica-ios-run.XXXXXX")
trap 'rm -rf "$TEST_DIR"' EXIT
mkdir -p "$TEST_DIR/bin"
export MULTICA_TEST_CALLS="$TEST_DIR/calls"
cat >"$TEST_DIR/bin/python3" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >>"$MULTICA_TEST_CALLS"
EOF
cat >"$TEST_DIR/bin/pnpm" <<'EOF'
#!/bin/sh
echo 'unexpected local native command' >&2
exit 99
EOF
chmod +x "$TEST_DIR/bin/python3" "$TEST_DIR/bin/pnpm"
export PATH="$TEST_DIR/bin:$PATH"
fail() { echo "FAIL: $1" >&2; exit 1; }
for mode in default explicit; do
  : >"$MULTICA_TEST_CALLS"
  if [[ "$mode" == default ]]; then "$SCRIPT_DIR/ios-run.sh"; else "$SCRIPT_DIR/ios-run.sh" --build-device; fi
  [[ "$(wc -l <"$MULTICA_TEST_CALLS")" -eq 1 ]] || fail 'remote build must delegate once'
  grep -q 'scripts/dev/ios_device_build.py$' "$MULTICA_TEST_CALLS" || fail 'missing remote dispatcher'
done
: >"$MULTICA_TEST_CALLS"
for mode in simulator device extras; do
  case "$mode" in
    simulator) args=(--target simulator) ;;
    device) args=(--device --configuration Release) ;;
    extras) args=(--build-device --device) ;;
  esac
  if "$SCRIPT_DIR/ios-run.sh" "${args[@]}" >/dev/null 2>&1; then fail 'unreviewed local compile path must stop'; fi
done
if APP_ENV=production "$SCRIPT_DIR/ios-run.sh" >/dev/null 2>&1; then fail 'production cannot silently use staging'; fi
[[ ! -s "$MULTICA_TEST_CALLS" ]] || fail 'blocked request must not invoke any builder'
echo 'ios-run.test.sh: all assertions passed'
