#!/usr/bin/env python3
"""Require existing test signing assets; never create or update Apple assets."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess

import ios_remote_env

TEAM = "CYVBAW2826"
BUNDLE = "cn.comosha.multica.staging"
DEVICE = "00008110-000A08840E6A401E"


def usable_profile(profile, identities, now):
    entitlements = profile.get("Entitlements", {})
    expires = profile.get("ExpirationDate")
    return (profile.get("TeamIdentifier") == [TEAM]
            and entitlements.get("application-identifier") == TEAM + "." + BUNDLE
            and entitlements.get("get-task-allow") is True
            and DEVICE in profile.get("ProvisionedDevices", [])
            and isinstance(expires, datetime.datetime) and expires.replace(tzinfo=datetime.timezone.utc) > now
            and any(hashlib.sha1(cert).hexdigest().upper() in identities for cert in profile.get("DeveloperCertificates", [])))


def verify():
    result = subprocess.run(["security", "find-identity", "-v", "-p", "codesigning"],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError("Existing signing identities could not be checked")
    identities = set(re.findall(r'\b([A-F0-9]{40})\b', result.stdout))
    now = datetime.datetime.now(datetime.timezone.utc)
    for folder in ("Library/MobileDevice/Provisioning Profiles", "Library/Developer/Xcode/UserData/Provisioning Profiles"):
        for path in (Path.home() / folder).glob("*.mobileprovision"):
            decoded = subprocess.run(["security", "cms", "-D", "-i", str(path)], capture_output=True, timeout=15)
            if decoded.returncode:
                continue
            if usable_profile(plistlib.loads(decoded.stdout), identities, now):
                return {"existing_test_signing": "verified", "team": TEAM, "bundle_id": BUNDLE}
    raise ValueError("Missing existing CYVBAW2826 development profile and matching private-key identity for cn.comosha.multica.staging / registered iPhone 14; sync existing match assets readonly first")


def sync_existing():
    """Use only the host's existing readonly match configuration and keychain."""
    signing = Path.home() / ".config/dev-platform/signing"
    config = (signing / "fastlane/Matchfile").read_text()
    if not re.search(r'readonly\s*\(\s*true\s*\)', config) or not re.search(r'team_id\s*\(\s*"CYVBAW2826"\s*\)', config):
        raise ValueError("Existing match configuration must be readonly and scoped to CYVBAW2826")
    binaries = list((Path.home() / ".local/share/dev-platform-tools/fastlane").glob("*/gems/ruby/*/bin/fastlane"))
    if len(binaries) != 1:
        raise ValueError("Host must provide one unambiguous existing fastlane installation")
    binary = binaries[0]
    gems = binary.parent.parent
    env = {**os.environ, "PATH": "/opt/homebrew/opt/ruby/bin:" + os.environ.get("PATH", ""),
           "GEM_HOME": str(gems), "GEM_PATH": str(gems), "CI": "1", "MATCH_READONLY": "true",
           "FASTLANE_SKIP_UPDATE_CHECK": "1", "FASTLANE_OPT_OUT_USAGE": "1"}
    # Match output can include account information; retain it only on the host.
    log = ios_remote_env.ROOT / ".remote-build/signing-match.log"
    descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        result = subprocess.run([str(binary), "match", "development", "--readonly", "--app_identifier", BUNDLE,
                                 "--team_id", TEAM], cwd=signing, env=env, stdout=stream, stderr=stream, timeout=180)
    if result.returncode:
        raise ValueError("Existing match assets could not be synchronized readonly; inspect host-only signing-match.log. No new Apple assets authorized")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--recipe", choices=("ios-device", "ios-branch-ipa"), required=True)
    args = parser.parse_args()
    ios_remote_env.load_context(args.source_commit, args.recipe)
    try:
        verify()
    except ValueError:
        sync_existing()
    print(json.dumps(verify()))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error))
