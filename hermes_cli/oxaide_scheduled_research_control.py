"""Signed Hermes-to-Oxaide control client for managed research schedules."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import urllib.error
import urllib.request
import uuid
from typing import Any

from cron.jobs import compute_next_run, parse_schedule
from hermes_cli.oxaide_scheduled_research import sign_request

_DEFAULT_ENDPOINT = "https://oxaide.com/api/agents/research-schedules"


class ScheduledResearchControlError(RuntimeError):
    def __init__(self, code: str, status: int = 500):
        super().__init__(code)
        self.code = code
        self.status = status


def enabled() -> bool:
    return all(
        str(os.environ.get(name) or "").strip()
        for name in (
            "HERMES_OXAIDE_WORKSPACE_ID",
            "HERMES_OXAIDE_RUNTIME_KEY",
            "HERMES_OXAIDE_SCHEDULED_RESEARCH_SIGNING_SECRET",
        )
    )


def _identity(user_id: str, request_id: str | None = None) -> dict[str, Any]:
    workspace_id = str(os.environ.get("HERMES_OXAIDE_WORKSPACE_ID") or "").strip()
    runtime_key = str(os.environ.get("HERMES_OXAIDE_RUNTIME_KEY") or "").strip()
    if not enabled() or not user_id:
        raise ScheduledResearchControlError("scheduled_research_not_configured", 503)
    try:
        stable_request_id = str(uuid.UUID(request_id)) if request_id else str(uuid.uuid4())
    except (ValueError, AttributeError, TypeError) as exc:
        raise ScheduledResearchControlError("scheduled_research_request_id_invalid", 400) from exc
    return {
        "schema_version": "scheduled-research.v1",
        "workspace_id": workspace_id,
        "user_id": user_id,
        "runtime_key": runtime_key,
        "request_id": stable_request_id,
    }


def request_control(
    user_id: str,
    action: str,
    *,
    request_id: str | None = None,
    **fields: Any,
) -> Any:
    import time

    if action in {"create", "update", "pause", "resume", "delete"} and not request_id:
        raise ScheduledResearchControlError("scheduled_research_request_id_required", 400)
    body = {**_identity(user_id, request_id), "action": action, **fields}
    raw_body = json.dumps(body, separators=(",", ":"), sort_keys=True)
    timestamp = str(int(time.time()))
    request = urllib.request.Request(
        str(os.environ.get("OXAIDE_SCHEDULED_RESEARCH_CONTROL_ENDPOINT") or _DEFAULT_ENDPOINT),
        data=raw_body.encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Oxaide-Hermes-Runtime/1",
            "X-Oxaide-Scheduled-Research-Timestamp": timestamp,
            "X-Oxaide-Scheduled-Research-Signature": sign_request(
                "control", timestamp, raw_body
            ),
        },
    )
    status = 0
    raw = b""
    try:
        with urllib.request.urlopen(request, timeout=15.0) as response:
            status = int(response.status)
            raw = response.read(128 * 1024)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        raw = exc.read(16 * 1024)
    except Exception as exc:
        raise ScheduledResearchControlError("scheduled_research_endpoint_unavailable", 503) from exc
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScheduledResearchControlError("scheduled_research_response_invalid", 502) from exc
    if not 200 <= status < 300 or not isinstance(decoded, dict) or decoded.get("ok") is not True:
        code = str(decoded.get("code") or f"scheduled_research_http_{status}") if isinstance(decoded, dict) else f"scheduled_research_http_{status}"
        raise ScheduledResearchControlError(code, status or 502)
    if action == "list":
        schedules = decoded.get("schedules")
        if not isinstance(schedules, list):
            raise ScheduledResearchControlError("scheduled_research_response_invalid", 502)
        return schedules
    if action == "list_occurrences":
        occurrences = decoded.get("occurrences")
        if not isinstance(occurrences, list):
            raise ScheduledResearchControlError("scheduled_research_response_invalid", 502)
        return {
            "occurrences": occurrences,
            "next_cursor": decoded.get("next_cursor"),
        }
    return decoded.get("schedule") if action != "delete" else {"ok": True}


def build_mutation(name: str, prompt: str, schedule_input: str) -> dict[str, Any]:
    schedule = parse_schedule(schedule_input)
    next_run_at = compute_next_run(schedule)
    if not next_run_at:
        raise ScheduledResearchControlError("invalid_schedule", 400)
    return {
        "name": name,
        "prompt": prompt,
        "schedule": schedule,
        "schedule_input": schedule_input,
        "schedule_display": str(schedule.get("display") or schedule_input),
        "timezone": "UTC",
        "next_run_at": next_run_at,
    }


def next_resume_at(schedule: dict[str, Any]) -> str:
    value = compute_next_run(schedule)
    if value:
        return value
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
