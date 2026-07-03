"""Hosted SaaS runtime contract helpers.

This module defines the minimal hosted-runtime seam for the two-repo SaaS v1
shape. It starts with:

- config discovery
- shared-secret validation for internal WebUI calls
- bootstrap payload normalization
- health/bootstrap placeholder builders

The intent is to keep hosted-runtime logic explicit and narrow instead of
smearing product-specific checks through the generic agent runtime.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home


HOSTED_RUNTIME_STATE_DIR = get_hermes_home() / "hosted_runtime"
HOSTED_RUNTIME_SESSIONS_FILE = HOSTED_RUNTIME_STATE_DIR / "sessions.json"


def _env(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def hosted_runtime_shared_secret() -> str | None:
    return _env("HERMES_HOSTED_RUNTIME_SHARED_SECRET")


def _default_runtime_state() -> dict[str, Any]:
    return {
        "sessions": {},
        "updated_at": int(time.time()),
    }


def load_hosted_runtime_state() -> dict[str, Any]:
    try:
        if not HOSTED_RUNTIME_SESSIONS_FILE.exists():
            return _default_runtime_state()
        payload = json.loads(HOSTED_RUNTIME_SESSIONS_FILE.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return _default_runtime_state()
        merged = _default_runtime_state()
        merged.update(payload)
        return merged
    except Exception:
        return _default_runtime_state()


def save_hosted_runtime_state(state: dict[str, Any]) -> dict[str, Any]:
    state = dict(state or {})
    state["updated_at"] = int(time.time())
    HOSTED_RUNTIME_STATE_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=HOSTED_RUNTIME_STATE_DIR, suffix=".hosted_runtime.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, HOSTED_RUNTIME_SESSIONS_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return state


def get_hosted_runtime_session(runtime_session_id: str) -> dict[str, Any] | None:
    state = load_hosted_runtime_state()
    sessions = state.get("sessions", {})
    if not isinstance(sessions, dict):
        return None
    row = sessions.get(runtime_session_id)
    return row if isinstance(row, dict) else None


def hosted_runtime_session_status_payload(runtime_session_id: str) -> dict[str, Any] | None:
    row = get_hosted_runtime_session(runtime_session_id)
    if not isinstance(row, dict):
        return None
    policy = row.get("policy") if isinstance(row.get("policy"), dict) else {}
    return {
        "runtime_session_id": row.get("runtime_session_id") or runtime_session_id,
        "status": row.get("status") or "unknown",
        "workspace_id": row.get("workspace_id"),
        "workspace_slug": row.get("workspace_slug"),
        "session_id": row.get("session_id"),
        "profile_id": row.get("profile_id"),
        "user_id": row.get("user_id"),
        "org_id": row.get("org_id"),
        "plan": row.get("plan"),
        "scaffolded": True,
        "live_session_binding": False,
        "effective_toolsets": policy.get("effective_toolsets") if isinstance(policy.get("effective_toolsets"), list) else [],
        "limits": policy.get("limits") if isinstance(policy.get("limits"), dict) else {},
        "updated_at": row.get("updated_at"),
        "session_record": row,
    }


def list_hosted_runtime_sessions() -> list[dict[str, Any]]:
    state = load_hosted_runtime_state()
    sessions = state.get("sessions", {})
    if not isinstance(sessions, dict):
        return []
    return [row for row in sessions.values() if isinstance(row, dict)]


def list_hosted_runtime_session_statuses() -> list[dict[str, Any]]:
    rows = []
    for row in list_hosted_runtime_sessions():
        runtime_session_id = str(row.get("runtime_session_id") or "").strip()
        if not runtime_session_id:
            continue
        payload = hosted_runtime_session_status_payload(runtime_session_id)
        if payload:
            rows.append(payload)
    return rows


def upsert_hosted_runtime_session(runtime_session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    state = load_hosted_runtime_state()
    sessions = state.setdefault("sessions", {})
    if not isinstance(sessions, dict):
        sessions = {}
        state["sessions"] = sessions
    existing = sessions.get(runtime_session_id) if isinstance(sessions.get(runtime_session_id), dict) else {}
    merged = {**existing, **(payload or {}), "runtime_session_id": runtime_session_id, "updated_at": int(time.time())}
    sessions[runtime_session_id] = merged
    save_hosted_runtime_state(state)
    return merged


def transition_hosted_runtime_session(runtime_session_id: str, next_status: str) -> dict[str, Any] | None:
    existing = get_hosted_runtime_session(runtime_session_id)
    if not isinstance(existing, dict):
        return None
    policy = existing.get("policy") if isinstance(existing.get("policy"), dict) else derive_hosted_runtime_policy(existing)
    merged = upsert_hosted_runtime_session(
        runtime_session_id,
        {
            **existing,
            "status": next_status,
            "policy": policy,
        },
    )
    return hosted_runtime_session_status_payload(runtime_session_id)


def hosted_runtime_request_authorized(headers: dict[str, Any] | None) -> bool:
    expected = hosted_runtime_shared_secret()
    if not expected:
        return False
    if not headers:
        return False
    actual = str(headers.get("X-Hermes-Hosted-Secret", "") or "").strip()
    return bool(actual and actual == expected)


def normalize_hosted_bootstrap_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    entitlements = payload.get("entitlements") if isinstance(payload.get("entitlements"), dict) else {}
    limits = payload.get("limits") if isinstance(payload.get("limits"), dict) else {}
    identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
    auth_context = payload.get("auth_context") if isinstance(payload.get("auth_context"), dict) else {}
    return {
        "user_id": payload.get("user_id"),
        "org_id": payload.get("org_id"),
        "workspace_id": payload.get("workspace_id"),
        "workspace_slug": payload.get("workspace_slug"),
        "session_id": payload.get("session_id"),
        "profile_id": payload.get("profile_id") or "hosted-default",
        "plan": payload.get("plan") or "starter",
        "entitlements": entitlements,
        "limits": limits,
        "identity": identity,
        "auth_context": auth_context,
    }


def derive_hosted_runtime_policy(payload: dict[str, Any]) -> dict[str, Any]:
    entitlements = payload.get("entitlements") if isinstance(payload.get("entitlements"), dict) else {}
    limits = payload.get("limits") if isinstance(payload.get("limits"), dict) else {}
    effective_toolsets = sorted(
        [
            name
            for name, enabled in {
                "file": True,
                "search": True,
                "terminal": bool(entitlements.get("terminal")),
                "web": bool(entitlements.get("browser")),
                "delegation": bool(entitlements.get("delegation")),
                "background_jobs": bool(entitlements.get("background_jobs")),
            }.items()
            if enabled
        ]
    )
    return {
        "policy_version": "v1",
        "effective_toolsets": effective_toolsets,
        "entitlements": entitlements,
        "limits": {
            "max_runtime_seconds": limits.get("max_runtime_seconds") or 1800,
            "max_concurrent_jobs": limits.get("max_concurrent_jobs") or 1,
            "max_upload_bytes": limits.get("max_upload_bytes") or 50 * 1024 * 1024,
            "monthly_token_budget": limits.get("monthly_token_budget") or None,
        },
    }


def hosted_runtime_config_status() -> dict[str, Any]:
    shared_secret_configured = bool(_env("HERMES_HOSTED_RUNTIME_SHARED_SECRET"))
    return {
        "api_host": _env("HERMES_HOSTED_RUNTIME_API_HOST") or "127.0.0.1",
        "api_port": _env("HERMES_HOSTED_RUNTIME_API_PORT") or "9001",
        "shared_secret_configured": shared_secret_configured,
        "token_issuer": _env("HERMES_HOSTED_RUNTIME_TOKEN_ISSUER") or "hermes-webui",
        "token_audience": _env("HERMES_HOSTED_RUNTIME_TOKEN_AUDIENCE") or "hermes-agent",
        "configured": shared_secret_configured,
        "scaffolded": True,
        "live_session_binding": False,
        "ready": shared_secret_configured,
    }


def hosted_runtime_bootstrap_placeholder() -> dict[str, Any]:
    return {
        "status": "not_implemented",
        "runtime_session_id": None,
        "effective_toolsets": [],
        "usage_snapshot": {"tokens_used": 0, "tool_calls": 0},
        "note": "Implement hosted runtime bootstrap here. This contract should be called only by hermes-webui, not directly by browsers.",
    }


def hosted_runtime_bootstrap_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized placeholder bootstrap response for hosted callers."""

    workspace_id = payload.get("workspace_id") or "unknown-workspace"
    session_id = payload.get("session_id") or "unknown-session"
    profile_id = payload.get("profile_id") or "hosted-default"
    runtime_session_id = f"rt::{workspace_id}::{session_id}"
    policy = derive_hosted_runtime_policy(payload)
    record = upsert_hosted_runtime_session(
        runtime_session_id,
        {
            "workspace_id": workspace_id,
            "session_id": session_id,
            "profile_id": profile_id,
            "user_id": payload.get("user_id"),
            "org_id": payload.get("org_id"),
            "workspace_slug": payload.get("workspace_slug"),
            "plan": payload.get("plan"),
            "entitlements": payload.get("entitlements") or {},
            "limits": payload.get("limits") or {},
            "identity": payload.get("identity") or {},
            "status": "idle",
            "policy": policy,
        },
    )
    return {
        "ok": True,
        "status": "idle",
        "scaffolded": True,
        "live_session_binding": False,
        "runtime_session_id": runtime_session_id,
        "policy_version": policy["policy_version"],
        "profile_id": profile_id,
        "effective_toolsets": policy["effective_toolsets"],
        "usage_snapshot": {"tokens_used": 0, "tool_calls": 0},
        "received": payload,
        "policy": policy,
        "session_record": record,
        "note": "Hosted runtime bootstrap now creates a durable hosted session record with lifecycle state, but it is not yet bound to live agent session creation.",
    }


def hosted_runtime_health_payload() -> dict[str, Any]:
    status = hosted_runtime_config_status()
    return {
        "configured": status["configured"],
        "scaffolded": status["scaffolded"],
        "live_session_binding": status["live_session_binding"],
        "api_host": status["api_host"],
        "api_port": status["api_port"],
        "token_issuer": status["token_issuer"],
        "token_audience": status["token_audience"],
        "status": "scaffolded_ready" if status["configured"] else "missing_shared_secret",
        "note": (
            "Hosted runtime routes are configured and authorized, but bootstrap still returns a scaffolded response "
            "until live agent session binding is implemented."
            if status["configured"]
            else "Configure HERMES_HOSTED_RUNTIME_SHARED_SECRET to enable hosted runtime authorization."
        ),
    }