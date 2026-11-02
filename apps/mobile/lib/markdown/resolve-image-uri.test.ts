import { describe, expect, it, vi } from "vitest";
import type { Attachment } from "@multica/core/types";
vi.mock("expo-constants", () => ({ default: {
  expoConfig: { extra: { clientRuntime: { apiUrl: "https://test.example:52101", buildCommit: "fixture" } } },
} }));
import { resolveImageUriWithBase } from "./resolve-image-uri";

const id = "11111111-2222-3333-4444-555555555555";
const localURL = `http://localhost:18939/uploads/workspaces/test/${id}.png`;
const baseUrl = "https://test.example:52101";
const attachment = {
  id,
  url: localURL,
  markdown_url: localURL,
  download_url: `/api/attachments/${id}/download`,
  content_type: "image/png",
  filename: "image.png",
} as Attachment;

describe("resolveImageUri", () => {
  it("renders an existing test issue image via the reachable public uploads route", () => {
    expect(resolveImageUriWithBase(localURL, [attachment], baseUrl)).toBe(
      `https://test.example:52101/uploads/workspaces/test/${id}.png`,
    );
  });

  it("uses the same public image URL for an attachment sequence item", () => {
    expect(resolveImageUriWithBase(attachment.download_url, [attachment], baseUrl)).toBe(
      `https://test.example:52101/uploads/workspaces/test/${id}.png`,
    );
  });

  it("preserves a signed download URL for private storage", () => {
    const signed = "https://cdn.example/image.png?Signature=fixture";
    expect(resolveImageUriWithBase(`/api/attachments/${id}/download`, [{
      ...attachment,
      url: "https://private.example/uploads/image.png",
      markdown_url: `https://api.example/api/attachments/${id}/download`,
      download_url: signed,
    }], baseUrl)).toBe(signed);
  });
});
