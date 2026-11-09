#!/usr/bin/env python3
"""Manage the persistent mobile development checkout, never the main branch."""
import argparse
import fcntl
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import quote

CONFIG = Path.home() / '.config/multica-mobile/config.json'
STATE = Path.home() / '.local/state/multica-mobile'
LABEL = 'com.multica.mobile-metro'
NATIVE = ['apps/mobile/package.json', 'apps/mobile/app.config.ts', 'apps/mobile/patches',
          'apps/mobile/ios', 'apps/mobile/android', 'pnpm-lock.yaml', 'pnpm-workspace.yaml', 'package.json']


def run(*args, cwd=None, capture=False, check=True, env=None):
    return subprocess.run([str(x) for x in args], cwd=cwd, check=check, text=True,
                          stdout=subprocess.PIPE if capture else None, env=env)


def git(root, *args):
    return run('git', *args, cwd=root, capture=True).stdout.strip()


def config():
    return json.loads(CONFIG.read_text())


@contextmanager
def locked():
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / 'update.lock').open('w') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def clean(root):
    if git(root, 'branch', '--show-current') != 'dev/mobile':
        raise RuntimeError('Refusing to modify a branch other than dev/mobile')
    if git(root, 'status', '--porcelain', '--untracked-files=normal'):
        raise RuntimeError('Development checkout has local changes; preserve and resolve them first')


def event(root, action, before):
    record = dict(time=datetime.now(timezone.utc).isoformat(), action=action,
                  before=before, after=git(root, 'rev-parse', 'HEAD'))
    with (STATE / 'history.jsonl').open('a') as out:
        out.write(json.dumps(record) + '\n')
    print(json.dumps(record))


def service(action, check=True):
    target = f'gui/{os.getuid()}/{LABEL}'
    args = ['launchctl', 'kill', 'SIGTERM', target] if action == 'restart' else ['launchctl', 'print', target]
    return run(*args, check=check)


def start_service(plist):
    # launchd can finish unloading after bootout returns.
    for attempt in range(40):
        result = run('launchctl', 'bootstrap', f'gui/{os.getuid()}', plist,
                     capture=True, check=False)
        if result.returncode == 0:
            return
        time.sleep(0.5)
    raise RuntimeError('Code integration finished, but Metro restart failed. Run the service installation script')


def update(args):
    cfg = config()
    root = cfg['checkout']
    with locked():
        clean(root)
        before = git(root, 'rev-parse', 'HEAD')
        if not all(re.fullmatch(r'[0-9a-fA-F]{7,40}', ref) for ref in args.commits):
            raise RuntimeError('Pass explicit commit SHA values, not HEAD or branch names')
        commits = [git(root, 'rev-parse', '--verify', f'{ref}^{{commit}}') for ref in args.commits]
        for sha in commits:
            if len(git(root, 'rev-list', '--parents', '-n', '1', sha).split()) != 2:
                raise RuntimeError('Deliver ordinary commits individually, not merge commits')
            native = git(root, 'diff', '--name-only', f'{sha}^', sha, '--', *NATIVE)
            if native:
                raise RuntimeError('Native/dependency inputs changed; rebuild and verify a development client first:\n' + native)
        # Stop serving bundles while Git applies a possibly multi-commit delivery.
        plist = Path.home() / f'Library/LaunchAgents/{LABEL}.plist'
        active = run('launchctl', 'print', f'gui/{os.getuid()}/{LABEL}', capture=True, check=False).returncode == 0
        if active:
            run('launchctl', 'bootout', f'gui/{os.getuid()}/{LABEL}')
        try:
            verb = 'revert' if args.command == 'rollback' else 'cherry-pick'
            try:
                run('git', verb, *commits, cwd=root)
            except subprocess.CalledProcessError:
                run('git', verb, '--abort', cwd=root, check=False)
                raise RuntimeError('Integration failed; operation aborted. Resolve in the task checkout and retry')
            event(root, args.command, before)
        finally:
            if active:
                start_service(plist)


def serve():
    cfg = config()
    root = Path(cfg['checkout'])
    env = os.environ.copy()
    env.pop('CI', None)
    env.update(APP_ENV='staging', EXPO_NO_DOTENV='1', EXPO_OFFLINE='1',
               EXPO_NO_TELEMETRY='1',
               REACT_NATIVE_PACKAGER_HOSTNAME=cfg['host'])
    if cfg.get('public_url'):
        env['EXPO_PACKAGER_PROXY_URL'] = cfg['public_url']
    print('mobile source:', git(root, 'rev-parse', 'HEAD'), flush=True)
    os.chdir(root / 'apps/mobile')
    binaries = root / 'apps/mobile/node_modules/.bin'
    # Capture the dotenv environment in memory, then replace this process with
    # Expo so launchd owns Metro itself instead of a separate dotenv parent.
    loaded = subprocess.run([str(binaries / 'dotenv'), '-e', cfg['env_file'], '--',
                             'node', '-e', 'process.stdout.write(JSON.stringify(process.env))'],
                            env=env, check=True, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    env = json.loads(loaded.stdout)
    command = str(binaries / 'expo')
    os.execvpe(command, [command, 'start', '--dev-client', '--host', 'lan',
                         '--max-workers', '2', '--port', str(cfg['port'])], env)


def init(args):
    with locked():
        if CONFIG.exists():
            raise RuntimeError('Config already exists; refusing to replace it')
        source = Path(__file__).resolve().parents[2]
        checkout = Path(args.checkout).expanduser().absolute()
        env_file = Path(args.env_file).expanduser().resolve(strict=True)
        if args.host in ['localhost', '127.0.0.1', '0.0.0.0']:
            raise RuntimeError('Use an address reachable by the phone')
        run('git', 'worktree', 'add', '-b', 'dev/mobile', checkout, 'HEAD', cwd=source)
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(dict(checkout=str(checkout), env_file=str(env_file),
                                         host=args.host, port=args.port,
                                         public_url=args.public_url), indent=2) + '\n')
        CONFIG.chmod(0o600)
        event(checkout, 'init', git(checkout, 'rev-parse', 'HEAD'))
        print('Install dependencies in', checkout, 'then run launchd_script/install_and_start_service.sh there')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('init')
    p.add_argument('--checkout', default=str(Path.home() / 'multica-mobile-dev'))
    p.add_argument('--env-file', required=True)
    p.add_argument('--host', required=True)
    p.add_argument('--port', type=int, default=8081)
    p.add_argument('--public-url', help='Persistent HTTPS proxy URL advertised to the phone')
    for name in ['deliver', 'rollback']:
        sub.add_parser(name).add_argument('commits', nargs='+')
    for name in ['serve', 'status', 'restart']:
        sub.add_parser(name)
    args = parser.parse_args()
    if args.command == 'init':
        init(args)
    elif args.command in ['deliver', 'rollback']:
        update(args)
    elif args.command == 'serve':
        serve()
    elif args.command == 'restart':
        service('restart')
    else:
        cfg = config()
        print('checkout:', cfg['checkout'])
        print('commit:', git(cfg['checkout'], 'rev-parse', 'HEAD'))
        url = cfg.get('public_url') or f'http://{cfg["host"]}:{cfg["port"]}'
        print('Metro:', url)
        print('Open:', 'exp+multica-mobile://expo-development-client/?url=' + quote(url, safe=''))
        service('status', check=False)
        run('curl', '--noproxy', '*', '--fail', '--max-time', '5', f'http://127.0.0.1:{cfg["port"]}/status')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
