import { describe, expect, it } from "vitest";

import {
  capabilityInfoFromSessionCreate,
  chatSessionIdentityFromInfo,
  emptyResearchTrace,
  generatedImageFromToolResult,
  isBrowserImageSource,
  reduceResearchTrace,
  researchMethodDescription,
  researchMethodLabel,
  summarizeResearchCapabilities,
  summarizeResearchTools,
} from "./chat-sidebar-events";

describe("chat sidebar event helpers", () => {
  it("extracts the real PTY session identity for result sharing", () => {
    expect(
      chatSessionIdentityFromInfo(" session-123 ", {
        title: " Market breadth review ",
      }),
    ).toEqual({ id: "session-123", title: "Market breadth review" });
    expect(chatSessionIdentityFromInfo("", { title: "Missing id" })).toBeNull();
  });

  it("hydrates the capability preview returned by session.create", () => {
    expect(
      capabilityInfoFromSessionCreate({
        session_id: "sidecar",
        info: {
          capability_preview: true,
          preloaded_skills: ["market-research", "risk-review"],
          tools: { research: ["web_search"] },
        },
      }),
    ).toEqual({
      capability_preview: true,
      preloaded_skills: ["market-research", "risk-review"],
      tools: { research: ["web_search"] },
    });
  });

  it("prefers a host-visible generated image", () => {
    expect(
      generatedImageFromToolResult({
        success: true,
        image: "/sandbox/generated.png",
        host_image: "/home/user/.hermes/cache/generated.png",
      }),
    ).toBe("/home/user/.hermes/cache/generated.png");
  });

  it("accepts serialized results and rejects failed generation", () => {
    expect(
      generatedImageFromToolResult(
        JSON.stringify({ success: true, image: "https://cdn.test/chart.png" }),
      ),
    ).toBe("https://cdn.test/chart.png");
    expect(
      generatedImageFromToolResult({ success: false, image: "https://bad.test/x.png" }),
    ).toBeNull();
  });

  it("only sends safe browser-native sources directly to img", () => {
    expect(isBrowserImageSource("https://cdn.test/chart.png")).toBe(true);
    expect(isBrowserImageSource("data:image/png;base64,AAAA")).toBe(true);
    expect(isBrowserImageSource("http://cdn.test/chart.png")).toBe(false);
    expect(isBrowserImageSource("/home/user/chart.png")).toBe(false);
  });

  it("presents research methods in customer language", () => {
    expect(researchMethodLabel("investment-research")).toBe(
      "Thesis and evidence review",
    );
    expect(researchMethodLabel("market-return-analysis")).toBe(
      "Return and risk analysis",
    );
    expect(researchMethodLabel("stocks")).toBe(
      "Market data and quote provenance",
    );
    expect(researchMethodLabel("polymarket")).toBe(
      "Prediction-market research",
    );
    expect(researchMethodDescription("stocks")).toContain("timestamped quotes");
    expect(researchMethodDescription("polymarket")).toContain("event probabilities");
  });

  it("groups internal toolsets into useful customer capabilities", () => {
    expect(
      summarizeResearchCapabilities({
        web: ["web_search"],
        terminal: ["terminal"],
        file: ["read_file"],
        memory: ["memory"],
        session_search: ["session_search"],
        clarify: ["clarify"],
        delegation: ["delegate_task"],
        todo: ["todo"],
        vision: ["vision_analyze"],
      }).map((capability) => capability.label),
    ).toEqual([
      "Evidence and sources",
      "Calculations and scripts",
      "Files and artifacts",
      "Saved research context",
      "Research coordination",
      "Chart and image review",
    ]);
  });

  it("tracks only observed calls within the latest answer", () => {
    let trace = reduceResearchTrace(emptyResearchTrace(), "message.start", {});
    trace = reduceResearchTrace(trace, "tool.start", {
      context: "SPY QQQ adjusted close",
      name: "web_search",
      tool_id: "search-1",
    });
    trace = reduceResearchTrace(trace, "tool.complete", {
      args: { query: "SPY QQQ adjusted close" },
      name: "web_search",
      summary: "Found 5 results",
      tool_id: "search-1",
    });
    trace = reduceResearchTrace(trace, "message.complete", { status: "complete" });

    expect(trace.phase).toBe("complete");
    expect(trace.tools).toEqual([
      expect.objectContaining({
        detail: "Found 5 results",
        name: "web_search",
        status: "complete",
      }),
    ]);
    expect(summarizeResearchTools(trace.tools)).toEqual([
      expect.objectContaining({
        count: 1,
        label: "Source search",
        running: false,
      }),
    ]);
  });

  it("aggregates repeated tools and records only explicitly opened methods", () => {
    let trace = reduceResearchTrace(emptyResearchTrace(), "message.start", {});
    trace = reduceResearchTrace(trace, "tool.complete", {
      args: { skill_name: "market-return-analysis" },
      name: "skill_view",
      tool_id: "skill-1",
    });
    trace = reduceResearchTrace(trace, "tool.complete", {
      name: "terminal",
      summary: "Calculated adjusted-return distribution",
      tool_id: "terminal-1",
    });
    trace = reduceResearchTrace(trace, "tool.complete", {
      name: "terminal",
      summary: "Generated comparison artifacts",
      tool_id: "terminal-2",
    });

    expect(trace.openedSkills).toEqual(["market-return-analysis"]);
    expect(summarizeResearchTools(trace.tools)).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ count: 1, label: "Research method" }),
        expect.objectContaining({
          count: 2,
          detail: "Generated comparison artifacts",
          label: "Data analysis",
        }),
      ]),
    );
  });

  it("clears the previous answer trace on the next message start", () => {
    const completed = reduceResearchTrace(
      reduceResearchTrace(emptyResearchTrace(), "tool.complete", {
        name: "web_extract",
        tool_id: "extract-1",
      }),
      "message.complete",
      {},
    );

    expect(reduceResearchTrace(completed, "message.start", {})).toEqual({
      openedSkills: [],
      phase: "running",
      tools: [],
    });
  });
});