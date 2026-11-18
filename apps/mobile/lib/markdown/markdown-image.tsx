/**
 * Block-level image with real aspect ratio + tap-to-lightbox.
 *
 *   - Aspect ratio detection uses RN's `Image.getSize` (cross-platform,
 *     network-friendly). While dimensions resolve we lay out at 16:9 as
 *     a placeholder — same width-100% so the surrounding flow is stable
 *     and only the height shifts once the real ratio lands.
 *   - Rendering uses `expo-image` for on-disk caching + smooth fade-in
 *     transition.
 *   - Tap dispatches into the global LightboxProvider for fullscreen
 *     viewing with pinch-zoom + swipe-down-to-dismiss.
 *
 * Stable attachment URLs are resolved against the API origin and loaded
 * with the mobile session when required.
 *
 * Cancellation: a content re-render that swaps the URI must not let the
 * previous getSize callback overwrite state — guard with a `cancelled`
 * flag in the cleanup path.
 */
import { useEffect, useMemo, useState } from "react";
import { Image as RNImage, Pressable, View } from "react-native";
import { Image as ExpoImage } from "expo-image";
import { api } from "@/data/api";
import type { Attachment } from "@multica/core/types";
import { useLightbox } from "./lightbox-provider";
import { useImageSequence } from "./image-sequence";
import { imageRequestHeaders, resolveImageUri } from "./resolve-image-uri";

interface Props {
  uri: string;
  alt?: string;
  attachments?: Attachment[];
}

export function MarkdownImage({ uri, attachments }: Props) {
  const { open } = useLightbox();
  const sequence = useImageSequence();
  const [aspect, setAspect] = useState<number | null>(null);

  const resolvedUri = useMemo(() => resolveImageUri(uri, attachments), [uri, attachments]);

  const token = api.getToken();
  const headers = useMemo(() => imageRequestHeaders(resolvedUri, token), [resolvedUri, token]);

  useEffect(() => {
    let cancelled = false;
    const onSize = (width: number, height: number) => {
      if (!cancelled && width && height) setAspect(width / height);
    };
    const onError = () => {
      if (!cancelled) setAspect(16 / 9);
    };
    if (headers) RNImage.getSizeWithHeaders(resolvedUri, headers, onSize, onError);
    else RNImage.getSize(resolvedUri, onSize, onError);
    return () => {
      cancelled = true;
    };
  }, [resolvedUri, headers]);

  return (
    <Pressable onPress={() => open(resolvedUri, sequence)}>
      <View className="rounded-lg overflow-hidden bg-muted">
        <ExpoImage
          source={{ uri: resolvedUri, headers }}
          cachePolicy={headers ? "none" : "disk"}
          style={{ width: "100%", aspectRatio: aspect ?? 16 / 9 }}
          contentFit="contain"
          transition={150}
        />
      </View>
    </Pressable>
  );
}
