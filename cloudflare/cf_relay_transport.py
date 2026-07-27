"""
Cloudflare Relay Transport — connects Hermes gateway to CF GatewaySocket DO.

Drop-in replacement for gateway/relay/ws_transport.py. Activated when
HERMES_CF_CONNECTOR_URL is set. Dials the GatewaySocket Durable Object
instead of a VM-based relay connector.
"""
from __future__ import annotations
import asyncio, json, logging, os, uuid
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)
try: import websockets
except ImportError: websockets = None

_HANDSHAKE_TIMEOUT_S = 30.0
_OUTBOUND_TIMEOUT_S = 30.0
_RECONNECT_BACKOFF_BASE = 1.0
_RECONNECT_BACKOFF_MAX = 60.0
_HEARTBEAT_INTERVAL_S = 30.0

class CFRelayTransport:
    """Cloudflare-backed relay transport dialing the GatewaySocket DO."""

    def __init__(self, platform: str, bot_id: str):
        self._platform = platform; self._bot_id = bot_id
        self._ws = None; self._inbound_handler = None
        self._pending_outbound: Dict[str, asyncio.Future] = {}
        self._connected = False; self._reader_task = None; self._heartbeat_task = None
        self._reconnect_backoff = _RECONNECT_BACKOFF_BASE

    async def connect(self) -> bool:
        url = os.environ.get("HERMES_CF_CONNECTOR_URL", "").strip()
        token = os.environ.get("HERMES_CF_CONNECTOR_TOKEN", "").strip()
        if not url: logger.error("HERMES_CF_CONNECTOR_URL not set"); return False
        try:
            self._ws = await websockets.connect(
                f"{url}/connect?platform={self._platform}&botId={self._bot_id}",
                additional_headers={"Authorization": f"Bearer {token}"},
                ping_interval=None, close_timeout=10)
        except Exception:
            logger.exception("CF connector dial failed"); return False
        await self._ws.send(json.dumps({"type": "hello", "platform": self._platform, "botId": self._bot_id}))
        self._connected = True; self._reconnect_backoff = _RECONNECT_BACKOFF_BASE
        self._reader_task = asyncio.create_task(self._read_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("CF relay connected: platform=%s botId=%s", self._platform, self._bot_id)
        return True

    async def disconnect(self):
        self._connected = False
        for t in (self._reader_task, self._heartbeat_task):
            if t and not t.done(): t.cancel()
        if self._ws:
            try: await self._ws.close()
            except Exception: pass; self._ws = None
        for rid, fut in self._pending_outbound.items():
            if not fut.done(): fut.set_result({"type": "outbound_result", "requestId": rid, "result": {"success": False, "error": "disconnected"}})
        self._pending_outbound.clear()

    async def handshake(self):
        # CF DO doesn't send descriptor — use reasonable defaults
        from gateway.relay.descriptor import CapabilityDescriptor
        return CapabilityDescriptor(contract_version=1, platform=self._platform, label="CF Relay",
            max_message_length=4096, supports_draft_streaming=False, supports_edit=True,
            supports_threads=False, markdown_dialect="plain", len_unit="chars")

    def set_inbound_handler(self, handler): self._inbound_handler = handler

    async def send_outbound(self, action: Dict[str, Any], *, platform: Optional[str] = None) -> Dict[str, Any]:
        if not self._ws or not self._connected: return {"success": False, "error": "not connected"}
        rid = str(uuid.uuid4())
        frame = {"type": "outbound", "requestId": rid, "platform": platform or self._platform, "botId": self._bot_id, "action": action}
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_outbound[rid] = fut
        try:
            await self._ws.send(json.dumps(frame))
            result = await asyncio.wait_for(fut, timeout=_OUTBOUND_TIMEOUT_S)
            return result.get("result", {"success": False})
        except asyncio.TimeoutError: return {"success": False, "error": "timeout"}
        finally: self._pending_outbound.pop(rid, None)

    async def send_interrupt(self, session_key: str, reason: str = ""):
        if not self._ws: return
        try: await self._ws.send(json.dumps({"type": "interrupt", "session_key": session_key, "reason": reason}))
        except Exception: pass

    async def _read_loop(self):
        try:
            async for raw in self._ws:
                try: frame = json.loads(raw)
                except json.JSONDecodeError: continue
                if frame.get("type") == "inbound" and self._inbound_handler:
                    ev = frame.get("event", {})
                    from gateway.platforms.base import MessageEvent, MessageType
                    from gateway.session import SessionSource
                    from gateway.config import Platform as GWPlatform
                    mt = {"image": MessageType.PHOTO, "voice": MessageType.VOICE, "video": MessageType.VIDEO,
                          "document": MessageType.DOCUMENT, "location": MessageType.LOCATION,
                          "audio": MessageType.AUDIO, "sticker": MessageType.STICKER,
                          "callback": MessageType.COMMAND}.get(ev.get("type"), MessageType.TEXT)
                    plat_str = ev.get("platform", self._platform)
                    try: plat = GWPlatform(plat_str)
                    except ValueError: plat = GWPlatform.webhook
                    src = SessionSource(
                        platform=plat,
                        chat_id=ev.get("chat_id", ""),
                        user_id=ev.get("user_id", ""),
                        user_name=ev.get("user_name"),
                    )
                    await self._inbound_handler(MessageEvent(
                        text=ev.get("text") or "",
                        message_type=mt,
                        source=src,
                        raw_message=ev.get("raw", {}),
                        reply_to_message_id=ev.get("reply_to_message_id"),
                        media_urls=[ev["media_url"]] if ev.get("media_url") else [],
                    ))
                elif frame.get("type") == "outbound_result":
                    fut = self._pending_outbound.get(frame.get("requestId", ""))
                    if fut and not fut.done(): fut.set_result(frame)
        except Exception: logger.exception("CF relay read error")
        finally: self._connected = False; asyncio.create_task(self._reconnect())

    async def _heartbeat_loop(self):
        while self._connected:
            await asyncio.sleep(_HEARTBEAT_INTERVAL_S)
            if self._ws and self._connected:
                try: await asyncio.wait_for(self._ws.ping(), timeout=5.0)
                except Exception: self._connected = False; asyncio.create_task(self._reconnect()); break

    async def _reconnect(self):
        while not self._connected:
            await asyncio.sleep(self._reconnect_backoff)
            if await self.connect(): return
            self._reconnect_backoff = min(self._reconnect_backoff * 2, _RECONNECT_BACKOFF_MAX)
