const { writeFileSync } = require("node:fs");
const { join } = require("node:path");
const { execFileSync } = require("node:child_process");
const applyMacUpdateFloor = require("./mac-update-floor.cjs");

module.exports = function prepareClientRuntime(context) {
  applyMacUpdateFloor(context);
  const root = context.packager.projectDir;
  const apiUrl = process.env.VITE_BRANCH_API_URL || "";
  const appUrl = process.env.VITE_BRANCH_APP_URL || "";
  const runtime = {
    apiUrl,
    appUrl,
    buildCommit: execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8" }).trim(),
  };
  writeFileSync(join(root, "client-runtime.json"), JSON.stringify(runtime));
};
