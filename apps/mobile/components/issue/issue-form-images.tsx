import { View } from "react-native";
import { IconButton } from "@/components/ui/icon-button";
import { ComposerAttachmentRow } from "./composer-attachment-row";
import type { useIssueFormImages } from "./use-issue-form-images";
import { useT } from "@/lib/i18n";

export function IssueFormImages({ images, disabled }: {
  images: ReturnType<typeof useIssueFormImages>;
  disabled: boolean;
}) {
  const { t } = useT("chat");
  return (
    <View className="gap-2">
      {images.attachments.length > 0 ? (
        <ComposerAttachmentRow
          mentions={[]}
          attachments={images.attachments}
          onRemoveMention={() => {}}
          onRemoveAttachment={images.removeImage}
          onRetryAttachment={images.retryImage}
        />
      ) : null}
      <IconButton
        name="image-outline"
        iconSize={20}
        onPress={images.pickImage}
        disabled={disabled || images.uploading}
        accessibilityLabel={t("composer.upload_image")}
        className="h-8 w-8"
      />
    </View>
  );
}
