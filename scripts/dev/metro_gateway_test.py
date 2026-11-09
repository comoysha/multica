"""Exercise the gateway with a fake Expo server, never a real phone or account."""
import hashlib
import json
import os
import random
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import unittest

import requests


class GatewayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = self.root = Path(self.tmp.name).resolve()
        (root / "scripts/dev").mkdir(parents=True)
        (root / "apps/mobile").mkdir(parents=True)
        shutil.copy(Path(__file__).with_name("metro_gateway.cjs"), root / "scripts/dev/metro_gateway.cjs")
        (root / "native.txt").write_text("fixture")
        (root / "dev-platform.json").write_text(json.dumps({"ios_debug": {"native_inputs": ["native.txt"]}}))
        (root / ".dev-platform-workspace.json").write_text(json.dumps({"issue": "MU-396"}))
        # Find two unused ports without affecting a running Metro.
        for _ in range(100):
            with socket.socket() as first, socket.socket() as second:
                port = random.SystemRandom().randrange(20000, 48000)
                try:
                    first.bind(("127.0.0.1", port))
                    second.bind(("127.0.0.1", port + 1000))
                except OSError:
                    continue
                self.port = port
                break
        else:
            self.fail("No free fixture ports")
        self.url = f"http://127.0.0.1:{self.port}"
        self.bundle = self.url + "/index.bundle?platform=ios"
        fake = root / "pnpm"
        fake.write_text('''#!/usr/bin/env node
const http = require('node:http');
const {execFileSync}=require('node:child_process');
http.createServer((req,res)=>{
 if(req.url==='/status')return res.end('packager-status:running');
 if(req.url.startsWith('/index.bundle'))return res.end('fixture JS');
 res.writeHead(200,{'expo-protocol-version':'0','content-type':'application/expo+json'});
 res.end(JSON.stringify({id:require('node:crypto').randomUUID(),launchAsset:{url:process.env.FIXTURE_BUNDLE},extra:{
 expoClient:{extra:{clientRuntime:{buildCommit:execFileSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).trim(),apiUrl:'https://fixture.invalid'}}},
 expoGo:{developer:{projectRoot:process.cwd()+'/apps/mobile'}}}}));
}).listen(Number(process.argv[process.argv.indexOf('--port')+1]),'127.0.0.1');
''')
        fake.chmod(0o700)
        for args in (["init", "-q"], ["add", "."], ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture"]):
            subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
        self.child = subprocess.Popen(["node", "scripts/dev/metro_gateway.cjs", str(self.port)], cwd=root,
            env={**os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"], "EXPO_PACKAGER_PROXY_URL": self.url,
                 "EXPO_PUBLIC_API_URL": "https://fixture.invalid", "FIXTURE_BUNDLE": self.bundle},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.addCleanup(self.stop)
        self.http = requests.Session()
        self.http.trust_env = False
        self.addCleanup(self.http.close)
        for _ in range(100):
            try:
                if self.http.get(self.url + "/status", timeout=0.2).status_code == 200:
                    return
            except requests.RequestException:
                pass
            time.sleep(0.05)
        self.fail("Fixture gateway did not start")

    def stop(self):
        if self.child.poll() is None:
            os.killpg(self.child.pid, signal.SIGTERM)
        self.child.wait(timeout=5)

    def get(self, path):
        return self.http.get(self.url + path, timeout=5)

    def post(self, path, value):
        return self.http.post(self.url + path, json=value, timeout=5)

    def manifest(self):
        return self.http.get(self.url, headers={"expo-platform": "ios"}, timeout=5)

    def test_identity_hashes_the_exact_manifest_delivered_to_phone(self):
        observed = self.get("/_task/identity").json()
        first, second = self.manifest(), self.manifest()
        self.assertEqual(first.content, second.content)
        self.assertEqual(observed["manifest_sha256"], hashlib.sha256(first.content).hexdigest())
        self.assertEqual(observed["root"], str(self.root))
        self.assertEqual(observed["port"], self.port)
        self.assertEqual(self.get("/_task/loaded").json(), {})
        (self.root / "native.txt").write_text("changed")
        self.assertEqual(self.get("/_task/identity").status_code, 503)

    def test_only_loaded_js_for_current_install_can_produce_session_evidence(self):
        self.assertEqual(self.post("/_task/session", {"receipt_id": "a" * 32, "device": "0000-1234"}).status_code, 200)
        manifest = self.manifest().json()
        nonce = self.get("/_task/challenge").json()["nonce"]
        value = {"nonce": nonce, "manifest_id": manifest["id"], "build_commit": manifest["extra"]["expoClient"]["extra"]["clientRuntime"]["buildCommit"],
            "api_url": "https://fixture.invalid", "script_url": self.bundle}
        self.assertEqual(self.post("/_task/loaded", value).status_code, 503)
        self.get("/index.bundle?platform=ios")
        self.assertEqual(self.post("/_task/loaded", {**value, "script_url": "https://foreign.invalid/index.bundle"}).status_code, 503)
        self.assertEqual(self.post("/_task/loaded", value).status_code, 200)
        self.assertEqual(self.get("/_task/loaded").json()["receipt_id"], "a" * 32)
        self.post("/_task/session", {"receipt_id": "b" * 32, "device": "0000-1234"})
        self.assertEqual(self.get("/_task/loaded").json(), {})
        self.assertEqual(self.post("/_task/loaded", value).status_code, 503)


if __name__ == "__main__":
    unittest.main()
