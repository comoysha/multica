/**
 * Workspace Issues list: All / Members / Agents plus the user's saved
 * views, in the same order desktop's view bar uses. Selecting a view
 * applies that view's status (and priority / assignee-kind) filters.
 * The view's own conditions stay in the pill — they are not repeated as
 * chips. The filter sheet still adds extra status / priority constraints.
 */
import { useEffect, useMemo } from "react";
import { Pressable, ScrollView, SectionList, View } from "react-native";
import { useQuery } from "@tanstack/react-query";
import { router, useIsFocused } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import type { IssueView } from "@multica/core/api/schemas";
import type { IssuePriority, IssueStatus } from "@multica/core/types";
import { Text } from "@/components/ui/text";
import { Button } from "@/components/ui/button";
import { StatusIcon } from "@/components/ui/status-icon";
import { IssueRow } from "@/components/issue/issue-row";
import { IssuesLoading } from "@/components/issue/issues-loading";
import { issueListOptions } from "@/data/queries/issues";
import {
  issueViewListOptions,
  issueViewPreferenceOptions,
} from "@/data/queries/issue-views";
import { useWorkspaceStore } from "@/data/workspace-store";
import { useIssuesViewStore } from "@/data/stores/issues-view-store";
import { useClearFiltersOnWorkspaceChange } from "@/lib/use-clear-filters-on-workspace-change";
import { PRIORITY_LABEL } from "@/lib/issue-status";
import { useT } from "@/lib/i18n";
import { useIssueStatuses } from "@/lib/use-issue-statuses";
import { groupIssuesByStatus } from "@/lib/group-issues-by-status";
import {
  filtersFromView,
  filterWorkspaceIssues,
  visibleViewBarItems,
  type WorkspaceActorKind,
} from "@/lib/issue-view-bar";
import { useColorScheme } from "@/lib/use-color-scheme";
import { THEME } from "@/lib/theme";

const EMPTY_VIEWS: IssueView[] = [];

export function WorkspaceIssuesList() {
  const isFocused = useIsFocused();
  const wsId = useWorkspaceStore((s) => s.currentWorkspaceId);
  const wsSlug = useWorkspaceStore((s) => s.currentWorkspaceSlug);
  const { t } = useT("issues");

  const scope = useIssuesViewStore((s) => s.scope);
  const activeViewId = useIssuesViewStore((s) => s.activeViewId);
  const statusFilters = useIssuesViewStore((s) => s.statusFilters);
  const priorityFilters = useIssuesViewStore((s) => s.priorityFilters);

  const openFilter = () => {
    if (!wsSlug) return;
    router.push({
      pathname: "/[workspace]/issues-filter",
      params: { workspace: wsSlug, scope: "all" },
    });
  };

  useClearFiltersOnWorkspaceChange(
    useIssuesViewStore.getState().resetForWorkspace,
    wsId,
  );

  const issuesQuery = useQuery(issueListOptions(wsId));
  const viewsQuery = useQuery(issueViewListOptions(wsId));
  const prefsQuery = useQuery(issueViewPreferenceOptions(wsId));

  const barReady = viewsQuery.isFetched && prefsQuery.isFetched;
  const views = viewsQuery.data ?? EMPTY_VIEWS;
  const barItems = useMemo(() => {
    const labels: Record<WorkspaceActorKind, string> = {
      all: t("tabs.all"),
      members: t("tabs.members"),
      agents: t("tabs.agents"),
    };
    // Until prefs land, only All is shown — otherwise Members/Agents flash
    // in and then disappear for people who hid them on desktop.
    if (!barReady) {
      return visibleViewBarItems(labels, [], {
        hidden: ["builtin:members", "builtin:agents"],
        order: [],
      });
    }
    return visibleViewBarItems(labels, views, prefsQuery.data?.prefs);
  }, [barReady, views, prefsQuery.data, t]);

  const activeView = activeViewId
    ? (views.find((view) => view.id === activeViewId) ?? null)
    : null;

  useEffect(() => {
    if (!barReady || !activeViewId) return;
    const stillVisible = barItems.some(
      (item) => item.kind === "view" && item.viewId === activeViewId,
    );
    if (!stillVisible) useIssuesViewStore.getState().setActiveViewId(null);
  }, [barReady, activeViewId, barItems]);

  const viewFilter = useMemo(
    () =>
      activeView
        ? filtersFromView(activeView)
        : {
            status: [] as IssueStatus[],
            priority: [] as IssuePriority[],
            actorKind: scope,
          },
    [activeView, scope],
  );

  const catalog = useIssueStatuses();

  const filtered = useMemo(
    () =>
      filterWorkspaceIssues(issuesQuery.data ?? [], {
        actorKind: viewFilter.actorKind,
        viewStatus: viewFilter.status,
        viewPriority: viewFilter.priority,
        manualStatus: statusFilters,
        manualPriority: priorityFilters,
      }),
    [issuesQuery.data, viewFilter, statusFilters, priorityFilters],
  );

  const sections = useMemo(
    () => groupIssuesByStatus(filtered, catalog.statuses),
    [filtered, catalog.statuses],
  );

  const hasManualFilters = statusFilters.length > 0 || priorityFilters.length > 0;
  const viewConstrained =
    !!activeView &&
    (viewFilter.status.length > 0 ||
      viewFilter.priority.length > 0 ||
      viewFilter.actorKind !== "all");

  const showEmptyState = !issuesQuery.isLoading && !issuesQuery.error && filtered.length === 0;
  const emptyMessage =
    hasManualFilters || viewConstrained
      ? t("empty.filtered")
      : t(`empty.${viewFilter.actorKind}`);

  return (
    <View className="flex-1 bg-background">
      <View className="flex-row items-center px-4 pt-2 pb-2 gap-2">
        <View className="flex-1">
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={{ gap: 4, alignItems: "center" }}
          >
            {barItems.map((item) => {
              const active =
                item.kind === "view"
                  ? item.viewId === activeViewId
                  : !activeViewId && item.builtinKey === scope;
              return (
                <Button
                  key={item.barItemId}
                  variant="outline"
                  size="sm"
                  onPress={() => {
                    if (item.kind === "view" && item.viewId) {
                      useIssuesViewStore.getState().selectView(item.viewId);
                    } else if (item.builtinKey) {
                      useIssuesViewStore.getState().selectBuiltin(item.builtinKey);
                    }
                  }}
                  className={active ? "bg-accent max-w-40" : "max-w-40"}
                  accessibilityState={{ selected: active }}
                >
                  <Text
                    numberOfLines={1}
                    className={active ? "text-accent-foreground" : "text-muted-foreground"}
                  >
                    {item.label}
                  </Text>
                </Button>
              );
            })}
          </ScrollView>
        </View>
        <FilterButton onPress={openFilter} hasActiveFilters={hasManualFilters} />
      </View>

      {hasManualFilters ? (
        <ActiveFilterChips
          statusFilters={statusFilters}
          priorityFilters={priorityFilters}
          statusLabelOf={catalog.labelOf}
          onClearStatus={(s) => useIssuesViewStore.getState().toggleStatusFilter(s)}
          onClearPriority={(p) => useIssuesViewStore.getState().togglePriorityFilter(p)}
        />
      ) : null}

      {issuesQuery.isLoading ? (
        <IssuesLoading />
      ) : issuesQuery.error ? (
        <View className="px-4 gap-3 pt-4">
          <Text className="text-sm text-destructive">
            {t("errors.load_failed", {
              message:
                issuesQuery.error instanceof Error ? issuesQuery.error.message : "unknown",
            })}
          </Text>
          <Button variant="outline" onPress={() => issuesQuery.refetch()}>
            <Text>{t("common:actions.retry")}</Text>
          </Button>
        </View>
      ) : showEmptyState ? (
        <EmptyState message={emptyMessage} />
      ) : (
        <SectionList
          sections={sections}
          keyExtractor={(item) => item.id}
          stickySectionHeadersEnabled={false}
          ItemSeparatorComponent={() => <View className="h-px bg-border ml-4" />}
          renderSectionHeader={({ section }) => (
            <SectionHeader status={section.status} count={section.data.length} />
          )}
          contentContainerClassName="pb-6"
          renderItem={({ item }) => (
            <IssueRow
              issue={item}
              onPress={() => {
                if (wsSlug) router.push(`/${wsSlug}/issue/${item.id}`);
              }}
            />
          )}
          refreshing={isFocused && issuesQuery.isRefetching}
          onRefresh={() => {
            void issuesQuery.refetch();
            void viewsQuery.refetch();
            void prefsQuery.refetch();
          }}
        />
      )}
    </View>
  );
}

function FilterButton({
  onPress,
  hasActiveFilters,
}: {
  onPress: () => void;
  hasActiveFilters: boolean;
}) {
  const { t } = useT("issues");
  const { colorScheme } = useColorScheme();
  return (
    <View style={{ position: "relative" }}>
      <Button
        variant="outline"
        size="sm"
        onPress={onPress}
        accessibilityLabel={t("filters.title")}
        className="w-9 px-0"
      >
        <Ionicons
          name="options-outline"
          size={16}
          color={THEME[colorScheme].mutedForeground}
        />
      </Button>
      {hasActiveFilters ? (
        <View
          pointerEvents="none"
          className="absolute top-1 right-1 size-1.5 rounded-full bg-brand"
        />
      ) : null}
    </View>
  );
}

function ActiveFilterChips({
  statusFilters,
  priorityFilters,
  statusLabelOf,
  onClearStatus,
  onClearPriority,
}: {
  statusFilters: IssueStatus[];
  priorityFilters: IssuePriority[];
  statusLabelOf: (statusKey: string) => string;
  onClearStatus: (s: IssueStatus) => void;
  onClearPriority: (p: IssuePriority) => void;
}) {
  const { t } = useT("issues");
  return (
    <View className="flex-row flex-wrap gap-1.5 px-4 pb-2">
      {statusFilters.map((s) => (
        <Chip key={`s-${s}`} label={statusLabelOf(s)} onClear={() => onClearStatus(s)} />
      ))}
      {priorityFilters.map((p) => (
        <Chip key={`p-${p}`} label={t(PRIORITY_LABEL[p])} onClear={() => onClearPriority(p)} />
      ))}
    </View>
  );
}

function Chip({ label, onClear }: { label: string; onClear: () => void }) {
  const { colorScheme } = useColorScheme();
  return (
    <Pressable
      onPress={onClear}
      className="flex-row items-center gap-1 pl-2.5 pr-2 py-1 rounded-full border border-border bg-secondary/40 active:bg-secondary"
    >
      <Text className="text-xs text-foreground">{label}</Text>
      <Ionicons name="close" size={12} color={THEME[colorScheme].mutedForeground} />
    </Pressable>
  );
}

function SectionHeader({ status, count }: { status: IssueStatus; count: number }) {
  const catalog = useIssueStatuses();
  return (
    <View className="flex-row items-center gap-2 px-4 py-2 bg-background">
      <StatusIcon
        status={status}
        category={catalog.categoryOf(status)}
        icon={catalog.iconOf(status)}
        color={catalog.colorOf(status)}
        size={14}
      />
      <Text className="text-xs uppercase tracking-wider text-muted-foreground font-medium">
        {catalog.labelOf(status)}
      </Text>
      <Text className="text-xs text-muted-foreground/60">{count}</Text>
    </View>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <View className="flex-1 items-center justify-center px-6">
      <Text className="text-sm text-muted-foreground text-center">{message}</Text>
    </View>
  );
}
