export type ManagedFilePreviewKind =
  | "image"
  | "pdf"
  | "html"
  | "markdown"
  | "text"
  | "unsupported";

export function managedFilePreviewPath(search: string): string | null {
  const path = new URLSearchParams(search).get("preview")?.trim();
  return path || null;
}

export function managedFilePreviewKind(
  mimeType: string | null | undefined,
  name: string,
): ManagedFilePreviewKind {
  const mime = (mimeType || "").toLowerCase();
  const extension = name.toLowerCase().split(".").pop() || "";

  if (mime.startsWith("image/") || extension === "svg") return "image";
  if (mime === "application/pdf" || extension === "pdf") return "pdf";
  if (mime === "text/html" || ["html", "htm"].includes(extension)) return "html";
  if (
    ["text/markdown", "text/x-markdown"].includes(mime) ||
    ["md", "markdown"].includes(extension)
  ) {
    return "markdown";
  }
  if (
    mime.startsWith("text/") ||
    [
      "csv",
      "json",
      "jsonl",
      "ndjson",
      "txt",
      "log",
      "yaml",
      "yml",
      "toml",
      "xml",
      "py",
      "r",
      "sql",
    ].includes(extension)
  ) {
    return "text";
  }
  return "unsupported";
}
