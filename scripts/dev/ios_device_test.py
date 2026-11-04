#!/usr/bin/env python3
"""Isolated checks for phone routing, build admission and installation evidence."""
import contextlib
import json
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import tempfile
import types
import unittest
from unittest import mock

import client_adapter as adapter
import ios_device_build as build
import ios_metro as metro
import testbench_auth as auth


class DeviceTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.ready = self.root / ".env.ios-device"
        self.app = self.root / "build/MulticaStaging.app"
        self.app.mkdir(parents=True)
        self.info = {"CFBundleIdentifier": build.BUNDLE, "CFBundleSupportedPlatforms": ["iPhoneOS"],
                     "DPBuildCommit": "a" * 40, "DPNativeInputsSHA256": "b" * 64}
        (self.app / "Info.plist").write_bytes(plistlib.dumps(self.info))
        for patch in (mock.patch.multiple(adapter, ROOT=self.root, DATA=self.root / "data"),
                      mock.patch.multiple(metro, ROOT=self.root, READY=self.ready),
                      mock.patch.multiple(build, ROOT=self.root, APP=self.app)):
            patch.start()
            self.addCleanup(patch.stop)
        self.api = "https://test.example:42102"
        self.url = "https://test.example:43102"
        self.env = {"PORT": "18813", "METRO_PORT": "43102"}
        self.ident = {"port": 42102}

    def prepare_patches(self):
        return (mock.patch.object(adapter, "identity", return_value=self.ident),
                mock.patch.object(adapter, "guarded_env", return_value=(self.env, None)),
                mock.patch.object(adapter, "listeners", return_value=set()),
                mock.patch.object(adapter, "shared_tool", return_value=types.SimpleNamespace(
                    verify=mock.Mock(return_value={"public_url": self.api}))),
                mock.patch.object(adapter, "run", return_value=self.url),
                mock.patch.object(metro, "expose"),
                mock.patch.object(auth, "main", return_value="fixture-copy-token"))

    def test_metro_supplies_verified_routes_and_copy_credential(self):
        with contextlib.ExitStack() as stack:
            mocks = [stack.enter_context(patch) for patch in self.prepare_patches()]
            port, url, api, env = metro.prepare()
        self.assertEqual((port, url, api), (43102, self.url, self.api))
        self.assertEqual(env["EXPO_PUBLIC_API_URL"], self.api)
        self.assertEqual(env["EXPO_PACKAGER_PROXY_URL"], self.url)
        self.assertEqual(env["EXPO_PUBLIC_LOCAL_AUTH_TOKEN"], "fixture-copy-token")
        self.assertEqual(env["EXPO_NO_DOTENV"], "1")
        self.assertEqual(env["APP_ENV"], "staging")
        self.assertEqual(env["EXPO_BUNDLE_IDENTIFIER_STAGING"], build.BUNDLE)
        mocks[3].return_value.verify.assert_called_once_with(self.root, include_public=False)
        mocks[5].assert_called_once_with(42102, self.api)
        self.assertEqual(mocks[6].call_args.args[0], "18813")

    def test_busy_metro_never_mints_auth_or_changes_route(self):
        with contextlib.ExitStack() as stack:
            patches = self.prepare_patches()
            mocks = [stack.enter_context(patch) for patch in patches]
            mocks[2].return_value = {"123"}
            with self.assertRaisesRegex(ValueError, "occupied"):
                metro.prepare()
            mocks[3].assert_not_called()
            mocks[5].assert_not_called()
            mocks[6].assert_not_called()

    def test_database_failure_prevents_exposure_and_auth(self):
        with contextlib.ExitStack() as stack:
            mocks = [stack.enter_context(patch) for patch in self.prepare_patches()]
            mocks[3].return_value.verify.side_effect = RuntimeError("copy mismatch")
            with self.assertRaisesRegex(RuntimeError, "copy mismatch"):
                metro.prepare()
            mocks[5].assert_not_called()
            mocks[6].assert_not_called()

    def test_changed_route_is_not_accepted(self):
        with mock.patch.object(adapter, "run", return_value="https://wrong.example"):
            with self.assertRaisesRegex(ValueError, "differs"):
                metro.expose(43102, self.url)

    def test_phone_localhost_is_rejected(self):
        with contextlib.ExitStack() as stack:
            mocks = [stack.enter_context(patch) for patch in self.prepare_patches()]
            mocks[4].return_value = "https://localhost:43102"
            with self.assertRaisesRegex(ValueError, "assigned HTTPS"):
                metro.prepare()
            mocks[6].assert_not_called()

    def test_ready_marker_is_atomic_and_contains_source_and_local_port(self):
        with mock.patch.object(adapter, "run", return_value="a" * 40):
            metro.write_ready(43102, self.url, self.api)
        saved = adapter.env_values(self.ready)
        self.assertEqual(saved, {"DEV_PLATFORM_IOS_READY_SHA": "a" * 40,
            "IOS_DEVICE_METRO_LOCAL_PORT": "43102", "IOS_DEVICE_METRO_URL": self.url,
            "IOS_DEVICE_API_URL": self.api})
        self.assertEqual(self.ready.stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.ready.with_name(self.ready.name + ".tmp").exists())

    def test_runtime_rechecks_existing_route_without_creating_it(self):
        with mock.patch.object(adapter, "run", return_value="a" * 40):
            metro.write_ready(43102, self.url, self.api)
        with mock.patch.object(adapter, "run", side_effect=["a" * 40, self.url, self.url]) as run, \
                mock.patch.object(adapter, "public_url", return_value=self.api):
            self.assertEqual(adapter.device_runtime_urls(43102), (self.url, self.api))
        self.assertEqual(run.call_args.args[0][-1], "--check")
        self.assertIn("expose", run.call_args.args[0])
        with mock.patch.object(adapter, "run", side_effect=["a" * 40, self.url, "https://other.example"]), \
                mock.patch.object(adapter, "public_url", return_value=self.api):
            with self.assertRaisesRegex(ValueError, "Actual public Metro route"):
                adapter.device_runtime_urls(43102)

    def test_old_ready_marker_cannot_reuse_current_route(self):
        with mock.patch.object(adapter, "run", return_value="a" * 40):
            metro.write_ready(43102, self.url, self.api)
        with mock.patch.object(adapter, "run", return_value="b" * 40) as run:
            with self.assertRaisesRegex(ValueError, "not ready"):
                adapter.device_runtime_urls(43102)
        self.assertEqual(run.call_count, 1)

    def test_exposure_failure_stops_only_own_child_and_removes_ready(self):
        child = mock.Mock(pid=1234)
        child.poll.return_value = None
        self.ready.write_text("stale")
        with mock.patch.object(adapter, "identity"), mock.patch.object(metro, "prepare",
                return_value=(43102, self.url, self.api, {})), \
                mock.patch.object(metro.subprocess, "Popen", return_value=child) as popen, \
                mock.patch.object(metro, "wait_ready"), mock.patch.object(metro, "expose", side_effect=ValueError("route failed")), \
                mock.patch.object(metro.signal, "signal"), mock.patch.object(metro.os, "killpg") as kill:
            self.assertEqual(metro.main(), 1)
        kill.assert_called_once_with(1234, signal.SIGTERM)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertFalse(self.ready.exists())

    def test_build_cannot_accept_formal_simulator_or_old_app(self):
        for key, value in (("CFBundleIdentifier", "ai.multica.mobile"), ("DPBuildCommit", "c" * 40),
                           ("CFBundleSupportedPlatforms", ["iPhoneSimulator"]), ("DPNativeInputsSHA256", "")):
            with self.subTest(key=key):
                (self.app / "Info.plist").write_bytes(plistlib.dumps({**self.info, key: value}))
                with self.assertRaisesRegex(ValueError, "current signed iPhone"):
                    build.inspect_app(self.app, "a" * 40)

    def receipt(self):
        path = self.root / "installation.json"
        value = {"receipt_id": "receipt", "status": "passed", "kind": "physical", "device": "device",
            "bundle_id": build.BUNDLE, "operation": "devicectl-install", "data_policy": "preserve",
            "source_path": str(self.app), "artifact_sha256": "actual-app-hash",
            "source": {"root": str(self.root), "git_sha": "a" * 40}}
        path.write_text(json.dumps(value))
        return path, value

    def test_installation_receipt_binds_actual_local_artifact_device_and_source(self):
        path, value = self.receipt()
        with mock.patch.object(adapter, "shared_module", return_value=types.SimpleNamespace(tree_sha=lambda path: "actual-app-hash")), \
                mock.patch.object(adapter, "run", return_value="a" * 40):
            self.assertEqual(adapter.verify_installation(self.app, build.BUNDLE, "device", path), "receipt")
            for key, wrong in (("device", "other"), ("source_path", "/other/app"), ("source", {}),
                               ("artifact_sha256", "stale"), ("data_policy", "reset")):
                with self.subTest(key=key):
                    path.write_text(json.dumps({**value, key: wrong}))
                    with self.assertRaisesRegex(ValueError, "does not match"):
                        adapter.verify_installation(self.app, build.BUNDLE, "device", path)

    def test_auth_requests_remain_local_without_redirect_or_proxy(self):
        session = mock.MagicMock()
        session.__enter__.return_value = session
        session.request.return_value.status_code = 200
        session.request.return_value.json.return_value = {"id": "user"}
        with mock.patch.object(auth.requests, "Session", return_value=session):
            self.assertEqual(auth.request("http://127.0.0.1:18813", "/api/me", "fixture"), {"id": "user"})
        self.assertFalse(session.trust_env)
        self.assertFalse(session.request.call_args.kwargs["allow_redirects"])
        self.assertEqual(session.request.call_args.args[1], "http://127.0.0.1:18813/api/me")


if __name__ == "__main__":
    unittest.main()
