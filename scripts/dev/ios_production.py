#!/usr/bin/env python3
"""Formal iOS input and artifact checks; only the existing main config is authorized."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import uuid
from urllib.parse import urlsplit
import zipfile

import client_adapter as adapter

ROOT = adapter.ROOT
BUNDLE = "cn.comosha.multica"
TEAM = "8ALC9Q43AC"
DESTINATION = "data/ios-production-input.json"


def public_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0")
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise ValueError("Formal API/Web URL must be the explicitly configured private HTTPS endpoint")
    return value.rstrip("/")


def context(path):
    value = json.loads(Path(path).read_text())
    if (value.get("version") != 1 or Path(value.get("root", "")).resolve() != ROOT
            or value.get("recipe") != "production-ipa" or value.get("bundle_id") != BUNDLE
            or value.get("team") != TEAM or value.get("required_signing") != "adhoc"
            or value.get("source_commit") != adapter.run(["git", "rev-parse", "HEAD"])
            or value.get("build_number") != adapter.run(["git", "rev-list", "--count", "HEAD"])):
        raise ValueError("Formal build context does not match current source, bundle or signing requirement")
    if set(value.get("settings", {})) != {"EXPO_PUBLIC_API_URL", "EXPO_PUBLIC_WEB_URL"}:
        raise ValueError("Formal API/Web values must come from the existing private install-hub configuration")
    for setting in value["settings"].values():
        public_url(setting)
    return value


def input_value(value, token):
    """Pure schema construction; callers must separately authorize credential access."""
    if not re.fullmatch(r"mul_[A-Za-z0-9_-]+", token):
        raise ValueError("An explicitly authorized formal token is required; no test-token fallback")
    return {"version": 1, "recipe": "production-ipa", "source_commit": value["source_commit"],
        "build_number": value["build_number"], "api_url": public_url(value["settings"]["EXPO_PUBLIC_API_URL"]),
        "web_url": public_url(value["settings"]["EXPO_PUBLIC_WEB_URL"]), "auth_token": token,
        "team": TEAM, "bundle_id": BUNDLE, "required_signing": "adhoc"}


def prepare(path):
    value = context(path)
    common = Path(adapter.run(["git", "rev-parse", "--git-common-dir"]))
    common = (ROOT / common).resolve() if not common.is_absolute() else common.resolve()
    if (common.parent != ROOT or (ROOT / ".dev-platform-workspace.json").exists()
            or adapter.run(["git", "status", "--porcelain"])):
        raise ValueError("Formal input requires the clean main checkout, never a test-data worktree")
    inputs = value.get("inputs")
    if not isinstance(inputs, list) or len(inputs) != 1 or inputs[0].get("destination") != DESTINATION:
        raise ValueError("Formal build requires one explicitly allowlisted production JSON input")
    state = Path(os.environ.get("INSTALL_HUB_HOME") or "~/.local/state/dev-platform/install-hub").expanduser().resolve()
    target = state / "inputs/multica-ios-production-input.json"
    if (Path(inputs[0].get("source", "")).expanduser().absolute() != target
            or target.is_symlink() or target.resolve().is_relative_to(ROOT)):
        raise ValueError("Formal input path must be the dedicated private install-hub state file")
    source = ROOT / "apps/mobile/.env.local"
    before = adapter.sha256(source)
    token = adapter.env_values(source).get("EXPO_PUBLIC_LOCAL_AUTH_TOKEN", "")
    prepared = input_value(value, token)
    if adapter.sha256(source) != before:
        raise ValueError("Formal credential source changed while preparing input")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.with_name(target.name + "." + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(prepared, stream)
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {"prepared": True, "input_sha256": adapter.sha256(target)}


def inspect(path, artifact):
    value = context(path)
    actual = adapter.inspect_artifact(artifact, "ios")
    with zipfile.ZipFile(artifact) as package:
        names = [name for name in package.namelist() if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", name)]
        constants = [name for name in package.namelist()
                     if re.fullmatch(r"Payload/[^/]+\.app/(?:Frameworks/[^/]+/)?EXConstants\.bundle/app\.config", name)]
        if len(names) != 1 or len(constants) != 1:
            raise ValueError("Formal IPA requires unique actual app and embedded Expo configuration")
        info = plistlib.loads(package.read(names[0]))
        extra = json.loads(package.read(constants[0]))["extra"]
    if (actual["build_commit"] != value["source_commit"]
            or actual["api_url"] != public_url(value["settings"]["EXPO_PUBLIC_API_URL"])
            or info.get("DPBuildCommit") != value["source_commit"] or info.get("CFBundleIdentifier") != BUNDLE
            or str(info.get("CFBundleVersion")) != value["build_number"] or extra.get("APP_ENV") != "production"):
        raise ValueError("Formal IPA environment, API, bundle or source identity does not match the requested release")
    return actual


def signing_ready(commit):
    import ios_remote_env
    ios_remote_env.load_context(commit, "production-ipa")
    proc = subprocess.run(["security", "find-identity", "-v", "-p", "codesigning"], capture_output=True, text=True, timeout=30)
    if proc.returncode or not any(f"({TEAM})" in line and ("Apple Distribution:" in line or "iPhone Distribution:" in line)
                                  for line in proc.stdout.splitlines()):
        raise ValueError("M2 has no valid distribution signing identity for the required team; Ad Hoc cannot fall back to development")
    return {"distribution_identity": "available"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "inspect", "signing-ready"))
    parser.add_argument("--context", type=Path)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--source-commit")
    args = parser.parse_args()
    try:
        if args.action == "signing-ready":
            result = signing_ready(args.source_commit)
        elif not args.context:
            parser.error("--context is required")
        elif args.action == "prepare":
            result = prepare(args.context)
        elif not args.artifact:
            parser.error("--artifact is required")
        else:
            result = inspect(args.context, args.artifact)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        print(f"ios-production: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
