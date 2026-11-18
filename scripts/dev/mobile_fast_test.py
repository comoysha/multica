"""Exercise integration and failure recovery against a disposable Git repository."""
import json
import plistlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import mobile_fast as fast


class DevelopmentClientTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Path(self.temp.name) / 'Multica.app'
        self.app.mkdir()

    def client(self, code, schemes=('multica', 'exp+multica-mobile'), debug_code=None):
        info = dict(CFBundleExecutable='Multica', CFBundleIdentifier='ai.multica.mobile.staging',
                    CFBundleVersion='5745',
                    CFBundleURLTypes=[dict(CFBundleURLSchemes=list(schemes))])
        (self.app / 'Info.plist').write_bytes(plistlib.dumps(info, fmt=plistlib.FMT_BINARY))
        (self.app / 'Multica').write_bytes(code)
        if debug_code is not None:
            (self.app / 'Multica.debug.dylib').write_bytes(debug_code)

    def test_release_with_builtin_bundle_and_matching_scheme_is_rejected(self):
        self.client(b'\x00Multica\x00RCTBridge\x00main.jsbundle\x00')
        (self.app / 'main.jsbundle').write_text('embedded release code')
        with self.assertRaisesRegex(RuntimeError, 'no EXDevLauncher'):
            fast.verify_client(self.app)

    def test_launcher_without_development_url_scheme_is_rejected(self):
        self.client(b'\x00EXDevLauncherController\x00', schemes=('multica',))
        with self.assertRaisesRegex(RuntimeError, 'missing the Metro URL scheme'):
            fast.verify_client(self.app)

    def test_development_client_is_identified(self):
        self.client(b'\x00EXDevLauncherController\x00')
        result = fast.verify_client(self.app)
        self.assertTrue(result['development_client'])
        self.assertEqual(result['scheme'], 'exp+multica-mobile')
        self.assertEqual(result['bundle_id'], 'ai.multica.mobile.staging')

    def test_debug_dylib_launcher_is_supported(self):
        self.client(b'\x00__debug_dylib\x00', debug_code=b'\x00EXDevLauncherController\x00')
        self.assertTrue(fast.verify_client(self.app)['development_client'])


class MobileEnvironmentTest(unittest.TestCase):
    def test_stock_staging_env_is_rejected_instead_of_serving_the_wrong_backend(self):
        env = dict(EXPO_PUBLIC_API_URL='https://official.example.invalid',
                   EXPO_PUBLIC_WEB_URL='https://official.example.invalid')
        with self.assertRaisesRegex(RuntimeError, 'EXPO_PUBLIC_LOCAL_AUTH_TOKEN'):
            fast.validate_mobile_env(env, 'https://personal.example.invalid:52101')

    def test_personal_auth_is_not_sent_to_a_different_server(self):
        env = dict(EXPO_PUBLIC_API_URL='https://official.example.invalid',
                   EXPO_PUBLIC_WEB_URL='https://personal.example.invalid:13100',
                   EXPO_PUBLIC_LOCAL_AUTH_TOKEN='mul_fixture_not_a_real_credential')
        with self.assertRaisesRegex(RuntimeError, 'same personal server'):
            fast.validate_mobile_env(env, 'https://personal.example.invalid:52101')

    def test_distinct_api_and_metro_ports_on_the_personal_server_are_valid(self):
        env = dict(EXPO_PUBLIC_API_URL='https://personal.example.invalid:13100',
                   EXPO_PUBLIC_WEB_URL='https://personal.example.invalid:13100',
                   EXPO_PUBLIC_LOCAL_AUTH_TOKEN='mul_fixture_not_a_real_credential')
        fast.validate_mobile_env(env, 'https://personal.example.invalid:52101')


class MetroStartupTest(unittest.TestCase):
    def test_waits_for_a_fresh_source_line_and_healthy_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / f'data/logs/{fast.LABEL}.stdout.log'
            log.parent.mkdir(parents=True)
            source = 'mobile source: expected\n'
            log.write_text(source)
            offline = subprocess.CompletedProcess([], 7, '')
            running = subprocess.CompletedProcess([], 0, 'packager-status:running')

            def probe(ready, timeout, message):
                self.assertFalse(ready())
                status.assert_not_called()
                with log.open('a') as output:
                    output.write(source)
                self.assertFalse(ready())
                self.assertTrue(ready())

            with (patch.object(fast, 'start_service'),
                  patch.object(fast, 'wait_for', side_effect=probe),
                  patch.object(fast, 'metro_status', side_effect=[offline, running]) as status):
                fast.start_metro(dict(checkout=root, port=8081), root / 'metro.plist', 'expected')


class IntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.remote = self.root / 'github.git'
        fast.run('git', 'init', '-q', '--bare', self.remote)
        fast.run('git', 'init', '-q', '-b', 'task', cwd=self.repo)
        fast.run('git', 'config', 'user.name', 'Fixture', cwd=self.repo)
        fast.run('git', 'config', 'user.email', 'fixture@example.invalid', cwd=self.repo)
        self.before = self.commit('screen.txt', 'base')
        fast.run('git', 'branch', fast.BRANCH, cwd=self.repo)
        cfg = self.root / 'config.json'
        cfg.write_text(json.dumps(dict(checkout=str(self.repo), port=8081)))
        self.enterContext(patch.object(fast, 'CONFIG', cfg))
        self.enterContext(patch.object(fast, 'STATE', self.root / 'state'))
        self.start_metro = self.enterContext(patch.object(fast, 'start_metro'))
        self.enterContext(patch.object(fast, 'metro_port_closed', return_value=True))
        self.launchctl = []
        self.loaded = True
        self.pr = dict(state='OPEN', baseRefName=fast.BRANCH, mergeCommit=None,
                       url='https://github.com/comoysha/multica/pull/42')
        original = fast.run

        def run(*args, **kwargs):
            if args[:3] == ('gh', 'pr', 'view'):
                self.assertEqual(args[3:6], (42, '--repo', fast.REPO))
                return subprocess.CompletedProcess(args, 0, json.dumps(self.pr))
            if args[:2] == ('git', 'fetch'):
                self.assertEqual(args[-2:], (f'https://github.com/{fast.REPO}.git', f'refs/heads/{fast.BRANCH}'))
                args = (*args[:-2], self.remote, args[-1])
            if args[0] == 'launchctl':
                action = args[1]
                self.launchctl.append(action)
                if action == 'bootout':
                    self.loaded = False
                return subprocess.CompletedProcess(args, int(action == 'print' and not self.loaded), '')
            return original(*args, **kwargs)

        self.enterContext(patch.object(fast, 'run', run))

    def commit(self, path, value):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)
        fast.run('git', 'add', path, cwd=self.repo)
        fast.run('git', 'commit', '-qm', value, cwd=self.repo)
        return fast.git(self.repo, 'rev-parse', 'HEAD')

    def switch(self, branch=fast.BRANCH):
        fast.run('git', 'checkout', '-q', branch, cwd=self.repo)

    def publish(self, sha):
        fast.run('git', 'push', '-q', self.remote, f'{sha}:refs/heads/{fast.BRANCH}', cwd=self.repo)

    def mark_merged(self, sha):
        self.pr.update(state='MERGED', mergeCommit={'oid': sha})

    def assert_unchanged(self):
        self.assertEqual(fast.git(self.repo, 'rev-parse', 'HEAD'), self.before)
        self.assertEqual(self.launchctl, [])
        self.start_metro.assert_not_called()
        self.assertFalse((fast.STATE / 'history.jsonl').exists())

    def test_open_pr_is_rejected_even_if_its_commit_is_available(self):
        sha = self.commit('screen.txt', 'visible demo')
        self.publish(sha)
        self.switch()
        with self.assertRaisesRegex(RuntimeError, 'must be merged'):
            fast.deliver(42)
        self.assert_unchanged()

    def test_merge_into_another_base_is_rejected(self):
        self.mark_merged(self.commit('screen.txt', 'visible demo'))
        self.pr['baseRefName'] = 'main'
        self.switch()
        with self.assertRaisesRegex(RuntimeError, 'must be merged'):
            fast.deliver(42)
        self.assert_unchanged()

    def test_merge_commit_must_be_on_the_fetched_branch(self):
        self.mark_merged(self.commit('screen.txt', 'visible demo'))
        self.publish(self.before)
        self.switch()
        with self.assertRaisesRegex(RuntimeError, 'absent from GitHub'):
            fast.deliver(42)
        self.assert_unchanged()

    def test_delivers_exact_merge_commit_and_retry_is_a_no_op(self):
        self.commit('screen.txt', 'visible demo')
        fast.run('git', 'checkout', '-qb', 'github', self.before, cwd=self.repo)
        fast.run('git', 'merge', '--no-ff', '-qm', 'Merge PR', 'task', cwd=self.repo)
        merged = fast.git(self.repo, 'rev-parse', 'HEAD')
        self.mark_merged(merged)
        newer = self.commit('screen.txt', 'newer change')
        self.publish(newer)
        self.switch()

        fast.deliver(42)
        fast.deliver(42)

        self.assertEqual((self.repo / 'screen.txt').read_text(), 'visible demo')
        self.assertEqual(fast.git(self.repo, 'rev-parse', 'HEAD'), merged)
        self.start_metro.assert_called_once()
        self.assertEqual(self.launchctl, ['print', 'bootout'])
        record = json.loads((fast.STATE / 'history.jsonl').read_text())
        self.assertEqual((record['before'], record['after'], record['pr']),
                         (self.before, merged, self.pr['url']))
        self.assertTrue(record['time'].endswith('+08:00'))

    def test_failed_metro_start_restores_previous_checkout_without_delivery_record(self):
        sha = self.commit('screen.txt', 'visible demo')
        self.mark_merged(sha)
        self.publish(sha)
        self.switch()

        def start(cfg, plist, expected):
            self.assertEqual(fast.git(self.repo, 'rev-parse', 'HEAD'), expected)
            self.loaded = True
            if expected == sha:
                raise RuntimeError('startup failed')

        self.start_metro.side_effect = start
        with self.assertRaisesRegex(RuntimeError, f'Metro restored at {self.before}'):
            fast.deliver(42)
        self.assertEqual((self.repo / 'screen.txt').read_text(), 'base')
        self.assertEqual(fast.git(self.repo, 'rev-parse', 'HEAD'), self.before)
        self.assertEqual(self.launchctl, ['print', 'bootout', 'print', 'bootout'])
        self.assertTrue(self.loaded)
        self.assertFalse((fast.STATE / 'history.jsonl').exists())

    def test_native_change_is_rejected_before_modification(self):
        self.commit('apps/mobile/package.json', '{}')
        sha = self.commit('screen.txt', 'visible demo')
        self.mark_merged(sha)
        self.publish(sha)
        self.switch()
        with self.assertRaisesRegex(RuntimeError, 'rebuild'):
            fast.deliver(42)
        self.assert_unchanged()

    def test_diverged_checkout_is_preserved(self):
        sha = self.commit('screen.txt', 'visible demo')
        self.mark_merged(sha)
        self.publish(sha)
        self.switch()
        local = self.commit('screen.txt', 'local commit')
        with self.assertRaisesRegex(RuntimeError, 'diverged'):
            fast.deliver(42)
        self.assertEqual(fast.git(self.repo, 'rev-parse', 'HEAD'), local)
        self.assertEqual(self.launchctl, [])

    def test_dirty_checkout_is_preserved(self):
        self.switch()
        (self.repo / 'screen.txt').write_text('user edit')
        with self.assertRaisesRegex(RuntimeError, 'local changes'):
            fast.deliver(42)
        self.assertEqual((self.repo / 'screen.txt').read_text(), 'user edit')

    def test_concurrent_delivery_is_rejected(self):
        self.switch()
        with fast.locked():
            with self.assertRaises(BlockingIOError):
                with fast.locked():
                    self.fail('Second integration acquired the lock')


if __name__ == '__main__':
    unittest.main()
