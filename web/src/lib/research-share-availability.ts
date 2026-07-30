import type { SessionInfo } from "@/lib/api";

/** Public snapshots are available only when the runtime enables sharing and
 * the session contains a transcript worth publishing. */
export function canShareResearchSession(
  session: Pick<SessionInfo, "message_count">,
  sharingEnabled: boolean,
): boolean {
  return sharingEnabled && session.message_count > 0;
}
