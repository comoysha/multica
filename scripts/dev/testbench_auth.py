#!/usr/bin/env python3
"""Mint and reuse a testbench-only token for the private client account."""
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request


def request(base, path, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json",
    })
    # Local traffic must never be routed through a configured proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=5) as response:
        return json.load(response)


def read_token(path, key):
    text = Path(path).read_text(encoding="utf-8")
    match = re.search(r"^\s*(?:export\s+)?" + re.escape(key) + r"\s*=\s*['\"]?(mul_[A-Za-z0-9_-]+)['\"]?\s*$", text, re.MULTILINE)
    if not match:
        raise ValueError("未找到主包账号的本地凭据")
    return match.group(1)


def main(port, env_file, key, cache_file):
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("测试台后端端口无效")
    base = "http://127.0.0.1:" + port
    source = read_token(env_file, key)
    user = request(base, "/api/me", source)
    workspaces = request(base, "/api/workspaces", source)
    if not isinstance(user, dict) or not user.get("id") or not isinstance(workspaces, list) or not workspaces:
        raise ValueError("测试台副本中找不到该账号或它的工作区")
    cache = Path(cache_file)
    try:
        saved = json.loads(cache.read_text(encoding="utf-8"))
        token = saved["token"]
        if (isinstance(token, str) and token.startswith("mul_") and
                request(base, "/api/me", token).get("id") == user["id"] and
                request(base, "/api/workspaces", token)):
            return token
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
        pass
    result = request(base, "/api/tokens", source, {"name": "branch-test-build", "expires_in_days": 365})
    token = result.get("token")
    if not isinstance(token, str) or not token.startswith("mul_") or request(base, "/api/me", token).get("id") != user["id"]:
        raise ValueError("无法验证测试台专用凭据")
    cache.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(cache, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"token": token}, f)
    os.chmod(cache, 0o600)
    return token


if __name__ == "__main__":
    try:
        print(main(*sys.argv[1:]))
    except (OSError, ValueError, TypeError, urllib.error.URLError) as exc:
        print("testbench-auth: 无法为测试台创建免登录凭据；请检查副本是否包含主包账号及其工作区。" , file=sys.stderr)
        print("testbench-auth: " + type(exc).__name__, file=sys.stderr)
        sys.exit(1)
