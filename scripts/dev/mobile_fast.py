#!/usr/bin/env python3
"""Manage the persistent mobile development checkout, never the main branch."""
import argparse
import fcntl
import json
import os
import plistlib
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

CONFIG = Path.home() / '.config/multica-mobile/config.json'
STATE = Path.home() / '.local/state/multica-mobile'
LABEL = 'com.multica.mobile-metro'
REPO = 'comoysha/multica'
BRANCH = 'dev/mobile'
DEV_CLIENT_SCHEME = 'exp+multica-mobile'
NATIVE = ['apps/mobile/package.json', 'apps/mobile/app.config.ts', 'apps/mobile/app.config.js',
          'apps/mobile/app.json', 'apps/mobile/plugins', 'apps/mobile/patches', 'apps/mobile/ios',
          'apps/mobile/android', 'apps/mobile/assets/icon.png', 'apps/mobile/scripts/ios-run.sh',
          'pnpm-lock.yaml', 'pnpm-workspace.yaml', 'package.json']


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
    if git(root, 'branch', '--show-current') != BRANCH:
        raise RuntimeError(f'Refusing to modify a branch other than {BRANCH}')
    if git(root, 'status', '--porcelain', '--untracked-files=normal'):
        raise RuntimeError('Development checkout has local changes; preserve and resolve them first')


def event(root, action, before, **details):
    record = dict(time=datetime.now(timezone(timedelta(hours=8))).isoformat(), action=action,
                  before=before, after=git(root, 'rev-parse', 'HEAD'), **details)
    with (STATE / 'history.jsonl').open('a') as out:
        out.write(json.dumps(record) + '\n')
    return record


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
    raise RuntimeError('Could not start the Metro LaunchAgent')


def validate_mobile_env(env, public_url=None):
    required = ['EXPO_PUBLIC_API_URL', 'EXPO_PUBLIC_WEB_URL', 'EXPO_PUBLIC_LOCAL_AUTH_TOKEN']
    missing = [key for key in required if not env.get(key, '').strip()]
    if missing:
        raise RuntimeError('Private mobile environment is missing: ' + ', '.join(missing))
    if not env['EXPO_PUBLIC_LOCAL_AUTH_TOKEN'].startswith('mul_'):
        raise RuntimeError('Private mobile environment has an invalid local-auth token format')
    for key in required[:2]:
        url = urlsplit(env[key])
        if url.scheme not in ['http', 'https'] or not url.hostname or url.hostname in ['localhost', '127.0.0.1', '::1']:
            raise RuntimeError(key + ' must be a phone-reachable HTTP(S) URL')
        if public_url and url.hostname != urlsplit(public_url).hostname:
            raise RuntimeError(key + ' must point to the same personal server as the Metro entry')


def verify_client(app):
    app = Path(app).expanduser().resolve(strict=True)
    if app.suffix != '.app' or not app.is_dir():
        raise RuntimeError('Pass an extracted iOS .app directory')
    try:
        with (app / 'Info.plist').open('rb') as handle:
            info = plistlib.load(handle)
    except (plistlib.InvalidFileException, ValueError) as error:
        raise RuntimeError('Client Info.plist is invalid') from error
    executable = info.get('CFBundleExecutable')
    if not isinstance(executable, str) or not executable or Path(executable).name != executable:
        raise RuntimeError('Client Info.plist has no valid CFBundleExecutable')
    # Xcode Debug builds may move the executable code into a sibling dylib.
    binaries = [app / executable, app / (executable + '.debug.dylib')]
    if not binaries[0].is_file():
        raise RuntimeError('Client executable is missing')
    if not any(b'EXDevLauncherController' in binary.read_bytes()
               for binary in binaries if binary.is_file()):
        raise RuntimeError('Client has no EXDevLauncher; a Staging Release app cannot load Metro. Build a Debug development client')
    schemes = {scheme for entry in info.get('CFBundleURLTypes', [])
               for scheme in entry.get('CFBundleURLSchemes', [])}
    if DEV_CLIENT_SCHEME not in schemes:
        raise RuntimeError('Client is missing the Metro URL scheme: ' + DEV_CLIENT_SCHEME)
    return dict(bundle_id=info.get('CFBundleIdentifier'),
                build=info.get('CFBundleVersion'), scheme=DEV_CLIENT_SCHEME,
                development_client=True)


def merged_pr(number):
    result = run('gh', 'pr', 'view', number, '--repo', REPO,
                 '--json', 'state,baseRefName,mergeCommit,url', capture=True)
    pr = json.loads(result.stdout)
    if pr['state'] != 'MERGED' or pr['baseRefName'] != BRANCH or not pr['mergeCommit']:
        raise RuntimeError(f'PR #{number} must be merged into {BRANCH} before delivery')
    return pr


def is_ancestor(root, older, newer):
    result = run('git', 'merge-base', '--is-ancestor', older, newer, cwd=root, check=False)
    if result.returncode > 1:
        result.check_returncode()
    return result.returncode == 0


def wait_for(predicate, timeout, message):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise RuntimeError(message)
        time.sleep(0.5)


def metro_port_closed(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            return False
    except ConnectionRefusedError:
        return True


def metro_status(port):
    return run('curl', '--noproxy', '*', '--fail', '--silent', '--max-time', '2',
               f'http://127.0.0.1:{port}/status', capture=True, check=False)


def start_metro(cfg, plist, sha):
    log = Path(cfg['checkout']) / f'data/logs/{LABEL}.stdout.log'
    offset = log.stat().st_size if log.exists() else 0
    start_service(plist)

    def ready():
        if not log.exists():
            return False
        with log.open('rb') as handle:
            handle.seek(offset)
            if f'mobile source: {sha}'.encode() not in handle.read().splitlines():
                return False
        status = metro_status(cfg['port'])
        return status.returncode == 0 and status.stdout.strip() == 'packager-status:running'

    wait_for(ready, 30, f'Metro did not become ready at {sha}')


def deliver(number):
    cfg = config()
    root = cfg['checkout']
    with locked():
        clean(root)
        before = git(root, 'rev-parse', 'HEAD')
        pr = merged_pr(number)
        sha = pr['mergeCommit']['oid']
        run('git', 'fetch', '--no-tags', '--no-recurse-submodules',
            f'https://github.com/{REPO}.git', f'refs/heads/{BRANCH}', cwd=root)
        commit = run('git', 'rev-parse', '--verify', '--quiet', f'{sha}^{{commit}}',
                     cwd=root, capture=True, check=False)
        if commit.returncode != 0 or not is_ancestor(root, sha, 'FETCH_HEAD'):
            raise RuntimeError(f'PR #{number} merge commit is absent from GitHub {BRANCH}')
        if is_ancestor(root, sha, before):
            print(f'PR #{number} is already included in the Metro checkout at {before}')
            return
        if not is_ancestor(root, before, sha):
            raise RuntimeError(f'Metro checkout has diverged from {BRANCH}; preserve it and investigate')
        native = git(root, 'diff', '--name-only', '--no-renames', before, sha, '--', *NATIVE)
        if native:
            raise RuntimeError('Native/dependency inputs changed; rebuild and verify a development client first:\n' + native)

        plist = Path.home() / f'Library/LaunchAgents/{LABEL}.plist'
        target = f'gui/{os.getuid()}/{LABEL}'
        if run('launchctl', 'print', target, capture=True, check=False).returncode != 0:
            raise RuntimeError('Start the Metro LaunchAgent before delivery')
        run('launchctl', 'bootout', target)
        try:
            wait_for(lambda: metro_port_closed(cfg['port']), 15, 'Previous Metro process did not stop')
            run('git', 'merge', '--ff-only', sha, cwd=root)
            start_metro(cfg, plist, sha)
            record = event(root, 'deliver', before, pr=pr['url'])
        except (OSError, RuntimeError, subprocess.CalledProcessError, KeyboardInterrupt) as error:
            try:
                if run('launchctl', 'print', target, capture=True, check=False).returncode == 0:
                    run('launchctl', 'bootout', target)
                if git(root, 'rev-parse', 'HEAD') != before:
                    run('git', 'reset', '--keep', before, cwd=root)
                wait_for(lambda: metro_port_closed(cfg['port']), 15, 'Metro process did not stop')
                start_metro(cfg, plist, before)
            except (OSError, RuntimeError, subprocess.CalledProcessError) as recovery_error:
                raise RuntimeError(
                    f'Delivery failed ({error}); could not restore Metro at {before}: {recovery_error}'
                ) from recovery_error
            raise RuntimeError(f'Delivery failed ({error}); Metro restored at {before}') from error
        print(json.dumps(record))


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
    validate_mobile_env(env, cfg.get('public_url'))
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
        run('git', 'worktree', 'add', '-b', BRANCH, checkout, 'HEAD', cwd=source)
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(dict(checkout=str(checkout), env_file=str(env_file),
                                         host=args.host, port=args.port,
                                         public_url=args.public_url), indent=2) + '\n')
        CONFIG.chmod(0o600)
        print(json.dumps(event(checkout, 'init', git(checkout, 'rev-parse', 'HEAD'))))
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
    sub.add_parser('deliver').add_argument('pr', type=int, help=f'GitHub PR number in {REPO}')
    for name in ['serve', 'status', 'restart']:
        sub.add_parser(name)
    sub.add_parser('verify-client').add_argument('app', help='Extracted iOS .app directory')
    args = parser.parse_args()
    if args.command == 'init':
        init(args)
    elif args.command == 'deliver':
        deliver(args.pr)
    elif args.command == 'serve':
        serve()
    elif args.command == 'restart':
        service('restart')
    elif args.command == 'verify-client':
        print(json.dumps(verify_client(args.app)))
    else:
        cfg = config()
        print('checkout:', cfg['checkout'])
        print('commit:', git(cfg['checkout'], 'rev-parse', 'HEAD'))
        url = cfg.get('public_url') or f'http://{cfg["host"]}:{cfg["port"]}'
        print('Metro:', url)
        print('Open:', DEV_CLIENT_SCHEME + '://expo-development-client/?url=' + quote(url, safe=''))
        service('status', check=False)
        run('curl', '--noproxy', '*', '--fail', '--max-time', '5', f'http://127.0.0.1:{cfg["port"]}/status')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
