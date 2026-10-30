#!/usr/bin/env python3
"""Multica facts for the shared client contract; never infer clone provenance."""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit, unquote
import uuid
import zipfile

import requests


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
SOURCE_ID = "docker:multica-postgres-1/multica"


def run(argv, *, env=None, timeout=120):
    result = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise ValueError(f"{Path(argv[0]).name} failed ({result.returncode}); inspect its local service logs")
    return result.stdout.strip()


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat()


def env_values(path=None):
    values = {}
    for line in (path or ROOT / ".env.worktree").read_text().splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, raw = line.split("=", 1)
        parts = shlex.split(raw)
        values[key] = parts[0] if len(parts) == 1 else raw
    return values


def identity():
    ident = read_json(ROOT / ".dev-platform-workspace.json")
    if Path(ident["real_root"]).resolve() != ROOT or Path(ident["main_root"]).resolve() == ROOT:
        raise ValueError("Not the registered task worktree")
    return ident


def hub(platform="ios"):
    return (os.environ.get("MULTICA_MAC_HUB" if platform == "mac" else "MULTICA_IOS_HUB")
            or os.environ.get("MULTICA_IOS_HUB") or os.environ.get("MULTICA_MAC_HUB")
            or str(Path.home() / "dev-platform/services/install-hub/hub.py"))


def public_url():
    return run([sys.executable, hub(), "url", str(identity()["port"])])


def shared_tool():
    path = Path(os.environ.get("MULTICA_CLIENT_TOOL", str(Path.home() / "dev-platform/tools/client/contract.py")))
    if not path.is_file():
        raise ValueError("Shared client contract is not installed; set MULTICA_CLIENT_TOOL to the reviewed platform worktree")
    spec = importlib.util.spec_from_file_location("client_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ports():
    contract = shared_tool().resolve(ROOT)
    return {"backend": contract["services"]["backend"]["port"], "testbench": contract["worktree_port"],
            "public_url": contract["public_url"], "fingerprint": contract["fingerprint"]}


def guarded_env():
    ident, env = identity(), env_values()
    url = urlsplit(env["DATABASE_URL"])
    if (url.scheme not in ("postgres", "postgresql") or url.hostname not in ("localhost", "127.0.0.1")
            or url.port != int(ident["port"]) + 20000 or url.port != int(env["POSTGRES_PORT"])
            or unquote(url.path.lstrip("/")) != env["POSTGRES_DB"] or env["POSTGRES_DB"] == "multica"
            or env.get("COMPOSE_PROJECT_NAME") != "multica-dp-" + ident.get("slug", "")
            or (ROOT / ".env").exists()):
        raise ValueError("Refusing a database connection without worktree isolation")
    return env, url


def database_query(sql, *, read_only=True):
    env, url = guarded_env()
    process_env = {**os.environ, "COMPOSE_PROJECT_NAME": env["COMPOSE_PROJECT_NAME"],
                   "POSTGRES_PORT": env["POSTGRES_PORT"], "TZ": "Asia/Shanghai"}
    # The container supplies only psql. Connect through the exact host-published port,
    # not its own Unix socket or internal 5432, so this is the backend's database.
    argv = ["docker", "compose", "exec", "-T", "-e", "PGCONNECT_TIMEOUT=5",
            "-e", "PGOPTIONS=-c default_transaction_read_only=" + ("on" if read_only else "off"),
            "-e", "PGPASSWORD=" + unquote(url.password or ""), "postgres", "psql",
            "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-h", "host.docker.internal",
            "-p", str(url.port), "-U", unquote(url.username or "multica"),
            "-d", unquote(url.path.lstrip("/")), "-c", sql]
    return run(argv, env=process_env, timeout=30)


def probe():
    env, _ = guarded_env()
    with requests.Session() as session:
        session.trust_env = False
        response = session.get(f"http://127.0.0.1:{int(env['PORT'])}/health", timeout=5)
        response.raise_for_status()
        health = response.json()
    pid = health.get("pid")
    if not isinstance(pid, int) or pid <= 1:
        raise ValueError("Backend health has no valid PID")
    command = run(["ps", "eww", "-p", str(pid), "-o", "command="], timeout=10)
    matches = re.findall(r"(?:^|\s)DATABASE_URL=(\S+)", command)
    if matches != [env["DATABASE_URL"]]:
        raise ValueError("Backend effective DATABASE_URL cannot be proven equal to this worktree configuration")
    sql = """BEGIN READ ONLY;
SELECT json_build_object(
  'read_only', current_setting('transaction_read_only') = 'on',
  'database_id', current_database(),
  'clone_id', payload->>'clone_id',
  'source_id', payload->>'source_id',
  'snapshot_sha256', payload->'snapshot'->>'sha256')
FROM dev_platform.clone_identity;
COMMIT;"""
    output = database_query(sql)
    records = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    if len(records) != 1:
        raise ValueError("Existing clone has no unique verified provenance marker; do not label it as the current snapshot")
    return records[0]


def snapshot_record():
    common = Path(run(["git", "rev-parse", "--git-common-dir"]))
    main = (ROOT / common).resolve().parent
    dump = main / "data/golden/multica.dump"
    record = {"source_id": SOURCE_ID, "snapshot": {"sha256": sha256(dump), "captured_at": now()}}
    write_json(dump.with_suffix(".json"), record)


def clone_prepare():
    env, _ = guarded_env()
    record = read_json(ROOT / "data/golden/multica.json")
    if record.get("source_id") != SOURCE_ID:
        raise ValueError("Snapshot source is unknown")
    target = DATA / "client-restore.dump"
    shutil.copyfile(ROOT / "data/golden/multica.dump", target)
    if sha256(target) != record["snapshot"]["sha256"]:
        target.unlink()
        raise ValueError("Snapshot changed while preparing restore")
    manifest = {"mode": "isolated-copy", "source_id": SOURCE_ID, "clone_id": str(uuid.uuid4()),
                "database_id": env["POSTGRES_DB"], "snapshot": record["snapshot"]}
    write_json(DATA / "client-clone-pending.json", manifest)


def clone_record():
    manifest = read_json(DATA / "client-clone-pending.json")
    env, _ = guarded_env()
    if manifest["database_id"] != env["POSTGRES_DB"] or sha256(DATA / "client-restore.dump") != manifest["snapshot"]["sha256"]:
        raise ValueError("Restored clone no longer matches the prepared snapshot")
    literal = json.dumps(manifest).replace("'", "''")
    database_query("BEGIN; CREATE SCHEMA IF NOT EXISTS dev_platform; "
                   "CREATE TABLE IF NOT EXISTS dev_platform.clone_identity (payload jsonb NOT NULL); "
                   "TRUNCATE dev_platform.clone_identity; "
                   f"INSERT INTO dev_platform.clone_identity VALUES ('{literal}'::jsonb); COMMIT;", read_only=False)
    write_json(DATA / "client-data.json", manifest)
    (DATA / "client-clone-pending.json").unlink()
    (DATA / "client-restore.dump").unlink()


def artifact_path(platform):
    slug = identity()["slug"]
    slot = f"multica-{slug}" if platform == "ios" else f"multica-mac-{slug}"
    return run([sys.executable, hub(platform), "artifact-path", slot])


def inspect_ios(path):
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist()
                 if re.fullmatch(r"Payload/[^/]+\.app/(?:Frameworks/[^/]+/)?EXConstants\.bundle/app\.config", name)]
        if len(names) != 1:
            raise ValueError("IPA has no unique embedded Expo Constants configuration")
        runtime = json.loads(archive.read(names[0]))["extra"]["clientRuntime"]
    return {"api_url": runtime["apiUrl"], "build_commit": runtime["buildCommit"],
            "evidence": names[0] + ":extra.clientRuntime"}


def inspect_mac(path):
    mount = Path(tempfile.mkdtemp(prefix="multica-artifact-"))
    mounted = False
    try:
        run(["hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", str(mount), str(path)], timeout=40)
        mounted = True
        archives = list(mount.glob("*.app/Contents/Resources/app.asar"))
        modules = list((ROOT / "node_modules/.pnpm").glob("@electron+asar@*/node_modules/@electron/asar/lib/asar.js"))
        if len(archives) != 1 or not modules:
            raise ValueError("Cannot locate the packaged app or the installed ASAR reader")
        source = "const a=require(process.argv[1]);process.stdout.write(a.extractFile(process.argv[2],'client-runtime.json').toString())"
        runtime = json.loads(run(["node", "-e", source, str(modules[0]), str(archives[0])], timeout=30))
        return {"api_url": runtime["apiUrl"], "build_commit": runtime["buildCommit"],
                "evidence": "Contents/Resources/app.asar:client-runtime.json"}
    finally:
        if mounted:
            run(["hdiutil", "detach", str(mount)], timeout=15)
        shutil.rmtree(mount, ignore_errors=True)


def inspect_artifact(path, platform):
    path = Path(path).resolve()
    result = inspect_ios(path) if platform == "ios" else inspect_mac(path)
    return {**result, "artifact_sha256": sha256(path)}


def metro_owner(port):
    pids = listeners(port)
    if not pids:
        raise ValueError("The assigned Metro port has no listener")
    for pid in pids:
        output = run(["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"], timeout=10)
        paths = [Path(line[1:]).resolve() for line in output.splitlines() if line.startswith("n/")]
        if not paths or any(path != ROOT and ROOT not in path.parents for path in paths):
            raise ValueError("Metro listener belongs to another worktree")
    return pids


def probe_client(app_path, metro_url):
    """Read the same iOS manifest requested by Expo's development launcher."""
    ident, env = identity(), env_values()
    address = urlsplit(metro_url)
    metro_port = int(env["METRO_PORT"])
    if (address.scheme != "http" or address.hostname not in ("localhost", "127.0.0.1", "::1")
            or address.port != metro_port or metro_port != int(ident["port"]) + 1000
            or address.username or address.password or address.query or address.fragment
            or address.path not in ("", "/")):
        raise ValueError("Client probe must use the assigned local Metro URL")
    app_path = Path(app_path).resolve()
    with (app_path / "Info.plist").open("rb") as stream:
        bundle_id = plistlib.load(stream).get("CFBundleIdentifier")
    if not bundle_id or bundle_id != read_json(ROOT / "dev-platform.json")["ios_debug"]["bundle_id"]:
        raise ValueError("Installed app does not match the configured iOS bundle")
    pids = metro_owner(metro_port)
    # Match EXDevLauncherManifestParser; Constants.expoConfig reads extra.expoClient.
    with requests.Session() as session:
        session.trust_env = False
        response = session.get(metro_url, headers={"expo-platform": "ios",
            "Accept": "application/expo+json,application/json"}, timeout=10, allow_redirects=False)
        if (response.status_code != 200 or response.headers.get("expo-protocol-version") != "0"
                or response.headers.get("content-type", "").split(";", 1)[0].strip()
                not in ("application/expo+json", "application/json")):
            raise ValueError("Metro did not return an iOS Expo manifest")
        body = response.content
    if len(body) > 2 * 1024 * 1024:
        raise ValueError("Metro manifest exceeds the probe size limit")
    try:
        manifest = json.loads(body)
        config = manifest["extra"]["expoClient"]
        runtime = config["extra"]["clientRuntime"]
        api_url, build_commit = runtime["apiUrl"], runtime["buildCommit"]
        project_root = Path(manifest["extra"]["expoGo"]["developer"]["projectRoot"]).resolve()
        manifest_bundle_id = config["ios"]["bundleIdentifier"]
        bundle_url = urlsplit(manifest["launchAsset"]["url"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Metro manifest is missing the consumed client runtime configuration") from exc
    if project_root != (ROOT / "apps/mobile").resolve() or manifest_bundle_id != bundle_id:
        raise ValueError("Metro manifest belongs to another project or installed app")
    if (bundle_url.scheme, bundle_url.hostname, bundle_url.port) != (address.scheme, address.hostname, address.port):
        raise ValueError("Metro manifest loads its JavaScript from another server")
    if api_url != f"http://127.0.0.1:{int(env['PORT'])}":
        raise ValueError("Metro client runtime points to another backend")
    if not isinstance(build_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", build_commit):
        raise ValueError("Metro client runtime has no valid source commit")
    if metro_owner(metro_port) != pids:
        raise ValueError("Metro listener changed during the runtime probe")
    return {"api_url": api_url, "build_commit": build_commit, "bundle_id": bundle_id,
            "manifest_sha256": hashlib.sha256(body).hexdigest(), "metro_pids": sorted(pids),
            "evidence": metro_url + "#extra.expoClient.extra.clientRuntime"}


def listeners(port):
    result = subprocess.run(["lsof", "-nP", f"-iTCP:{int(port)}", "-sTCP:LISTEN", "-Fp"],
                            capture_output=True, text=True, timeout=10)
    if result.returncode not in (0, 1):
        raise ValueError("Cannot prove whether a service port is free")
    return {line[1:] for line in result.stdout.splitlines() if line.startswith("p") and line[1:].isdigit()}


def registry_records():
    home = Path(os.environ.get("MULTICA_DEV_HOME", str(Path.home() / ".multica/dev")))
    return [(path, env_values(path)) for path in (home / "envs").glob("*/manifest.env")
            if Path(env_values(path).get("DIR", "/missing")).resolve() == ROOT]


def repair_registry(apply=False):
    env, _ = guarded_env()
    records = registry_records()
    candidates = []
    for path, values in records:
        if values.get("BACKEND_PORT") == env["PORT"] and values.get("FRONTEND_PORT") == env["FRONTEND_PORT"]:
            continue
        if values.get("DB_NAME") != env["POSTGRES_DB"]:
            raise ValueError("Stale registry refers to another database; manual ownership review is required")
        for key in ("BACKEND_PORT", "FRONTEND_PORT", "DESKTOP_RENDERER_PORT"):
            if values.get(key) and listeners(values[key]):
                raise ValueError("Stale registry still has a listening service; it cannot be quarantined")
        for pidfile in path.parent.glob("*.pid"):
            raw = pidfile.read_text().strip()
            if raw.isdigit():
                try:
                    os.kill(int(raw), 0)
                except ProcessLookupError:
                    continue
                raise ValueError("Stale registry still has a live process; it cannot be quarantined")
        candidates.append((path, sha256(path)))
    moved = []
    if apply:
        batch = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        for path, digest in candidates:
            values = env_values(path)
            if sha256(path) != digest or any(listeners(values[key]) for key in ("BACKEND_PORT", "FRONTEND_PORT") if values.get(key)):
                raise ValueError("Registry changed while preparing quarantine")
            target = path.parents[2] / "quarantine" / batch / path.parent.name / "manifest.env"
            target.parent.mkdir(parents=True, mode=0o700)
            path.rename(target)
            moved.append({"from": str(path), "to": str(target), "sha256": digest})
    return {"dry_run": not apply, "candidates": [str(path) for path, _ in candidates], "moved": moved,
            "database_action": "none"}


def recovery_check():
    env, url = guarded_env()
    for key in ("PORT", "FRONTEND_PORT"):
        for pid in listeners(env[key]):
            out = run(["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"], timeout=10)
            paths = [Path(line[1:]).resolve() for line in out.splitlines() if line.startswith("n/")]
            if not paths or not any(path == ROOT or ROOT in path.parents for path in paths):
                raise ValueError("Recovery would touch a service owned by another worktree")
    context = {**os.environ, "COMPOSE_PROJECT_NAME": env["COMPOSE_PROJECT_NAME"], "POSTGRES_PORT": env["POSTGRES_PORT"]}
    container = run(["docker", "compose", "ps", "-q", "postgres"], env=context).strip()
    if not container or "\n" in container:
        raise ValueError("Recovery requires this worktree's existing PostgreSQL container")
    item = json.loads(run(["docker", "inspect", container]))[0]
    labels = item.get("Config", {}).get("Labels", {})
    bindings = item.get("NetworkSettings", {}).get("Ports", {}).get("5432/tcp") or []
    working_dir = Path(labels.get("com.docker.compose.project.working_dir", "/missing")).resolve()
    config_files = [Path(path).resolve() for path in labels.get("com.docker.compose.project.config_files", "").split(",") if path]
    if (labels.get("com.docker.compose.project") != env["COMPOSE_PROJECT_NAME"]
            or labels.get("com.docker.compose.service") != "postgres"
            or working_dir != ROOT or not config_files
            or any(ROOT not in path.parents for path in config_files)
            or not item.get("State", {}).get("Running")
            or not any(binding.get("HostPort") == str(url.port) for binding in bindings)):
        raise ValueError("Database container ownership or published port does not match the worktree")
    actual = database_query("SELECT current_database()")
    if actual != env["POSTGRES_DB"]:
        raise ValueError("The database being preserved is not the configured test database")
    return env


def replace_env(path, changes):
    original = path.read_text().splitlines()
    found = set()
    updated = []
    for line in original:
        key = line.split("=", 1)[0]
        if key in changes:
            line = key + "=" + shlex.quote(changes[key])
            found.add(key)
        updated.append(line)
    updated.extend(key + "=" + shlex.quote(value) for key, value in changes.items() if key not in found)
    path.write_text("\n".join(updated) + "\n")


def renew_copy(no_start=False):
    env = recovery_check()
    repair_registry()
    stamp = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y%m%d-%H%M%S")
    suffix = uuid.uuid4().hex[:6]
    backup = DATA / "client-recovery" / f"{stamp}-{suffix}"
    backup.mkdir(parents=True, mode=0o700)
    shutil.copy2(ROOT / ".env.worktree", backup / "env.worktree")
    records = registry_records()
    for path, _ in records:
        shutil.copy2(path, backup / (path.parent.name + ".manifest.env"))
    new_name = env["POSTGRES_DB"][:32] + "_client_" + stamp.replace("-", "") + "_" + suffix
    plan = {"old_database": env["POSTGRES_DB"], "new_database": new_name,
            "old_database_preserved": True, "started_at": now(), "stage": "backed-up"}
    write_json(backup / "recovery.json", plan)
    ident = identity()
    context = {**os.environ, "DP_REAL_ROOT": str(ROOT), "DP_PORT": str(ident["port"]), "DP_SLUG": ident["slug"]}
    run(["bash", "scripts/dev/dp_env.sh", "down"], env=context)
    if listeners(env["PORT"]) or listeners(env["FRONTEND_PORT"]):
        raise ValueError("Owned services did not stop; the database configuration was preserved")
    repair_registry(apply=True)
    run(["bash", "scripts/dev/dp_env.sh", "refresh-snapshot"], env=context)
    new_url = urlsplit(env["DATABASE_URL"])._replace(path="/" + new_name).geturl()
    replace_env(ROOT / ".env.worktree", {"POSTGRES_DB": new_name, "DATABASE_URL": new_url,
                                         "METRO_PORT": str(int(ident["port"]) + 1000)})
    for path, values in registry_records():
        if values.get("DB_NAME") != env["POSTGRES_DB"]:
            raise ValueError("Registry database changed during recovery; old database was preserved")
        replace_env(path, {"DB_NAME": new_name, "DATABASE_URL": new_url})
    for name in ("client-data.json", "client-clone-pending.json", "client-restore.dump"):
        source = DATA / name
        if source.exists():
            source.rename(backup / name)
    plan["stage"] = "new-database-selected"
    write_json(backup / "recovery.json", plan)
    run(["bash", "scripts/dev/dp_env.sh", "prepare-copy"], env=context)
    plan["stage"] = "new-clone-verified"
    write_json(backup / "recovery.json", plan)
    if not no_start:
        run(["bash", "scripts/dev/dp_env.sh", "up"], env=context)
        plan["stage"] = "services-started"
        write_json(backup / "recovery.json", plan)
    return {**plan, "recovery_record": str(backup / "recovery.json")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("ports", "public-url", "probe", "snapshot-record", "clone-prepare",
                                           "clone-record", "artifact-path", "inspect", "verify", "repair-registry",
                                           "renew-copy", "recovery-check", "probe-client"))
    parser.add_argument("--platform", choices=("ios", "mac"), default="ios")
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--app-path", type=Path)
    parser.add_argument("--metro-url")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-start", action="store_true")
    args = parser.parse_args()
    if args.action == "public-url":
        print(public_url())
    elif args.action == "artifact-path":
        print(artifact_path(args.platform))
    elif args.action == "snapshot-record":
        snapshot_record()
    elif args.action == "clone-prepare":
        clone_prepare()
    elif args.action == "clone-record":
        clone_record()
    else:
        if args.action == "renew-copy":
            result = renew_copy(args.no_start)
        elif args.action == "repair-registry":
            result = repair_registry(args.apply)
        elif args.action == "recovery-check":
            recovery_check()
            result = {"ownership": "verified"}
        elif args.action == "probe":
            result = probe()
        elif args.action == "probe-client":
            if not args.app_path or not args.metro_url:
                parser.error("probe-client requires --app-path and --metro-url")
            result = probe_client(args.app_path, args.metro_url)
        elif args.action == "inspect":
            if not args.artifact:
                parser.error("inspect requires --artifact")
            result = inspect_artifact(args.artifact, args.platform)
        elif args.action == "verify":
            result = shared_tool().verify(ROOT, include_public=False)
        else:
            result = ports()
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, RuntimeError, requests.RequestException, subprocess.TimeoutExpired) as exc:
        print(f"client-adapter: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
