export interface ChatSidebarGateway {
  connect(): Promise<void>;
  request<T>(method: string, params?: Record<string, unknown>): Promise<T>;
}

export interface ChatSidebarBootstrapOptions {
  profile?: string;
  readOnly: boolean;
  /** Do not open the sidecar until the server-derived access state is known. */
  accessResolved?: boolean;
}

export async function bootstrapChatSidebar(
  gateway: ChatSidebarGateway,
  { profile, accessResolved = true, readOnly }: ChatSidebarBootstrapOptions,
): Promise<{ session_id: string; info?: unknown } | null> {
  // Auth resolution and the WebSocket bootstrap are independent async
  // operations. Starting a read-only connection while auth is still loading
  // lets the component clean it up and immediately reuse the same client for
  // a second connect, which can race the first socket's opening handshake.
  if (!accessResolved) {
    return null;
  }

  await gateway.connect();

  if (readOnly) {
    return null;
  }

  return gateway.request<{ session_id: string; info?: unknown }>(
    "session.create",
    {
      close_on_disconnect: true,
      source: "tool",
      ...(profile ? { profile } : {}),
    },
  );
}