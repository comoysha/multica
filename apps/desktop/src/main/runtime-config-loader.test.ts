// @vitest-environment node
import { mkdtemp, writeFile } from "fs/promises";
import { join } from "path";
import { tmpdir } from "os";
import { describe, expect, it } from "vitest";
import { loadRuntimeConfig } from "./runtime-config-loader";

describe("loadRuntimeConfig", () => {
  it("uses dev env and ignores desktop.json during electron-vite dev", async () => {
    const dir = await mkdtemp(join(tmpdir(), "multica-desktop-config-"));
    const configPath = join(dir, "desktop.json");
    await writeFile(
      configPath,
      JSON.stringify({ schemaVersion: 1, apiUrl: "https://prod.example.com" }),
    );

    await expect(
      loadRuntimeConfig({
        isDev: true,
        configPath,
        env: {
          apiUrl: "http://localhost:8080",
          wsUrl: "ws://localhost:8080/ws",
          appUrl: "http://localhost:3000",
        },
      }),
    ).resolves.toEqual({
      ok: true,
      config: {
        schemaVersion: 1,
        apiUrl: "http://localhost:8080",
        wsUrl: "ws://localhost:8080/ws",
        appUrl: "http://localhost:3000",
      },
    });
  });

  it("uses cloud defaults when packaged config is absent", async () => {
    const dir = await mkdtemp(join(tmpdir(), "multica-desktop-config-"));
    await expect(
      loadRuntimeConfig({
        isDev: false,
        userDataConfigPath: join(dir, "missing-userdata.json"),
        configPath: join(dir, "missing.json"),
        env: {},
      }),
    ).resolves.toEqual({
      ok: true,
      config: {
        schemaVersion: 1,
        apiUrl: "https://api.multica.ai",
        wsUrl: "wss://api.multica.ai/ws",
        appUrl: "https://multica.ai",
      },
    });
  });

  it("parses a valid packaged desktop.json", async () => {
    const dir = await mkdtemp(join(tmpdir(), "multica-desktop-config-"));
    const configPath = join(dir, "desktop.json");
    await writeFile(
      configPath,
      JSON.stringify({ schemaVersion: 1, apiUrl: "https://api.example.com" }),
    );

    await expect(
      loadRuntimeConfig({
        isDev: false,
        userDataConfigPath: join(dir, "missing-userdata.json"),
        configPath,
        env: {},
      }),
    ).resolves.toEqual({
      ok: true,
      config: {
        schemaVersion: 1,
        apiUrl: "https://api.example.com",
        wsUrl: "wss://api.example.com/ws",
        appUrl: "https://example.com",
      },
    });
  });

  it("fails closed when packaged desktop.json is invalid", async () => {
    const dir = await mkdtemp(join(tmpdir(), "multica-desktop-config-"));
    const configPath = join(dir, "desktop.json");
    await writeFile(configPath, "{");

    const result = await loadRuntimeConfig({
      isDev: false,
      userDataConfigPath: join(dir, "missing-userdata.json"),
      configPath,
      env: {},
    });

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.message).toContain(configPath);
      expect(result.error.message).toContain("Invalid desktop runtime config JSON");
    }
  });

  it("uses the packaged branch endpoint ahead of shared config", async () => {
    const result = await loadRuntimeConfig({
      isDev: false, env: {}, branchEnv: { apiUrl: "http://127.0.0.1:18589", appUrl: "http://127.0.0.1:42103" },
      userDataConfigPath: "/missing/branch.json", configPath: "/missing/shared.json",
    });
    expect(result).toEqual({ ok: true, config: {
      schemaVersion: 1, apiUrl: "http://127.0.0.1:18589",
      wsUrl: "ws://127.0.0.1:18589/ws", appUrl: "http://127.0.0.1:42103",
    } });
  });

  it("prefers a per-variant userData config over the shared desktop.json", async () => {
    const dir = await mkdtemp(join(tmpdir(), "multica-desktop-config-"));
    const userDataConfigPath = join(dir, "runtime-config.json");
    const configPath = join(dir, "desktop.json");
    await writeFile(
      userDataConfigPath,
      JSON.stringify({ schemaVersion: 1, apiUrl: "https://branch.example.com" }),
    );
    await writeFile(
      configPath,
      JSON.stringify({ schemaVersion: 1, apiUrl: "https://shared.example.com" }),
    );

    await expect(
      loadRuntimeConfig({ isDev: false, userDataConfigPath, configPath, env: {} }),
    ).resolves.toEqual({
      ok: true,
      config: {
        schemaVersion: 1,
        apiUrl: "https://branch.example.com",
        wsUrl: "wss://branch.example.com/ws",
        appUrl: "https://branch.example.com",
      },
    });
  });

  it("fails closed naming the userData config when it exists but is invalid", async () => {
    const dir = await mkdtemp(join(tmpdir(), "multica-desktop-config-"));
    const userDataConfigPath = join(dir, "runtime-config.json");
    const configPath = join(dir, "desktop.json");
    await writeFile(userDataConfigPath, "{");
    await writeFile(
      configPath,
      JSON.stringify({ schemaVersion: 1, apiUrl: "https://shared.example.com" }),
    );

    const result = await loadRuntimeConfig({
      isDev: false,
      userDataConfigPath,
      configPath,
      env: {},
    });

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.message).toContain(userDataConfigPath);
      expect(result.error.message).not.toContain(configPath);
    }
  });
});
