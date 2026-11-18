export const ISSUE_IDENTIFIER = /^[a-z][a-z0-9]*-\d+$/i;
const UUID = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i;

// Match the authority directly because React Native's URL implementation
// does not reliably resolve relative URLs or parse IPv6 hosts.
const HTTP_ORIGIN = /^https?:\/\/[a-z0-9.\-\[\]:]+(?=\/|[?#]|$)/i;
const LOOPBACK_ORIGIN = /^https?:\/\/(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$/i;
const ISSUE_PATH = /^\/(?:(?<workspace>[a-z0-9-]+)\/)?issues?\/(?<id>[a-z0-9-]+)\/?(?:#comment-(?<highlight>[a-z0-9_-]+))?$/i;

export function parseIssueLink(
  href: string,
  workspace: string,
  webUrl: string | undefined,
  allowLoopback: boolean,
): { id: string; highlight?: string } | null {
  const origin = href.match(HTTP_ORIGIN)?.[0];
  if (origin) {
    const configuredOrigin = webUrl?.match(HTTP_ORIGIN)?.[0];
    const sameOrigin = origin.toLowerCase() === configuredOrigin?.toLowerCase();
    if (!sameOrigin && !(allowLoopback && LOOPBACK_ORIGIN.test(origin))) return null;
  }

  const target = ISSUE_PATH.exec(origin ? href.slice(origin.length) : href)?.groups;
  if (!target || (target.workspace && target.workspace !== workspace)) return null;
  if (!ISSUE_IDENTIFIER.test(target.id) && !UUID.test(target.id)) return null;
  return { id: target.id, highlight: target.highlight };
}
