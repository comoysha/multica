#!/usr/bin/env python3
"""Remote boundaries are verified with fake jobs, credentials, and endpoints."""
import copy
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

import client_adapter as adapter
import ios_device_build as build
import ios_remote_env as worker

PROJECT = Path(__file__).resolve().parents[2]


class RemoteTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.app = self.root / "build/current.app"
        self.platform = self.root / "platform"
        tool = self.platform / "tools/build/remote.py"
        tool.parent.mkdir(parents=True)
        tool.write_text("# fake remote entry\n")
        self.input = self.root / "data/ios-remote-input.json"
        self.input.parent.mkdir()
        self.commit = "a" * 40
        self.value = {"version": 1, "recipe": "ios-device", "source_commit": self.commit, "build_number": "17",
                      "api_url": "https://test.example:52102", "auth_token": "", "contract_fingerprint": "b" * 64}
        self.contract = {"fingerprint": "b" * 64, "public_url": self.value["api_url"],
            "verification": {"backend": {"identity": {"pid": 123, "started_at": "fixture"}},
                             "data": {"clone_id": "copy-only", "snapshot_sha256": "c" * 64}}}
        self.before = build.runtime_identity(self.contract)
        for patch in (mock.patch.multiple(adapter, ROOT=self.root, DATA=self.root / "data"),
                      mock.patch.multiple(build, ROOT=self.root, APP=self.app),
                      mock.patch.multiple(worker, ROOT=self.root, INPUT=self.input)):
            patch.start();self.addCleanup(patch.stop)

    def write_input(self, **changes):
        self.input.write_text(json.dumps({**self.value, **changes}))

    def test_device_input_contains_no_database_identity_or_account_token(self):
        contract = types.SimpleNamespace(verify=mock.Mock(return_value=copy.deepcopy(self.contract)))
        with mock.patch.object(adapter, "identity"), mock.patch.object(adapter, "guarded_env", return_value=({"PORT": "18813"}, None)), \
                mock.patch.object(adapter, "run", side_effect=[self.commit, "17"]), \
                mock.patch.object(adapter, "shared_tool", return_value=contract), mock.patch.object(build.testbench_auth, "main") as token:
            value, _, _, path = build.prepare("ios-device")
        self.assertEqual(value, self.value)
        self.assertEqual(set(json.loads(path.read_text())), worker.FIELDS)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("copy-only", path.read_text())
        token.assert_not_called()

    def test_branch_token_is_minted_only_after_verified_copy(self):
        verify = mock.Mock(return_value=copy.deepcopy(self.contract))
        with mock.patch.object(adapter, "identity"), mock.patch.object(adapter, "guarded_env", return_value=({"PORT": "18813"}, None)), \
                mock.patch.object(adapter, "run", side_effect=[self.commit, "17"]), \
                mock.patch.object(adapter, "shared_tool", return_value=types.SimpleNamespace(verify=verify)), \
                mock.patch.object(build.testbench_auth, "main", return_value="mul_fixture") as token:
            value, _, _, _ = build.prepare("ios-branch-ipa")
        self.assertEqual(value["auth_token"], "mul_fixture")
        self.assertEqual(token.call_args.args[0], "18813")
        verify.assert_called_once_with(self.root, include_public=False)

    def test_unverified_copy_cannot_mint_or_write_build_input(self):
        with mock.patch.object(adapter, "identity"), mock.patch.object(adapter, "guarded_env", return_value=({"PORT": "18813"}, None)), \
                mock.patch.object(adapter, "run", return_value=self.commit), \
                mock.patch.object(adapter, "shared_tool", return_value=types.SimpleNamespace(verify=mock.Mock(side_effect=RuntimeError("bad copy")))), \
                mock.patch.object(build.testbench_auth, "main") as token:
            with self.assertRaisesRegex(RuntimeError, "bad copy"):
                build.prepare("ios-branch-ipa")
        token.assert_not_called()
        self.assertFalse(self.input.exists())

    def test_remote_native_compilation_is_serial_and_generic(self):
        config = json.loads((PROJECT / "dev-platform.json").read_text())
        for name, recipe in config["remote_build"]["recipes"].items():
            for step in recipe.get("steps", []):
                argv = step["argv"]
                if "xcodebuild" in argv and any(action in argv for action in ("build", "archive", "build-for-testing")):
                    self.assertEqual(argv[argv.index("-jobs") + 1], "1", name)
                    self.assertIn("generic/platform=iOS", argv)

    def fake_fetch(self, argv, **kwargs):
        if argv[:2] == ["git", "rev-parse"]:
            return self.commit
        if argv[0] == "codesign":
            return ""
        if argv[2] == "ack":
            return json.dumps({"acknowledged": True})
        self.assertEqual(argv[2], "run")
        self.assertEqual(argv[argv.index("--input") + 1], str(self.input) + "=data/ios-remote-input.json")
        output = Path(argv[argv.index("--output") + 1])
        artifact = output / "artifacts/app/MulticaStaging.app"
        artifact.mkdir(parents=True)
        (artifact / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": build.BUNDLE,
            "DPBuildCommit": self.commit, "CFBundleSupportedPlatforms": ["iPhoneOS"], "DPNativeInputsSHA256": "c" * 64}))
        result = {"status": "verified", "job_id": "job", "source_git_sha": self.commit, "recipe_sha256": "d" * 64,
                  "artifacts": {"app": str(artifact)}, "result_file": str(output / "result.json")}
        (output / "result.json").write_text(json.dumps(result))
        return json.dumps(result)

    def test_device_build_dispatches_then_rechecks_same_backend_before_promoting(self):
        with mock.patch.object(adapter, "platform_root", return_value=self.platform), \
                mock.patch.object(build, "prepare", return_value=(self.value, self.contract, self.before, self.input)), \
                mock.patch.object(adapter, "run", side_effect=self.fake_fetch) as run, \
                mock.patch.object(adapter, "shared_tool", return_value=types.SimpleNamespace(verify=mock.Mock(return_value=self.contract))):
            result = build.build()
        self.assertEqual(result["app"], str(self.app))
        self.assertEqual(plistlib.loads((self.app / "Info.plist").read_bytes())["DPBuildCommit"], self.commit)
        commands = [c.args[0] for c in run.call_args_list]
        self.assertFalse(any(c[0] in ("xcodebuild", "pnpm") for c in commands))

    def test_changed_backend_keeps_old_local_artifact(self):
        self.app.mkdir(parents=True)
        (self.app / "old").write_text("preserved")
        changed = copy.deepcopy(self.contract)
        changed["verification"]["backend"]["identity"]["pid"] = 124
        with mock.patch.object(adapter, "platform_root", return_value=self.platform), \
                mock.patch.object(build, "prepare", return_value=(self.value, self.contract, self.before, self.input)), \
                mock.patch.object(adapter, "run", side_effect=self.fake_fetch), \
                mock.patch.object(adapter, "shared_tool", return_value=types.SimpleNamespace(verify=mock.Mock(return_value=changed))):
            with self.assertRaisesRegex(ValueError, "backend instance"):
                build.build()
        self.assertEqual((self.app / "old").read_text(), "preserved")

    def test_missing_or_failed_remote_never_compiles_locally(self):
        (self.platform / "tools/build/remote.py").unlink()
        with mock.patch.object(adapter, "platform_root", return_value=self.platform), mock.patch.object(build, "prepare") as prepare:
            with self.assertRaisesRegex(ValueError, "cannot fall back"):
                build.build()
        prepare.assert_not_called()
        self.assertFalse(self.app.exists())

    def remote_context(self):
        return mock.patch.dict(os.environ, {"DP_REMOTE_JOB_ID": "e" * 32, "DP_REMOTE_SOURCE_COMMIT": self.commit})

    def fake_git(self, *args):
        return "true" if args[-1] == "--is-shallow-repository" else self.commit

    def test_environment_helper_rejects_m4_worktree_or_missing_worker_context(self):
        self.write_input()
        with mock.patch.dict(os.environ, {"DP_REMOTE_JOB_ID": ""}), mock.patch.object(worker, "git", side_effect=self.fake_git):
            with self.assertRaisesRegex(ValueError, "remote worker"):
                worker.load_context(self.commit, "ios-device")
        (self.root / ".dev-platform-workspace.json").write_text("{}")
        with self.remote_context(), mock.patch.object(worker, "git", side_effect=self.fake_git):
            with self.assertRaisesRegex(ValueError, "remote worker"):
                worker.load_context(self.commit, "ios-device")

    def test_environment_rejects_wrong_source_loopback_and_token_scope(self):
        for change in ({"source_commit": "f" * 40}, {"api_url": "http://127.0.0.1:18813"},
                       {"auth_token": "mul_forbidden"}, {"recipe": "ios-branch-ipa"}):
            with self.subTest(change=change), self.remote_context(), mock.patch.object(worker, "git", side_effect=self.fake_git):
                self.write_input(**change)
                with self.assertRaises(ValueError):
                    worker.load_context(self.commit, "ios-device")

    def test_secret_redaction_and_worker_process_group_preserved(self):
        self.write_input(recipe="ios-branch-ipa", auth_token="mul_fixture_secret")
        captured = types.SimpleNamespace(buffer=io.BytesIO())
        with self.remote_context(), mock.patch.object(worker, "git", side_effect=self.fake_git), \
                mock.patch.object(worker.sys, "stdout", captured), \
                mock.patch.object(worker.subprocess, "Popen", wraps=subprocess.Popen) as process:
            code = worker.execute([sys.executable, "-c", "import os;print(os.environ['EXPO_PUBLIC_LOCAL_AUTH_TOKEN'])"], self.commit, "ios-branch-ipa")
        self.assertEqual(code, 0)
        self.assertEqual(captured.buffer.getvalue(), b"[REDACTED]\n")
        self.assertNotIn("start_new_session", process.call_args.kwargs)
        self.assertEqual(process.call_args.kwargs["env"]["EXPO_NO_DOTENV"], "1")

    def production(self):
        import ios_production as production
        value = {"version":1,"root":str(self.root),"recipe":"production-ipa","source_commit":self.commit,
                 "build_number":"17","bundle_id":production.BUNDLE,"team":production.TEAM,"required_signing":"adhoc",
                 "settings":{"EXPO_PUBLIC_API_URL":"https://formal.example:13100","EXPO_PUBLIC_WEB_URL":"https://formal.example:13100"}}
        context = self.root/"context.json"
        context.write_text(json.dumps(value))
        return production, value, context

    def test_production_schema_does_not_read_credentials_and_environment_is_explicit(self):
        production, context, path = self.production()
        prepared = production.input_value(context, "mul_formal_fixture")
        self.assertEqual(set(prepared),worker.PRODUCTION_FIELDS)
        self.assertNotIn("contract_fingerprint",prepared)
        target=self.root/"production-input.json"
        target.write_text(json.dumps(prepared))
        with mock.patch.object(worker,"PRODUCTION_INPUT",target), self.remote_context(), \
             mock.patch.object(worker,"git",side_effect=self.fake_git), \
             mock.patch.dict(os.environ,{"APP_ENV":"development","EXPO_PUBLIC_API_URL":"http://wrong.invalid","EXPO_NO_DOTENV":"0"}):
            _, env, _ = worker.load_context(self.commit,"production-ipa")
        self.assertEqual(env["APP_ENV"],"production")
        self.assertEqual(env["EXPO_NO_DOTENV"],"1")
        self.assertEqual(env["EXPO_PUBLIC_API_URL"],"https://formal.example:13100")
        self.assertEqual(env["EXPO_PUBLIC_WEB_URL"],"https://formal.example:13100")
        self.assertEqual(env["EXPO_PUBLIC_LOCAL_AUTH_TOKEN"],"mul_formal_fixture")
        self.assertEqual(env["EXPO_BUNDLE_IDENTIFIER_PROD"],production.BUNDLE)

    def test_production_preparer_reads_only_the_fake_main_source_and_writes_private_input(self):
        production, value, path = self.production()
        state = tempfile.TemporaryDirectory()
        self.addCleanup(state.cleanup)
        target=Path(state.name).resolve()/"inputs/multica-ios-production-input.json"
        value["inputs"]=[{"source":str(target),"destination":production.DESTINATION}]
        path.write_text(json.dumps(value))
        source=self.root/"apps/mobile/.env.local"
        source.parent.mkdir(parents=True)
        source.write_text('EXPO_PUBLIC_LOCAL_AUTH_TOKEN="mul_formal_fixture"\nUNRELATED_SECRET=must_not_transfer\n')
        with mock.patch.object(production,"ROOT",self.root), \
             mock.patch.dict(os.environ,{"INSTALL_HUB_HOME":state.name}), \
             mock.patch.object(adapter,"run",side_effect=[self.commit,"17",str(self.root/".git"),""]):
            result=production.prepare(path)
        payload=json.loads(target.read_text())
        self.assertEqual(set(payload),worker.PRODUCTION_FIELDS)
        self.assertEqual(payload["auth_token"],"mul_formal_fixture")
        self.assertNotIn("must_not_transfer",target.read_text())
        self.assertNotIn("mul_formal_fixture",json.dumps(result))
        self.assertEqual(target.stat().st_mode & 0o777,0o600)
        self.assertEqual(source.read_text(),'EXPO_PUBLIC_LOCAL_AUTH_TOKEN="mul_formal_fixture"\nUNRELATED_SECRET=must_not_transfer\n')

    def test_missing_or_changing_formal_token_source_never_creates_input(self):
        production,value,path=self.production()
        with tempfile.TemporaryDirectory() as state:
            target=Path(state).resolve()/"inputs/multica-ios-production-input.json"
            value["inputs"]=[{"source":str(target),"destination":production.DESTINATION}]
            path.write_text(json.dumps(value))
            for token, hashes in (("",["same"]),("mul_formal_fixture",["before","after"])):
                with self.subTest(token=bool(token)),mock.patch.object(production,"ROOT",self.root), \
                     mock.patch.dict(os.environ,{"INSTALL_HUB_HOME":state}), \
                     mock.patch.object(adapter,"run",side_effect=[self.commit,"17",str(self.root/".git"),""]), \
                     mock.patch.object(adapter,"env_values",return_value={"EXPO_PUBLIC_LOCAL_AUTH_TOKEN":token}), \
                     mock.patch.object(adapter,"sha256",side_effect=hashes):
                    with self.assertRaises(ValueError):production.prepare(path)
                self.assertFalse(target.exists())

    def test_test_worktree_cannot_read_formal_token(self):
        production, _, path = self.production()
        with mock.patch.object(production,"ROOT",self.root), \
             mock.patch.object(adapter,"run",side_effect=[self.commit,"17",str(self.root/"other/.git")]), \
             mock.patch.object(adapter,"env_values") as credential_read:
            with self.assertRaisesRegex(ValueError,"main checkout"):
                production.prepare(path)
        credential_read.assert_not_called()

    def test_formal_ipa_checks_actual_production_environment_api_and_source(self):
        import zipfile
        production, value, path = self.production()
        artifact=self.root/"formal.ipa"
        for changed in (None,"environment","api","commit"):
            extra={"APP_ENV":"production","clientRuntime":{"apiUrl":value["settings"]["EXPO_PUBLIC_API_URL"],"buildCommit":self.commit}}
            if changed=="environment":extra["APP_ENV"]="development"
            if changed=="api":extra["clientRuntime"]["apiUrl"]="https://wrong.example"
            if changed=="commit":extra["clientRuntime"]["buildCommit"]="f"*40
            with zipfile.ZipFile(artifact,"w") as package:
                package.writestr("Payload/Multica.app/Info.plist",plistlib.dumps({"CFBundleIdentifier":production.BUNDLE,"DPBuildCommit":self.commit,"CFBundleVersion":"17"}))
                package.writestr("Payload/Multica.app/EXConstants.bundle/app.config",json.dumps({"extra":extra}))
            with mock.patch.object(production,"ROOT",self.root),mock.patch.object(adapter,"run",side_effect=[self.commit,"17"]):
                if changed:
                    with self.assertRaisesRegex(ValueError,"does not match"):
                        production.inspect(path,artifact)
                else:
                    self.assertEqual(production.inspect(path,artifact)["build_commit"],self.commit)

    def test_adhoc_requires_distribution_identity_without_development_fallback(self):
        production, _, _ = self.production()
        with mock.patch.object(worker,"load_context"), \
             mock.patch.object(production.subprocess,"run",return_value=types.SimpleNamespace(returncode=0,stdout='Apple Development: Fixture (8ALC9Q43AC)')):
            with self.assertRaisesRegex(ValueError,"cannot fall back"):
                production.signing_ready(self.commit)
        with mock.patch.object(worker,"load_context"), \
             mock.patch.object(production.subprocess,"run",return_value=types.SimpleNamespace(returncode=0,stdout='Apple Distribution: Fixture (8ALC9Q43AC)')):
            self.assertEqual(production.signing_ready(self.commit),{"distribution_identity":"available"})



class ToolchainTest(unittest.TestCase):
    def test_compatible_versions_use_the_hosts_selected_xcode(self):
        import ios_toolchain
        def result(argv, **kwargs):
            self.assertNotIn("env", kwargs)
            self.assertEqual(os.environ["DEVELOPER_DIR"],"/fixture/Xcode-26.6.app/Contents/Developer")
            return types.SimpleNamespace(returncode=0,stdout="Xcode 26.6\nBuild version fixture\n" if argv[0]=="xcodebuild" else "v22.13.0\n")
        with mock.patch.dict(os.environ,{"DEVELOPER_DIR":"/fixture/Xcode-26.6.app/Contents/Developer"}), \
             mock.patch.object(ios_toolchain.subprocess,"run",side_effect=result) as calls:
            self.assertEqual(ios_toolchain.check(),{"xcode":"26.6.0","node":"22.13.0"})
        self.assertEqual([call.args[0] for call in calls.call_args_list],[["xcodebuild","-version"],["node","--version"]])

    def test_old_xcode_fails_before_node_or_account_access(self):
        import ios_toolchain
        with mock.patch.object(ios_toolchain.subprocess,"run",return_value=types.SimpleNamespace(returncode=0,stdout="Xcode 26.2\n")) as calls:
            with self.assertRaisesRegex(ValueError,"Xcode 26.4"):
                ios_toolchain.check()
        self.assertEqual(calls.call_count,1)

    def test_old_node_and_unknown_toolchain_fail_closed(self):
        import ios_toolchain
        for old in ("v22.12.9", "v20.20.0", "unknown"):
            values=[types.SimpleNamespace(returncode=0,stdout="Xcode 26.4\n"),types.SimpleNamespace(returncode=0,stdout=old)]
            with self.subTest(old=old),mock.patch.object(ios_toolchain.subprocess,"run",side_effect=values):
                with self.assertRaises(ValueError):ios_toolchain.check()

    def test_every_multica_native_recipe_checks_versions_first(self):
        recipes=json.loads((PROJECT/"dev-platform.json").read_text())["remote_build"]["recipes"]
        for name in ("ios-device","ios-branch-ipa","production-ipa"):
            self.assertEqual(recipes[name]["steps"][0]["argv"],["python3","scripts/dev/ios_toolchain.py"])


class PublishTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        subprocess.run(["git", "init", "-q", "-b", "feat/mu-396"], cwd=self.root, check=True)
        subprocess.run(["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "--allow-empty", "-qm", "fixture"], cwd=self.root, check=True)
        scripts = self.root / "scripts/dev"
        scripts.mkdir(parents=True)
        (self.root / "apps/mobile").mkdir(parents=True)
        (self.root / ".dev-platform-workspace.json").write_text(json.dumps({"slug": "mu-396"}))
        (scripts / "publish_ios_test_build.sh").write_text((PROJECT / "scripts/dev/publish_ios_test_build.sh").read_text())
        (scripts / "client_adapter.py").write_text("import json\nprint(json.dumps({'worktree_port':42102,'services':{'backend':{'port':18813}},'public_url':'https://test.example:52102'}))\n")
        (scripts / "testbench_auth.py").write_text("print('mul_fixture')\n")
        (scripts / "ios_device_build.py").write_text("import json,hashlib,os,sys\n"
            "if os.environ.get('FAKE_REMOTE_FAIL'):sys.exit(7)\n"
            "print(json.dumps({'artifacts':{'ipa':'/fixture/MulticaStaging.ipa'},'result_file':'/fixture/result.json','api_url':os.environ.get('FAKE_API','https://test.example:52102'),'auth_fingerprint':hashlib.sha256(b'mul_fixture').hexdigest()}))\n")
        self.calls = self.root / "hub.jsonl"
        hub = self.root / "hub.py"
        hub.write_text("#!/usr/bin/env python3\nimport json,os,sys\n"
            "with open(os.environ['FAKE_HUB_CALLS'],'a') as f:f.write(json.dumps(sys.argv[1:])+'\\n')\n"
            "if sys.argv[1]=='base-url':print('https://test.example')\n")
        hub.chmod(0o755)
        verifier = self.root / "verify.py"
        verifier.write_text("# fake only; no network\n")
        binaries = self.root / "bin"
        binaries.mkdir()
        for name in ('pnpm', 'xcodebuild', 'npx'):
            tool = binaries / name
            tool.write_text("#!/bin/sh\necho local-native-build-forbidden >&2\nexit 99\n")
            tool.chmod(0o755)
        self.env = {**os.environ, 'MULTICA_IOS_HUB':str(hub),'MULTICA_CLIENT_TOOL':str(verifier),
                    'DP_REAL_ROOT':str(self.root),'DP_SLUG':'mu-396','FAKE_HUB_CALLS':str(self.calls),
                    'PATH':str(binaries)+':'+os.environ['PATH'],'TZ':'Asia/Shanghai'}

    def publish(self, **env):
        return subprocess.run(['bash','scripts/dev/publish_ios_test_build.sh','--force'],cwd=self.root,
                              env={**self.env,**env},capture_output=True,text=True)

    def published(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines() if line] if self.calls.exists() else []

    def test_branch_publisher_only_passes_fetched_artifact_and_receipt(self):
        result = self.publish()
        self.assertEqual(result.returncode,0,result.stderr)
        command = next(row for row in self.published() if row[0]=='publish')
        self.assertEqual(command[command.index('--project')+1],str(self.root))
        self.assertEqual(command[command.index('--artifact')+1],'/fixture/MulticaStaging.ipa')
        self.assertEqual(command[command.index('--remote-receipt')+1],'/fixture/result.json')
        self.assertNotIn('--setting',command)
        self.assertNotIn('--prebuild',command)

    def test_remote_failure_or_wrong_api_cannot_publish(self):
        for env in ({'FAKE_REMOTE_FAIL':'1'},{'FAKE_API':'https://wrong.example'}):
            with self.subTest(env=env):
                result=self.publish(**env)
                self.assertNotEqual(result.returncode,0)
                self.assertFalse(any(row[0]=='publish' for row in self.published()))

    def test_wrong_task_cannot_publish_under_another_slot(self):
        result=self.publish(DP_SLUG='mu-999')
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(any(row[0]=='publish' for row in self.published()))


if __name__ == "__main__":
    unittest.main()
