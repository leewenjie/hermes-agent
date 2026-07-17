const COMPACTION_PREFIXES = [
  "[CONTEXT COMPACTION — REFERENCE ONLY]",
  "[CONTEXT COMPACTION - REFERENCE ONLY]",
  "[CONTEXT SUMMARY]:",
] as const;

const COMPACTION_END_MARKER =
  "--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---";

export interface CompactionSplit {
  summary: string;
  remainder: string;
}

export function splitCompactionContent(content: string): CompactionSplit | null {
  const head = content.trimStart();
  if (!COMPACTION_PREFIXES.some((prefix) => head.startsWith(prefix))) return null;
  const markerIndex = content.indexOf(COMPACTION_END_MARKER);
  if (markerIndex < 0) return { summary: content, remainder: "" };
  return {
    summary: content.slice(0, markerIndex),
    remainder: content
      .slice(markerIndex + COMPACTION_END_MARKER.length)
      .replace(/^\s+/, ""),
  };
}

export function managedSessionMessageContent(role: string, content: string): string | null {
  if (role !== "user" && role !== "assistant") return null;
  const split = splitCompactionContent(content);
  if (!split) return content;
  return split.remainder || null;
}