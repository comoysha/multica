// The branch app must never contain the production credential used to mint its
// isolated testbench token, even if a future packaging rule includes a file.
const fs = require("node:fs");
const path = require("node:path");

async function containsSecret(file, secret) {
  let previous = Buffer.alloc(0);
  for await (const chunk of fs.createReadStream(file)) {
    const combined = Buffer.concat([previous, chunk]);
    if (combined.includes(secret)) return true;
    previous = combined.subarray(Math.max(0, combined.length - secret.length + 1));
  }
  return false;
}

async function checkDirectory(dir, secret) {
  for (const entry of await fs.promises.readdir(dir, { withFileTypes: true })) {
    const file = path.join(dir, entry.name);
    if (entry.isDirectory()) await checkDirectory(file, secret);
    else if (entry.isFile() && await containsSecret(file, secret)) {
      throw new Error("Branch package contains the production login credential");
    }
  }
}

async function guard(context) {
  if (!process.env.VITE_BRANCH_API_URL) return;
  if (context.electronPlatformName !== "darwin") {
    throw new Error("Branch credential guard requires a macOS package");
  }
  const envPath = path.join(context.packager.projectDir, ".env.local");
  const env = await fs.promises.readFile(envPath, "utf8");
  const match = env.match(/^\s*(?:export\s+)?VITE_LOCAL_AUTH_TOKEN\s*=\s*['"]?(mul_[A-Za-z0-9_-]+)['"]?\s*$/m);
  if (!match || process.env.VITE_LOCAL_AUTH_TOKEN === match[1]) {
    throw new Error("Cannot verify isolated branch credentials");
  }
  const resources = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`, "Contents", "Resources");
  await checkDirectory(resources, Buffer.from(match[1]));
}

module.exports = guard;
module.exports.containsSecret = containsSecret;
