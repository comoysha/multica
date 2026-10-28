// @vitest-environment node
import { describe, expect, it } from "vitest";
import type { Issue } from "@multica/core/types";
import {
  filtersFromView,
  filterWorkspaceIssues,
  visibleViewBarItems,
  type ViewBarPrefs,
} from "./issue-view-bar";

const LABELS = { all: "All", members: "Members", agents: "Agents" };

function issue(
  id: string,
  status: string,
  extra: Partial<Pick<Issue, "priority" | "assignee_type">> = {},
): Issue {
  return {
    id,
    workspace_id: "ws-1",
    number: 1,
    identifier: `MU-${id}`,
    title: id,
    description: null,
    status,
    priority: extra.priority ?? "none",
    assignee_type: extra.assignee_type ?? null,
    assignee_id: null,
    creator_type: "member",
    creator_id: "user-1",
    parent_issue_id: null,
    project_id: null,
    position: 0,
    stage: null,
    start_date: null,
    due_date: null,
    metadata: {},
    properties: {},
    created_at: "",
    updated_at: "",
  };
}

const ROWS = [
  issue("backlog", "backlog", { assignee_type: "member" }),
  issue("todo", "todo", { assignee_type: "member" }),
  issue("progress", "in_progress", { assignee_type: "agent" }),
  issue("review", "in_review", { assignee_type: "squad" }),
  issue("qa", "started_2", { assignee_type: "member" }),
  issue("done", "done", { assignee_type: "agent" }),
  issue("cancelled", "cancelled"),
];

describe("visibleViewBarItems", () => {
  const views = [
    { id: "v-backlog", name: "backlog" },
    { id: "v-work", name: "人和agent工作" },
  ];

  it("keeps All and saved views when Members and Agents are hidden", () => {
    const prefs: ViewBarPrefs = {
      hidden: ["builtin:members", "builtin:agents"],
      order: ["builtin:all", "view:v-backlog", "view:v-work"],
    };
    expect(visibleViewBarItems(LABELS, views, prefs).map((item) => item.label)).toEqual([
      "All",
      "backlog",
      "人和agent工作",
    ]);
  });

  it("refuses to hide the All anchor", () => {
    const prefs: ViewBarPrefs = { hidden: ["builtin:all", "builtin:members"], order: [] };
    const labels = visibleViewBarItems(LABELS, [], prefs).map((item) => item.builtinKey);
    expect(labels).toEqual(["all", "agents"]);
  });

  it("appends views the order list does not mention", () => {
    const prefs: ViewBarPrefs = { hidden: [], order: ["view:v-work", "builtin:all"] };
    expect(visibleViewBarItems(LABELS, views, prefs).map((item) => item.barItemId)).toEqual([
      "view:v-work",
      "builtin:all",
      "builtin:members",
      "builtin:agents",
      "view:v-backlog",
    ]);
  });
});

describe("filtersFromView", () => {
  it("reads status keys, including a custom status", () => {
    expect(
      filtersFromView({
        query: {
          statusFilters: ["todo", "in_progress", "in_review", "started_2", "done", 1, ""],
        },
      }),
    ).toEqual({
      status: ["todo", "in_progress", "in_review", "started_2", "done"],
      priority: [],
      actorKind: "all",
    });
  });

  it("drops unknown priorities and maps scope_variant", () => {
    expect(
      filtersFromView({
        query: { priorityFilters: ["high", "nope"] },
        scope_variant: "agents",
      }).actorKind,
    ).toBe("agents");
    expect(
      filtersFromView({
        query: { priorityFilters: ["high", "nope"] },
        scope_variant: "agents",
      }).priority,
    ).toEqual(["high"]);
  });
});

describe("filterWorkspaceIssues", () => {
  it("keeps only Backlog for that view", () => {
    const ids = filterWorkspaceIssues(ROWS, {
      actorKind: "all",
      viewStatus: ["backlog"],
      viewPriority: [],
      manualStatus: [],
      manualPriority: [],
    }).map((row) => row.id);
    expect(ids).toEqual(["backlog"]);
  });

  it("keeps the working statuses and drops backlog and cancelled", () => {
    const ids = filterWorkspaceIssues(ROWS, {
      actorKind: "all",
      viewStatus: ["todo", "in_progress", "in_review", "started_2", "done"],
      viewPriority: [],
      manualStatus: [],
      manualPriority: [],
    }).map((row) => row.id);
    expect(ids).toEqual(["todo", "progress", "review", "qa", "done"]);
  });

  it("matches nothing when the extra filter misses every view status", () => {
    expect(
      filterWorkspaceIssues(ROWS, {
        actorKind: "all",
        viewStatus: ["backlog"],
        viewPriority: [],
        manualStatus: ["done"],
        manualPriority: [],
      }),
    ).toEqual([]);
  });

  it("filters agents and squads for the agents tab", () => {
    const ids = filterWorkspaceIssues(ROWS, {
      actorKind: "agents",
      viewStatus: [],
      viewPriority: [],
      manualStatus: [],
      manualPriority: [],
    }).map((row) => row.id);
    expect(ids).toEqual(["progress", "review", "done"]);
  });
});
