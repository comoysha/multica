import type { Attachment } from "@multica/core/types";
import { matchAttachmentByURL } from "@multica/core/attachments/image-sequence";
import { resolveAttachmentUrl, resolveAttachmentUrlWithBase } from "@/lib/attachment-url";

function imageCandidate(uri: string, attachments?: Attachment[]): string {
  const match = matchAttachmentByURL(uri, attachments);
  // Local storage exposes /uploads publicly. Its markdown URL is the same as
  // the stored URL, but on a test backend it can contain localhost. The
  // generic /api/attachments/:id/download URL needs bearer auth, which an
  // ExpoImage request cannot supply, so keep the public storage route.
  let publicStorageUrl: string | undefined;
  if (match?.url && match.markdown_url === match.url) {
    try {
      if (new URL(match.url).pathname.startsWith("/uploads/")) publicStorageUrl = match.url;
    } catch {
      if (match.url.startsWith("/uploads/")) publicStorageUrl = match.url;
    }
  }
  return publicStorageUrl || match?.download_url || match?.markdown_url || match?.url || uri;
}

/** Pick a URL that native image loaders can fetch without API auth headers. */
export function resolveImageUri(uri: string, attachments?: Attachment[]): string {
  return resolveAttachmentUrl(imageCandidate(uri, attachments)) ?? uri;
}

export function resolveImageUriWithBase(uri: string, attachments: Attachment[] | undefined, baseUrl: string): string {
  return resolveAttachmentUrlWithBase(imageCandidate(uri, attachments), baseUrl) ?? uri;
}
