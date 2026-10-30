#!/usr/bin/env python3
"""Consume the one approved build input inside the remote shallow checkout."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / "data/ios-remote-input.json"
PRODUCTION_INPUT = ROOT / "data/ios-production-input.json"
PRODUCTION_FIELDS = {"version", "recipe", "source_commit", "build_number", "api_url", "web_url", "auth_token", "team", "bundle_id", "required_signing"}
FIELDS = {"version", "recipe", "source_commit", "build_number", "api_url", "auth_token", "contract_fingerprint"}


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def load_context(expected_commit, expected_recipe):
    if (not re.fullmatch(r"[0-9a-f]{32}", os.environ.get("DP_REMOTE_JOB_ID", ""))
            or os.environ.get("DP_REMOTE_SOURCE_COMMIT") != expected_commit
            or (ROOT / ".dev-platform-workspace.json").exists()
            or git("rev-parse", "--is-shallow-repository") != "true"):
        raise ValueError("Native recipe requires the remote worker's shallow checkout; no local fallback")
    production = expected_recipe == "production-ipa"
    input_path = PRODUCTION_INPUT if production else INPUT
    value = json.loads(input_path.read_text())
    if (set(value) != (PRODUCTION_FIELDS if production else FIELDS) or value["version"] != 1 or value["recipe"] != expected_recipe
            or value["source_commit"] != expected_commit or git("rev-parse", "HEAD") != expected_commit
            or not re.fullmatch(r"[0-9a-f]{40}", expected_commit)
            or (not production and not re.fullmatch(r"[0-9a-f]{64}", value["contract_fingerprint"]))
            or not isinstance(value["build_number"], str) or not value["build_number"].isdigit()):
        raise ValueError("Remote input does not match this committed recipe")
    url = urlsplit(value["api_url"])
    if (url.scheme != "https" or not url.hostname or url.hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0")
            or url.username or url.password or url.query or url.fragment or url.path not in ("", "/")):
        raise ValueError("Remote input must contain the assigned public API URL")
    token = value["auth_token"]
    if (not isinstance(token, str) or (token and not re.fullmatch(r"mul_[A-Za-z0-9_-]+", token))
            or (value["recipe"] == "ios-device" and token)
            or (value["recipe"] in ("ios-branch-ipa", "production-ipa") and not token)):
        raise ValueError("Remote token scope does not match the build recipe")
    if production:
        from ios_production import public_url
        public_url(value["web_url"])
        if (value["team"] != "8ALC9Q43AC" or value["bundle_id"] != "cn.comosha.multica"
                or value["required_signing"] != "adhoc"):
            raise ValueError("Production signing identity cannot be replaced or downgraded")
    env = {**os.environ, "TZ": "Asia/Shanghai", "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
        "CI": "1", "APP_ENV": "production" if production else "staging", "EXPO_NO_DOTENV": "1", "EXPO_APPLE_TEAM_ID": "8ALC9Q43AC",
        "EXPO_BUNDLE_IDENTIFIER_STAGING": "cn.comosha.multica.staging",
        "EXPO_BUNDLE_IDENTIFIER_PROD": "cn.comosha.multica",
        "EXPO_PUBLIC_API_URL": value["api_url"], "EXPO_PUBLIC_WEB_URL": value["web_url"] if production else "",
        "EXPO_PUBLIC_LOCAL_AUTH_TOKEN": token, "EXPO_IOS_BUILD_NUMBER": value["build_number"]}
    return value, env, hashlib.sha256(input_path.read_bytes()).hexdigest()


def execute(argv, expected_commit, expected_recipe):
    value, env, before = load_context(expected_commit, expected_recipe)
    if not argv:
        raise ValueError("Missing declared recipe command")
    # Remain inside the worker's process group so its cancellation owns all children.
    process = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    secret = value["auth_token"].encode()
    with process.stdout as stream:
        for line in stream:
            sys.stdout.buffer.write(line.replace(secret, b"[REDACTED]") if secret else line)
            sys.stdout.buffer.flush()
    code = process.wait()
    if git("rev-parse", "HEAD") != expected_commit or hashlib.sha256((PRODUCTION_INPUT if expected_recipe == "production-ipa" else INPUT).read_bytes()).hexdigest() != before:
        raise ValueError("Remote source or prepared input changed during compilation")
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--recipe", choices=("ios-device", "ios-branch-ipa", "production-ipa"), required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    argv = args.command[1:] if args.command[:1] == ["--"] else args.command
    try:
        return execute(argv, args.source_commit, args.recipe)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(f"ios-remote-env: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
