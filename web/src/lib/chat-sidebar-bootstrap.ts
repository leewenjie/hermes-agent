export interface ChatSidebarGateway {
  connect(): Promise<void>;
  request<T>(method: string, params?: Record<string, unknown>): Promise<T>;
}

export interface ChatSidebarBootstrapOptions {
  profile?: string;
  readOnly: boolean;
}

export async function bootstrapChatSidebar(
  gateway: ChatSidebarGateway,
  { profile, readOnly }: ChatSidebarBootstrapOptions,
): Promise<{ session_id: string; info?: unknown } | null> {
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