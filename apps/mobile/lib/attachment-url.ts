/**
 * Resolve a server-relative attachment URL against the configured API base.
 *
 * Background: when the backend has no CloudFront signer configured (e.g.
 * the self-hosted RustFS / private-S3 case in MUL-2976), `attachment.url`
 * and `attachment.download_url` come back as server-relative paths like
 * `/api/attachments/{id}/download`. Web is happy with that — same-origin
 * `<img src="/api/...">` resolves against the document base — but RN
 * needs an absolute http(s) URL for both `Linking.openURL` (`Cannot open
 * URL` otherwise) and `<Image source={{ uri }}>` (no document origin to
 * resolve against; the request is silently dropped).
 *
 * Mirrors `packages/core/workspace/avatar-url.ts:resolvePublicFileUrl`
 * with native loopback upload handling. We don't import the core helper because its `getBaseUrl()`
 * pulls from a singleton ApiClient that lives in `@multica/core/api` —
 * not on the mobile sharing whitelist (apps/mobile/CLAUDE.md "mirror,
 * don't import"). Mobile reads its own `EXPO_PUBLIC_API_URL` from the
 * Expo env, the same value the rest of `data/api.ts` uses.
 *
 * Contract:
 *   - null / undefined / "" → null (caller should treat as "no URL").
 *   - already-absolute URL  → returned unchanged.
 *   - server-relative path  → API base + path, with a single boundary
 *                             slash (we trim trailing slashes from the
 *                             base before joining).
 */

const API_URL = process.env.EXPO_PUBLIC_API_URL ?? "";

export function resolveAttachmentUrlWithBase(
  rawUrl: string | null | undefined,
  baseUrl: string,
): string | null {
  if (!rawUrl) return null;
  const trimmedBaseUrl = baseUrl.replace(/\/+$/, "");
  if (rawUrl.startsWith("/")) return `${trimmedBaseUrl}${rawUrl}`;

  // A local test backend may advertise its public /uploads route using its
  // own loopback address. That address points at the phone when embedded in
  // issue markdown, so use the app's reachable API origin for this route.
  // Leave other absolute URLs (including signed CDN links) untouched.
  if (trimmedBaseUrl) {
    try {
      const parsed = new URL(rawUrl);
      if (
        (parsed.hostname === "localhost" || parsed.hostname === "127.0.0.1" || parsed.hostname === "::1") &&
        parsed.pathname.startsWith("/uploads/")
      ) {
        return `${trimmedBaseUrl}${parsed.pathname}${parsed.search}${parsed.hash}`;
      }
    } catch {
      // Preserve unknown schemes and malformed legacy links as they were.
    }
  }
  return rawUrl;
}

export function resolveAttachmentUrl(
  rawUrl: string | null | undefined,
): string | null {
  return resolveAttachmentUrlWithBase(rawUrl, API_URL);
}
