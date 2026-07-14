const PTY_ATTACH_TOKEN_PREFIX = "hermes.pty.token.chat";

interface TokenStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

function mintAttachToken(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function ptyAttachTokenStorageKey(scope: string): string {
  return `${PTY_ATTACH_TOKEN_PREFIX}.${encodeURIComponent(scope || "fresh")}`;
}

export function ptyAttachToken(
  storage: TokenStorage,
  scope: string,
  rotate = false,
  mint: () => string = mintAttachToken,
): string {
  const key = ptyAttachTokenStorageKey(scope);
  let token = "";

  if (!rotate) {
    try {
      token = storage.getItem(key) ?? "";
    } catch {
      // Private mode or blocked storage: mint an in-memory token below.
    }
  }

  if (!token) {
    token = mint();
    try {
      storage.setItem(key, token);
    } catch {
      // Reconnect persistence is best-effort when storage is unavailable.
    }
  }

  return token;
}
