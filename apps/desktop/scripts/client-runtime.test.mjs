// @vitest-environment node
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it, vi } from "vitest";

const prepare = createRequire(import.meta.url)("./client-runtime.cjs");
let root;
afterEach(() => {
  vi.unstubAllEnvs();
  if (root) rmSync(root, { recursive: true, force: true });
});

it("packages the endpoint consumed by main and preserves the macOS update floor", () => {
  root = mkdtempSync(join(tmpdir(), "multica-client-runtime-"));
  execFileSync("git", ["init", "-q", "-b", "main"], { cwd: root });
  execFileSync("git", ["-c", "user.name=Fixture", "-c", "user.email=fixture@localhost", "commit", "--allow-empty", "-qm", "fixture"], { cwd: root });
  const sha = execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8" }).trim();
  vi.stubEnv("VITE_BRANCH_API_URL", "http://127.0.0.1:18939");
  vi.stubEnv("VITE_BRANCH_APP_URL", "http://127.0.0.1:42101");
  const context = { electronPlatformName: "darwin", packager: {
    projectDir: root, platformSpecificBuildOptions: {},
  } };
  prepare(context);
  expect(JSON.parse(readFileSync(join(root, "client-runtime.json"), "utf8"))).toEqual({
    apiUrl: "http://127.0.0.1:18939", appUrl: "http://127.0.0.1:42101", buildCommit: sha,
  });
  expect(context.packager.platformSpecificBuildOptions.releaseInfo.minimumSystemVersion).toBe("22.0.0");
});
