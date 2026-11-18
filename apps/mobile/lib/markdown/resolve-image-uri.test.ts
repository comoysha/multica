import { describe, expect, it, vi } from "vitest";
import type { Attachment } from "@multica/core/types";
vi.mock("expo-constants", () => ({ default: {
  expoConfig: { extra: { clientRuntime: { apiUrl: "https://test.example:52101", buildCommit: "fixture" } } },
} }));
import { imageRequestHeaders, resolveImageUriWithBase } from "./resolve-image-uri";

const id = "11111111-2222-3333-4444-555555555555";
const localURL = `http://localhost:18939/uploads/workspaces/33333333-3333-4333-8333-333333333333/${id}.png`;
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
  it("renders an existing test issue image through the authenticated attachment route", () => {
    const updatedAttachment = {
      ...attachment,
      url: `${baseUrl}/api/attachments/${id}/download`,
      markdown_url: `${baseUrl}/api/attachments/${id}/download`,
    };
    expect(resolveImageUriWithBase(localURL, [updatedAttachment], baseUrl)).toBe(
      `${baseUrl}/api/attachments/${id}/download`,
    );
  });

  it("uses the same authenticated URL for an attachment sequence item", () => {
    expect(resolveImageUriWithBase(attachment.download_url, [attachment], baseUrl)).toBe(
      `${baseUrl}/api/attachments/${id}/download`,
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


describe("imageRequestHeaders", () => {
  const path = `/api/attachments/${id}/download`;

  it("uses the current session for attachment requests to the configured origin", () => {
    expect(imageRequestHeaders(`${baseUrl}${path}`, "token", baseUrl))
      .toEqual({ Authorization: "Bearer token" });
  });

  it.each([
    `https://other.example.test${path}`,
    `https://test.example${path}`,
    `http://test.example:52101${path}`,
    `//other.example.test${path}`,
    `${baseUrl}/external.png`,
  ])("omits the session from %s", (uri) => {
    expect(imageRequestHeaders(uri, "token", baseUrl)).toBeUndefined();
  });
});
