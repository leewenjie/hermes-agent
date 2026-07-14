import { describe, expect, it } from "vitest";

import { brandingLines, getDashboardBranding } from "./branding";

const fallback = {
  name: "Hermes Agent",
  shortName: "HA",
  orgName: "Nous Research",
};

describe("dashboard branding", () => {
  it("keeps generic Hermes defaults without an injected product shell", () => {
    expect(getDashboardBranding(fallback)).toMatchObject({
      product: "hermes",
      name: "Hermes Agent",
      orgName: "Nous Research",
      accountUrl: "",
      billingUrl: "",
    });
  });

  it("uses the injected Oxaide product shell and account links", () => {
    const originalWindow = globalThis.window;
    Object.defineProperty(globalThis, "window", {
      configurable: true,
      value: {
        __HERMES_DASHBOARD_BRANDING__: {
          product: "oxaide",
          name: "Oxaide Research",
          short_name: "Oxaide",
          org_name: "Oxaide",
          org_url: "https://oxaide.com",
          account_url: "https://oxaide.com/app",
          billing_url: "https://oxaide.com/console/billing",
          docs_url: "https://oxaide.com/docs",
        },
      },
    });

    expect(getDashboardBranding(fallback)).toEqual({
      product: "oxaide",
      name: "Oxaide Research",
      shortName: "Oxaide",
      orgName: "Oxaide",
      orgUrl: "https://oxaide.com",
      accountUrl: "https://oxaide.com/app",
      billingUrl: "https://oxaide.com/console/billing",
      docsUrl: "https://oxaide.com/docs",
    });
    Object.defineProperty(globalThis, "window", {
      configurable: true,
      value: originalWindow,
    });
  });

  it("splits two-part product names for the sidebar wordmark", () => {
    expect(brandingLines("Oxaide Research")).toEqual(["Oxaide", "Research"]);
    expect(brandingLines("Oxaide")).toEqual(["Oxaide"]);
  });
});
