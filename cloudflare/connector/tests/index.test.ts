import { describe, it, expect } from "vitest";
import { safeString, isoNow, hexToBytes } from "../src/platforms/types";

describe("Platform types", () => {
  it("safeString returns strings", () => {
    expect(safeString("hello")).toBe("hello");
    expect(safeString(42)).toBe("42");
    expect(safeString(undefined)).toBe("");
    expect(safeString(null)).toBe("");
    expect(safeString(undefined, "fallback")).toBe("fallback");
  });

  it("isoNow returns a timestamp", () => {
    const ts = isoNow();
    expect(typeof ts).toBe("number");
    expect(ts).toBeGreaterThan(1700000000); // After 2023
  });

  it("hexToBytes works", () => {
    const bytes = hexToBytes("0a0b");
    expect(bytes).toEqual(new Uint8Array([10, 11]));
    expect(hexToBytes("ff")).toEqual(new Uint8Array([255]));
  });
});

describe("Message routing", () => {
  it("builds correct DO key from platform+botId", () => {
    const platform = "telegram";
    const botId = "12345:abc";
    const key = `${platform}:${botId}`;
    expect(key).toBe("telegram:12345:abc");
  });
});
