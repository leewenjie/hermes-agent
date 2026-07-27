/** Shared types for Hermes CF Connector platform adapters. */

export interface NormalizedMessageEvent {
  type: "text" | "image" | "voice" | "video" | "document" | "location" | "callback" | "interactive";
  chat_id: string;
  user_id: string;
  user_name?: string;
  text?: string;
  media_url?: string;
  mime_type?: string;
  reply_to_message_id?: string;
  timestamp: number;
  raw?: Record<string, unknown>;
}

export interface InboundMessage {
  type: "inbound";
  platform: string;
  botId: string;
  event: NormalizedMessageEvent;
  bufferId?: string;
}

export interface SendAction {
  op: "send";
  chat_id: string;
  text: string;
  parse_mode?: "html" | "markdown";
  reply_to_message_id?: string;
  reply_markup?: unknown;
  media_url?: string;
}

export interface OutboundAction {
  type: "outbound";
  requestId: string;
  platform: string;
  botId: string;
  action: SendAction | Record<string, unknown>;
}

export interface OutboundResult {
  type: "outbound_result";
  requestId: string;
  result: { success: boolean; message_id?: string; error?: string };
}

export function safeString(v: unknown, fallback = ""): string {
  if (typeof v === "string") return v;
  if (typeof v === "number") return String(v);
  return fallback;
}

export function isoNow(): number {
  return Math.floor(Date.now() / 1000);
}

export function hexToBytes(hex: string): Uint8Array {
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < hex.length; i += 2) bytes[i / 2] = parseInt(hex.substring(i, i + 2), 16);
  return bytes;
}
