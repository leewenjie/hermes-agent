export const OXAIDE_MANAGED_PATHS = new Set([
  "/chat",
  "/sessions",
  "/files",
  "/scheduled-research",
  "/skills",
  "/docs",
]);

export function getOxaideManagedPaths(
  scheduledResearchEnabled: boolean,
): Set<string> {
  if (scheduledResearchEnabled) return OXAIDE_MANAGED_PATHS;
  return new Set(
    [...OXAIDE_MANAGED_PATHS].filter((path) => path !== "/scheduled-research"),
  );
}

export const OXAIDE_RESEARCH_ENGINE_LABEL = "Oxaide Research Engine";

export const OXAIDE_RESEARCH_SKILLS = new Set([
  "investment-research",
  "market-return-analysis",
  "polymarket",
  "stocks",
]);

export interface OxaideMarketCoverage {
  description: string;
  id: string;
  name: string;
  searchTerms: string[];
}

export const OXAIDE_MARKET_COVERAGE: OxaideMarketCoverage[] = [
  {
    id: "equities",
    name: "Equities & ETFs",
    description: "Company, sector, index-proxy and fund research with comparable return windows.",
    searchTerms: ["stocks", "shares", "public markets", "index", "funds"],
  },
  {
    id: "ficc",
    name: "Rates & credit (FICC)",
    description: "Fixed-income, yield-curve, sovereign and credit questions using available evidence.",
    searchTerms: ["bonds", "fixed income", "rates", "yields", "sovereign", "credit"],
  },
  {
    id: "fx-macro",
    name: "FX & macro",
    description: "Currencies, central banks, inflation, growth and cross-asset regime context.",
    searchTerms: ["forex", "currencies", "economics", "central bank", "inflation", "growth"],
  },
  {
    id: "commodities",
    name: "Commodities",
    description: "Energy, metals and agricultural market context from public and supplied data.",
    searchTerms: ["oil", "gas", "gold", "silver", "metals", "agriculture"],
  },
  {
    id: "crypto",
    name: "Crypto",
    description: "Spot, perpetuals, funding, liquidity and on-chain evidence when sources allow.",
    searchTerms: ["digital assets", "bitcoin", "ethereum", "perps", "blockchain", "on-chain"],
  },
  {
    id: "prediction-markets",
    name: "Prediction markets",
    description: "Event probabilities, price history, volume and order-book context from public markets.",
    searchTerms: ["polymarket", "odds", "probability", "events", "forecasting"],
  },
];

export function filterOxaideMarketCoverage(
  query: string,
): OxaideMarketCoverage[] {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return OXAIDE_MARKET_COVERAGE;
  return OXAIDE_MARKET_COVERAGE.filter((market) =>
    [market.name, market.description, ...market.searchTerms].some((value) =>
      value.toLowerCase().includes(normalized),
    ),
  );
}

export function filterOxaideResearchSkills<T extends { name: string }>(
  skills: T[],
  managed: boolean,
): T[] {
  if (!managed) return skills;
  return skills.filter((skill) => OXAIDE_RESEARCH_SKILLS.has(skill.name));
}

export function customerRuntimeLabel(
  rawModel: string | null | undefined,
  managed: boolean,
  unknown = "Unknown",
): string {
  if (managed) return OXAIDE_RESEARCH_ENGINE_LABEL;
  return (rawModel || unknown).split("/").pop() || unknown;
}

export function filterOxaideManagedRoutes<T>(
  routes: Record<string, T>,
  managed: boolean,
  scheduledResearchEnabled = true,
): Record<string, T> {
  if (!managed) return routes;
  const managedPaths = getOxaideManagedPaths(scheduledResearchEnabled);
  return Object.fromEntries(
    Object.entries(routes).filter(
      ([path]) => path === "/" || managedPaths.has(path),
    ),
  );
}

export function isOxaideManagedDashboard(
  injected?: { product?: string },
): boolean {
  const branding =
    injected ??
    (typeof window !== "undefined"
      ? window.__HERMES_DASHBOARD_BRANDING__
      : undefined);
  return branding?.product?.trim() === "oxaide";
}
