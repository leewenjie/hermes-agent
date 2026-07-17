import { describe, expect, it } from "vitest";

import {
  customerRuntimeLabel,
  filterOxaideManagedRoutes,
  isOxaideManagedDashboard,
  OXAIDE_MANAGED_PATHS,
  OXAIDE_RESEARCH_ENGINE_LABEL,
} from "./managed-dashboard";

describe("managed Oxaide dashboard policy", () => {
  it("keeps only customer research routes", () => {
    const routes = {
      "/": "root",
      "/chat": "chat",
      "/sessions": "sessions",
      "/files": "files",
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
      "/docs",
      "/skills",
    ]);
    expect(filterOxaideManagedRoutes(routes, false)).toBe(routes);
  });

  it("exposes skills without exposing model routes", () => {
    expect(OXAIDE_MANAGED_PATHS.has("/skills")).toBe(true);
    expect(OXAIDE_MANAGED_PATHS.has("/models")).toBe(false);
    expect(OXAIDE_MANAGED_PATHS.has("/chat")).toBe(true);
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
