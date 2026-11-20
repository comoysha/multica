/**
 * Walk `GET /api/issues` until every row is in hand.
 *
 * The server caps a page at 100 and defaults the order to `position`.
 * In a workspace whose earliest positions are all `done`, the first page
 * is 100 done issues. A client filter for backlog (or for todo / in
 * progress) then looks empty even though those issues exist. Callers that
 * filter in memory have to collect every page first.
 *
 * `total: 0` is the schema fallback for a response that omitted the
 * count, so a full page with total 0 is not "there is nothing left".
 */
export const ISSUE_LIST_PAGE_SIZE = 100;
const MAX_PAGES = 50;

export async function collectIssuePages<T extends { id: string }>(
  fetchPage: (offset: number, limit: number) => Promise<{ issues: T[]; total: number }>,
  pageSize = ISSUE_LIST_PAGE_SIZE,
): Promise<T[]> {
  const out: T[] = [];
  const seen = new Set<string>();
  for (let page = 0; page < MAX_PAGES; page++) {
    const res = await fetchPage(page * pageSize, pageSize);
    for (const issue of res.issues) {
      if (seen.has(issue.id)) continue;
      seen.add(issue.id);
      out.push(issue);
    }
    if (res.issues.length < pageSize) break;
    if (res.total > 0 && out.length >= res.total) break;
  }
  return out;
}
