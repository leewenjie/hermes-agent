import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, fetchJSON } from "./api";

const SESSION_HEADER = "X-Hermes-Session-Token";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function jsonFetchMock(body: unknown = { ok: true }) {
  return vi.fn<typeof fetch>(
    async () =>
      new Response(JSON.stringify(body), {
        headers: { "Content-Type": "application/json" },
        status: 200,
      }),
  );
}

describe("api.getModelOptions", () => {
  it("requests a live model refresh when asked", async () => {
    vi.stubGlobal("window", {});

    const fetchMock = jsonFetchMock({ providers: [] });
    vi.stubGlobal("fetch", fetchMock);

    await api.getModelOptions({ refresh: true });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/model/options?refresh=1&include_unconfigured=1",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("keeps explicit profile scoping when refreshing", async () => {
    vi.stubGlobal("window", {});

    const fetchMock = jsonFetchMock({ providers: [] });
    vi.stubGlobal("fetch", fetchMock);

    await api.getModelOptions({ profile: "default", refresh: true });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/model/options?profile=default&refresh=1&include_unconfigured=1",
      expect.objectContaining({ credentials: "include" }),
    );
  });
});

describe("fetchJSON errors", () => {
  it("preserves HTTP status and structured error detail", async () => {
    vi.stubGlobal("window", { __HERMES_AUTH_REQUIRED__: true });
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async () =>
        new Response(JSON.stringify({ detail: "Session does not belong to this user" }), {
          headers: { "Content-Type": "application/json" },
          status: 403,
        }),
      ),
    );

    const error = await fetchJSON("/api/research-shares/preview").catch(
      (reason: unknown) => reason,
    );

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 403,
      detail: "Session does not belong to this user",
    });
  });
});

describe("api.getStatus", () => {
  it("forwards an abort signal to the capability probe", async () => {
    vi.stubGlobal("window", {});
    const fetchMock = jsonFetchMock({ research_sharing_enabled: true });
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await api.getStatus(controller.signal);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/status",
      expect.objectContaining({ signal: controller.signal }),
    );
  });
});

describe("api.logout", () => {
  it("returns an Oxaide-branded loopback session to the account shell", async () => {
    const assign = vi.fn();
    vi.stubGlobal("window", { location: { assign, origin: "http://127.0.0.1:9119" } });
    vi.stubGlobal("fetch", jsonFetchMock({ ok: true, redirect_to: "/login" }));

    await api.logout("https://oxaide.com");

    expect(assign).toHaveBeenCalledWith("https://oxaide.com");
  });

  it("uses the signed same-origin bridge for an Oxaide runtime logout", async () => {
    const assign = vi.fn();
    vi.stubGlobal("window", {
      location: {
        assign,
        origin: "https://runtimekey1234567890abcd.oxaide.com",
      },
    });
    vi.stubGlobal("fetch", jsonFetchMock({
      ok: true,
      redirect_to: "https://oxaide.com/auth/runtime-logout",
      logout_token: "signed.logout-token",
    }));

    await api.logout("https://oxaide.com");

    expect(assign).toHaveBeenCalledWith(
      "https://oxaide.com/auth/runtime-logout?token=signed.logout-token",
    );
  });
});

describe("api OAuth helpers", () => {
  it("starts OAuth login in gated mode without requiring an injected session token", async () => {
    vi.stubGlobal("window", { __HERMES_AUTH_REQUIRED__: true });
    const fetchMock = jsonFetchMock({
      flow: "device_code",
      session_id: "oauth-session",
    });
    vi.stubGlobal("fetch", fetchMock);

    await api.startOAuthLogin("openai-codex");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/providers/oauth/openai-codex/start",
      expect.objectContaining({
        body: "{}",
        credentials: "include",
        method: "POST",
      }),
    );
    const headers = fetchMock.mock.calls[0][1]?.headers as Headers;
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.has(SESSION_HEADER)).toBe(false);
  });

  it("still sends the injected session token for OAuth login in loopback mode", async () => {
    vi.stubGlobal("window", { __HERMES_SESSION_TOKEN__: "loopback-token" });
    const fetchMock = jsonFetchMock({
      flow: "device_code",
      session_id: "oauth-session",
    });
    vi.stubGlobal("fetch", fetchMock);

    await api.startOAuthLogin("openai-codex");

    const headers = fetchMock.mock.calls[0][1]?.headers as Headers;
    expect(headers.get(SESSION_HEADER)).toBe("loopback-token");
  });

  it("runs provider auth mutations in gated mode via cookie auth", async () => {
    vi.stubGlobal("window", { __HERMES_AUTH_REQUIRED__: true });
    const fetchMock = jsonFetchMock({ ok: true });
    vi.stubGlobal("fetch", fetchMock);

    await api.disconnectOAuthProvider("anthropic");
    await api.submitOAuthCode("anthropic", "oauth-session", "code-123");
    await api.cancelOAuthSession("oauth-session");
    await api.revealEnvVar("OPENAI_API_KEY");

    for (const call of fetchMock.mock.calls) {
      const init = call[1] as RequestInit;
      expect(init.credentials).toBe("include");
      expect((init.headers as Headers).has(SESSION_HEADER)).toBe(false);
    }
  });
});
