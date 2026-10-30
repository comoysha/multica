export function descriptionWithImages(description: string, urls: string[]): string {
  const body = description.trim();
  const images = urls.map((url) => `![](${url})`).join("\n\n");
  return [body, images].filter(Boolean).join("\n\n");
}
