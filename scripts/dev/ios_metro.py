#!/usr/bin/env python3
"""Run this worktree's Expo server with verified phone-reachable routes."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.parse import urlsplit

import requests
import client_adapter as adapter
import testbench_auth

ROOT = adapter.ROOT
READY = ROOT / ".env.ios-device"


def expose(port, expected):
    actual = adapter.run([sys.executable, adapter.hub(), "expose", "--project", str(ROOT), "--port", str(port)])
    if actual != expected:
        raise ValueError("Verified public route differs from the URL supplied to Expo")


def prepare():
    ident = adapter.identity()
    env, _ = adapter.guarded_env()
    port = int(env["METRO_PORT"])
    if port != int(ident["port"]) + 1000:
        raise ValueError("Metro port differs from the registered worktree")
    if adapter.listeners(port):
        raise ValueError("Metro port is already occupied; keep the existing process intact")
    # Prove the backend and copied database before minting a copy-only credential.
    contract = adapter.shared_tool().verify(ROOT, include_public=False)
    api = contract["public_url"]
    expose(int(ident["port"]), api)
    metro = adapter.run([sys.executable, adapter.hub(), "url", str(port)])
    address = urlsplit(metro)
    if (address.scheme != "https" or not address.hostname or address.hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0")
            or address.username or address.password or address.query or address.fragment or address.path not in ("", "/")):
        raise ValueError("Phone Metro URL must be an assigned HTTPS route")
    token = testbench_auth.main(env["PORT"], ROOT / "apps/mobile/.env.local",
        "EXPO_PUBLIC_LOCAL_AUTH_TOKEN", ROOT / "data/client-ios-auth.json")
    context = {**os.environ, "APP_ENV": "staging", "EXPO_NO_DOTENV": "1",
        "EXPO_BUNDLE_IDENTIFIER_STAGING": "cn.comosha.multica.staging", "EXPO_APPLE_TEAM_ID": "8ALC9Q43AC",
        "EXPO_PUBLIC_API_URL": api, "EXPO_PUBLIC_WEB_URL": "", "EXPO_PUBLIC_LOCAL_AUTH_TOKEN": token,
        "EXPO_PACKAGER_PROXY_URL": metro, "CI": "1"}
    return port, metro, api, context


def wait_ready(child, port, timeout=90):
    deadline = time.monotonic() + timeout
    with requests.Session() as session:
        session.trust_env = False
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise ValueError("Metro exited before becoming ready")
            try:
                adapter.metro_owner(port)
                response = session.get(f"http://127.0.0.1:{port}/status", timeout=2, allow_redirects=False)
                if response.status_code == 200 and response.text.strip() == "packager-status:running":
                    return
            except (ValueError, requests.RequestException):
                pass
            time.sleep(0.25)
    raise ValueError("Metro did not become ready within the startup deadline")


def write_ready(port, metro, api):
    sha = adapter.run(["git", "rev-parse", "HEAD"])
    temporary = READY.with_name(READY.name + ".tmp")
    temporary.write_text(f"DEV_PLATFORM_IOS_READY_SHA={sha}\nIOS_DEVICE_METRO_LOCAL_PORT={port}\n"
                         f"IOS_DEVICE_METRO_URL={metro}\nIOS_DEVICE_API_URL={api}\n")
    os.chmod(temporary, 0o600)
    temporary.replace(READY)


def stop(child):
    if child is not None and child.poll() is None:
        os.killpg(child.pid, signal.SIGTERM)
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)


def main():
    child = None
    adapter.identity()
    # The readiness marker belongs only to this worktree's managed Metro process.
    READY.unlink(missing_ok=True)
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        port, metro, api, context = prepare()
        child = subprocess.Popen(["pnpm", "-C", "apps/mobile", "exec", "expo", "start", "--dev-client",
                                  "--port", str(port), "--host", "lan"], cwd=ROOT, env=context, start_new_session=True)
        wait_ready(child, port)
        expose(port, metro)
        adapter.shared_tool().verify(ROOT, include_public=True)
        write_ready(port, metro, api)
        return child.wait()
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError, RuntimeError, requests.RequestException, subprocess.TimeoutExpired) as exc:
        print(f"ios-metro: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        stop(child)
        READY.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
