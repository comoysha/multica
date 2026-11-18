import type { Attachment } from "@multica/core/types";
import { attachmentIdFromDownloadURL } from "@multica/core/types/attachment-url";
import { matchAttachmentByURL } from "@multica/core/attachments/image-sequence";
import { resolveAttachmentUrlWithBase } from "@/lib/attachment-url";

const API_URL = process.env.EXPO_PUBLIC_API_URL ?? "";

export function resolveImageUri(uri: string, attachments?: Attachment[]): string {
  return resolveImageUriWithBase(uri, attachments, API_URL);
}

export function resolveImageUriWithBase(uri: string, attachments: Attachment[] | undefined, baseUrl: string): string {
  const match = matchAttachmentByURL(uri, attachments);
  // Upgraded self-hosted servers serve legacy local uploads through the same
  // authenticated download endpoint as new attachments.
  const candidate = match?.download_url || match?.markdown_url || match?.url || uri;
  return resolveAttachmentUrlWithBase(candidate, baseUrl) ?? uri;
}

export function imageRequestHeaders(uri: string, token: string | null, baseUrl = API_URL) {
  if (!token || !attachmentIdFromDownloadURL(uri)) return undefined;
  try {
    const api = new URL(baseUrl);
    if (api.protocol !== "http:" && api.protocol !== "https:") return undefined;
    const image = new URL(uri, api);
    if (image.origin === api.origin) return { Authorization: `Bearer ${token}` };
  } catch {
    // A malformed image URL is never sent a credential.
  }
  return undefined;
}
