import type { ManagedFileEntry, ManagedFilesResponse } from "@/lib/api";

export function researchFileReference(
  entry: ManagedFileEntry,
  listing: Pick<ManagedFilesResponse, "locked_root" | "root"> | null,
): string {
  const root = (listing?.locked_root ?? listing?.root ?? "").replace(/[\\/]+$/, "");
  const path = entry.path.trim();
  const relativePath = root && (path.startsWith(`${root}/`) || path.startsWith(`${root}\\`))
    ? path.slice(root.length + 1)
    : entry.name;

  return `Use the research file "${relativePath}" in this request.`;
}