"""Minimal Oxaide turn authorization and settlement client.

The Oxaide control plane owns entitlement and billing policy. This module only
submits an immutable event identity through authorize/complete/release and
keeps failed settlement deliveries in a small profile-safe outbox.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

_DEFAULT_ENDPOINT = "https://oxaide.com/api/agents/billing/usage/record"
_TIMEOUT_SECONDS = 5.0
_HEARTBEAT_INTERVAL_SECONDS = 30.0
_TERMINAL_SETTLEMENT_CODES = {
    "runtime_session_not_authorized",
    "turn_already_completed",
    "turn_event_not_reusable",
    "turn_event_runtime_mismatch",
    "turn_reservation_binding_fenced",
    "turn_reservation_not_active",
    "turn_reservation_not_found",
}
_EXPECTED_CODES = {
    "authorize": "turn_authorized",
    "heartbeat": "turn_lease_renewed",
    "complete": "turn_completed",
    "release": "turn_released",
}


class OxaideTurnError(RuntimeError):
    """The Oxaide turn contract could not be satisfied."""

    def __init__(self, message: str, *, code: str = "") -> None:
        self.code = code
        super().__init__(message)


class OxaideTurnDenied(OxaideTurnError):
    """Oxaide explicitly denied authorization for a turn."""

    def __init__(self, code: str) -> None:
        self.code = code or "turn_authorization_denied"
        super().__init__(self.code, code=self.code)


@dataclass(frozen=True)
class OxaideTurn:
    client: "OxaideTurnClient"
    event_id: str

    def complete(self, details: dict[str, Any] | None = None) -> None:
        self.client.stop_heartbeat()
        self.client.settle("complete", self.event_id, details=details)

    def release(self) -> None:
        self.client.stop_heartbeat()
        self.client.settle("release", self.event_id)


class OxaideTurnClient:
    """Strict stdlib HTTP client for one trusted Oxaide runtime identity."""

    def __init__(self, trusted_context: dict[str, Any]) -> None:
        self.context_kind = str(
            trusted_context.get("context_kind") or "browser_launch"
        ).strip()
        self.workspace_id = str(trusted_context.get("workspace_id") or "").strip()
        self.runtime_session_id = str(
            trusted_context.get("runtime_session_id") or ""
        ).strip()
        self.runtime_key = str(trusted_context.get("runtime_key") or "").strip()
        self.user_id = str(trusted_context.get("user_id") or "").strip()
        self.jti = str(trusted_context.get("jti") or "").strip()
        try:
            self.expires_at = int(trusted_context.get("expires_at") or 0)
        except (TypeError, ValueError):
            self.expires_at = 0

        required_identity = all((
            self.workspace_id,
            self.runtime_session_id,
            self.runtime_key,
            self.user_id,
        ))
        browser_context_complete = bool(self.jti and self.expires_at)
        if (
            not required_identity
            or self.context_kind not in {"browser_launch", "scheduled_research"}
            or (self.context_kind == "browser_launch" and not browser_context_complete)
        ):
            raise OxaideTurnError("trusted Oxaide launch context is incomplete")

        self.endpoint = str(
            os.environ.get("OXAIDE_TURN_ENDPOINT") or _DEFAULT_ENDPOINT
        ).strip()
        self.secret = str(
            os.environ.get("HERMES_OXAIDE_USAGE_SIGNING_SECRET") or ""
        ).strip()
        lowered_secret = self.secret.lower()
        if (
            not self.endpoint
            or len(self.secret) < 32
            or lowered_secret.startswith("replace-with-")
            or lowered_secret.startswith("__replace_with_")
        ):
            raise OxaideTurnError("Oxaide turn authorization is not configured")
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._settlement_started = False

    @classmethod
    def from_scheduled_occurrence(
        cls,
        *,
        workspace_id: str,
        user_id: str,
        runtime_key: str,
        runtime_session_id: str,
    ) -> "OxaideTurnClient":
        return cls({
            "context_kind": "scheduled_research",
            "workspace_id": workspace_id,
            "user_id": user_id,
            "runtime_key": runtime_key,
            "runtime_session_id": runtime_session_id,
        })

    def authorize(self, on_lease_lost=None, *, event_id: str | None = None) -> OxaideTurn:
        # ``expires_at`` limits the one-time browser launch exchange. Once the
        # authenticated PTY is established, the durable runtime session in
        # Oxaide is the authority for subsequent turns. Reapplying the launch
        # TTL here would break a valid research desk after a few minutes.
        self.flush_outbox()
        effective_event_id = str(event_id or uuid.uuid4().hex).strip()
        if not effective_event_id or len(effective_event_id) > 200:
            raise OxaideTurnError("invalid Oxaide turn event identity")
        self._request("authorize", effective_event_id)
        if on_lease_lost is not None:
            self._start_heartbeat(effective_event_id, on_lease_lost)
        return OxaideTurn(client=self, event_id=effective_event_id)

    def _start_heartbeat(self, event_id: str, on_lease_lost) -> None:
        if self._heartbeat_thread is not None:
            raise OxaideTurnError("Oxaide turn heartbeat already started")

        def heartbeat_loop() -> None:
            while not self._heartbeat_stop.wait(_HEARTBEAT_INTERVAL_SECONDS):
                try:
                    self._request("heartbeat", event_id)
                except OxaideTurnError as exc:
                    with self._state_lock:
                        if self._settlement_started:
                            return
                    self._heartbeat_stop.set()
                    logger.error(
                        "Oxaide turn lease lost event=%s error=%s",
                        event_id[:12],
                        exc,
                    )
                    try:
                        on_lease_lost(str(exc) or "turn_lease_renewal_failed")
                    except Exception:
                        logger.exception("Oxaide turn lease-loss callback failed")
                    return

        self._heartbeat_thread = threading.Thread(
            target=heartbeat_loop,
            name=f"oxaide-turn-heartbeat-{event_id[:8]}",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def stop_heartbeat(self) -> None:
        with self._state_lock:
            self._settlement_started = True
        self._heartbeat_stop.set()
        thread = self._heartbeat_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=_TIMEOUT_SECONDS + 1.0)

    def settle(
        self,
        phase: str,
        event_id: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        if phase not in {"complete", "release"}:
            raise ValueError("settlement phase must be complete or release")
        payload = self._payload(phase, event_id, details=details)
        pending = None
        try:
            pending = self._write_outbox(payload)
        except OSError:
            logger.warning(
                "Oxaide turn outbox write failed event=%s", event_id[:12]
            )
        try:
            self._request_payload(payload)
        except OxaideTurnError as exc:
            if pending is not None and self._is_terminal_settlement_error(exc):
                self._dead_letter(pending, str(exc))
                return
            logger.warning(
                "Oxaide turn %s delivery deferred event=%s error=%s",
                phase,
                event_id[:12],
                exc,
            )
            return
        if pending is not None:
            try:
                pending.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "Oxaide turn outbox cleanup failed event=%s", event_id[:12]
                )

    def flush_outbox(self) -> None:
        outbox = self._outbox_dir()
        if not outbox.is_dir():
            return
        attempted = 0
        for pending in sorted(outbox.glob("*.json")):
            try:
                payload = json.loads(pending.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("outbox payload is not an object")
                if payload.get("workspace_id") != self.workspace_id:
                    continue
                if attempted >= 20:
                    break
                attempted += 1
                self._request_payload(payload)
                pending.unlink(missing_ok=True)
            except OxaideTurnError as exc:
                if self._is_terminal_settlement_error(exc):
                    self._dead_letter(pending, str(exc))
                    continue
                logger.debug("Oxaide turn outbox retry deferred", exc_info=True)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                self._dead_letter(pending, f"invalid_outbox_record:{type(exc).__name__}")

    def _payload(
        self,
        phase: str,
        event_id: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "phase": phase,
            "workspace_id": self.workspace_id,
            "runtime_session_id": self.runtime_session_id,
            "hermes_event_id": event_id,
        }
        if phase == "complete":
            payload["completed_at"] = (
                datetime.now(timezone.utc).isoformat(timespec="seconds")
            )
            if details:
                payload["details"] = details
        return payload

    def _request(self, phase: str, event_id: str) -> dict[str, Any]:
        return self._request_payload(self._payload(phase, event_id))

    def _request_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        phase = str(payload.get("phase") or "")
        if phase not in _EXPECTED_CODES:
            raise OxaideTurnError("invalid Oxaide turn phase")
        body = json.dumps(
            payload, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        timestamp = str(int(time.time()))
        signature = hmac.new(
            self.secret.encode("utf-8"),
            timestamp.encode("ascii") + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        request = urllib.request.Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "Oxaide-Hermes-Runtime/1",
                "X-Oxaide-Usage-Timestamp": timestamp,
                "X-Oxaide-Usage-Signature": signature,
            },
        )
        status = 0
        raw = b""
        try:
            with urllib.request.urlopen(
                request, timeout=_TIMEOUT_SECONDS
            ) as response:
                status = int(response.status)
                raw = response.read(16 * 1024)
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            raw = exc.read(16 * 1024)
        except Exception as exc:
            raise OxaideTurnError(
                f"Oxaide turn endpoint unavailable ({type(exc).__name__})"
            ) from exc

        try:
            response = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OxaideTurnError("Oxaide turn endpoint returned invalid JSON") from exc
        if not isinstance(response, dict):
            raise OxaideTurnError("Oxaide turn endpoint returned an invalid response")

        code = str(response.get("code") or "").strip()
        if status < 200 or status >= 300 or response.get("ok") is not True:
            if phase == "authorize":
                raise OxaideTurnDenied(code)
            raise OxaideTurnError(
                code or f"Oxaide turn endpoint returned HTTP {status}",
                code=code,
            )
        if response.get("phase") != phase:
            raise OxaideTurnError("Oxaide turn response phase mismatch")
        if code != _EXPECTED_CODES[phase]:
            raise OxaideTurnError("Oxaide turn response code mismatch")
        if response.get("workspace_id") != self.workspace_id:
            raise OxaideTurnError("Oxaide turn response workspace mismatch")
        return response

    def _outbox_dir(self) -> Path:
        return get_hermes_home() / "oxaide-turn-outbox"

    @staticmethod
    def _is_terminal_settlement_error(error: OxaideTurnError) -> bool:
        return error.code in _TERMINAL_SETTLEMENT_CODES

    def _dead_letter(self, pending: Path, reason: str) -> None:
        dead_letters = self._outbox_dir() / "dead-letter"
        try:
            dead_letters.mkdir(parents=True, exist_ok=True)
            os.chmod(dead_letters, 0o700)
            target = dead_letters / pending.name
            os.replace(pending, target)
            metadata = dead_letters / f"{pending.name}.reason.json"
            metadata.write_text(
                json.dumps(
                    {
                        "dead_lettered_at": datetime.now(timezone.utc).isoformat(
                            timespec="seconds"
                        ),
                        "reason": reason[:500],
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            os.chmod(metadata, 0o600)
            logger.warning(
                "Oxaide turn settlement dead-lettered file=%s reason=%s",
                pending.name,
                reason,
            )
        except OSError:
            logger.warning(
                "Oxaide turn dead-letter write failed file=%s",
                pending.name,
                exc_info=True,
            )

    def _write_outbox(self, payload: dict[str, Any]) -> Path:
        outbox = self._outbox_dir()
        outbox.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(outbox, 0o700)
        except OSError:
            pass
        event_id = str(payload["hermes_event_id"])
        phase = str(payload["phase"])
        file_id = hashlib.sha256(event_id.encode("utf-8")).hexdigest()
        target = outbox / f"{file_id}.{phase}.json"
        temporary = outbox / f".{file_id}.{phase}.{uuid.uuid4().hex}.tmp"
        data = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        with open(temporary, "x", encoding="utf-8") as handle:
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        return target
