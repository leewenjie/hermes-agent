import { describe, expect, it } from "vitest";

import {
  customerRuntimeLabel,
  filterOxaideMarketCoverage,
  filterOxaideResearchSkills,
  filterOxaideManagedRoutes,
  getOxaideManagedPaths,
  isOxaideManagedDashboard,
  OXAIDE_MANAGED_PATHS,
  OXAIDE_RESEARCH_ENGINE_LABEL,
  OXAIDE_RESEARCH_SKILLS,
} from "./managed-dashboard";

describe("managed Oxaide dashboard policy", () => {
  it("keeps only customer research routes", () => {
    const routes = {
      "/": "root",
      "/chat": "chat",
      "/sessions": "sessions",
      "/files": "files",
      "/scheduled-research": "scheduled research",
      "/docs": "docs",
      "/skills": "skills",
      "/models": "models",
      "/plugins": "plugins",
      "/config": "config",
    };

    expect(Object.keys(filterOxaideManagedRoutes(routes, true))).toEqual([
      "/",
      "/chat",
      "/sessions",
      "/files",
      "/scheduled-research",
      "/docs",
      "/skills",
    ]);
    expect(filterOxaideManagedRoutes(routes, false)).toBe(routes);
  });

  it("exposes skills without exposing model routes", () => {
    expect(OXAIDE_MANAGED_PATHS.has("/skills")).toBe(true);
    expect(OXAIDE_MANAGED_PATHS.has("/models")).toBe(false);
    expect(OXAIDE_MANAGED_PATHS.has("/cron")).toBe(false);
    expect(OXAIDE_MANAGED_PATHS.has("/scheduled-research")).toBe(true);
    expect(OXAIDE_MANAGED_PATHS.has("/chat")).toBe(true);
  });

  it("removes scheduled research when the runtime capability is disabled", () => {
    const routes = {
      "/": "root",
      "/sessions": "sessions",
      "/scheduled-research": "scheduled research",
    };

    expect(Object.keys(filterOxaideManagedRoutes(routes, true, false))).toEqual([
      "/",
      "/sessions",
    ]);
    expect(getOxaideManagedPaths(false).has("/scheduled-research")).toBe(false);
    expect(getOxaideManagedPaths(true).has("/scheduled-research")).toBe(true);
  });

  it("keeps only the managed research skill bundle", () => {
    const skills = [
      { name: "stocks" },
      { name: "investment-research" },
      { name: "market-return-analysis" },
      { name: "polymarket" },
      { name: "github" },
      { name: "skill-creator" },
    ];

    expect(filterOxaideResearchSkills(skills, true).map((skill) => skill.name)).toEqual([
      "stocks",
      "investment-research",
      "market-return-analysis",
      "polymarket",
    ]);
    expect(filterOxaideResearchSkills(skills, false)).toBe(skills);
    expect(OXAIDE_RESEARCH_SKILLS.has("github")).toBe(false);
  });

  it("finds multi-asset coverage using customer market language", () => {
    expect(filterOxaideMarketCoverage("FICC").map((market) => market.id)).toEqual([
      "ficc",
    ]);
    expect(filterOxaideMarketCoverage("bonds").map((market) => market.id)).toEqual([
      "ficc",
    ]);
    expect(filterOxaideMarketCoverage("bitcoin").map((market) => market.id)).toEqual([
      "crypto",
    ]);
    expect(filterOxaideMarketCoverage("crypto").map((market) => market.id)).toEqual([
      "crypto",
    ]);
    expect(filterOxaideMarketCoverage("prediction").map((market) => market.id)).toEqual([
      "prediction-markets",
    ]);
    expect(filterOxaideMarketCoverage("")).toHaveLength(6);
  });

  it("detects the injected Oxaide product shell", () => {
    expect(isOxaideManagedDashboard({ product: "oxaide" })).toBe(true);
    expect(isOxaideManagedDashboard({ product: "hermes" })).toBe(false);
    expect(isOxaideManagedDashboard()).toBe(false);
  });

  it("keeps raw model identity out of the managed customer shell", () => {
    expect(customerRuntimeLabel("azure-foundry/gpt-5.6-luna", true)).toBe(
      OXAIDE_RESEARCH_ENGINE_LABEL,
    );
    expect(customerRuntimeLabel("azure-foundry/gpt-5.6-luna", false)).toBe(
      "gpt-5.6-luna",
    );
  });
});
