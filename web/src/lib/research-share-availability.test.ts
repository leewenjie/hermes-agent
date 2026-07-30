import { describe, expect, it } from "vitest";

import { canShareResearchSession } from "@/lib/research-share-availability";

describe("research session sharing availability", () => {
  it("offers sharing for every populated session when the runtime enables it", () => {
    expect(canShareResearchSession({ message_count: 1 }, true)).toBe(true);
    expect(canShareResearchSession({ message_count: 42 }, true)).toBe(true);
  });

  it("does not offer sharing for empty sessions or disabled runtimes", () => {
    expect(canShareResearchSession({ message_count: 0 }, true)).toBe(false);
    expect(canShareResearchSession({ message_count: 1 }, false)).toBe(false);
  });
});
