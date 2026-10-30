import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

const fileSystem = vi.hoisted(() => ({
  create: vi.fn(),
  copy: vi.fn(async () => {}),
  upload: vi.fn(),
  delete: vi.fn(),
}));

vi.mock("expo-file-system", () => ({
  Directory: class {
    create = fileSystem.create;
    delete = fileSystem.delete;
  },
  File: class {
    copy = fileSystem.copy;
    upload = fileSystem.upload;
  },
  Paths: { cache: "file:///cache" },
  UploadType: { MULTIPART: 1 },
}));

// api.ts refuses to load without a base URL; set it before the import runs.
vi.hoisted(() => {
  process.env.EXPO_PUBLIC_API_URL = "https://api.example.test";
});

// The real store pulls in expo-secure-store; the client only needs the slug.
vi.mock("@/data/workspace-store", () => ({ getCurrentSlug: () => null }));

describe("api.deleteComment", () => {
  const fetchMock = vi.fn(async () => new Response(null, { status: 204 }));

  beforeEach(() => {
    fetchMock.mockClear();
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(console, "log").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  // #8296: servers that keep a deleted comment's replies also route
  // /keep-replies; older servers do not, so a keep-replies delete that reaches
  // one fails instead of deleting the replies too.
  it.each([
    [{ keepReplies: true }, "https://api.example.test/api/comments/comment-1/keep-replies"],
    [{ keepReplies: false }, "https://api.example.test/api/comments/comment-1"],
    [undefined, "https://api.example.test/api/comments/comment-1"],
  ])("with %j sends DELETE %s", async (opts, url) => {
    await api.deleteComment("comment-1", opts);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(url, expect.objectContaining({ method: "DELETE" }));
  });
});

describe("api.uploadFile", () => {
  beforeEach(() => {
    fileSystem.create.mockClear();
    fileSystem.copy.mockClear();
    fileSystem.upload.mockReset();
    fileSystem.delete.mockClear();
    vi.spyOn(console, "log").mockImplementation(() => {});
  });

  afterEach(() => {
    api.setToken(null);
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("streams a named multipart file and returns the validated attachment", async () => {
    const attachment = { id: "attachment-1", filename: "photo.png", url: "https://example.test/photo.png" };
    fileSystem.upload.mockResolvedValue({ status: 200, body: JSON.stringify(attachment), headers: {} });
    api.setToken("test-token");

    const result = await api.uploadFile(
      { uri: "file:///picker/random.png", name: "photo.png", type: "image/png" },
      { issueId: "issue-1" },
    );

    expect(result).toMatchObject(attachment);
    expect(fileSystem.copy).toHaveBeenCalledTimes(1);
    expect(fileSystem.upload).toHaveBeenCalledWith(
      "https://api.example.test/api/upload-file",
      expect.objectContaining({
        uploadType: 1,
        fieldName: "file",
        mimeType: "image/png",
        parameters: { issue_id: "issue-1" },
        headers: expect.objectContaining({ Authorization: "Bearer test-token" }),
      }),
    );
    expect(fileSystem.delete).toHaveBeenCalledTimes(1);
  });

  it("keeps the staged file until a timed-out native upload settles", async () => {
    vi.useFakeTimers();
    let rejectUpload: (error: Error) => void = () => {};
    fileSystem.upload.mockImplementation(() => new Promise((_resolve, reject) => {
      rejectUpload = reject;
    }));
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const pending = api.uploadFile({ uri: "file:///picker/random.png", name: "photo.png", type: "image/png" });
    const rejected = expect(pending).rejects.toMatchObject({ status: 0, message: "Upload stalled for 60000ms" });

    await vi.advanceTimersByTimeAsync(60_000);
    await rejected;
    expect(fileSystem.delete).not.toHaveBeenCalled();

    rejectUpload(new Error("cancelled"));
    await Promise.resolve();
    expect(fileSystem.delete).toHaveBeenCalledTimes(1);
  });

  it("allows a progressing upload to run longer than the stall window", async () => {
    vi.useFakeTimers();
    let resolveUpload: (value: unknown) => void = () => {};
    fileSystem.upload.mockImplementation(() => new Promise((resolve) => {
      resolveUpload = resolve;
    }));
    const pending = api.uploadFile({ uri: "file:///picker/large.png", name: "large.png", type: "image/png" });
    await Promise.resolve();
    const uploadOptions = (fileSystem.upload.mock.calls as unknown as [string, { onProgress: () => void }][])[0][1];

    await vi.advanceTimersByTimeAsync(50_000);
    uploadOptions.onProgress();
    await vi.advanceTimersByTimeAsync(50_000);
    uploadOptions.onProgress();
    await vi.advanceTimersByTimeAsync(50_000);
    resolveUpload({ status: 200, body: JSON.stringify({ id: "large-1", filename: "large.png", url: "https://example.test/large.png" }), headers: {} });

    await expect(pending).resolves.toMatchObject({ id: "large-1" });
    expect(fileSystem.delete).toHaveBeenCalledTimes(1);
  });

});
