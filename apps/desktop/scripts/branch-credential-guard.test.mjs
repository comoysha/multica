import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import guard from "./branch-credential-guard.cjs";

describe("branch package credential guard", () => {
  let dir;
  afterEach(async () => { vi.unstubAllEnvs(); if (dir) await rm(dir, { recursive: true, force: true }); });
  async function fixture(contents) {
    dir = await mkdtemp(join(tmpdir(), "branch-guard-"));
    const resources = join(dir, "out", "Test.app", "Contents", "Resources");
    await mkdir(resources, { recursive: true });
    await writeFile(join(dir, ".env.local"), "VITE_LOCAL_AUTH_TOKEN=mul_original_secret\n");
    await writeFile(join(resources, "app.asar"), contents);
    vi.stubEnv("VITE_BRANCH_API_URL", "http://127.0.0.1:12345");
    vi.stubEnv("VITE_LOCAL_AUTH_TOKEN", "mul_testbench_secret");
    return { electronPlatformName: "darwin", appOutDir: join(dir, "out"), packager: { projectDir: dir, appInfo: { productFilename: "Test" } } };
  }
  it("accepts a package with only the testbench credential", async () => {
    await expect(guard(await fixture("mul_testbench_secret"))).resolves.toBeUndefined();
  });
  it("rejects a package containing the original credential", async () => {
    await expect(guard(await fixture("packaged mul_original_secret"))).rejects.toThrow("production login credential");
  });
  it("rejects using the original credential as the branch credential", async () => {
    const context = await fixture("");
    vi.stubEnv("VITE_LOCAL_AUTH_TOKEN", "mul_original_secret");
    await expect(guard(context)).rejects.toThrow("isolated branch credentials");
  });
});
