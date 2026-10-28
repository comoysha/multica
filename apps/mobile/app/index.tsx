import { ActivityIndicator, View } from "react-native";
import { Redirect } from "expo-router";
import { Button } from "@/components/ui/button";
import { Text } from "@/components/ui/text";
import { useAuthStore } from "@/data/auth-store";
import { localAuthToken } from "@/data/local-auth";
import { useWorkspaceStore } from "@/data/workspace-store";
import { useT } from "@/lib/i18n";

/**
 * Entry redirect. AuthInitializer (in _layout.tsx) finishes auth + slug
 * hydration before this renders meaningfully — until then, isLoading is true.
 *
 *   no user            → /login
 *   user, no slug      → /select-workspace
 *   user, slug         → /[slug]/inbox
 */
export default function Index() {
  const user = useAuthStore((s) => s.user);
  const isLoading = useAuthStore((s) => s.isLoading);
  const initialize = useAuthStore((s) => s.initialize);
  const slug = useWorkspaceStore((s) => s.currentWorkspaceSlug);
  const { t } = useT("auth");

  if (isLoading) {
    return (
      <View className="flex-1 items-center justify-center bg-background">
        <ActivityIndicator />
      </View>
    );
  }

  if (!user) {
    if (!localAuthToken) return <Redirect href="/login" />;
    return (
      <View className="flex-1 items-center justify-center gap-4 bg-background px-6">
        <Text className="text-center">{t("local_auth_unavailable")}</Text>
        <Button onPress={() => void initialize()}>
          <Text>{t("retry")}</Text>
        </Button>
      </View>
    );
  }
  if (!slug) return <Redirect href="/select-workspace" />;
  return <Redirect href={`/${slug}/inbox`} />;
}
