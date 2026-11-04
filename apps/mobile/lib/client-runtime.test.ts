// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

const runtime = vi.hoisted(() => ({ value: {
  apiUrl: "https://testbench.example:52101",
  buildCommit: "commit-from-artifact",
} as unknown }));

vi.mock("expo-constants", () => ({ default: {
  get expoConfig() { return { extra: { clientRuntime: runtime.value } }; },
} }));

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("embedded client runtime", () => {
  it("uses the artifact config even when a different environment URL exists", async () => {
    runtime.value = { apiUrl: "https://testbench.example:52101", buildCommit: "commit-from-artifact" };
    vi.stubEnv("EXPO_PUBLIC_API_URL", "https://production.example");
    const { API_URL, clientRuntime } = await import("./client-runtime");
    expect(API_URL).toBe("https://testbench.example:52101");
    expect(clientRuntime.buildCommit).toBe("commit-from-artifact");
  });

  it.each([undefined, { apiUrl: "file:///tmp/backend", buildCommit: "x" }])(
    "rejects missing or invalid runtime configuration", async (value) => {
      runtime.value = value;
      await expect(import("./client-runtime")).rejects.toThrow();
    },
  );
});
