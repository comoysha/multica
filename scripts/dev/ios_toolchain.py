#!/usr/bin/env python3
"""Check the active remote Expo iOS toolchain before dependency or signing work."""
import json
import re
import subprocess
import sys


def version(argv, expression):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    match = re.search(expression, result.stdout) if result.returncode == 0 else None
    if not match:
        raise ValueError(f"Cannot identify the active {argv[0]} version")
    return tuple(int(part or 0) for part in match.groups())


def check():
    # xcodebuild honors the worker host's DEVELOPER_DIR; never select a local fallback.
    xcode = version(["xcodebuild", "-version"], r"(?m)^Xcode\s+(\d+)\.(\d+)(?:\.(\d+))?")
    if xcode < (26, 4, 0):
        raise ValueError("Expo 57 requires Xcode 26.4 or newer on M2; select a compatible host DEVELOPER_DIR. No M4 fallback")
    node = version(["node", "--version"], r"^v(\d+)\.(\d+)\.(\d+)")
    if node < (22, 13, 0):
        raise ValueError("Expo 57 requires Node 22.13.0 or newer on M2; no local compilation fallback")
    return {"xcode": ".".join(map(str, xcode)), "node": ".".join(map(str, node))}


if __name__ == "__main__":
    try:
        print(json.dumps(check()))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"ios-toolchain: {exc}", file=sys.stderr)
        sys.exit(1)
