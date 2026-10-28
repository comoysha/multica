/**
 * Workspace Issues view bar — the same model as desktop's
 * `packages/views/issues/components/view-bar.tsx`.
 *
 * Built-in tabs (All / Members / Agents) and saved views share one ordered
 * row. Per-user prefs (`hidden` + `order`) decide what is on screen; the
 * anchor built-in (`builtin:all`) cannot be hidden. A saved view's own
 * conditions are `query.statusFilters` / `query.priorityFilters` plus
 * `scope_variant` (members / agents). Empty arrays mean "no constraint",
 * matching desktop. An intersection that collapses to nothing matches
 * nothing — it must not fall through to "show all".
 */
import type { Issue, IssuePriority, IssueStatus } from "@multica/core/types";

export type WorkspaceActorKind = "all" | "members" | "agents";

export const BUILTIN_KEYS: readonly WorkspaceActorKind[] = ["all", "members", "agents"];
export const VIEW_BAR_ANCHOR_ID = "builtin:all";

const PRIORITIES: readonly IssuePriority[] = ["none", "low", "medium", "high", "urgent"];

export interface ViewBarPrefs {
  hidden: string[];
  order: string[];
}

export const EMPTY_VIEW_BAR_PREFS: ViewBarPrefs = { hidden: [], order: [] };

export interface ViewBarItem {
  barItemId: string;
  label: string;
  kind: "builtin" | "view";
  builtinKey?: WorkspaceActorKind;
  viewId?: string;
}

export interface ViewQueryFilter {
  status: IssueStatus[];
  priority: IssuePriority[];
  actorKind: WorkspaceActorKind;
}

interface Dimension<T extends string> {
  /** Both sides named values and none survived. Callers must match nothing. */
  matchNone: boolean;
  values: T[];
}

function stringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string" && item.length > 0);
}

function applyViewBarPrefs<T extends { barItemId: string }>(
  items: T[],
  prefs: ViewBarPrefs | undefined,
  anchorId: string,
): T[] {
  const order = prefs?.order ?? [];
  const hidden = new Set(prefs?.hidden ?? []);
  hidden.delete(anchorId);

  const byId = new Map(items.map((item) => [item.barItemId, item]));
  const ordered: T[] = [];
  for (const id of order) {
    const item = byId.get(id);
    if (!item) continue;
    ordered.push(item);
    byId.delete(id);
  }
  for (const item of items) {
    if (byId.has(item.barItemId)) ordered.push(item);
  }
  return ordered.filter((item) => !hidden.has(item.barItemId));
}

export function visibleViewBarItems(
  builtinLabels: Record<WorkspaceActorKind, string>,
  views: { id: string; name: string }[],
  prefs: ViewBarPrefs | undefined,
): ViewBarItem[] {
  const items: ViewBarItem[] = [
    ...BUILTIN_KEYS.map((key) => ({
      barItemId: `builtin:${key}`,
      label: builtinLabels[key],
      kind: "builtin" as const,
      builtinKey: key,
    })),
    ...views.map((view) => ({
      barItemId: `view:${view.id}`,
      label: view.name,
      kind: "view" as const,
      viewId: view.id,
    })),
  ];
  return applyViewBarPrefs(items, prefs, VIEW_BAR_ANCHOR_ID);
}

/** Status / priority / assignee-kind a saved view fixes. Unknown priority
 *  members are dropped; any non-empty string is a status key (custom
 *  statuses are not a closed enum). */
export function filtersFromView(view: {
  query: Record<string, unknown>;
  scope_variant?: string | null;
}): ViewQueryFilter {
  const variant = view.scope_variant;
  return {
    status: stringArray(view.query.statusFilters),
    priority: stringArray(view.query.priorityFilters).filter((p): p is IssuePriority =>
      (PRIORITIES as readonly string[]).includes(p),
    ),
    actorKind: variant === "members" || variant === "agents" ? variant : "all",
  };
}

function dimension<T extends string>(view: readonly T[], manual: readonly T[]): Dimension<T> {
  if (view.length === 0) return { matchNone: false, values: [...manual] };
  if (manual.length === 0) return { matchNone: false, values: [...view] };
  const manualSet = new Set(manual);
  const values = view.filter((value) => manualSet.has(value));
  return { matchNone: values.length === 0, values };
}

export interface WorkspaceListFilter {
  actorKind: WorkspaceActorKind;
  viewStatus: readonly IssueStatus[];
  viewPriority: readonly IssuePriority[];
  manualStatus: readonly IssueStatus[];
  manualPriority: readonly IssuePriority[];
}

export function filterWorkspaceIssues(issues: Issue[], filter: WorkspaceListFilter): Issue[] {
  const status = dimension(filter.viewStatus, filter.manualStatus);
  const priority = dimension(filter.viewPriority, filter.manualPriority);
  if (status.matchNone || priority.matchNone) return [];

  return issues.filter((issue) => {
    if (filter.actorKind === "members" && issue.assignee_type !== "member") return false;
    if (
      filter.actorKind === "agents" &&
      issue.assignee_type !== "agent" &&
      issue.assignee_type !== "squad"
    ) {
      return false;
    }
    if (status.values.length > 0 && !status.values.includes(issue.status)) return false;
    if (priority.values.length > 0 && !priority.values.includes(issue.priority)) return false;
    return true;
  });
}
