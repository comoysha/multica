// @vitest-environment node
import { describe, expect, it } from "vitest";
import { parseIssueLink } from "./issue-link";

const webUrl = "https://multica.example.test:8443";
const uuid = "12345678-1234-4abc-8abc-123456789abc";

describe("parseIssueLink", () => {
  it.each([
    [`${webUrl}/team/issues/MU-480`, "MU-480"],
    [`${webUrl}/team/issue/MU-480`, "MU-480"],
    ["http://localhost:8180/team/issues/MU-480", "MU-480"],
    ["http://127.0.0.1:8180/issues/MU-480", "MU-480"],
    [`http://[::1]:8180/team/issues/${uuid}`, uuid],
    [`/team/issues/${uuid}`, uuid],
  ])("opens %s in the current workspace", (href, id) => {
    expect(parseIssueLink(href, "team", webUrl, true)).toEqual({ id });
  });

  it("keeps a linked comment as the detail highlight", () => {
    expect(parseIssueLink("/issues/MU-480#comment-example", "team", webUrl, true))
      .toEqual({ id: "MU-480", highlight: "example" });
  });

  it.each([
    "https://external.example.test/team/issues/MU-480",
    "https://multica.example.test/team/issues/MU-480",
    "http://localhost.example.test:8180/team/issues/MU-480",
    "http://localhost:8180@external.example.test/team/issues/MU-480",
    "//external.example.test/team/issues/MU-480",
    "/other/issues/MU-480",
    "/team/issues/MU-480/edit",
    "/team/issues/MU-480?filter=open",
    "/team/issues/MU-480#other",
    "/team/issues/not-an-id",
  ])("leaves %s to the existing link handler", (href) => {
    expect(parseIssueLink(href, "team", webUrl, true)).toBeNull();
  });

  it("limits loopback aliases to the personal development client", () => {
    expect(parseIssueLink("http://localhost:8180/issues/MU-480", "team", webUrl, false))
      .toBeNull();
  });
});
