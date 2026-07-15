/**
 * ChatSidebar — structured-events panel that sits next to the xterm.js
 * terminal in the dashboard Chat tab.
 *
 * Two WebSockets, one per concern:
 *
 *   1. **JSON-RPC sidecar** (`GatewayClient` → /api/ws) — a lightweight
 *      session used only for connection state (the "live" badge) and
 *      credential warnings. Independent of the PTY pane's session by
 *      design. The model badge does NOT come from here: it reads the
 *      effective config model over REST (`/api/model/info`), and the model
 *      picker writes config over REST (`/api/model/set`) then offers a
 *      dashboard reload so the running chat adopts the new model.
 *
 *   2. **Event subscriber** (/api/events?channel=…) — passive, receives
 *      every dispatcher emit from the PTY-side `tui_gateway.entry` that
 *      the dashboard fanned out.  The sidebar uses it for `session.info`
 *      (live chat title) and `dashboard.new_session_requested`.  The
 *      `channel` id ties this listener to the same chat tab's PTY child —
 *      see `ChatPage.tsx` for where the id is generated.
 *
 * Best-effort throughout: WS failures show in the badge / banner, the
 * terminal pane keeps working unimpaired.
 */

import { Button } from "@nous-research/ui/ui/components/button";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Card } from "@nous-research/ui/ui/components/card";

import { ModelPickerDialog } from "@/components/ModelPickerDialog";
import { ModelReloadConfirm } from "@/components/ModelReloadConfirm";
import { ReasoningPicker } from "@/components/ReasoningPicker";
import { GatewayClient, type ConnectionState } from "@/lib/gatewayClient";
import { api, buildWsUrl } from "@/lib/api";
import {
  capabilityInfoFromSessionCreate,
  emptyResearchTrace,
  generatedImageFromToolResult,
  isBrowserImageSource,
  reduceResearchTrace,
  researchMethodLabel,
  summarizeResearchTools,
  type ChatSessionCapabilityInfo,
  type ResearchTraceState,
} from "@/lib/chat-sidebar-events";
import { titleFromSessionInfoPayload } from "@/lib/chat-title";
import {
  isOxaideManagedDashboard,
  OXAIDE_RESEARCH_ENGINE_LABEL,
} from "@/lib/managed-dashboard";

import { cn } from "@/lib/utils";
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ExternalLink,
  ImageIcon,
  LockKeyhole,
  LoaderCircle,
  RefreshCw,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type SessionInfo = ChatSessionCapabilityInfo;

interface RpcEnvelope {
  method?: string;
  params?: { type?: string; payload?: unknown };
}

interface ToolCompletePayload {
  name?: unknown;
  result?: unknown;
}

interface GeneratedImage {
  src: string;
  source: string;
}

const STATE_LABEL: Record<ConnectionState, string> = {
  idle: "idle",
  connecting: "connecting",
  open: "live",
  closed: "closed",
  error: "error",
};

const STATE_TONE: Record<
  ConnectionState,
  "secondary" | "warning" | "success" | "destructive"
> = {
  idle: "secondary",
  connecting: "warning",
  open: "success",
  closed: "secondary",
  error: "destructive",
};

interface ChatSidebarProps {
  channel: string;
  /** Chat profile from the dashboard switcher / URL scope. */
  profile?: string;
  className?: string;
  onDashboardNewSessionRequest?: () => void;
  onSessionTitleChange?: (title: string | null) => void;
}

export function ChatSidebar({
  channel,
  profile,
  className,
  onDashboardNewSessionRequest,
  onSessionTitleChange,
}: ChatSidebarProps) {
  // `version` bumps on reconnect; gw is derived so we never call setState
  // for it inside an effect (React 19's set-state-in-effect rule). The
  // counter is the dependency on purpose — it's not read in the memo body,
  // it's the signal that says "rebuild the client".
  const [version, setVersion] = useState(0);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const gw = useMemo(() => new GatewayClient(), [version]);

  const [state, setState] = useState<ConnectionState>("idle");
  const [info, setInfo] = useState<SessionInfo>({});
  const [generatedImage, setGeneratedImage] = useState<GeneratedImage | null>(null);
  const [researchTrace, setResearchTrace] = useState<ResearchTraceState>(
    emptyResearchTrace,
  );
  const mediaRequestRef = useRef(0);
  const [modelOpen, setModelOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The badge shows config.yaml's main model (`model.default`) via
  // `/api/model/info` — the same value the Models page writes and a new chat
  // session boots from. We deliberately don't use the sidecar's `session.info`
  // model: that's a one-time snapshot of the throwaway sidecar agent taken when
  // its session is created, and it never updates when the model is changed
  // elsewhere, so the badge would go stale. Pass the chat profile explicitly so
  // this card stays scoped to the PTY even if the global dashboard switcher
  // changes while the chat is open.
  const [effectiveModel, setEffectiveModel] = useState("");
  // Whether the effective model supports reasoning effort — gates the
  // ReasoningPicker. Read from the same `/api/model/info` capabilities the
  // (currently unused) ModelInfoCard surfaces, so the dashboard exposes a
  // control to *set* the level, not just a read-only "Reasoning" badge.
  const [supportsReasoning, setSupportsReasoning] = useState(false);
  // Bumped on model change/save so ReasoningPicker re-reads the saved effort
  // (config is profile-scoped the same way the model badge is).
  const [modelRefreshKey, setModelRefreshKey] = useState(0);
  // Set after the picker saves a model and the user declines the reload: config
  // is updated but the running session keeps its model until rebuilt.
  const [modelNotice, setModelNotice] = useState<string | null>(null);
  // Short name of a just-saved model awaiting confirm to reload (a fresh chat
  // session is how the running chat adopts it; we confirm before discarding it).
  const [pendingReloadModel, setPendingReloadModel] = useState<string | null>(
    null,
  );
  const managedOxaide = isOxaideManagedDashboard();

  const refreshEffectiveModel = useCallback(() => {
    void api
      .getModelInfo(profile)
      .then((r) => {
        if (r?.model) setEffectiveModel(String(r.model));
        setSupportsReasoning(!!r?.capabilities?.supports_reasoning);
        // Bump so ReasoningPicker re-reads the saved effort for the new model.
        setModelRefreshKey((k) => k + 1);
      })
      .catch(() => {
        // Best-effort: keep the last known label rather than blanking it.
      });
  }, [profile]);

  // Profile or PTY channel change tears down both WebSockets. Bump `version`
  // (same path as the manual Reconnect button) so the gateway client is
  // recreated and the events feed resubscribes — otherwise the old events
  // socket's close handler can leave a stale error banner after a switch.
  const scopeKey = `${channel}\0${profile ?? ""}`;
  const prevScopeKey = useRef<string | null>(null);
  useEffect(() => {
    if (prevScopeKey.current === null) {
      prevScopeKey.current = scopeKey;
      return;
    }
    if (prevScopeKey.current === scopeKey) return;
    prevScopeKey.current = scopeKey;
    setError(null);
    setGeneratedImage(null);
    setResearchTrace(emptyResearchTrace());
    mediaRequestRef.current += 1;
    setVersion((v) => v + 1);
  }, [scopeKey]);

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      setInfo({});
      setError(null);
    });
    const offState = gw.onState(setState);

    const offSessionInfo = gw.on<SessionInfo>("session.info", (ev) => {
      if (ev.payload) {
        setInfo((prev) => ({ ...prev, ...ev.payload }));
      }
    });

    const offError = gw.on<{ message?: string }>("error", (ev) => {
      const message = ev.payload?.message;

      if (message) {
        setError(message);
      }
    });

    // Create the sidecar session so the gateway surfaces session-scoped
    // signals (connection state, credential warnings). It's independent of the
    // PTY pane's session by design. The model picker no longer rides this
    // session — it writes config.yaml over REST — so we don't track its id.
    gw.connect()
      .then(() => {
        if (cancelled) {
          return;
        }
        // close_on_disconnect: the gateway reaps this sidecar session (and its
        // slash_worker subprocess) when the WS drops, instead of leaking it.
        return gw.request<{ session_id: string; info?: unknown }>("session.create", {
          close_on_disconnect: true,
          source: "tool",
          ...(profile ? { profile } : {}),
        }).then((result) => {
          if (cancelled) return;
          const preview = capabilityInfoFromSessionCreate(result);
          if (preview) setInfo((prev) => ({ ...prev, ...preview }));
        });
      })
      .catch((e: Error) => {
        if (!cancelled) {
          setError(e.message);
        }
      });

    return () => {
      cancelled = true;
      offState();
      offSessionInfo();
      offError();
      gw.close();
    };
    // `profile` is read from render; scope changes bump `version` → new `gw`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gw]);

  // Event subscriber WebSocket — receives the rebroadcast of every
  // dispatcher emit from the PTY child's gateway.  See /api/pub +
  // /api/events in hermes_cli/web_server.py for the broadcast hop.
  //
  // Failures (auth/loopback rejection, server too old to expose the
  // endpoint, transient drops) surface in the same banner as the
  // JSON-RPC sidecar so the sidebar matches its documented best-effort
  // UX and the user always has a reconnect affordance.
  useEffect(() => {
    if (!channel) {
      return;
    }
    // In loopback mode the legacy ?token=<session> path is fine; in gated
    // mode we have to mint a single-use ticket from the cookie. The IIFE
    // keeps the outer effect synchronous so its ``return cleanup`` stays
    // at the top level; the local ``ws`` is hoisted to a closed-over
    // binding the cleanup reads via ``wsRef``.
    let unmounting = false;
    let ws: WebSocket | null = null;
    void (async () => {
      const url = await buildWsUrl("/api/events", { channel });
      if (unmounting) {
        return;
      }
      ws = new WebSocket(url);

      // `unmounting` suppresses the banner during cleanup — `ws.close()`
      // from the effect's return fires a close event with code 1005 that
      // would otherwise look like an unexpected drop.
      const DISCONNECTED = "events feed disconnected — tool calls may not appear";
      const surface = (msg: string) => !unmounting && setError(msg);

      ws.addEventListener("error", () => surface(DISCONNECTED));

      ws.addEventListener("close", (ev) => {
        if (ev.code === 4401 || ev.code === 4403) {
          surface(`events feed rejected (${ev.code}) — reload the page`);
        } else if (ev.code !== 1000) {
          surface(DISCONNECTED);
        }
      });

      ws.addEventListener("message", (ev) => {
        let frame: RpcEnvelope;

        try {
          frame = JSON.parse(ev.data);
        } catch {
          return;
        }

        if (frame.method !== "event" || !frame.params) {
          return;
        }

        const { type, payload } = frame.params;

        if (type === "session.info") {
          if (payload && typeof payload === "object" && !Array.isArray(payload)) {
            setInfo((prev) => ({ ...prev, ...(payload as SessionInfo) }));
          }
          const title = titleFromSessionInfoPayload(payload);
          if (title !== undefined) {
            onSessionTitleChange?.(title);
          }
        } else if (
          type === "message.start" ||
          type === "message.complete" ||
          type === "tool.start" ||
          type === "tool.complete"
        ) {
          setResearchTrace((prev) => reduceResearchTrace(prev, type, payload));
          if (type !== "tool.complete") return;
          const tool = payload as ToolCompletePayload | null;
          if (tool?.name !== "image_generate") return;
          const source = generatedImageFromToolResult(tool.result);
          if (!source) return;

          if (isBrowserImageSource(source)) {
            mediaRequestRef.current += 1;
            setGeneratedImage({ src: source, source });
            return;
          }

          const requestedSource = source;
          const mediaRequest = ++mediaRequestRef.current;
          void api
            .getMedia(source)
            .then(({ data_url }) => {
              if (!unmounting && mediaRequestRef.current === mediaRequest) {
                setGeneratedImage({ src: data_url, source: requestedSource });
              }
            })
            .catch(() => {
              // The media endpoint intentionally rejects paths outside the
              // Hermes image/cache roots. Keep chat working and omit the card.
            });
        } else if (type === "dashboard.new_session_requested") {
          onDashboardNewSessionRequest?.();
        }
      });
    })();

    return () => {
      unmounting = true;
      ws?.close();
    };
  }, [channel, onDashboardNewSessionRequest, onSessionTitleChange, version]);

  // Seed the badge on mount and re-read it whenever the sockets are rebuilt
  // (a profile/channel switch bumps `version`).
  useEffect(() => {
    refreshEffectiveModel();
  }, [refreshEffectiveModel, version]);

  const reconnect = useCallback(() => {
    setError(null);
    setModelNotice(null);
    setPendingReloadModel(null);
    setVersion((v) => v + 1);
  }, []);

  // The picker writes config.yaml over REST and reloads — it doesn't ride the
  // sidecar gateway session, so it's available whenever the sidebar is mounted.
  const modelName = effectiveModel || info.model || "—";
  const modelLabel = modelName.split("/").slice(-1)[0] ?? "—";
  const banner = error ?? info.credential_warning ?? null;
  const preloadedSkills = info.preloaded_skills ?? [];
  const capabilityKnown = info.preloaded_skills !== undefined;
  const capabilityPreview = Boolean(info.capability_preview);
  const toolCount = Object.values(info.tools ?? {}).reduce(
    (total, names) => total + names.length,
    0,
  );
  const usedTools = summarizeResearchTools(researchTrace.tools);

  return (
    <aside
      className={cn(
        "flex h-full w-full min-w-0 shrink-0 flex-col gap-3 overflow-y-auto overflow-x-hidden pr-1",
        className,
      )}
    >
      <Card className="flex items-center justify-between gap-2 px-3 py-2">
        <div className="min-w-0 flex-1">
          <div className="text-display text-xs tracking-wider text-text-tertiary">
            model
          </div>

          {managedOxaide ? (
            <div
              className="min-w-0 max-w-full"
              title="Managed research runtime. Provider details are available in the Trust Center."
            >
              <div className="flex min-w-0 items-center gap-1.5 text-sm font-medium">
                <span className="truncate">{OXAIDE_RESEARCH_ENGINE_LABEL}</span>
                <LockKeyhole className="size-3.5 shrink-0 text-text-tertiary" />
              </div>
              <div className="mt-0.5 line-clamp-2 text-[0.6875rem] leading-snug text-text-tertiary">
                Source-linked analysis with saved research context
              </div>
            </div>
          ) : (
            <Button
              ghost
              size="sm"
              onClick={() => setModelOpen(true)}
              className={cn(
                "max-w-full min-w-0 px-0 py-0",
                "self-start normal-case tracking-normal text-sm font-medium",
                "hover:underline disabled:no-underline",
              )}
              title={modelName === "—" ? "switch model" : modelName}
            >
              <span className="flex min-w-0 max-w-full items-center gap-1">
                <span className="truncate">{modelLabel}</span>

                <ChevronDown className="size-3.5 shrink-0 text-text-secondary" />
              </span>
            </Button>
          )}
        </div>

        <Badge tone={STATE_TONE[state]} className="shrink-0">
          {STATE_LABEL[state]}
        </Badge>
      </Card>

      {managedOxaide && (
        <Card className="px-3 py-3">
          <div className="text-display text-xs tracking-wider text-text-tertiary">
            research setup
          </div>
          <div className="mt-1 text-sm font-medium">
            {preloadedSkills.length > 0
              ? `${preloadedSkills.length} methods ${capabilityPreview ? "configured" : "loaded for every answer"}`
              : capabilityKnown
                ? "No research methods configured"
                : "Loading research methods…"}
          </div>
          {preloadedSkills.length > 0 && (
            <div className="mt-2 flex max-h-24 flex-wrap gap-1 overflow-y-auto">
              {preloadedSkills.map((skill) => (
                <Badge
                  key={skill}
                  tone="secondary"
                  className="max-w-full px-1.5 py-0.5 text-[0.6875rem] font-normal leading-tight normal-case tracking-normal"
                  title={researchMethodLabel(skill)}
                >
                  <span className="truncate">{researchMethodLabel(skill)}</span>
                </Badge>
              ))}
            </div>
          )}
          {toolCount > 0 && (
            <div className="mt-2 text-xs text-text-secondary">
              {toolCount} {capabilityPreview ? "configured" : "available"} tools across{" "}
              {Object.keys(info.tools ?? {}).length} toolsets
            </div>
          )}
        </Card>
      )}

      {managedOxaide && researchTrace.phase !== "idle" && (
        <Card className="px-3 py-3">
          <div className="flex items-center justify-between gap-2">
            <div className="text-display text-xs tracking-wider text-text-tertiary">
              used in this answer
            </div>
            <Badge
              tone={researchTrace.phase === "running" ? "warning" : "success"}
              className="shrink-0"
            >
              {researchTrace.phase === "running" ? "researching" : "complete"}
            </Badge>
          </div>

          {usedTools.length > 0 ? (
            <div className="mt-2 flex flex-col gap-2">
              {usedTools.map((tool) => (
                <div key={tool.name} className="min-w-0">
                  <div className="flex min-w-0 items-center gap-1.5 text-xs font-medium">
                    {tool.running ? (
                      <LoaderCircle className="size-3.5 shrink-0 animate-spin text-warning" />
                    ) : (
                      <CheckCircle2 className="size-3.5 shrink-0 text-success" />
                    )}
                    <span className="truncate">{tool.label}</span>
                    {tool.count > 1 && (
                      <span className="shrink-0 text-text-tertiary">×{tool.count}</span>
                    )}
                  </div>
                  {tool.detail && (
                    <div className="mt-0.5 line-clamp-2 pl-5 text-[0.6875rem] leading-snug text-text-secondary">
                      {tool.detail}
                    </div>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <div className="mt-2 text-xs text-text-secondary">
              {researchTrace.phase === "running"
                ? "Reviewing the question before selecting data tools."
                : "No data tools were needed for this answer."}
            </div>
          )}

          {researchTrace.openedSkills.length > 0 && (
            <div className="mt-2 border-t border-border pt-2">
              <div className="text-[0.6875rem] text-text-tertiary">
                Opened this answer
              </div>
              <div className="mt-1 flex flex-wrap gap-1">
                {researchTrace.openedSkills.map((skill) => (
                  <Badge key={skill} tone="secondary" className="normal-case tracking-normal">
                    {researchMethodLabel(skill)}
                  </Badge>
                ))}
              </div>
            </div>
          )}

          <div className="mt-2 border-t border-border pt-2 text-[0.6875rem] leading-snug text-text-tertiary">
            Loaded methods guide the session. This trace lists observed calls only.
          </div>
        </Card>
      )}

      {generatedImage && (
        <Card className="overflow-hidden p-0">
          <div className="flex items-center justify-between gap-2 px-3 py-2">
            <div className="flex min-w-0 items-center gap-1.5 text-xs font-medium">
              <ImageIcon className="size-3.5 shrink-0 text-success" />
              <span className="truncate">Latest generated image</span>
            </div>
            <a
              href={generatedImage.src}
              target="_blank"
              rel="noreferrer"
              className="shrink-0 text-text-secondary hover:text-foreground"
              aria-label="Open generated image"
              title="Open generated image"
            >
              <ExternalLink className="size-3.5" />
            </a>
          </div>
          <a
            href={generatedImage.src}
            target="_blank"
            rel="noreferrer"
            title={generatedImage.source}
            className="block border-t border-border bg-black/20 p-2"
          >
            <img
              src={generatedImage.src}
              alt="Latest image generated in this chat"
              className="mx-auto max-h-52 w-full rounded object-contain"
            />
          </a>
        </Card>
      )}

      {supportsReasoning && !managedOxaide && (
        <Card className="py-0">
          <ReasoningPicker
            currentModel={modelName}
            profile={profile}
            refreshKey={modelRefreshKey}
            onChanged={(effort) =>
              setModelNotice(
                `Reasoning effort set to ${effort}. Run /new or refresh the page to apply it to this chat.`,
              )
            }
          />
        </Card>
      )}

      {modelNotice && (
        <Card className="flex items-start gap-2 border-warning/40 bg-warning/5 px-3 py-2 text-xs">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />

          <div className="wrap-break-word min-w-0 flex-1 text-text-secondary">
            {modelNotice}
          </div>
        </Card>
      )}

      {banner && (
        <Card className="flex items-start gap-2 border-destructive/40 bg-destructive/5 px-3 py-2 text-xs">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive" />

          <div className="min-w-0 flex-1">
            <div className="wrap-break-word text-destructive">{banner}</div>

            {error && (
              <Button
                size="sm"
                outlined
                className="mt-1"
                onClick={reconnect}
                prefix={<RefreshCw />}
              >
                reconnect tools feed
              </Button>
            )}
          </div>
        </Card>
      )}

      {modelOpen && !managedOxaide && (
        <ModelPickerDialog
          // Same path the Models page uses (REST /api/model/set), not the
          // sidecar config.set RPC, which didn't reliably land in the
          // config.yaml the agent boots from. Always persisted (alwaysGlobal).
          loader={() => api.getModelOptions(profile)}
          alwaysGlobal
          onApply={async ({ provider, model, confirmExpensiveModel }) => {
            setModelNotice(null);
            setPendingReloadModel(null);
            const result = await api.setModelAssignment(
              {
                confirm_expensive_model: confirmExpensiveModel,
                scope: "main",
                provider,
                model,
              },
              profile,
            );
            // confirm_required => the dialog shows the expensive-model prompt
            // and calls back; don't announce until the user confirms.
            if (!result.confirm_required) {
              refreshEffectiveModel();
              // Ask before reloading: applying the model starts a fresh chat.
              setPendingReloadModel(model.split("/").slice(-1)[0]);
            }
            return result;
          }}
          onClose={() => {
            setModelOpen(false);
            refreshEffectiveModel();
          }}
        />
      )}

      {!managedOxaide && (
        <ModelReloadConfirm
          model={pendingReloadModel}
          onCancel={() => {
            const m = pendingReloadModel;
            setPendingReloadModel(null);
            setModelNotice(
              `Model set to ${m}. Run /new or refresh the page to apply it to this chat.`,
            );
          }}
        />
      )}
    </aside>
  );
}
