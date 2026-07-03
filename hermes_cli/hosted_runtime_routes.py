"""Hosted SaaS runtime routes.

This router is intentionally small and internal-facing. It provides the first
runtime endpoints used by the two-repo hosted SaaS shape:

- health/readiness
- bootstrap placeholder

Requests are authorized with the shared secret configured in the hosted runtime
env until a stronger signed-token flow is introduced.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from agent.hosted_runtime_contract import (
    derive_hosted_runtime_policy,
    get_hosted_runtime_session,
    hosted_runtime_bootstrap_response,
    hosted_runtime_health_payload,
    hosted_runtime_request_authorized,
    hosted_runtime_session_status_payload,
    list_hosted_runtime_sessions,
    list_hosted_runtime_session_statuses,
    normalize_hosted_bootstrap_payload,
    transition_hosted_runtime_session,
)

router = APIRouter()


def _require_hosted_secret(secret: str | None) -> None:
    if not hosted_runtime_request_authorized({"X-Hermes-Hosted-Secret": secret or ""}):
        raise HTTPException(status_code=401, detail="Hosted runtime authorization failed")


@router.get("/api/hosted/runtime/health")
async def hosted_runtime_health(
    x_hermes_hosted_secret: str | None = Header(default=None),
):
    _require_hosted_secret(x_hermes_hosted_secret)
    return hosted_runtime_health_payload()


@router.post("/api/hosted/runtime/bootstrap")
async def hosted_runtime_bootstrap(
    body: dict,
    x_hermes_hosted_secret: str | None = Header(default=None),
):
    _require_hosted_secret(x_hermes_hosted_secret)
    normalized = normalize_hosted_bootstrap_payload(body)
    return hosted_runtime_bootstrap_response(normalized)


@router.post("/api/hosted/runtime/policy/evaluate")
async def hosted_runtime_policy_evaluate(
    body: dict,
    x_hermes_hosted_secret: str | None = Header(default=None),
):
    _require_hosted_secret(x_hermes_hosted_secret)
    normalized = normalize_hosted_bootstrap_payload(body)
    return derive_hosted_runtime_policy(normalized)


@router.get("/api/hosted/runtime/sessions")
async def hosted_runtime_sessions(
    x_hermes_hosted_secret: str | None = Header(default=None),
):
    _require_hosted_secret(x_hermes_hosted_secret)
    return {"sessions": list_hosted_runtime_session_statuses()}


@router.get("/api/hosted/runtime/sessions/{runtime_session_id}")
async def hosted_runtime_session_get(
    runtime_session_id: str,
    x_hermes_hosted_secret: str | None = Header(default=None),
):
    _require_hosted_secret(x_hermes_hosted_secret)
    session = hosted_runtime_session_status_payload(runtime_session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Hosted runtime session not found")
    return session


@router.post("/api/hosted/runtime/sessions")
async def hosted_runtime_session_create(
    body: dict,
    x_hermes_hosted_secret: str | None = Header(default=None),
):
    _require_hosted_secret(x_hermes_hosted_secret)
    normalized = normalize_hosted_bootstrap_payload(body)
    return hosted_runtime_bootstrap_response(normalized)


@router.post("/api/hosted/runtime/sessions/{runtime_session_id}/pause")
async def hosted_runtime_session_pause(
    runtime_session_id: str,
    x_hermes_hosted_secret: str | None = Header(default=None),
):
    _require_hosted_secret(x_hermes_hosted_secret)
    session = transition_hosted_runtime_session(runtime_session_id, "paused")
    if not session:
        raise HTTPException(status_code=404, detail="Hosted runtime session not found")
    return session


@router.post("/api/hosted/runtime/sessions/{runtime_session_id}/resume")
async def hosted_runtime_session_resume(
    runtime_session_id: str,
    x_hermes_hosted_secret: str | None = Header(default=None),
):
    _require_hosted_secret(x_hermes_hosted_secret)
    session = transition_hosted_runtime_session(runtime_session_id, "idle")
    if not session:
        raise HTTPException(status_code=404, detail="Hosted runtime session not found")
    return session


@router.post("/api/hosted/runtime/sessions/{runtime_session_id}/kill")
async def hosted_runtime_session_kill(
    runtime_session_id: str,
    x_hermes_hosted_secret: str | None = Header(default=None),
):
    _require_hosted_secret(x_hermes_hosted_secret)
    session = transition_hosted_runtime_session(runtime_session_id, "killed")
    if not session:
        raise HTTPException(status_code=404, detail="Hosted runtime session not found")
    return session