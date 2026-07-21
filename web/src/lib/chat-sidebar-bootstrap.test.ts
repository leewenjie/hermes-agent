import { describe, expect, it, vi } from "vitest";

import {
  bootstrapChatSidebar,
  type ChatSidebarGateway,
} from "./chat-sidebar-bootstrap";

function gatewayMock() {
  return {
    connect: vi.fn(async () => undefined),
    request: vi.fn(async () => ({ session_id: "sidecar-session" })),
  } as unknown as ChatSidebarGateway & {
    connect: ReturnType<typeof vi.fn>;
    request: ReturnType<typeof vi.fn>;
  };
}

describe("bootstrapChatSidebar", () => {
  it("connects for read events without creating a session in read-only mode", async () => {
    const gateway = gatewayMock();

    const result = await bootstrapChatSidebar(gateway, {
      profile: "researcher",
      readOnly: true,
    });

    expect(gateway.connect).toHaveBeenCalledOnce();
    expect(gateway.request).not.toHaveBeenCalled();
    expect(result).toBeNull();
  });

  it("creates the disposable sidecar session when research is writable", async () => {
    const gateway = gatewayMock();

    const result = await bootstrapChatSidebar(gateway, {
      profile: "researcher",
      readOnly: false,
    });

    expect(gateway.request).toHaveBeenCalledWith("session.create", {
      close_on_disconnect: true,
      source: "tool",
      profile: "researcher",
    });
    expect(result).toEqual({ session_id: "sidecar-session" });
  });
});