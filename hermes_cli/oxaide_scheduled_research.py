"""Durable execution bridge for Oxaide-managed Scheduled Research.

Oxaide owns schedules, due-time calculation, and global dispatch leases. Hermes
only authenticates immutable occurrences, fences local replay in SQLite, runs
one authorized agent turn, and reports ordered lifecycle events.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

from hermes_constants import get_hermes_home
from hermes_state import SessionDB

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = "scheduled-research.v1"
_SIGNATURE_PREFIX = {
    "control": "scheduled-research-control:v1",
    "dispatch": "scheduled-research-dispatch:v1",
    "event": "scheduled-research-event:v1",
}
_DEFAULT_EVENT_ENDPOINT = "https://oxaide.com/api/agents/research-schedule-events"
_EVENT_TIMEOUT_SECONDS = 10.0
_LOCAL_LEASE_SECONDS = 240
_LOCAL_LEASE_RENEWAL_SECONDS = 60.0
_worker_lock = threading.Lock()
_worker_thread: threading.Thread | None = None
_worker_wake = threading.Event()


def _session_db() -> SessionDB:
    return SessionDB(get_hermes_home() / "state.db")

_OCCURRENCE_KEYS = {
    "schema_version", "occurrence_id", "schedule_id", "schedule_revision",
    "workspace_id", "user_id", "runtime_key", "runtime_session_id",
    "nominal_fire_at", "dispatched_at", "name", "prompt", "schedule", "timezone",
}
_START_KEYS = {
    "schema_version", "command", "occurrence_id", "schedule_id",
    "workspace_id", "user_id", "runtime_key", "runtime_session_id",
}
_RUNTIME_KEY_RE = re.compile(r"^[a-z0-9]{20,64}$")
_RUNTIME_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{3,200}$")


class InvalidOccurrenceDispatch(ValueError):
    pass


def _timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value or len(value) > 100:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.tzinfo is not None
    except ValueError:
        return False


def parse_occurrence_dispatch(raw_body: bytes) -> dict[str, Any]:
    if not raw_body or len(raw_body) > 64 * 1024:
        raise InvalidOccurrenceDispatch("invalid_body")
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidOccurrenceDispatch("invalid_json") from exc
    if not isinstance(payload, dict) or set(payload) != _OCCURRENCE_KEYS:
        raise InvalidOccurrenceDispatch("invalid_schema")
    if payload.get("schema_version") != _SCHEMA_VERSION:
        raise InvalidOccurrenceDispatch("unsupported_schema")
    for key in ("occurrence_id", "schedule_id"):
        try:
            uuid.UUID(str(payload.get(key) or ""))
        except ValueError as exc:
            raise InvalidOccurrenceDispatch(f"invalid_{key}") from exc
    if (
        isinstance(payload.get("schedule_revision"), bool)
        or not isinstance(payload.get("schedule_revision"), int)
        or payload["schedule_revision"] < 1
    ):
        raise InvalidOccurrenceDispatch("invalid_schedule_revision")
    for key, maximum in (
        ("workspace_id", 200), ("user_id", 200), ("timezone", 100),
    ):
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise InvalidOccurrenceDispatch(f"invalid_{key}")
    if not _RUNTIME_KEY_RE.fullmatch(str(payload.get("runtime_key") or "")):
        raise InvalidOccurrenceDispatch("invalid_runtime_key")
    if not _RUNTIME_SESSION_RE.fullmatch(str(payload.get("runtime_session_id") or "")):
        raise InvalidOccurrenceDispatch("invalid_runtime_session_id")
    if not _timestamp(payload.get("nominal_fire_at")) or not _timestamp(payload.get("dispatched_at")):
        raise InvalidOccurrenceDispatch("invalid_timestamp")
    name = payload.get("name")
    prompt = payload.get("prompt")
    if not isinstance(name, str) or len(name.strip()) > 120:
        raise InvalidOccurrenceDispatch("invalid_name")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt.strip()) > 12_000:
        raise InvalidOccurrenceDispatch("invalid_prompt")
    schedule = payload.get("schedule")
    if not isinstance(schedule, dict) or schedule.get("kind") not in {"once", "interval", "cron"}:
        raise InvalidOccurrenceDispatch("invalid_schedule")
    expected_schedule_keys = {
        "once": {"kind", "run_at", "display"},
        "interval": {"kind", "minutes", "display"},
        "cron": {"kind", "expr", "display"},
    }[schedule["kind"]]
    if set(schedule) != expected_schedule_keys:
        raise InvalidOccurrenceDispatch("invalid_schedule")
    display = schedule.get("display")
    if not isinstance(display, str) or not display.strip() or len(display.strip()) > 200:
        raise InvalidOccurrenceDispatch("invalid_schedule")
    if schedule["kind"] == "once" and not _timestamp(schedule.get("run_at")):
        raise InvalidOccurrenceDispatch("invalid_schedule")
    if schedule["kind"] == "interval" and (
        isinstance(schedule.get("minutes"), bool)
        or not isinstance(schedule.get("minutes"), int)
        or not 1 <= schedule["minutes"] <= 14_398_560
    ):
        raise InvalidOccurrenceDispatch("invalid_schedule")
    if schedule["kind"] == "cron":
        expr = schedule.get("expr")
        fields = expr.split() if isinstance(expr, str) else []
        if len(fields) != 5 or any(not re.fullmatch(r"[\d*\-,/]+", field) for field in fields):
            raise InvalidOccurrenceDispatch("invalid_schedule")

    workspace_pin = str(os.environ.get("HERMES_OXAIDE_WORKSPACE_ID") or "").strip()
    runtime_pin = str(os.environ.get("HERMES_OXAIDE_RUNTIME_KEY") or "").strip()
    if not workspace_pin or not runtime_pin:
        raise InvalidOccurrenceDispatch("runtime_identity_not_configured")
    if payload["workspace_id"] != workspace_pin or payload["runtime_key"] != runtime_pin:
        raise InvalidOccurrenceDispatch("runtime_identity_mismatch")
    return payload


def parse_occurrence_start(raw_body: bytes) -> dict[str, Any]:
    if not raw_body or len(raw_body) > 16 * 1024:
        raise InvalidOccurrenceDispatch("invalid_body")
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidOccurrenceDispatch("invalid_json") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != _START_KEYS
        or payload.get("schema_version") != _SCHEMA_VERSION
        or payload.get("command") != "start"
    ):
        raise InvalidOccurrenceDispatch("invalid_start_schema")
    for key in ("occurrence_id", "schedule_id"):
        try:
            uuid.UUID(str(payload.get(key) or ""))
        except ValueError as exc:
            raise InvalidOccurrenceDispatch(f"invalid_{key}") from exc
    for key, maximum in (("workspace_id", 200), ("user_id", 200)):
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise InvalidOccurrenceDispatch(f"invalid_{key}")
    if not _RUNTIME_KEY_RE.fullmatch(str(payload.get("runtime_key") or "")):
        raise InvalidOccurrenceDispatch("invalid_runtime_key")
    if not _RUNTIME_SESSION_RE.fullmatch(str(payload.get("runtime_session_id") or "")):
        raise InvalidOccurrenceDispatch("invalid_runtime_session_id")
    workspace_pin = str(os.environ.get("HERMES_OXAIDE_WORKSPACE_ID") or "").strip()
    runtime_pin = str(os.environ.get("HERMES_OXAIDE_RUNTIME_KEY") or "").strip()
    if not workspace_pin or not runtime_pin:
        raise InvalidOccurrenceDispatch("runtime_identity_not_configured")
    if payload["workspace_id"] != workspace_pin or payload["runtime_key"] != runtime_pin:
        raise InvalidOccurrenceDispatch("runtime_identity_mismatch")
    return payload


def _signing_secret() -> str:
    secret = str(
        os.environ.get("HERMES_OXAIDE_SCHEDULED_RESEARCH_SIGNING_SECRET") or ""
    ).strip()
    lowered = secret.lower()
    if (
        len(secret) < 32
        or lowered.startswith("replace-with-")
        or lowered.startswith("__replace_with_")
    ):
        raise RuntimeError("scheduled research signing is not configured")
    return secret


def sign_request(purpose: str, timestamp: str, raw_body: str) -> str:
    prefix = _SIGNATURE_PREFIX[purpose]
    message = f"{prefix}:{timestamp}.{raw_body}".encode("utf-8")
    return hmac.new(_signing_secret().encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_occurrence_dispatch(raw_body: bytes, timestamp: str, signature: str) -> bool:
    if not timestamp.isdigit() or len(timestamp) not in {10, 11, 12}:
        return False
    try:
        sent_at = int(timestamp)
    except ValueError:
        return False
    if abs(int(time.time()) - sent_at) > 300:
        return False
    if len(signature) != 64:
        return False
    try:
        expected = sign_request("dispatch", timestamp, raw_body.decode("utf-8"))
    except (RuntimeError, UnicodeDecodeError):
        return False
    return hmac.compare_digest(expected, signature.lower())


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _event_body(payload: dict[str, Any], status: str, **fields: Any) -> tuple[str, str]:
    event_id = str(uuid.uuid4())
    event = {
        "schema_version": _SCHEMA_VERSION,
        "event_id": event_id,
        "occurrence_id": payload["occurrence_id"],
        "schedule_id": payload["schedule_id"],
        "workspace_id": payload["workspace_id"],
        "user_id": payload["user_id"],
        "runtime_key": payload["runtime_key"],
        "runtime_session_id": payload["runtime_session_id"],
        "status": status,
        "occurred_at": _iso_now(),
    }
    event.update({key: value for key, value in fields.items() if value is not None})
    return event_id, json.dumps(event, separators=(",", ":"), sort_keys=True)


def enqueue_occurrence_event(
    db: SessionDB,
    payload: dict[str, Any],
    sequence: int,
    status: str,
    **fields: Any,
) -> None:
    event_id, raw_body = _event_body(payload, status, **fields)
    db.enqueue_scheduled_research_event(
        payload["occurrence_id"], sequence, event_id, raw_body
    )


def accept_occurrence(payload: dict[str, Any], raw_body: bytes) -> bool:
    digest = hashlib.sha256(raw_body).hexdigest()
    db = _session_db()
    try:
        replayed = db.accept_scheduled_research_occurrence(payload, digest)
    finally:
        db.close()
    return replayed


def authorize_occurrence(payload: dict[str, Any]) -> bool:
    db = _session_db()
    try:
        replayed = db.authorize_scheduled_research_occurrence(payload)
        stored = db.get_scheduled_research_occurrence_payload(payload["occurrence_id"])
        enqueue_occurrence_event(db, stored, 1, "accepted")
    finally:
        db.close()
    wake_occurrence_worker()
    return replayed


def _compute_next_run(payload: dict[str, Any]) -> str | None:
    schedule = payload.get("schedule")
    if not isinstance(schedule, dict) or schedule.get("kind") == "once":
        return None
    from cron.jobs import compute_next_run

    return compute_next_run(schedule, payload.get("nominal_fire_at"))


def _persist_occurrence_result(
    occurrence_id: str,
    document: Any,
    final_response: Any,
) -> str:
    """Atomically persist a completed result under the managed files root."""
    uuid.UUID(occurrence_id)
    configured_root = str(
        os.environ.get("HERMES_DASHBOARD_FILES_ROOT") or ""
    ).strip()
    if configured_root:
        root = Path(configured_root).expanduser()
    else:
        root = Path("/opt/data") if Path("/opt/data").is_dir() else get_hermes_home()
    results = root / "research-results"
    results.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = results / f"{occurrence_id}.md"
    content = str(document or final_response or "Scheduled research completed without a text result.")
    temporary = results / f".{occurrence_id}.{uuid.uuid4().hex}.tmp"
    temporary.write_text(content, encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, target)
    return f"/research-results/{occurrence_id}.md"


def _event_endpoint() -> str:
    return str(
        os.environ.get("OXAIDE_SCHEDULED_RESEARCH_EVENT_ENDPOINT")
        or _DEFAULT_EVENT_ENDPOINT
    ).strip()


def _deliver_event(raw_body: str) -> tuple[bool, bool, str]:
    timestamp = str(int(time.time()))
    request = urllib.request.Request(
        _event_endpoint(),
        data=raw_body.encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Oxaide-Hermes-Runtime/1",
            "X-Oxaide-Scheduled-Research-Timestamp": timestamp,
            "X-Oxaide-Scheduled-Research-Signature": sign_request(
                "event", timestamp, raw_body
            ),
        },
    )
    status = 0
    response_body = b""
    try:
        with urllib.request.urlopen(request, timeout=_EVENT_TIMEOUT_SECONDS) as response:
            status = int(response.status)
            response_body = response.read(16 * 1024)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        response_body = exc.read(16 * 1024)
    except Exception as exc:
        return False, False, f"event_endpoint_unavailable:{type(exc).__name__}"

    try:
        decoded = json.loads(response_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        decoded = {}
    code = str(decoded.get("code") or "") if isinstance(decoded, dict) else ""
    if 200 <= status < 300 and isinstance(decoded, dict) and decoded.get("ok") is True:
        return True, False, ""
    if status == 409 and code == "research_occurrence_transition_invalid":
        return False, False, code
    permanent = 400 <= status < 500
    return False, permanent, code or f"event_endpoint_http_{status}"


def flush_occurrence_events(db: SessionDB | None = None) -> None:
    owns_db = db is None
    db = db or _session_db()
    try:
        for event in db.list_pending_scheduled_research_events(limit=20):
            delivered, permanent, error = _deliver_event(event["raw_body"])
            attempts = int(event.get("attempt_count") or 0) + 1
            db.settle_scheduled_research_event(
                event["event_id"],
                delivered=delivered,
                dead_letter=permanent,
                retry_delay_seconds=min(900, 5 * (2 ** min(attempts, 7))),
                error=error,
            )
            if not delivered:
                break
    finally:
        if owns_db:
            db.close()


def _run_occurrence(db: SessionDB, claim: dict[str, Any]) -> None:
    from cron.scheduler import run_job
    from tui_gateway.oxaide_turns import OxaideTurnClient, OxaideTurnDenied

    payload = claim["payload"]
    occurrence_id = payload["occurrence_id"]
    lease_token = claim["lease_token"]
    lease_lost = threading.Event()
    turn = None
    terminal_status = "failed"
    error_code = None
    error_message = None
    success = False
    local_lease_stop = threading.Event()

    def renew_local_lease() -> None:
        while not local_lease_stop.wait(timeout=_LOCAL_LEASE_RENEWAL_SECONDS):
            if not db.renew_scheduled_research_occurrence_lease(
                occurrence_id,
                lease_token,
                lease_seconds=_LOCAL_LEASE_SECONDS,
            ):
                logger.error(
                    "Scheduled research local lease renewal lost id=%s",
                    occurrence_id,
                )
                lease_lost.set()
                return

    local_lease_thread = threading.Thread(
        target=renew_local_lease,
        daemon=True,
        name=f"scheduled-research-lease-{occurrence_id[:8]}",
    )
    local_lease_thread.start()

    try:
        client = OxaideTurnClient.from_scheduled_occurrence(
            workspace_id=payload["workspace_id"],
            user_id=payload["user_id"],
            runtime_key=payload["runtime_key"],
            runtime_session_id=payload["runtime_session_id"],
        )
        turn = client.authorize(
            lambda _reason: lease_lost.set(),
            event_id=occurrence_id,
        )
        enqueue_occurrence_event(db, payload, 2, "running")
        flush_occurrence_events(db)

        job = {
            "id": occurrence_id,
            "name": payload.get("name") or "Scheduled research",
            "prompt": payload["prompt"],
            "schedule": payload["schedule"],
            "schedule_display": payload["schedule"].get("display", ""),
            "deliver": "local",
            "enabled": True,
            "skills": [
                "investment-research", "market-return-analysis", "polymarket", "stocks"
            ],
            "enabled_toolsets": [
                "delegation", "file", "session_search", "terminal", "todo", "vision", "web"
            ],
            "no_agent": False,
            "attach_to_session": False,
            "origin": {"type": "oxaide-scheduled-research-v1"},
        }
        success, document, final_response, run_error = run_job(
            job, cancel_requested=lease_lost.is_set
        )
        if "managed_scheduled_research_timeout" in str(run_error or ""):
            terminal_status = "failed"
            error_code = "execution_timeout"
            error_message = (
                "Scheduled research exceeded the 3 minute execution limit."
            )
            turn.release()
        elif lease_lost.is_set():
            terminal_status = "released"
            error_code = "billing_lease_lost"
            error_message = "Scheduled research authorization expired during execution."
            turn.release()
        elif success:
            result_artifact_ref = _persist_occurrence_result(
                occurrence_id, document, final_response
            )
            turn.complete({
                "schema_version": "2026-07-18-v1",
                "origin": "scheduled_research",
            })
            terminal_status = "completed"
        else:
            turn.release()
            terminal_status = "failed"
            error_code = "execution_failed"
            error_message = str(run_error or "Scheduled research execution failed.")[:2000]
    except OxaideTurnDenied as exc:
        terminal_status = "released"
        error_code = exc.code or "turn_authorization_denied"
        error_message = "Scheduled research was not authorized."
    except Exception as exc:
        if turn is not None:
            turn.release()
        terminal_status = "released" if lease_lost.is_set() else "failed"
        error_code = (
            "billing_lease_lost" if lease_lost.is_set()
            else "scheduled_research_execution_failed"
        )
        error_message = str(exc)[:2000] or error_code
        logger.exception("Scheduled research occurrence failed id=%s", occurrence_id)

    local_lease_stop.set()
    local_lease_thread.join(timeout=5.0)
    finished = db.finish_scheduled_research_occurrence(
        occurrence_id,
        lease_token,
        terminal_status,
        billing_event_id=occurrence_id,
        error_code=error_code if terminal_status == "failed" else None,
        error=error_message if terminal_status == "failed" else None,
    )
    if not finished:
        logger.error("Scheduled research local lease lost id=%s", occurrence_id)
        return
    event_fields: dict[str, Any] = {"next_run_at": _compute_next_run(payload)}
    if terminal_status == "completed":
        event_fields["result_artifact_ref"] = result_artifact_ref
    if terminal_status == "failed":
        event_fields.update({
            "error_code": error_code or "execution_failed",
            "error_message": error_message or "Scheduled research execution failed.",
        })
    enqueue_occurrence_event(db, payload, 3, terminal_status, **event_fields)
    flush_occurrence_events(db)


def _worker_loop() -> None:
    while True:
        _worker_wake.clear()
        db = _session_db()
        try:
            flush_occurrence_events(db)
            while True:
                claim = db.claim_scheduled_research_occurrence(
                    lease_seconds=_LOCAL_LEASE_SECONDS
                )
                if claim is None:
                    break
                _run_occurrence(db, claim)
        except Exception:
            logger.exception("Scheduled research worker iteration failed")
        finally:
            db.close()
        # Remain alive so delayed outbox retries and expired local leases are
        # recovered without requiring a new occurrence or process restart.
        _worker_wake.wait(timeout=30.0)


def wake_occurrence_worker() -> None:
    global _worker_thread
    _worker_wake.set()
    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return
        _worker_thread = threading.Thread(
            target=_worker_loop,
            daemon=True,
            name="oxaide-scheduled-research",
        )
        _worker_thread.start()


def resume_pending_occurrences() -> None:
    """Resume accepted work and event delivery after a process restart."""
    wake_occurrence_worker()
