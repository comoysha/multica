import { app } from "electron";
import { readFile } from "fs/promises";
import { join } from "path";
import {
  DEFAULT_RUNTIME_CONFIG,
  parseRuntimeConfig,
  runtimeConfigFromDevEnv,
  type RuntimeConfig,
  type RuntimeConfigEnv,
  type RuntimeConfigResult,
} from "../shared/runtime-config";

export async function loadRuntimeConfig(options: {
  isDev: boolean;
  env: RuntimeConfigEnv;
  configPath?: string;
  userDataConfigPath?: string;
}): Promise<RuntimeConfigResult> {
  if (options.isDev) {
    try {
      return { ok: true, config: runtimeConfigFromDevEnv(options.env) };
    } catch (err) {
      return { ok: false, error: { message: errorMessage(err) } };
    }
  }

  // A per-variant config inside the app's own userData directory (written by
  // the branch test-build installer) lets a "Multica·分支" build point at its
  // worktree testbench without touching the shared ~/.multica/desktop.json
  // the production build reads. An existing but invalid file fails closed
  // naming its own path; a missing one falls through to the next candidate.
  const candidates = [
    options.userDataConfigPath ?? userDataConfigPath(),
    options.configPath ?? desktopConfigPath(),
  ];
  for (const configPath of candidates) {
    try {
      const raw = await readFile(configPath, "utf-8");
      return { ok: true, config: parseRuntimeConfig(raw) };
    } catch (err) {
      if (isMissingFileError(err)) continue;
      return {
        ok: false,
        error: {
          message: `Invalid ${configPath}: ${errorMessage(err)}`,
        },
      };
    }
  }
  return { ok: true, config: { ...DEFAULT_RUNTIME_CONFIG } };
}

export function desktopConfigPath(): string {
  return join(app.getPath("home"), ".multica", "desktop.json");
}

export function userDataConfigPath(): string {
  return join(app.getPath("userData"), "runtime-config.json");
}

function isMissingFileError(err: unknown): boolean {
  return Boolean(
    err &&
      typeof err === "object" &&
      "code" in err &&
      (err as NodeJS.ErrnoException).code === "ENOENT",
  );
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export type { RuntimeConfig, RuntimeConfigResult };
