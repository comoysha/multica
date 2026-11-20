/**
 * View store for the workspace Issues list (the second tab, and More →
 * Issues). Built-in tabs are `all / members / agents`. A saved view, when
 * open, replaces that tab: its status / priority / assignee-kind come from
 * the view definition, not from this store's scope.
 *
 * `statusFilters` / `priorityFilters` are extra constraints the filter
 * sheet layers on top of the open tab or view. Empty array = no extra
 * constraint. `clearFilters` clears only those extras — it does not leave
 * the open view. `resetForWorkspace` also drops the open view, because
 * view ids belong to one workspace.
 *
 * Scope and the open view are client-side. The list query stays
 * `issueKeys.list(wsId)` so realtime invalidation is unchanged.
 */
import { create } from "zustand";
import type { IssuePriority, IssueStatus } from "@multica/core/types";
import type { WorkspaceActorKind } from "@/lib/issue-view-bar";

export type IssuesScope = WorkspaceActorKind;

interface IssuesViewState {
  scope: IssuesScope;
  /** Open saved view. Null means a built-in tab (`scope`) is showing. */
  activeViewId: string | null;
  statusFilters: IssueStatus[];
  priorityFilters: IssuePriority[];
  selectBuiltin: (scope: IssuesScope) => void;
  selectView: (id: string) => void;
  setActiveViewId: (id: string | null) => void;
  toggleStatusFilter: (status: IssueStatus) => void;
  togglePriorityFilter: (priority: IssuePriority) => void;
  clearFilters: () => void;
  resetForWorkspace: () => void;
}

export const useIssuesViewStore = create<IssuesViewState>((set) => ({
  scope: "all",
  activeViewId: null,
  statusFilters: [],
  priorityFilters: [],
  selectBuiltin: (scope) => set({ scope, activeViewId: null }),
  selectView: (id) => set({ activeViewId: id }),
  setActiveViewId: (id) => set({ activeViewId: id }),
  toggleStatusFilter: (status) =>
    set((state) => ({
      statusFilters: state.statusFilters.includes(status)
        ? state.statusFilters.filter((s) => s !== status)
        : [...state.statusFilters, status],
    })),
  togglePriorityFilter: (priority) =>
    set((state) => ({
      priorityFilters: state.priorityFilters.includes(priority)
        ? state.priorityFilters.filter((p) => p !== priority)
        : [...state.priorityFilters, priority],
    })),
  clearFilters: () => set({ statusFilters: [], priorityFilters: [] }),
  resetForWorkspace: () =>
    set({ statusFilters: [], priorityFilters: [], activeViewId: null }),
}));
