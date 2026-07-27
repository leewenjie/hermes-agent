/**
 * Hermes CF Connector — Cloudflare Workers edge layer for Hermes Agent.
 *
 * Architecture: Platform → Webhook → Worker → Queue → Durable Object → Agent Runtime
 */
import { Hono, type Context } from "hono";
import { Toucan } from "toucan-js";
import type { InboundMessage, OutboundAction, OutboundResult, SendAction } from "./platforms/types";
import { safeString, isoNow, hexToBytes } from "./platforms/types";

// ---------------------------------------------------------------------------
// Environment
// ---------------------------------------------------------------------------
export interface Env {
  GATEWAY_SOCKET: DurableObjectNamespace;
  INBOUND_QUEUE: Queue<InboundMessage>;
  OUTBOUND_QUEUE: Queue<OutboundAction>;
  SESSION_STORE: KVNamespace;
  SKILLS_STORE: R2Bucket;
  AGENT_RUNTIME_URL: string;
  AGENT_RUNTIME_TOKEN: string;
  TELEGRAM_BOT_TOKEN?: string;
  DISCORD_BOT_TOKEN?: string;
  DISCORD_APPLICATION_PUBLIC_KEY?: string;
  SLACK_BOT_TOKEN?: string;
  SLACK_SIGNING_SECRET?: string;
  WHATSAPP_ACCESS_TOKEN?: string;
  WHATSAPP_PHONE_NUMBER_ID?: string;
  WHATSAPP_VERIFY_TOKEN?: string;
  SIGNAL_REST_URL?: string;
  SIGNAL_ACCOUNT?: string;
  MATRIX_ACCESS_TOKEN?: string;
  MATRIX_HOMESERVER_URL?: string;
  MATRIX_USER_ID?: string;
  RELAY_SHARED_SECRET: string;
  SENTRY_DSN?: string;
}

// ---------------------------------------------------------------------------
// Durable Object: GatewaySocket
// ---------------------------------------------------------------------------
export class GatewaySocket implements DurableObject {
  private ctx: DurableObjectState;
  private env: Env;
  private ws: WebSocket | null = null;
  private inboundBuffer: InboundMessage[] = [];
  private state: "disconnected" | "connecting" | "connected" | "dormant" = "disconnected";
  private lastActivity = Date.now();
  private platformTag = "";

  constructor(ctx: DurableObjectState, env: Env) {
    this.ctx = ctx;
    this.env = env;
    ctx.blockConcurrencyWhile(async () => {
      const stored = await ctx.storage.get<InboundMessage[]>("inboundBuffer");
      if (stored) this.inboundBuffer = stored;
    });
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/connect") return this.handleAgentConnect(request);

    if (url.pathname === "/status") {
      return new Response(JSON.stringify({
        state: this.state, buffered: this.inboundBuffer.length, lastActivity: this.lastActivity,
      }), { headers: { "Content-Type": "application/json" } });
    }

    if (url.pathname === "/deliver" && request.method === "POST") {
      const msg = await request.json() as InboundMessage;
      await this.deliverInbound(msg);
      return new Response("ok");
    }

    return new Response("Not Found", { status: 404 });
  }

  private async handleAgentConnect(request: Request): Promise<Response> {
    const auth = request.headers.get("Authorization");
    if (auth !== `Bearer ${this.env.AGENT_RUNTIME_TOKEN}`) {
      return new Response("Unauthorized", { status: 401 });
    }
    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    this.ctx.acceptWebSocket(server);
    this.state = "connected";
    this.lastActivity = Date.now();
    await this.flushBuffer();
    return new Response(null, { status: 101, webSocket: client });
  }

  async deliverInbound(msg: InboundMessage): Promise<void> {
    this.platformTag = `${msg.platform}:${msg.botId}`;
    if (this.state === "connected" && this.ws) {
      try { this.ws.send(JSON.stringify(msg)); this.lastActivity = Date.now(); return; } catch { /* fall through */ }
    }
    this.inboundBuffer.push(msg);
    if (this.inboundBuffer.length > 1000) this.inboundBuffer = this.inboundBuffer.slice(-500);
    await this.ctx.storage.put("inboundBuffer", this.inboundBuffer);
    if (this.state === "dormant") await this.wakeAgent();
  }

  private async flushBuffer(): Promise<void> {
    while (this.inboundBuffer.length > 0 && this.ws) {
      const msg = this.inboundBuffer.shift()!;
      try { this.ws.send(JSON.stringify(msg)); } catch { this.inboundBuffer.unshift(msg); break; }
    }
    await this.ctx.storage.put("inboundBuffer", this.inboundBuffer);
    if (this.inboundBuffer.length === 0) await this.ctx.storage.delete("inboundBuffer");
  }

  private async wakeAgent(): Promise<void> {
    try {
      await fetch(`${this.env.AGENT_RUNTIME_URL}/wake`, {
        method: "POST",
        headers: { Authorization: `Bearer ${this.env.AGENT_RUNTIME_TOKEN}`, "Content-Type": "application/json" },
        body: JSON.stringify({ platform: this.platformTag }),
      });
      this.state = "connecting";
    } catch (err) { console.error("Wake failed:", err); }
  }

  async webSocketMessage(ws: WebSocket, message: string): Promise<void> {
    this.ws = ws; this.lastActivity = Date.now();
    try {
      const frame = JSON.parse(message);
      switch (frame.type) {
        case "hello":
          console.log(`Agent connected: platform=${frame.platform} botId=${frame.botId}`);
          this.platformTag = `${frame.platform}:${frame.botId}`;
          await this.flushBuffer();
          break;
        case "outbound":
          await this.deliverOutbound(frame as OutboundAction);
          break;
        case "outbound_result":
          break;
        default: console.warn("Unknown frame:", frame.type);
      }
    } catch (err) { console.error("Frame parse error:", err); }
  }

  private async deliverOutbound(outbound: OutboundAction): Promise<void> {
    const { platform, action } = outbound;
    const act = action as SendAction;
    let result: { success: boolean; message_id?: string; error?: string };

    try {
      switch (platform) {
        case "telegram": result = await sendTelegram(this.env, act); break;
        case "discord":  result = await sendDiscord(this.env, act); break;
        case "slack":    result = await sendSlack(this.env, act); break;
        case "whatsapp": result = await sendWhatsApp(this.env, act); break;
        case "matrix":   result = await sendMatrix(this.env, act); break;
        case "signal":   result = await sendSignal(this.env, act); break;
        default: result = { success: false, error: `Unknown platform: ${platform}` };
      }
    } catch (err) { result = { success: false, error: String(err) }; }

    if (this.ws && this.state === "connected") {
      try {
        this.ws.send(JSON.stringify({ type: "outbound_result", requestId: outbound.requestId, result } as OutboundResult));
      } catch { /* agent disconnected */ }
    }
  }

  async webSocketClose(_ws: WebSocket, code: number, reason: string, wasClean: boolean): Promise<void> {
    this.ws = null; this.state = "disconnected";
    console.log(`WS closed: code=${code} reason=${reason} clean=${wasClean}`);
  }

  async webSocketError(_ws: WebSocket, error: unknown): Promise<void> {
    console.error("WS error:", error);
    this.ws = null; this.state = "disconnected";
  }
}

// ---------------------------------------------------------------------------
// Outbound delivery helpers
// ---------------------------------------------------------------------------
async function sendTelegram(env: Env, a: SendAction) {
  const token = env.TELEGRAM_BOT_TOKEN; if (!token) return { success: false, error: "No token" };
  const resp = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: a.chat_id, text: a.text, parse_mode: a.parse_mode || "HTML", reply_to_message_id: a.reply_to_message_id }),
  });
  const data = await resp.json() as { ok: boolean; result?: { message_id: number }; description?: string };
  return data.ok ? { success: true, message_id: String(data.result!.message_id) } : { success: false, error: data.description };
}

async function sendDiscord(env: Env, a: SendAction) {
  const token = env.DISCORD_BOT_TOKEN; if (!token) return { success: false, error: "No token" };
  const parts = a.chat_id.split(":"); const channelId = parts.length === 2 ? parts[1] : parts[0];
  const resp = await fetch(`https://discord.com/api/v10/channels/${channelId}/messages`, {
    method: "POST", headers: { Authorization: `Bot ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ content: a.text.slice(0, 2000) }),
  });
  if (resp.ok) { const d = await resp.json() as { id: string }; return { success: true, message_id: d.id }; }
  return { success: false, error: `HTTP ${resp.status}` };
}

async function sendSlack(env: Env, a: SendAction) {
  const token = env.SLACK_BOT_TOKEN; if (!token) return { success: false, error: "No token" };
  const resp = await fetch("https://slack.com/api/chat.postMessage", {
    method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify({ channel: a.chat_id, text: a.text, mrkdwn: a.parse_mode !== "html" }),
  });
  const data = await resp.json() as { ok: boolean; ts?: string; error?: string };
  return data.ok ? { success: true, message_id: data.ts } : { success: false, error: data.error };
}

async function sendWhatsApp(env: Env, a: SendAction) {
  const token = env.WHATSAPP_ACCESS_TOKEN; const phoneId = env.WHATSAPP_PHONE_NUMBER_ID;
  if (!token || !phoneId) return { success: false, error: "Not configured" };
  const resp = await fetch(`https://graph.facebook.com/v21.0/${phoneId}/messages`, {
    method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ messaging_product: "whatsapp", to: a.chat_id, type: "text", text: { preview_url: false, body: a.text.slice(0, 4096) } }),
  });
  const data = await resp.json() as { messages?: Array<{ id: string }>; error?: { message: string } };
  return data.messages?.[0] ? { success: true, message_id: data.messages[0].id } : { success: false, error: data.error?.message };
}

async function sendMatrix(env: Env, a: SendAction) {
  const token = env.MATRIX_ACCESS_TOKEN; const hs = env.MATRIX_HOMESERVER_URL;
  if (!token || !hs) return { success: false, error: "Not configured" };
  const txnId = crypto.randomUUID();
  const resp = await fetch(`${hs}/_matrix/client/v3/rooms/${encodeURIComponent(a.chat_id)}/send/m.room.message/${txnId}`, {
    method: "PUT", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ msgtype: "m.text", body: a.text }),
  });
  const data = await resp.json() as { event_id?: string; error?: string };
  return data.event_id ? { success: true, message_id: data.event_id } : { success: false, error: data.error };
}

async function sendSignal(env: Env, a: SendAction) {
  const url = env.SIGNAL_REST_URL || "http://localhost:8080"; const account = env.SIGNAL_ACCOUNT;
  if (!account) return { success: false, error: "Not configured" };
  const resp = await fetch(`${url}/v2/send`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ number: account, recipients: [a.chat_id], message: a.text }),
  });
  const data = await resp.json() as { timestamp?: string; error?: string };
  return data.timestamp ? { success: true, message_id: data.timestamp } : { success: false, error: data.error };
}

// ---------------------------------------------------------------------------
// HMAC auth helper
// ---------------------------------------------------------------------------
async function verifyHmac(secret: string, header: string, body: string): Promise<boolean> {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["verify"]);
  const sigHex = header.replace(/^sha256=/, "");
  return await crypto.subtle.verify("HMAC", key, hexToBytes(sigHex), encoder.encode(body));
}

// ---------------------------------------------------------------------------
// Worker Router
// ---------------------------------------------------------------------------
const app = new Hono<{ Bindings: Env }>();

app.get("/health", (c: Context<{ Bindings: Env }>) => c.json({ status: "ok", timestamp: Date.now() }));

// --- Telegram ---
app.post("/webhook/telegram/:botToken", async (c: Context<{ Bindings: Env }>) => {
  if (c.req.param("botToken") !== c.env.TELEGRAM_BOT_TOKEN) return c.json({ error: "Invalid bot token" }, 403);
  const body = await c.req.json<Record<string, unknown>>();
  const msg = body.message as Record<string, unknown> | undefined;
  if (msg) {
    const chat = msg.chat as Record<string, unknown> | undefined;
    const from = msg.from as Record<string, unknown> | undefined;
    await c.env.INBOUND_QUEUE.send({
      type: "inbound", platform: "telegram", botId: c.env.TELEGRAM_BOT_TOKEN!,
      event: { type: "text", chat_id: safeString(chat?.id), user_id: safeString(from?.id),
        user_name: safeString((from as any)?.first_name) || safeString((from as any)?.username) || undefined,
        text: safeString(msg.text) || safeString(msg.caption) || undefined,
        timestamp: (msg.date as number) || isoNow(), raw: msg },
    });
  }
  return c.json({ ok: true });
});

// --- Discord ---
app.post("/webhook/discord", async (c: Context<{ Bindings: Env }>) => {
  const signature = c.req.header("X-Signature-Ed25519");
  const timestamp = c.req.header("X-Signature-Timestamp");
  const body = await c.req.text();
  let interaction: Record<string, unknown>;
  try { interaction = JSON.parse(body); } catch { return c.json({ error: "Invalid JSON" }, 400); }
  if (interaction.type === 1) return c.json({ type: 1 }); // PING → PONG

  // For type 2 (slash command), ingest
  if (interaction.type === 2) {
    const data = interaction.data as Record<string, unknown> | undefined;
    const member = interaction.member as Record<string, unknown> | undefined;
    const user = member?.user as Record<string, unknown> | undefined;
    const channelId = safeString(interaction.channel_id);
    const guildId = safeString(interaction.guild_id);
    await c.env.INBOUND_QUEUE.send({
      type: "inbound", platform: "discord", botId: safeString(interaction.application_id),
      event: { type: "text", chat_id: guildId ? `${guildId}:${channelId}` : channelId,
        user_id: safeString(user?.id || (interaction.user as any)?.id),
        user_name: safeString(user?.username || (interaction.user as any)?.username),
        text: `/${safeString(data?.name)}`, timestamp: isoNow(), raw: interaction },
    });
    return c.json({ type: 5 }); // DEFERRED
  }
  return c.json({ ok: true });
});

// --- Slack ---
app.post("/webhook/slack", async (c: Context<{ Bindings: Env }>) => {
  const body = await c.req.text();
  let payload: Record<string, unknown>;
  try { payload = JSON.parse(body); } catch { return c.json({ error: "Invalid JSON" }, 400); }
  if (payload.type === "url_verification") return c.json({ challenge: payload.challenge });
  if (payload.type === "event_callback") {
    const event = payload.event as Record<string, unknown> | undefined;
    if (event && event.type === "message" && !event.bot_id && !event.subtype) {
      const text = safeString(event.text).replace(/<@[A-Z0-9]+>\s*/g, "").trim();
      await c.env.INBOUND_QUEUE.send({
        type: "inbound", platform: "slack", botId: safeString((payload as any)?.team_id || event.team),
        event: { type: "text", chat_id: safeString(event.channel), user_id: safeString(event.user),
          text: text || "[message]", timestamp: isoNow(), raw: event },
      });
    }
  }
  return c.json({ ok: true });
});

// --- WhatsApp ---
app.get("/webhook/whatsapp", (c: Context<{ Bindings: Env }>) => {
  const mode = c.req.query("hub.mode"); const token = c.req.query("hub.verify_token");
  const challenge = c.req.query("hub.challenge");
  if (mode === "subscribe" && token === (c.env.WHATSAPP_VERIFY_TOKEN || "hermes_verify") && challenge) {
    return c.text(challenge);
  }
  return c.text("Verification failed", 403);
});

app.post("/webhook/whatsapp", async (c: Context<{ Bindings: Env }>) => {
  const body = await c.req.json<Record<string, unknown>>();
  const entries = body.entry as Array<Record<string, unknown>> | undefined;
  if (!entries) return c.json({ ok: true });
  for (const entry of entries) {
    const changes = entry.changes as Array<Record<string, unknown>> | undefined;
    if (!changes) continue;
    for (const change of changes) {
      const value = change.value as Record<string, unknown> | undefined;
      if (!value || value.messaging_product !== "whatsapp") continue;
      const msgs = (value.messages as Array<Record<string, unknown>>) || [];
      const contacts = value.contacts as Array<Record<string, unknown>> | undefined;
      const metadata = value.metadata as Record<string, unknown> | undefined;
      for (const msg of msgs) {
        const contact = contacts?.[0];
        const msgType = safeString(msg.type);
        let text = msgType === "text" ? safeString((msg.text as any)?.body) : undefined;
        if (msgType === "interactive") text = safeString(((msg.interactive as any)?.button_reply || (msg.interactive as any)?.list_reply)?.id);
        if (!text && msgType !== "image" && msgType !== "voice" && msgType !== "video" && msgType !== "document") continue;
        await c.env.INBOUND_QUEUE.send({
          type: "inbound", platform: "whatsapp", botId: safeString(metadata?.phone_number_id),
          event: { type: msgType === "image" ? "image" : msgType === "voice" || msgType === "audio" ? "voice" : msgType === "video" ? "video" : msgType === "document" ? "document" : "text",
            chat_id: safeString(msg.from), user_id: safeString(msg.from),
            user_name: contact ? safeString((contact.profile as any)?.name) : undefined,
            text, timestamp: safeString(msg.timestamp) ? parseInt(safeString(msg.timestamp), 10) : isoNow(), raw: msg },
        });
      }
    }
  }
  return c.json({ ok: true });
});

// --- Generic webhook ---
app.post("/webhook/generic/:platform", async (c: Context<{ Bindings: Env }>) => {
  const platform = c.req.param("platform")!;
  const hmacHeader = c.req.header("X-Hub-Signature-256") || c.req.header("X-Webhook-Signature");
  const body = await c.req.text();
  if (hmacHeader && c.env.RELAY_SHARED_SECRET) {
    const valid = await verifyHmac(c.env.RELAY_SHARED_SECRET, hmacHeader, body);
    if (!valid) return c.json({ error: "Invalid signature" }, 401);
  }
  let payload: Record<string, unknown>;
  try { payload = JSON.parse(body); } catch { payload = { raw: body }; }
  await c.env.INBOUND_QUEUE.send({
    type: "inbound", platform, botId: platform,
    event: { type: "text", chat_id: c.req.query("chat_id") || "webhook",
      user_id: "webhook", user_name: platform,
      text: JSON.stringify(payload, null, 2), timestamp: isoNow(), raw: payload },
  });
  return c.json({ ok: true });
});

// --- Agent WebSocket connect (agent runtime dials this) ---
// The agent connects via WS to this endpoint. We look up (or create) the
// right GatewaySocket DO and establish a WS relay between agent and DO.
app.get("/connect", async (c: Context<{ Bindings: Env }>) => {
  const upgradeHeader = c.req.header("Upgrade");
  if (upgradeHeader !== "websocket") {
    return c.json({ error: "Use WebSocket upgrade to connect" }, 426);
  }

  const auth = c.req.header("Authorization");
  if (auth !== `Bearer ${c.env.AGENT_RUNTIME_TOKEN}`) {
    return c.json({ error: "Unauthorized" }, 401);
  }

  // Determine which DO the agent wants to connect to
  const platform = c.req.query("platform") || "default";
  const botId = c.req.query("botId") || "default";

  const doId = c.env.GATEWAY_SOCKET.idFromName(`${platform}:${botId}`);
  const stub = c.env.GATEWAY_SOCKET.get(doId);

  // Proxy the WebSocket upgrade to the DO's /connect handler
  const doUrl = new URL(c.req.url);
  doUrl.pathname = "/connect";
  const doReq = new Request(doUrl.toString(), {
    headers: c.req.raw.headers,
  });

  return stub.fetch(doReq);
});

// --- Agent relay ---
app.post("/relay/outbound", async (c: Context<{ Bindings: Env }>) => {
  if (c.req.header("Authorization") !== `Bearer ${c.env.AGENT_RUNTIME_TOKEN}`) return c.json({ error: "Unauthorized" }, 401);
  const outbound = await c.req.json<OutboundAction>();
  await c.env.OUTBOUND_QUEUE.send(outbound);
  return c.json({ ok: true });
});

// ---------------------------------------------------------------------------
// Export: Worker + Queue consumer
// ---------------------------------------------------------------------------
export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const sentry = env.SENTRY_DSN ? new Toucan({ dsn: env.SENTRY_DSN, context: ctx, request }) : null;
    try { return await app.fetch(request, env, ctx); }
    catch (err) { sentry?.captureException(err); return new Response("Internal Error", { status: 500 }); }
  },

  async queue(batch: MessageBatch<InboundMessage>, env: Env): Promise<void> {
    for (const msg of batch.messages) {
      const inbound = msg.body;
      const doId = env.GATEWAY_SOCKET.idFromName(`${inbound.platform}:${inbound.botId}`);
      const stub = env.GATEWAY_SOCKET.get(doId);
      await stub.fetch(new Request("https://do/deliver", {
        method: "POST",
        body: JSON.stringify(inbound),
      }));
    }
  },
};
