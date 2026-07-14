import { describe, expect, it } from "vitest";

import { ptyAttachToken, ptyAttachTokenStorageKey } from "./pty-attach-token";

function memoryStorage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => void values.set(key, value),
  };
}

describe("ptyAttachToken", () => {
  it("reuses a token for reconnects to the same profile and session", () => {
    const storage = memoryStorage();
    let next = 0;
    const mint = () => `token-${++next}`;

    expect(ptyAttachToken(storage, "default\0session-a", false, mint)).toBe("token-1");
    expect(ptyAttachToken(storage, "default\0session-a", false, mint)).toBe("token-1");
  });

  it("uses distinct tokens for different resume targets and profiles", () => {
    const storage = memoryStorage();
    let next = 0;
    const mint = () => `token-${++next}`;

    const sessionA = ptyAttachToken(storage, "default\0session-a", false, mint);
    const sessionB = ptyAttachToken(storage, "default\0session-b", false, mint);
    const profileB = ptyAttachToken(storage, "research\0session-a", false, mint);

    expect(new Set([sessionA, sessionB, profileB]).size).toBe(3);
  });

  it("rotates the token for an explicit new chat within the same scope", () => {
    const storage = memoryStorage();
    let next = 0;
    const mint = () => `token-${++next}`;

    expect(ptyAttachToken(storage, "default\0", false, mint)).toBe("token-1");
    expect(ptyAttachToken(storage, "default\0", true, mint)).toBe("token-2");
    expect(ptyAttachToken(storage, "default\0", false, mint)).toBe("token-2");
  });

  it("creates storage-safe keys for full session scopes", () => {
    expect(ptyAttachTokenStorageKey("default\0session/with spaces")).toBe(
      "hermes.pty.token.chat.default%00session%2Fwith%20spaces",
    );
  });
});
