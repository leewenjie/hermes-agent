export interface DashboardBranding {
  product: string;
  name: string;
  shortName: string;
  orgName: string;
  orgUrl: string;
  accountUrl: string;
  billingUrl: string;
  docsUrl: string;
}

declare global {
  interface Window {
    __HERMES_DASHBOARD_BRANDING__?: {
      product?: string;
      name?: string;
      short_name?: string;
      org_name?: string;
      org_url?: string;
      account_url?: string;
      billing_url?: string;
      docs_url?: string;
    };
  }
}

interface BrandingFallbacks {
  name: string;
  shortName: string;
  orgName: string;
  orgUrl?: string;
  docsUrl?: string;
}

export function getDashboardBranding(fallbacks: BrandingFallbacks): DashboardBranding {
  const injected =
    typeof window !== "undefined"
      ? window.__HERMES_DASHBOARD_BRANDING__ ?? {}
      : {};
  return {
    product: injected.product?.trim() || "hermes",
    name: injected.name?.trim() || fallbacks.name,
    shortName: injected.short_name?.trim() || fallbacks.shortName,
    orgName: injected.org_name?.trim() || fallbacks.orgName,
    orgUrl: injected.org_url?.trim() || fallbacks.orgUrl || "https://nousresearch.com",
    accountUrl: injected.account_url?.trim() || "",
    billingUrl: injected.billing_url?.trim() || "",
    docsUrl:
      injected.docs_url?.trim() ||
      fallbacks.docsUrl ||
      "https://hermes-agent.nousresearch.com/docs/",
  };
}

export function brandingLines(name: string): [string, string?] {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length <= 1) return [name];
  return [words[0], words.slice(1).join(" ")];
}
