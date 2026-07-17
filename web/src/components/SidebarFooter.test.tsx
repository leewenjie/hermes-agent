import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { StatusResponse } from "@/lib/api";
import type { DashboardBranding } from "@/lib/branding";
import { SidebarFooter } from "./SidebarFooter";

const oxaideBranding: DashboardBranding = {
  product: "oxaide",
  name: "Oxaide",
  shortName: "OXAIDE",
  orgName: "Oxaide",
  orgUrl: "https://oxaide.com",
  accountUrl: "https://oxaide.com/app",
  billingUrl: "https://oxaide.com/console/billing",
  docsUrl: "https://oxaide.com/docs",
};

const status: StatusResponse = {
  active_sessions: 0,
  config_path: "",
  config_version: 1,
  env_path: "",
  gateway_exit_reason: null,
  gateway_health_url: null,
  gateway_pid: null,
  gateway_platforms: {},
  gateway_running: false,
  gateway_state: null,
  gateway_updated_at: null,
  hermes_home: "",
  latest_config_version: 1,
  release_date: "",
  version: "0.18.2",
};

describe("SidebarFooter", () => {
  it("keeps account actions but omits the redundant Oxaide organization link", () => {
    const html = renderToStaticMarkup(
      <SidebarFooter branding={oxaideBranding} status={null} />,
    );

    expect(html).toContain("Account");
    expect(html).toContain("Billing");
    expect(html).toContain("Sign out");
    expect(html).not.toContain('href="https://oxaide.com"');
  });

  it("keeps version and organization attribution for standard Hermes dashboards", () => {
    const html = renderToStaticMarkup(
      <SidebarFooter
        branding={{
          ...oxaideBranding,
          product: "hermes",
          name: "Hermes Agent",
          orgName: "Nous Research",
          orgUrl: "https://nousresearch.com",
          accountUrl: "",
          billingUrl: "",
        }}
        status={status}
      />,
    );

    expect(html).toContain("v0.18.2");
    expect(html).toContain("Nous Research");
    expect(html).toContain('href="https://nousresearch.com"');
  });
});
