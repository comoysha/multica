/**
 * Second tab. Workspace Issues — the same list desktop calls "Issues":
 * All, plus the user's saved views (for example a Backlog view and a
 * view of the remaining working statuses). Title is "任务" / "Issues".
 */
import { View } from "react-native";
import { Header } from "@/components/ui/header";
import { HeaderActions } from "@/components/ui/app-header-actions";
import { WorkspaceIssuesList } from "@/components/issue/workspace-issues-list";
import { useT } from "@/lib/i18n";

export default function IssuesTab() {
  const { t } = useT("navigation");
  return (
    <View className="flex-1 bg-background">
      <Header title={t("tabs.my_issues")} right={<HeaderActions />} />
      <WorkspaceIssuesList />
    </View>
  );
}
