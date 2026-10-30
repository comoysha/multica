import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

spec = importlib.util.spec_from_file_location("adapter", Path(__file__).with_name("client_adapter.py"))
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class AdapterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.data = self.root / "data"
        self.data.mkdir()
        self.patch = mock.patch.multiple(adapter, ROOT=self.root, DATA=self.data)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        (self.root / ".dev-platform-workspace.json").write_text(json.dumps({
            "real_root": str(self.root), "main_root": str(self.root / "main"), "port": 42101, "slug": "mu-389"}))
        self.env = "DATABASE_URL=postgres://multica:fixture@localhost:62101/test_mu389\nPOSTGRES_DB=test_mu389\nPOSTGRES_PORT=62101\nPORT=18939\nFRONTEND_PORT=42101\nMETRO_PORT=43101\nCOMPOSE_PROJECT_NAME=multica-dp-mu-389\n"
        (self.root / ".env.worktree").write_text(self.env)

    def test_ipa_reads_expo_constants_runtime_not_unrelated_strings(self):
        path = self.root / "test.ipa"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("Payload/App.app/EXConstants.bundle/app.config", json.dumps({"extra": {
                "clientRuntime": {"apiUrl": "https://actual.example", "buildCommit": "a" * 40}}}))
            archive.writestr("Payload/App.app/main.jsbundle", "https://incorrect.example")
        result = adapter.inspect_artifact(path, "ios")
        self.assertEqual(result["api_url"], "https://actual.example")
        self.assertEqual(result["artifact_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_legacy_ipa_without_consumed_config_is_not_inferred(self):
        path = self.root / "old.ipa"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("Payload/App.app/main.jsbundle", "https://expected.example")
        with self.assertRaisesRegex(ValueError, "configuration"):
            adapter.inspect_ios(path)

    def client_manifest(self):
        self.app = self.root / "Installed.app"
        self.app.mkdir()
        (self.app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "ai.multica.mobile.dev"}))
        (self.root / "dev-platform.json").write_text(json.dumps({"ios_debug": {"bundle_id": "ai.multica.mobile.dev"}}))
        return {"launchAsset": {"url": "http://127.0.0.1:43101/index.bundle?platform=ios"}, "extra": {
            "expoClient": {"ios": {"bundleIdentifier": "ai.multica.mobile.dev"},
                "extra": {"clientRuntime": {"apiUrl": "http://127.0.0.1:18939", "buildCommit": "a" * 40}}},
            "expoGo": {"developer": {"projectRoot": str(self.root / "apps/mobile")}}}}

    def runtime_session(self, manifest):
        session = mock.MagicMock()
        session.__enter__.return_value = session
        response = session.get.return_value
        response.status_code = 200
        response.headers = {"expo-protocol-version": "0", "content-type": "application/expo+json; charset=utf-8"}
        response.content = json.dumps(manifest).encode()
        return session

    def test_client_probe_reads_actual_metro_manifest_with_dev_launcher_headers(self):
        manifest = self.client_manifest()
        session = self.runtime_session(manifest)
        # Neither installed static config nor the caller's env overrides the live manifest.
        (self.app / "EXConstants.bundle").mkdir()
        (self.app / "EXConstants.bundle/app.config").write_text(json.dumps({"extra": {
            "clientRuntime": {"apiUrl": "http://incorrect.example", "buildCommit": "b" * 40}}}))
        with mock.patch.object(adapter.requests, "Session", return_value=session), \
                mock.patch.object(adapter, "metro_owner", return_value={"123"}), \
                mock.patch.dict(os.environ, {"EXPO_PUBLIC_API_URL": "http://incorrect.example"}):
            result = adapter.probe_client(self.app, "http://127.0.0.1:43101")
        self.assertEqual(result["api_url"], "http://127.0.0.1:18939")
        self.assertEqual(result["build_commit"], "a" * 40)
        self.assertEqual(result["manifest_sha256"], hashlib.sha256(session.get.return_value.content).hexdigest())
        self.assertFalse(session.trust_env)
        self.assertEqual(session.get.call_args.kwargs["headers"], {
            "expo-platform": "ios", "Accept": "application/expo+json,application/json"})
        self.assertFalse(session.get.call_args.kwargs["allow_redirects"])

    def test_client_probe_rejects_actual_manifest_pointing_to_another_backend(self):
        manifest = self.client_manifest()
        manifest["extra"]["expoClient"]["extra"]["clientRuntime"]["apiUrl"] = "http://127.0.0.1:18940"
        with mock.patch.object(adapter.requests, "Session", return_value=self.runtime_session(manifest)), \
                mock.patch.object(adapter, "metro_owner", return_value={"123"}):
            with self.assertRaisesRegex(ValueError, "another backend"):
                adapter.probe_client(self.app, "http://127.0.0.1:43101")

    def test_client_probe_rejects_malformed_or_unrelated_manifests(self):
        original = self.client_manifest()
        for problem in ("missing-config", "wrong-project", "wrong-app", "wrong-js-source", "missing-commit"):
            with self.subTest(problem=problem):
                manifest = json.loads(json.dumps(original))
                if problem == "missing-config":
                    del manifest["extra"]["expoClient"]["extra"]["clientRuntime"]
                elif problem == "wrong-project":
                    manifest["extra"]["expoGo"]["developer"]["projectRoot"] = str(self.root / "other")
                elif problem == "wrong-app":
                    manifest["extra"]["expoClient"]["ios"]["bundleIdentifier"] = "another.app"
                elif problem == "wrong-js-source":
                    manifest["launchAsset"]["url"] = "http://127.0.0.1:43102/index.bundle?platform=ios"
                else:
                    manifest["extra"]["expoClient"]["extra"]["clientRuntime"]["buildCommit"] = ""
                with mock.patch.object(adapter.requests, "Session", return_value=self.runtime_session(manifest)), \
                        mock.patch.object(adapter, "metro_owner", return_value={"123"}):
                    with self.assertRaises(ValueError):
                        adapter.probe_client(self.app, "http://127.0.0.1:43101")

    def test_client_probe_rejects_redirect_or_html_instead_of_manifest(self):
        manifest = self.client_manifest()
        for status, mime in ((302, "application/expo+json"), (200, "text/html")):
            session = self.runtime_session(manifest)
            session.get.return_value.status_code = status
            session.get.return_value.headers["content-type"] = mime
            with self.subTest(status=status, mime=mime), \
                    mock.patch.object(adapter.requests, "Session", return_value=session), \
                    mock.patch.object(adapter, "metro_owner", return_value={"123"}):
                with self.assertRaisesRegex(ValueError, "iOS Expo manifest"):
                    adapter.probe_client(self.app, "http://127.0.0.1:43101")

    def test_client_probe_rejects_wrong_metro_port_before_network_access(self):
        self.client_manifest()
        with mock.patch.object(adapter.requests, "Session") as session:
            with self.assertRaisesRegex(ValueError, "assigned local Metro"):
                adapter.probe_client(self.app, "http://127.0.0.1:43102")
            session.assert_not_called()

    def test_client_probe_rejects_foreign_or_replaced_metro_listener(self):
        manifest = self.client_manifest()
        with mock.patch.object(adapter, "listeners", return_value={"123"}), \
                mock.patch.object(adapter, "run", return_value="n/another-worktree"), \
                mock.patch.object(adapter.requests, "Session") as session:
            with self.assertRaisesRegex(ValueError, "another worktree"):
                adapter.probe_client(self.app, "http://127.0.0.1:43101")
            session.assert_not_called()
        with mock.patch.object(adapter.requests, "Session", return_value=self.runtime_session(manifest)), \
                mock.patch.object(adapter, "metro_owner", side_effect=[{"123"}, {"124"}]):
            with self.assertRaisesRegex(ValueError, "changed during"):
                adapter.probe_client(self.app, "http://127.0.0.1:43101")

    def test_physical_probe_consumes_public_manifest_and_installation_receipt(self):
        manifest = self.client_manifest()
        config = {"ios_debug": {"bundle_id": "ai.multica.mobile.dev", "runtime_probe": {"api_target": "public"}}}
        (self.root / "dev-platform.json").write_text(json.dumps(config))
        device_url = "https://test.example:43101"
        api = "https://test.example:42101"
        manifest["launchAsset"]["url"] = device_url + "/index.bundle?platform=ios"
        manifest["extra"]["expoClient"]["extra"]["clientRuntime"]["apiUrl"] = api
        session = self.runtime_session(manifest)
        with mock.patch.object(adapter.requests, "Session", return_value=session), \
                mock.patch.object(adapter, "metro_owner", return_value={"123"}), \
                mock.patch.object(adapter, "device_runtime_urls", return_value=(device_url, api)), \
                mock.patch.object(adapter, "verify_installation", return_value="receipt") as receipt:
            result = adapter.probe_client(self.app, "http://127.0.0.1:43101", "physical", "phone", device_url, "receipt.json")
        self.assertEqual(result["api_url"], api)
        receipt.assert_called_once_with(self.app, "ai.multica.mobile.dev", "phone", "receipt.json")
        self.assertEqual(session.get.call_args.args[0], "http://127.0.0.1:43101")

    def test_physical_probe_rejects_loopback_js_and_api_in_actual_manifest(self):
        manifest = self.client_manifest()
        (self.root / "dev-platform.json").write_text(json.dumps({"ios_debug": {
            "bundle_id": "ai.multica.mobile.dev", "runtime_probe": {"api_target": "public"}}}))
        for wrong in ("javascript", "api"):
            with self.subTest(wrong=wrong):
                changed = json.loads(json.dumps(manifest))
                if wrong == "javascript":
                    changed["extra"]["expoClient"]["extra"]["clientRuntime"]["apiUrl"] = "https://test.example:42101"
                else:
                    changed["launchAsset"]["url"] = "https://test.example:43101/index.bundle"
                with mock.patch.object(adapter.requests, "Session", return_value=self.runtime_session(changed)), \
                        mock.patch.object(adapter, "metro_owner", return_value={"123"}), \
                        mock.patch.object(adapter, "device_runtime_urls", return_value=("https://test.example:43101", "https://test.example:42101")), \
                        mock.patch.object(adapter, "verify_installation"):
                    with self.assertRaises(ValueError):
                        adapter.probe_client(self.app, "http://127.0.0.1:43101", "physical", "phone", "https://test.example:43101", "receipt.json")

    def test_physical_probe_requires_receipt_before_network(self):
        self.client_manifest()
        (self.root / "dev-platform.json").write_text(json.dumps({"ios_debug": {
            "bundle_id": "ai.multica.mobile.dev", "runtime_probe": {"api_target": "public"}}}))
        with mock.patch.object(adapter.requests, "Session") as session, \
                mock.patch.object(adapter, "device_runtime_urls", return_value=("https://test.example:43101", "https://test.example:42101")):
            with self.assertRaisesRegex(ValueError, "installation evidence"):
                adapter.probe_client(self.app, "http://127.0.0.1:43101", "physical", "phone", "https://test.example:43101")
            session.assert_not_called()

    def test_database_probe_uses_host_port_and_read_only_client(self):
        with mock.patch.object(adapter, "run", return_value="{}") as run:
            adapter.database_query("SELECT current_database()")
        args = run.call_args.args[0]
        self.assertIn("host.docker.internal", args)
        self.assertIn("62101", args)
        self.assertIn("test_mu389", args)
        self.assertIn("PGOPTIONS=-c default_transaction_read_only=on", args)

    def test_live_or_wrong_database_port_is_blocked(self):
        (self.root / ".env.worktree").write_text(self.env.replace("62101", "5432"))
        with mock.patch.object(adapter, "run") as run:
            with self.assertRaisesRegex(ValueError, "isolation"):
                adapter.database_query("SELECT 1")
            run.assert_not_called()

    def test_stale_backend_environment_is_blocked_before_query(self):
        session = mock.MagicMock()
        session.__enter__.return_value = session
        session.get.return_value.json.return_value = {"pid": 123}
        with mock.patch.object(adapter.requests, "Session", return_value=session), \
                mock.patch.object(adapter, "run", return_value="server DATABASE_URL=postgres://wrong"), \
                mock.patch.object(adapter, "database_query") as query:
            with self.assertRaisesRegex(ValueError, "effective DATABASE_URL"):
                adapter.probe()
            query.assert_not_called()

    def test_snapshot_hash_change_cannot_create_provenance(self):
        golden = self.data / "golden"
        golden.mkdir()
        (golden / "multica.dump").write_bytes(b"actual snapshot")
        (golden / "multica.json").write_text(json.dumps({"source_id": adapter.SOURCE_ID,
            "snapshot": {"sha256": "a" * 64, "captured_at": "2026-10-30T14:00:00+08:00"}}))
        with self.assertRaisesRegex(ValueError, "Snapshot changed"):
            adapter.clone_prepare()
        self.assertFalse((self.data / "client-data.json").exists())
        self.assertFalse((self.data / "client-clone-pending.json").exists())

    def stale_manifest(self, database="test_mu389"):
        path = self.root / "dev-home/envs/old/manifest.env"
        path.parent.mkdir(parents=True)
        path.write_text(f"DIR={self.root}\nBACKEND_PORT=18400\nFRONTEND_PORT=13320\nDB_NAME={database}\n")
        return path

    def test_stale_registry_quarantine_preserves_manifest_and_does_not_touch_database(self):
        path = self.stale_manifest()
        original = path.read_bytes()
        with mock.patch.dict(os.environ, {"MULTICA_DEV_HOME": str(self.root / "dev-home")}), \
                mock.patch.object(adapter, "listeners", return_value=set()), \
                mock.patch.object(adapter, "database_query") as database:
            dry = adapter.repair_registry()
            self.assertTrue(path.exists())
            self.assertEqual(dry["candidates"], [str(path)])
            done = adapter.repair_registry(apply=True)
            self.assertFalse(path.exists())
            self.assertEqual(Path(done["moved"][0]["to"]).read_bytes(), original)
            database.assert_not_called()

    def test_live_or_different_database_registry_is_not_quarantined(self):
        path = self.stale_manifest()
        with mock.patch.dict(os.environ, {"MULTICA_DEV_HOME": str(self.root / "dev-home")}), \
                mock.patch.object(adapter, "listeners", return_value={"123"}):
            with self.assertRaisesRegex(ValueError, "listening"):
                adapter.repair_registry(apply=True)
        path.write_text(path.read_text().replace("test_mu389", "another_database"))
        with mock.patch.dict(os.environ, {"MULTICA_DEV_HOME": str(self.root / "dev-home")}), \
                mock.patch.object(adapter, "listeners", return_value=set()):
            with self.assertRaisesRegex(ValueError, "another database"):
                adapter.repair_registry(apply=True)
        self.assertTrue(path.exists())

    def test_incomplete_clone_blocks_before_any_database_command(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        golden = self.data / "golden"
        golden.mkdir()
        (golden / "multica.dump").write_bytes(b"snapshot")
        (self.data / "client-clone-pending.json").write_text("{}")
        binary = self.root / "bin"
        binary.mkdir()
        docker = binary / "docker"
        docker.write_text("#!/bin/sh\ntouch \"$TEST_DOCKER_USED\"\nexit 1\n")
        docker.chmod(0o755)
        marker = self.root / "docker-used"
        script = Path(__file__).with_name("dp_env.sh")
        result = subprocess.run(["bash", str(script), "prepare-copy", str(self.root)],
            env={**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"], "TEST_DOCKER_USED": str(marker)},
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("incomplete clone", result.stderr)
        self.assertFalse(marker.exists())

    def test_renew_copy_preserves_old_database_and_supports_no_start(self):
        env = adapter.env_values()
        calls = []
        def run(argv, **kwargs):
            calls.append(argv[-1])
            return ""
        with mock.patch.object(adapter, "recovery_check", return_value=env), \
                mock.patch.object(adapter, "repair_registry"), \
                mock.patch.object(adapter, "registry_records", return_value=[]), \
                mock.patch.object(adapter, "listeners", return_value=set()), \
                mock.patch.object(adapter, "run", side_effect=run):
            result = adapter.renew_copy(no_start=True)
        self.assertEqual(calls, ["down", "refresh-snapshot", "prepare-copy"])
        self.assertEqual(result["old_database"], "test_mu389")
        self.assertTrue(result["old_database_preserved"])
        self.assertNotEqual(result["new_database"], "test_mu389")
        backup = Path(result["recovery_record"]).parent
        self.assertEqual((backup / "env.worktree").read_text(), self.env)
        self.assertEqual(adapter.env_values()["POSTGRES_DB"], result["new_database"])

    def test_failed_new_snapshot_preserves_existing_configuration(self):
        env = adapter.env_values()
        def run(argv, **kwargs):
            if argv[-1] == "refresh-snapshot":
                raise ValueError("snapshot failed")
            return ""
        with mock.patch.object(adapter, "recovery_check", return_value=env), \
                mock.patch.object(adapter, "repair_registry"), \
                mock.patch.object(adapter, "registry_records", return_value=[]), \
                mock.patch.object(adapter, "listeners", return_value=set()), \
                mock.patch.object(adapter, "run", side_effect=run):
            with self.assertRaisesRegex(ValueError, "snapshot failed"):
                adapter.renew_copy(no_start=True)
        self.assertEqual((self.root / ".env.worktree").read_text(), self.env)

    def test_container_with_matching_port_but_another_worktree_is_rejected(self):
        container = {"Config": {"Labels": {
            "com.docker.compose.project": "multica-dp-mu-389",
            "com.docker.compose.service": "postgres",
            "com.docker.compose.project.working_dir": str(self.root / "another-worktree"),
            "com.docker.compose.project.config_files": str(self.root / "another-worktree/docker-compose.yml"),
        }}, "State": {"Running": True}, "NetworkSettings": {"Ports": {"5432/tcp": [{"HostPort": "62101"}]}}}
        with mock.patch.object(adapter, "listeners", return_value=set()), \
                mock.patch.object(adapter, "run", side_effect=["container-id", json.dumps([container])]), \
                mock.patch.object(adapter, "database_query") as query:
            with self.assertRaisesRegex(ValueError, "container ownership"):
                adapter.recovery_check()
            query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
