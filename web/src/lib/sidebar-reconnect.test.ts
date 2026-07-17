import { describe, expect, it } from "vitest";

import {
  shouldRetrySidebarSocket,
  sidebarReconnectDelayMs,
} from "./sidebar-reconnect";

describe("sidebar reconnect policy", () => {
  it("uses the same short capped backoff as dashboard chat", () => {
    expect([1, 2, 3, 4, 5, 8].map(sidebarReconnectDelayMs)).toEqual([
      250, 500, 1000, 2000, 3000, 3000,
    ]);
  });

  it("retries transient transport drops but not terminal auth failures", () => {
    expect(shouldRetrySidebarSocket(1006)).toBe(true);
    expect(shouldRetrySidebarSocket(1011)).toBe(true);
    expect(shouldRetrySidebarSocket(1000)).toBe(false);
    expect(shouldRetrySidebarSocket(4401)).toBe(false);
    expect(shouldRetrySidebarSocket(4403)).toBe(false);
  });
});