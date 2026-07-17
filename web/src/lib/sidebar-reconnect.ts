const NON_RETRYABLE_CLOSE_CODES = new Set([1000, 4401, 4403, 4404, 4408]);

export function sidebarReconnectDelayMs(attempt: number): number {
  const boundedAttempt = Math.max(1, Math.min(Math.floor(attempt), 5));
  return Math.min(250 * 2 ** (boundedAttempt - 1), 3000);
}

export function shouldRetrySidebarSocket(closeCode: number): boolean {
  return !NON_RETRYABLE_CLOSE_CODES.has(closeCode);
}