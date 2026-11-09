// Task-owned HTTP gateway: pin one real Expo manifest for an immutable test run.
// Expo generates a random manifest ID on each request; all observers and the
// phone must receive the same bytes for the shared acceptance contract.
const http = require("node:http");
const net = require("node:net");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync, spawn } = require("node:child_process");

const root = fs.realpathSync(path.resolve(__dirname, "../.."));
const port = Number(process.argv[2]);
const upstream = port + 1000;
const git = (...args) => execFileSync("git", args, { cwd: root, encoding: "utf8" }).trim();
const sha = git("rev-parse", "HEAD");
const registration = JSON.parse(fs.readFileSync(path.join(root, ".dev-platform-workspace.json")));
const cfg = JSON.parse(fs.readFileSync(path.join(root, "dev-platform.json"))).ios_debug;
const digest = (value) => crypto.createHash("sha256").update(value).digest("hex");
const nativeHash = () => digest(JSON.stringify(Object.fromEntries([...cfg.native_inputs].sort().map(
  (name) => [name, digest(fs.readFileSync(path.join(root, name)))],
))));
const native = nativeHash();
let manifest;
let loading;
let session;

function unchanged() {
  if (git("rev-parse", "HEAD") !== sha || git("status", "--porcelain", "--untracked-files=no") || nativeHash() !== native) {
    throw new Error("Task source changed; restart the managed Metro gateway");
  }
}

async function pinnedManifest() {
  unchanged();
  if (!manifest) {
    loading ??= (async () => {
      const response = await fetch(`http://127.0.0.1:${upstream}/`, { headers: {
        "expo-platform": "ios", Accept: "application/expo+json,application/json",
      } });
      if (!response.ok || response.headers.get("expo-protocol-version") !== "0") throw new Error("Expo manifest unavailable");
      const bytes = Buffer.from(await response.arrayBuffer());
      const value = JSON.parse(bytes);
      const actual = value.extra.expoClient.extra.clientRuntime;
      if (actual.buildCommit !== sha || actual.apiUrl !== process.env.EXPO_PUBLIC_API_URL
          || fs.realpathSync(value.extra.expoGo.developer.projectRoot) !== path.join(root, "apps/mobile")) {
        throw new Error("Actual Expo manifest belongs to another source/backend");
      }
      const bundle = new URL(value.launchAsset.url);
      if (bundle.origin !== new URL(process.env.EXPO_PACKAGER_PROXY_URL).origin) throw new Error("Foreign bundle route");
      manifest = { bytes, value, hash: digest(bytes), bundle };
    })();
    try { await loading; } catch (error) { loading = undefined; throw error; }
  }
  return manifest;
}

function json(res, code, value) {
  res.writeHead(code, { "content-type": "application/json", "cache-control": "no-store" });
  res.end(JSON.stringify(value));
}

async function body(req) {
  let bytes = "";
  for await (const chunk of req) {
    bytes += chunk;
    if (bytes.length > 8192) throw new Error("Oversize session evidence");
  }
  return JSON.parse(bytes);
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, "http://gateway.invalid");
    if (url.pathname.startsWith("/_task/")) {
      const m = await pinnedManifest();
      if (url.pathname === "/_task/identity" && req.method === "GET") {
        return json(res, 200, { root, sha, issue: registration.issue, port,
          device_url: process.env.EXPO_PACKAGER_PROXY_URL,
          manifest_sha256: m.hash, native_input_sha256: native });
      }
      if (url.pathname === "/_task/session" && req.method === "POST") {
        const value = await body(req);
        if (!/^[a-f0-9]{32}$/.test(value.receipt_id) || !/^[A-Fa-f0-9-]+$/.test(value.device)) throw new Error("Invalid installation session");
        session = { ...value, nonce: crypto.randomBytes(32).toString("hex"), armed: Date.now(), bundleServed: false };
        return json(res, 200, { armed: true });
      }
      if (url.pathname === "/_task/challenge" && req.method === "GET") {
        return json(res, 200, session ? { nonce: session.nonce } : {});
      }
      if (url.pathname === "/_task/loaded" && req.method === "POST") {
        const value = await body(req);
        if (!session || !session.bundleServed || value.nonce !== session.nonce
            || value.manifest_id !== m.value.id || value.build_commit !== sha
            || value.api_url !== process.env.EXPO_PUBLIC_API_URL || value.script_url !== m.bundle.href) {
          throw new Error("Runtime JS does not match this installation session");
        }
        session.observed = { receipt_id: session.receipt_id, device: session.device,
          manifest_sha256: m.hash, script_url: value.script_url, observed_at: Date.now() };
        return json(res, 200, { recorded: true });
      }
      if (url.pathname === "/_task/loaded" && req.method === "GET") return json(res, 200, session?.observed ?? {});
      return json(res, 404, {});
    }
    if (url.pathname === "/" && (req.headers["expo-platform"] === "ios" || url.searchParams.get("platform") === "ios")) {
      const m = await pinnedManifest();
      res.writeHead(200, { "content-type": "application/expo+json", "expo-protocol-version": "0", "cache-control": "no-store" });
      return res.end(m.bytes);
    }
    const servingSession = session;
    const proxy = http.request({ hostname: "127.0.0.1", port: upstream, path: req.url,
      method: req.method, headers: req.headers }, (incoming) => {
      if (session && manifest && req.method === "GET" && req.url === manifest.bundle.pathname + manifest.bundle.search
          && incoming.statusCode === 200) {
        incoming.on("end", () => { if (session === servingSession) session.bundleServed = true; });
      }
      res.writeHead(incoming.statusCode, incoming.headers);
      incoming.pipe(res);
    });
    proxy.on("error", () => { if (!res.headersSent) json(res, 503, { error: "Metro unavailable" }); else res.destroy(); });
    req.pipe(proxy);
  } catch (error) { json(res, 503, { error: error.message }); }
});

server.on("upgrade", (req, socket, head) => {
  const target = net.connect(upstream, "127.0.0.1", () => {
    target.write(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n`);
    for (let i = 0; i < req.rawHeaders.length; i += 2) target.write(`${req.rawHeaders[i]}: ${req.rawHeaders[i + 1]}\r\n`);
    target.write("\r\n");
    target.write(head);
    socket.pipe(target).pipe(socket);
  });
  target.on("error", () => socket.destroy());
  socket.on("error", () => target.destroy());
});

// Refuse an occupied upstream port before Expo can prompt or select another one.
const guard = net.createServer();
guard.on("error", () => process.exit(1));
guard.listen(upstream, "0.0.0.0", () => guard.close(() => {
  const child = spawn("pnpm", ["-C", "apps/mobile", "exec", "expo", "start", "--dev-client", "--port", String(upstream), "--host", "lan"],
    { cwd: root, env: process.env, stdio: "inherit" });
  child.on("exit", (code) => { server.close(); process.exit(code ?? 1); });
  server.listen(port, "0.0.0.0");
}));
