import { useCallback, useState } from "react";
import { Alert } from "react-native";
import * as ImagePicker from "expo-image-picker";
import { attachmentDownloadPath } from "@multica/core/types/attachment-url";
import { api, MAX_FILE_SIZE } from "@/data/api";
import { useT } from "@/lib/i18n";
import type { ComposerAttachmentItem } from "./composer-attachment-row";

export function useIssueFormImages() {
  const { t } = useT("chat");
  const [attachments, setAttachments] = useState<ComposerAttachmentItem[]>([]);
  const uploading = attachments.some((item) => item.status === "uploading");

  const upload = useCallback(async (localId: string, asset: { uri: string; name: string; type: string }) => {
    try {
      // Leave the upload unbound until the issue create/update succeeds.
      const result = await api.uploadFile(asset);
      setAttachments((items) => items.map((item) => item.localId === localId
        ? {
            ...item,
            status: "completed",
            id: result.id,
            url: result.markdown_url || attachmentDownloadPath(result.id),
            downloadUrl: result.download_url,
          }
        : item));
    } catch (err) {
      const message = err instanceof Error ? err.message : t("common:states.error");
      setAttachments((items) => items.map((item) => item.localId === localId
        ? { ...item, status: "failed", error: message }
        : item));
      Alert.alert(t("common:states.error"), message);
    }
  }, [t]);

  const pickImage = useCallback(async () => {
    if (uploading) return;
    const picker = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      quality: 1,
    });
    if (picker.canceled) return;
    const picked = picker.assets[0];
    if (!picked) return;
    if (picked.fileSize != null && picked.fileSize > MAX_FILE_SIZE) {
      Alert.alert(t("composer.file_too_large_title"), t("composer.file_too_large_message"));
      return;
    }
    const filename = picked.fileName ?? `image-${Date.now()}.jpg`;
    const mimeType = picked.mimeType ?? "image/jpeg";
    const localId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    setAttachments((items) => [...items, {
      localId,
      localUri: picked.uri,
      filename,
      mimeType,
      status: "uploading",
    }]);
    await upload(localId, { uri: picked.uri, name: filename, type: mimeType });
  }, [upload, uploading, t]);

  const removeImage = useCallback((localId: string) => {
    setAttachments((items) => items.filter((item) => item.localId !== localId));
  }, []);

  const retryImage = useCallback((localId: string) => {
    const item = attachments.find((candidate) => candidate.localId === localId);
    if (!item || uploading) return;
    setAttachments((items) => items.map((candidate) => candidate.localId === localId
      ? { ...candidate, status: "uploading", error: undefined }
      : candidate));
    void upload(localId, { uri: item.localUri, name: item.filename, type: item.mimeType });
  }, [attachments, upload, uploading]);

  const completed = attachments.filter((item): item is ComposerAttachmentItem & { id: string; url: string } =>
    item.status === "completed" && !!item.id && !!item.url);

  return { attachments, completed, uploading, pickImage, removeImage, retryImage };
}
