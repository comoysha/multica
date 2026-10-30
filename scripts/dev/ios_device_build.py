#!/usr/bin/env python3
"""Prepare verified local inputs, dispatch M2, and inspect the returned artifact."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import uuid

import client_adapter as adapter
import testbench_auth

ROOT = adapter.ROOT
APP = ROOT / "build/ios-device/DerivedData/Build/Products/Debug-iphoneos/MulticaStaging.app"
BUNDLE = "cn.comosha.multica.staging"
TEAM = "8ALC9Q43AC"


def inspect_app(app, commit):
    with (app / "Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    if (info.get("CFBundleIdentifier") != BUNDLE or info.get("DPBuildCommit") != commit
            or info.get("CFBundleSupportedPlatforms") != ["iPhoneOS"]
            or not info.get("DPNativeInputsSHA256")):
        raise ValueError("Built app is not the current signed iPhone test variant")


def runtime_identity(contract):
    evidence = contract["verification"]
    return {"fingerprint": contract["fingerprint"], "api_url": contract["public_url"],
            "backend": evidence["backend"]["identity"], "data": evidence["data"]}


def prepare(recipe):
    adapter.identity()
    env, _ = adapter.guarded_env()
    commit = adapter.run(["git", "rev-parse", "HEAD"])
    contract = adapter.shared_tool().verify(ROOT, include_public=False)
    token = ""
    if recipe == "ios-branch-ipa":
        token = testbench_auth.main(env["PORT"], ROOT / "apps/mobile/.env.local",
            "EXPO_PUBLIC_LOCAL_AUTH_TOKEN", ROOT / "data/ios-test-auth.json")
    value = {"version": 1, "recipe": recipe, "source_commit": commit,
             "build_number": adapter.run(["git", "rev-list", "--count", "HEAD"]),
             "api_url": contract["public_url"], "auth_token": token,
             "contract_fingerprint": contract["fingerprint"]}
    target = ROOT / "data/ios-remote-input.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + "." + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream)
    temporary.replace(target)
    return value, contract, runtime_identity(contract), target


def promote(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".incoming-" + uuid.uuid4().hex)
    previous = destination.with_name(destination.name + ".previous-" + uuid.uuid4().hex)
    shutil.copytree(source, temporary, symlinks=True)
    try:
        if destination.exists():
            destination.rename(previous)
        temporary.rename(destination)
    except BaseException:
        if previous.exists() and not destination.exists():
            previous.rename(destination)
        raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    if previous.exists():
        shutil.rmtree(previous)


def build(recipe="ios-device"):
    remote = adapter.platform_root() / "tools/build/remote.py"
    if not remote.is_file():
        raise ValueError("Shared remote build API is missing; native compilation cannot fall back to M4")
    value, contract, before, input_path = prepare(recipe)
    stamp = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y%m%d-%H%M%S")
    output = ROOT / "data/ios-remote-build" / f"{recipe}-{stamp}-{uuid.uuid4().hex[:8]}"
    print(f"Remote build record: {output / 'job.json'}", file=sys.stderr)
    raw = adapter.run([sys.executable, str(remote), "run", "--project", str(ROOT), "--recipe", recipe,
        "--output", str(output), "--timeout", "7200", "--input", str(input_path) + "=data/ios-remote-input.json"], timeout=7260)
    result = json.loads(raw)
    if (result.get("status") != "verified" or result.get("source_git_sha") != value["source_commit"]
            or not result.get("job_id") or not re.fullmatch(r"[0-9a-f]{64}", result.get("recipe_sha256", ""))):
        raise ValueError("Returned remote result does not identify the current committed source and recipe")
    name = "app" if recipe == "ios-device" else "ipa"
    artifact = Path(result.get("artifacts", {}).get(name, "")).resolve()
    record = Path(result.get("result_file", "")).resolve()
    if (not artifact.is_relative_to(output) or not record.is_relative_to(output)
            or not record.is_file() or json.loads(record.read_text()) != result):
        raise ValueError("Returned artifact does not match this persisted fetch proof")
    if recipe == "ios-device":
        inspect_app(artifact, value["source_commit"])
        adapter.run(["codesign", "--verify", "--strict", str(artifact)])
    else:
        observed = adapter.inspect_ios(artifact)
        if observed["build_commit"] != value["source_commit"] or observed["api_url"] != value["api_url"]:
            raise ValueError("Actual IPA source or API differs from the verified build input")
    after = adapter.shared_tool().verify(ROOT, contract=contract, include_public=False)
    if runtime_identity(after) != before or adapter.run(["git", "rev-parse", "HEAD"]) != value["source_commit"]:
        raise ValueError("Local source, backend instance or database identity changed during the remote build")
    cleanup = json.loads(adapter.run([sys.executable, str(remote), "ack", "--job", str(output / "job.json"),
                                     "--receipt", str(record)], timeout=60))
    if cleanup.get("acknowledged") is not True:
        raise ValueError("Remote worker did not acknowledge removal of the prepared private input")
    if recipe == "ios-device":
        promote(artifact, APP)
        result = {**result, "app": str(APP)}
    return {**result, "api_url": value["api_url"], "auth_fingerprint": hashlib.sha256(value["auth_token"].encode()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipe", choices=("ios-device", "ios-branch-ipa"), default="ios-device")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = build(args.recipe)
        print(json.dumps(result, ensure_ascii=False) if args.json or args.recipe != "ios-device" else result["app"])
        return 0
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"ios-device-build: {type(exc).__name__}: {exc}", file=sys.stderr)
        return getattr(exc, "exit_code", 1)


if __name__ == "__main__":
    sys.exit(main())
