/**
 * Workspace-scoped saved issue views and the per-user view-bar preference.
 * Same container desktop's Issues page uses (`scope_type=workspace`).
 */
import { queryOptions } from "@tanstack/react-query";
import { api } from "@/data/api";

const WORKSPACE_SCOPE = { scope_type: "workspace" };

export const issueViewKeys = {
  all: (wsId: string | null) => ["issue-views", wsId] as const,
  list: (wsId: string | null) => [...issueViewKeys.all(wsId), "workspace"] as const,
  preference: (wsId: string | null) =>
    ["issue-view-prefs", wsId, "workspace"] as const,
};

export function issueViewListOptions(wsId: string | null) {
  return queryOptions({
    queryKey: issueViewKeys.list(wsId),
    queryFn: ({ signal }) => api.listIssueViews(WORKSPACE_SCOPE, { signal }),
    enabled: !!wsId,
  });
}

export function issueViewPreferenceOptions(wsId: string | null) {
  return queryOptions({
    queryKey: issueViewKeys.preference(wsId),
    queryFn: ({ signal }) => api.getIssueViewPreference(WORKSPACE_SCOPE, { signal }),
    enabled: !!wsId,
  });
}
