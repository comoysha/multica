"""Exercise integration and failure recovery against a disposable Git repository."""
import argparse
import contextlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import mobile_fast as fast


class IntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        fast.run('git', 'init', '-q', '-b', 'task', cwd=self.repo)
        fast.run('git', 'config', 'user.name', 'Fixture', cwd=self.repo)
        fast.run('git', 'config', 'user.email', 'fixture@example.invalid', cwd=self.repo)
        self.commit('screen.txt', 'base')
        fast.run('git', 'branch', 'dev/mobile', cwd=self.repo)
        cfg = self.root / 'config.json'
        cfg.write_text(json.dumps(dict(checkout=str(self.repo))))
        self.enterContext(patch.object(fast, 'CONFIG', cfg))
        self.enterContext(patch.object(fast, 'STATE', self.root / 'state'))
        original = fast.run

        def run(*args, **kwargs):
            if args[0] == 'launchctl':
                return subprocess.CompletedProcess(args, 1, '')
            return original(*args, **kwargs)

        self.enterContext(patch.object(fast, 'run', run))

    def commit(self, path, value):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)
        fast.run('git', 'add', path, cwd=self.repo)
        fast.run('git', 'commit', '-qm', value, cwd=self.repo)
        return fast.git(self.repo, 'rev-parse', 'HEAD')

    def switch(self):
        fast.run('git', 'checkout', '-q', 'dev/mobile', cwd=self.repo)

    def test_deliver_and_revert_preserves_history(self):
        sha = self.commit('screen.txt', 'visible demo')
        self.switch()
        fast.update(argparse.Namespace(command='deliver', commits=[sha]))
        self.assertEqual((self.repo / 'screen.txt').read_text(), 'visible demo')
        delivered = fast.git(self.repo, 'rev-parse', 'HEAD')
        fast.update(argparse.Namespace(command='rollback', commits=[delivered]))
        self.assertEqual((self.repo / 'screen.txt').read_text(), 'base')
        self.assertEqual(len((fast.STATE / 'history.jsonl').read_text().splitlines()), 2)

    def test_conflicting_batch_restores_previous_head(self):
        first = self.commit('extra.txt', 'new file')
        second = self.commit('screen.txt', 'task edit')
        self.switch()
        before = self.commit('screen.txt', 'shared edit')
        with self.assertRaisesRegex(RuntimeError, 'Integration failed'):
            fast.update(argparse.Namespace(command='deliver', commits=[first, second]))
        self.assertEqual(fast.git(self.repo, 'rev-parse', 'HEAD'), before)
        self.assertFalse((self.repo / 'extra.txt').exists())
        self.assertEqual(fast.git(self.repo, 'status', '--porcelain'), '')

    def test_native_change_is_rejected_before_modification(self):
        sha = self.commit('apps/mobile/package.json', '{}')
        self.switch()
        before = fast.git(self.repo, 'rev-parse', 'HEAD')
        with self.assertRaisesRegex(RuntimeError, 'rebuild'):
            fast.update(argparse.Namespace(command='deliver', commits=[sha]))
        self.assertEqual(fast.git(self.repo, 'rev-parse', 'HEAD'), before)

    def test_dirty_checkout_is_preserved(self):
        sha = self.commit('extra.txt', 'new file')
        self.switch()
        (self.repo / 'screen.txt').write_text('user edit')
        with self.assertRaisesRegex(RuntimeError, 'local changes'):
            fast.update(argparse.Namespace(command='deliver', commits=[sha]))
        self.assertEqual((self.repo / 'screen.txt').read_text(), 'user edit')

    def test_concurrent_delivery_is_rejected(self):
        self.switch()
        with fast.locked():
            with self.assertRaises(BlockingIOError):
                with fast.locked():
                    self.fail('Second integration acquired the lock')


if __name__ == '__main__':
    unittest.main()
