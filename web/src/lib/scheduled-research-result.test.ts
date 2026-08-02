import { afterEach, describe, expect, it, vi } from "vitest";

import { openScheduledResearchResult } from "./scheduled-research-result";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("openScheduledResearchResult", () => {
  it("navigates directly to the trusted occurrence projection", () => {
    const assign = vi.fn();
    vi.stubGlobal("window", { location: { assign } });
    const resultUrl =
      "https://oxaide.com/api/agents/research-schedules/occurrences/aaaaaaaa-0000-4000-8000-000000000001/result";

    openScheduledResearchResult(resultUrl);

    expect(assign).toHaveBeenCalledOnce();
    expect(assign).toHaveBeenCalledWith(resultUrl);
    expect(assign).not.toHaveBeenCalledWith(
      expect.stringContaining("/api/files/read"),
    );
  });
});