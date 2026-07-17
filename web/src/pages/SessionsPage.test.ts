import { describe, expect, it } from "vitest";

import { managedSessionMessageContent } from "@/lib/managed-session-message";

const MARKER =
  "--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---";

describe("managed session message presentation", () => {
  it("hides standalone compaction handoffs and internal roles", () => {
    expect(managedSessionMessageContent("assistant", "[CONTEXT SUMMARY]: private handoff")).toBeNull();
    expect(managedSessionMessageContent("system", "private system prompt")).toBeNull();
    expect(managedSessionMessageContent("tool", "private tool output")).toBeNull();
  });

  it("preserves the customer answer after a merged compaction handoff", () => {
    expect(
      managedSessionMessageContent(
        "assistant",
        `[CONTEXT SUMMARY]: private handoff\n${MARKER}\nCustomer-visible answer`,
      ),
    ).toBe("Customer-visible answer");
  });
});