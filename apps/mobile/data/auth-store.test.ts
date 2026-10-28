// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ storedToken: null as string | null }));
const api = vi.hoisted(() => ({
  getMe: vi.fn(),
  setToken: vi.fn(),
  sendCode: vi.fn(),
  verifyCode: vi.fn(),
}));

vi.mock("./api", () => ({ api, ApiError: class ApiError extends Error {} }));
vi.mock("./local-auth", () => ({ localAuthToken: "mul_local-test" }));
vi.mock("./secure-storage", () => ({
  getToken: vi.fn(async () => state.storedToken),
  setToken: vi.fn(async (token: string) => { state.storedToken = token; }),
  clearToken: vi.fn(async () => { state.storedToken = null; }),
}));
vi.mock("./session-epoch", () => ({ invalidateSessionEpoch: vi.fn() }));
vi.mock("./workspace-store", () => ({
  useWorkspaceStore: { getState: () => ({ restoreSlug: vi.fn(async () => {}) }) },
}));

import { useAuthStore } from "./auth-store";

describe("trusted local mobile startup", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.storedToken = null;
    useAuthStore.setState({ user: null, isLoading: true });
  });

  it("opens with the configured token and never requests an email code", async () => {
    const user = { id: "user-1", email: "owner@example.test" };
    api.getMe.mockResolvedValue(user);

    await useAuthStore.getState().initialize();

    expect(api.setToken).toHaveBeenCalledWith("mul_local-test");
    expect(state.storedToken).toBe("mul_local-test");
    expect(useAuthStore.getState().user).toEqual(user);
    expect(useAuthStore.getState().isLoading).toBe(false);
    expect(api.sendCode).not.toHaveBeenCalled();
    expect(api.verifyCode).not.toHaveBeenCalled();
  });
});
