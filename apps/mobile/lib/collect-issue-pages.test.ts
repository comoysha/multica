// @vitest-environment node
import { describe, expect, it } from "vitest";
import { collectIssuePages } from "./collect-issue-pages";

function page<T>(issues: T[], total: number) {
  return { issues, total };
}

describe("collectIssuePages", () => {
  it("keeps rows past the first page, which may be entirely one status", async () => {
    const calls: number[] = [];
    const issues = await collectIssuePages(async (offset, limit) => {
      calls.push(offset);
      if (offset === 0) {
        return page(
          Array.from({ length: limit }, (_, i) => ({ id: `done-${i}`, status: "done" })),
          110,
        );
      }
      return page(
        [
          { id: "backlog-1", status: "backlog" },
          { id: "todo-1", status: "todo" },
          { id: "progress-1", status: "in_progress" },
        ],
        110,
      );
    }, 100);

    expect(calls).toEqual([0, 100]);
    expect(issues.map((issue) => issue.status).filter((status) => status !== "done")).toEqual([
      "backlog",
      "todo",
      "in_progress",
    ]);
  });

  it("stops on a short page when total is missing", async () => {
    let calls = 0;
    const issues = await collectIssuePages(async (_offset, limit) => {
      calls += 1;
      return page(
        Array.from({ length: calls === 1 ? limit : 3 }, (_, i) => ({ id: `${calls}-${i}` })),
        0,
      );
    }, 10);
    expect(calls).toBe(2);
    expect(issues).toHaveLength(13);
  });

  it("does not loop when every page stays full and total never arrives", async () => {
    let calls = 0;
    const issues = await collectIssuePages(async (offset, limit) => {
      calls += 1;
      return page(
        Array.from({ length: limit }, (_, i) => ({ id: `${offset + i}` })),
        0,
      );
    }, 2);
    expect(calls).toBe(50);
    expect(issues).toHaveLength(100);
  });
});
