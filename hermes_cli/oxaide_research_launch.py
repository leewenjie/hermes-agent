"""Authenticated, idempotent first-turn dispatch for Oxaide runtimes."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import uuid
from typing import Any

from hermes_constants import get_hermes_home
from hermes_state import SessionDB
from tui_gateway.server import handle_request
from tui_gateway.transport import bind_transport, reset_transport

_SCHEMA_VERSION = "research-launch.v1"
_SIGNATURE_PREFIX = "oxaide-research-launch-dispatch:v1"
_PAYLOAD_KEYS = {
    "schema_version", "dispatch_id", "workspace_id", "user_id",
    "runtime_key", "runtime_session_id", "prompt",
}
_RUNTIME_KEY_RE = re.compile(r"^[a-z0-9]{20,64}$")
_RUNTIME_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{3,200}$")


class InvalidResearchLaunchDispatch(ValueError):
    pass


class ResearchLaunchDispatchInProgress(RuntimeError):
    pass


class _MachineTransport:
    """Trusted gateway context with a sink for unconsumed streaming events."""

    def __init__(self, payload: dict[str, Any]):
        self.trusted_context = {
            "workspace_id": payload["workspace_id"],
            "user_id": payload["user_id"],
            "runtime_key": payload["runtime_key"],
            "runtime_session_id": payload["runtime_session_id"],
            "access_state": "active",
            "dispatch_id": payload["dispatch_id"],
        }

    def write(self, _obj: dict) -> bool:
        return True

    def close(self) -> None:
        return None


def _session_db() -> SessionDB:
    return SessionDB(get_hermes_home() / "state.db")


def _signing_secret() -> str:
    secret = str(os.environ.get("HERMES_OXAIDE_RESEARCH_LAUNCH_SIGNING_SECRET") or "").strip()
    lowered = secret.lower()
    if len(secret) < 32 or lowered.startswith("replace-with-") or lowered.startswith("__replace_with_"):
        raise RuntimeError("research launch signing is not configured")
    return secret


def sign_research_launch_dispatch(timestamp: str, raw_body: str) -> str:
    message = f"{_SIGNATURE_PREFIX}:{timestamp}.{raw_body}".encode("utf-8")
    return hmac.new(_signing_secret().encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_research_launch_dispatch(raw_body: bytes, timestamp: str, signature: str) -> bool:
    if not timestamp.isdigit() or len(timestamp) not in {10, 11, 12}:
        return False
    try:
        sent_at = int(timestamp)
    except ValueError:
        return False
    if abs(int(time.time()) - sent_at) > 300 or not re.fullmatch(r"[a-fA-F0-9]{64}", signature):
        return False
    try:
        expected = sign_research_launch_dispatch(timestamp, raw_body.decode("utf-8"))
    except (RuntimeError, UnicodeDecodeError):
        return False
    return hmac.compare_digest(expected, signature.lower())


def parse_research_launch_dispatch(raw_body: bytes) -> dict[str, Any]:
    if not raw_body or len(raw_body) > 16 * 1024:
        raise InvalidResearchLaunchDispatch("invalid_body")
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidResearchLaunchDispatch("invalid_json") from exc
    if not isinstance(payload, dict) or set(payload) != _PAYLOAD_KEYS:
        raise InvalidResearchLaunchDispatch("invalid_schema")
    if payload.get("schema_version") != _SCHEMA_VERSION:
        raise InvalidResearchLaunchDispatch("unsupported_schema")
    for key in ("dispatch_id", "user_id"):
        try:
            parsed = uuid.UUID(str(payload.get(key) or ""))
        except ValueError as exc:
            raise InvalidResearchLaunchDispatch(f"invalid_{key}") from exc
        if str(parsed) != payload[key].lower():
            raise InvalidResearchLaunchDispatch(f"invalid_{key}")
    workspace_id = payload.get("workspace_id")
    if not isinstance(workspace_id, str) or not workspace_id.strip() or len(workspace_id) > 200:
        raise InvalidResearchLaunchDispatch("invalid_workspace_id")
    if not _RUNTIME_KEY_RE.fullmatch(str(payload.get("runtime_key") or "")):
        raise InvalidResearchLaunchDispatch("invalid_runtime_key")
    if not _RUNTIME_SESSION_RE.fullmatch(str(payload.get("runtime_session_id") or "")):
        raise InvalidResearchLaunchDispatch("invalid_runtime_session_id")
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt.strip()) > 4000:
        raise InvalidResearchLaunchDispatch("invalid_prompt")
    payload["prompt"] = prompt.strip()

    workspace_pin = str(os.environ.get("HERMES_OXAIDE_WORKSPACE_ID") or "").strip()
    runtime_pin = str(os.environ.get("HERMES_OXAIDE_RUNTIME_KEY") or "").strip()
    if not workspace_pin or not runtime_pin:
        raise InvalidResearchLaunchDispatch("runtime_identity_not_configured")
    if payload["workspace_id"] != workspace_pin or payload["runtime_key"] != runtime_pin:
        raise InvalidResearchLaunchDispatch("runtime_identity_mismatch")
    return payload


def _rpc(method: str, params: dict[str, Any], request_id: int) -> dict[str, Any]:
    response = handle_request({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
    if not isinstance(response, dict) or "error" in response:
        message = "gateway request failed"
        if isinstance(response, dict):
            error = response.get("error")
            if isinstance(error, dict):
                message = str(error.get("message") or message)
        raise RuntimeError(message)
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("gateway returned an invalid result")
    return result


def accept_research_launch_dispatch(payload: dict[str, Any], raw_body: bytes) -> dict[str, Any]:
    digest = hashlib.sha256(raw_body).hexdigest()
    db = _session_db()
    lease_token = None
    try:
        receipt = db.accept_oxaide_research_launch_dispatch(payload, digest)
        if receipt.get("status") == "submitted" and receipt.get("hermes_session_id"):
            return {
                "dispatch_id": payload["dispatch_id"],
                "session_id": receipt["hermes_session_id"],
                "replayed": True,
            }

        lease_token = db.claim_oxaide_research_launch_dispatch(payload["dispatch_id"])
        if not lease_token:
            current = db.get_oxaide_research_launch_dispatch(payload["dispatch_id"]) or {}
            if current.get("status") == "submitted" and current.get("hermes_session_id"):
                return {
                    "dispatch_id": payload["dispatch_id"],
                    "session_id": current["hermes_session_id"],
                    "replayed": True,
                }
            raise ResearchLaunchDispatchInProgress("research_launch_dispatch_in_progress")

        transport = _MachineTransport(payload)
        token = bind_transport(transport)
        try:
            current = db.get_oxaide_research_launch_dispatch(payload["dispatch_id"]) or {}
            durable_session_id = str(current.get("hermes_session_id") or "").strip()
            turn_is_durable = db.oxaide_research_launch_user_turn_is_durable(payload["dispatch_id"])
            if durable_session_id:
                resumed = _rpc("session.resume", {"session_id": durable_session_id}, 1)
                live_session_id = str(resumed.get("session_id") or "").strip()
            else:
                created = _rpc("session.create", {"source": "oxaide"}, 1)
                live_session_id = str(created.get("session_id") or "").strip()
                durable_session_id = str(created.get("stored_session_id") or "").strip()
                if not live_session_id or not durable_session_id:
                    raise RuntimeError("gateway did not create a durable session")
                if not db.attach_oxaide_research_launch_session(
                    payload["dispatch_id"], lease_token, durable_session_id,
                    payload["user_id"],
                ):
                    raise RuntimeError("research launch lease lost while attaching session")

            if not turn_is_durable:
                submitted = _rpc(
                    "prompt.submit",
                    {"session_id": live_session_id, "text": payload["prompt"]},
                    2,
                )
                if submitted.get("status") not in {"streaming", "queued"}:
                    raise RuntimeError("gateway did not accept the research turn")
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    if db.oxaide_research_launch_user_turn_is_durable(payload["dispatch_id"]):
                        turn_is_durable = True
                        break
                    time.sleep(0.05)
                if not turn_is_durable:
                    raise RuntimeError("research turn was not durably stored before timeout")
            if not db.complete_oxaide_research_launch_dispatch(payload["dispatch_id"], lease_token):
                raise RuntimeError("research launch lease lost after prompt acceptance")
            return {
                "dispatch_id": payload["dispatch_id"],
                "session_id": durable_session_id,
                "replayed": bool(receipt.get("replayed")),
            }
        finally:
            reset_transport(token)
    except Exception as exc:
        if lease_token:
            db.release_oxaide_research_launch_dispatch(
                payload["dispatch_id"], lease_token, type(exc).__name__
            )
        raise
    finally:
        db.close()
