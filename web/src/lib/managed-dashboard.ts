export const OXAIDE_MANAGED_PATHS = new Set([
  "/chat",
  "/sessions",
  "/files",
  "/docs",
]);

export const OXAIDE_RESEARCH_ENGINE_LABEL = "Oxaide Research Engine";

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
): Record<string, T> {
  if (!managed) return routes;
  return Object.fromEntries(
    Object.entries(routes).filter(
      ([path]) => path === "/" || OXAIDE_MANAGED_PATHS.has(path),
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
