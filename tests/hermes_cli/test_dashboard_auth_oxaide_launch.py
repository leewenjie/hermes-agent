"""End-to-end coverage for the Oxaide thin-client dashboard handoff."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from hermes_cli import web_server
from hermes_cli import hosted_runtime_bridge
from hermes_cli.dashboard_auth import clear_providers
from hermes_cli.dashboard_auth.ws_tickets import _reset_for_tests, consume_ticket
from hermes_cli.dashboard_auth.routes import (
    _OXAIDE_SESSION_TTL_SECONDS,
    _decode_oxaide_session_token,
    _reset_oxaide_launch_tokens_for_tests,
)
from hermes_cli.dashboard_auth import routes as dashboard_auth_routes


def _launch_token(
    secret: str,
    *,
    access_state: str | None = "active",
    expires_at: int | None = None,
) -> str:
    payload = {
        "sub": "oxaide-user-1",
        "user_id": "oxaide-user-1",
        "email": "user@example.com",
        "name": "Oxaide User",
        "workspace_id": "workspace-1",
        "workspace_slug": "alpha-workspace",
        "runtime_session_id": "rt_workspace_1",
        "runtime_key": "runtimekey1234567890abcd",
        "plan_key": "growth",
        "logout_url": "https://oxaide.com/auth/runtime-logout",
        "aud": "oxaide-hermes-runtime",
        "jti": "test-launch-jti",
        "iat": int(time.time()),
        "exp": expires_at or int(time.time()) + 600,
    }
    if access_state is not None:
        payload["access_state"] = access_state
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(
        secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{encoded}.{signature}"


def _logout_token(secret: str, **overrides) -> str:
    now = int(time.time())
    payload = {
        "sub": "oxaide-user-1",
        "workspace_id": "workspace-1",
        "runtime_key": "runtimekey1234567890abcd",
        "aud": "oxaide-runtime-logout",
        "jti": "logout-jti",
        "iat": now,
        "exp": now + 120,
        "return_url": "https://oxaide.com/auth/signin?logout=true",
        **overrides,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    signature = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


@pytest.fixture
def gated_app(monkeypatch):
    secret = "test-oxaide-handoff-secret-at-least-32-bytes"
    monkeypatch.setenv("HERMES_OXAIDE_DEMO_AUTH_SECRET", secret)
    monkeypatch.setenv("HERMES_OXAIDE_WORKSPACE_ID", "workspace-1")
    monkeypatch.setenv("HERMES_OXAIDE_RUNTIME_KEY", "runtimekey1234567890abcd")
    monkeypatch.setenv("HERMES_HOSTED_RUNTIME_SHARED_SECRET", "test-hosted-runtime-secret-at-least-32-bytes")
    monkeypatch.setattr(
        hosted_runtime_bridge,
        "load_config",
        lambda: {"hosted_runtime_bridge": {"enabled": True}},
    )
    clear_providers()
    _reset_for_tests()
    _reset_oxaide_launch_tokens_for_tests()
    previous = {
        "bound_host": getattr(web_server.app.state, "bound_host", None),
        "bound_port": getattr(web_server.app.state, "bound_port", None),
        "auth_required": getattr(web_server.app.state, "auth_required", None),
    }
    web_server.app.state.bound_host = "agents.oxaide.test"
    web_server.app.state.bound_port = 443
    web_server.app.state.auth_required = True
    client = TestClient(web_server.app, base_url="https://agents.oxaide.test")
    yield client, secret
    clear_providers()
    _reset_for_tests()
    _reset_oxaide_launch_tokens_for_tests()
    for key, value in previous.items():
        setattr(web_server.app.state, key, value)


def test_signed_launch_creates_verified_dashboard_session(gated_app):
    client, secret = gated_app
    launch_expires_at = int(time.time()) + 600
    token = _launch_token(secret, expires_at=launch_expires_at)

    launched = client.get(
        f"/auth/oxaide-launch?token={token}&next=/chat",
        follow_redirects=False,
    )

    assert launched.status_code == 302
    assert launched.headers["location"] == "/chat"

    identity = client.get("/api/auth/me")
    assert identity.status_code == 200
    assert identity.json() == {
        "user_id": "oxaide-user-1",
        "email": "user@example.com",
        "display_name": "Oxaide User",
        "org_id": "workspace-1",
        "provider": "oxaide-demo",
        "expires_at": launch_expires_at,
        "access_state": "active",
    }

    ticket = client.post("/api/auth/ws-ticket")
    assert ticket.status_code == 200
    ticket_info = consume_ticket(ticket.json()["ticket"])
    trusted_context = ticket_info["trusted_context"]
    session_jti = trusted_context.pop("jti")
    assert session_jti != "test-launch-jti"
    assert len(session_jti) == 32
    assert trusted_context == {
        "workspace_id": "workspace-1",
        "runtime_session_id": "rt_workspace_1",
        "runtime_key": "runtimekey1234567890abcd",
        "user_id": "oxaide-user-1",
        "expires_at": launch_expires_at,
        "access_state": "active",
    }

    access_cookie = client.cookies.get("__Host-hermes_session_at")
    assert access_cookie is not None
    exchanged = _decode_oxaide_session_token(access_cookie)
    assert exchanged["aud"] == "oxaide-hermes-session"
    assert exchanged["workspace_id"] == "workspace-1"
    assert exchanged["runtime_key"] == "runtimekey1234567890abcd"
    assert exchanged["jti"] == session_jti
    assert exchanged["access_state"] == "active"
    assert exchanged["exp"] == launch_expires_at
    assert exchanged["exp"] - exchanged["iat"] <= _OXAIDE_SESSION_TTL_SECONDS


def test_dashboard_session_cannot_outlive_signed_access_state(gated_app, monkeypatch):
    client, secret = gated_app
    launch_expires_at = int(time.time()) + 600
    client.get(
        f"/auth/oxaide-launch?token={_launch_token(secret, expires_at=launch_expires_at)}",
        follow_redirects=False,
    )
    access_cookie = client.cookies.get("__Host-hermes_session_at")
    assert access_cookie is not None
    assert _decode_oxaide_session_token(access_cookie)["exp"] == launch_expires_at

    monkeypatch.setattr(
        dashboard_auth_routes.time,
        "time",
        lambda: launch_expires_at + 1,
    )
    with pytest.raises(HTTPException) as exc_info:
        _decode_oxaide_session_token(access_cookie)
    assert exc_info.value.status_code == 401


@pytest.mark.parametrize("access_state", [None, "", "paused", "ACTIVE"])
def test_launch_requires_known_signed_access_state(gated_app, access_state):
    client, secret = gated_app

    response = client.get(
        f"/auth/oxaide-launch?token={_launch_token(secret, access_state=access_state)}",
        follow_redirects=False,
    )

    assert response.status_code == 401


def test_frozen_launch_propagates_state_and_blocks_http_mutations(gated_app):
    client, secret = gated_app
    launched = client.get(
        f"/auth/oxaide-launch?token={_launch_token(secret, access_state='frozen')}",
        follow_redirects=False,
    )

    assert launched.status_code == 302
    assert client.get("/api/auth/me").json()["access_state"] == "frozen"

    ticket = client.post("/api/auth/ws-ticket")
    assert ticket.status_code == 200
    assert consume_ticket(ticket.json()["ticket"])["trusted_context"]["access_state"] == "frozen"

    blocked = client.post("/api/files/mkdir", json={"path": "blocked"})
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "access_frozen"


def test_public_status_exposes_only_nonsecret_tenant_identity(gated_app):
    client, _secret = gated_app

    response = client.get("/api/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["oxaide_runtime_key"] == "runtimekey1234567890abcd"
    assert payload["oxaide_workspace_fingerprint"] == hmac.new(
        _secret.encode(),
        b"oxaide-workspace-fingerprint:v1:workspace-1",
        hashlib.sha256,
    ).hexdigest()
    assert "workspace-1" not in json.dumps(payload)


def test_oxaide_tenant_login_redirects_to_single_customer_login(gated_app):
    client, _secret = gated_app

    response = client.get(
        "/login?next=%2Fchat&redirect=https%3A%2F%2Fattacker.example",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == (
        "https://oxaide.com/agents?workspace=workspace-1"
    )
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_oxaide_tenant_breakglass_keeps_operator_login(gated_app):
    client, _secret = gated_app

    response = client.get(
        "/login?breakglass=1&next=%2Fchat",
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "location" not in response.headers


def test_incomplete_oxaide_pin_keeps_generic_hermes_login(gated_app, monkeypatch):
    client, _secret = gated_app
    monkeypatch.delenv("HERMES_OXAIDE_RUNTIME_KEY")

    response = client.get("/login?next=%2Fchat", follow_redirects=False)

    assert response.status_code == 200
    assert "location" not in response.headers


def test_oxaide_logout_clears_runtime_cookies_and_returns_signed_continuation(gated_app):
    client, secret = gated_app
    token = _launch_token(secret)
    client.get(f"/auth/oxaide-launch?token={token}", follow_redirects=False)

    response = client.post(
        "/auth/logout",
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["redirect_to"] == "https://oxaide.com/auth/runtime-logout"
    encoded, signature = body["logout_token"].split(".", 1)
    expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    assert hmac.compare_digest(signature, expected)
    payload = json.loads(
        base64.urlsafe_b64decode(encoded + "=" * ((4 - len(encoded) % 4) % 4))
    )
    assert payload["aud"] == "oxaide-runtime-logout"
    assert payload["sub"] == "oxaide-user-1"
    assert payload["workspace_id"] == "workspace-1"
    assert payload["runtime_key"] == "runtimekey1234567890abcd"
    assert payload["exp"] - payload["iat"] == 120
    set_cookies = response.headers.get_list("set-cookie")
    secure_deletions = [cookie for cookie in set_cookies if cookie.startswith("__Host-")]
    assert secure_deletions and all("Secure" in cookie for cookie in secure_deletions)


def test_oxaide_logout_rejects_untrusted_continuation_url(gated_app):
    client, secret = gated_app
    token = _launch_token(secret)
    encoded, _signature = token.split(".", 1)
    payload = json.loads(
        base64.urlsafe_b64decode(encoded + "=" * ((4 - len(encoded) % 4) % 4))
    )
    payload["logout_url"] = "https://attacker.example/logout"
    tampered = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    signature = hmac.new(secret.encode(), tampered.encode(), hashlib.sha256).hexdigest()
    client.get(
        f"/auth/oxaide-launch?token={tampered}.{signature}",
        follow_redirects=False,
    )

    response = client.post(
        "/auth/logout",
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "redirect_to": "/login"}


def test_signed_oxaide_logout_command_clears_runtime_session(gated_app):
    client, secret = gated_app
    client.get(
        f"/auth/oxaide-launch?token={_launch_token(secret)}",
        follow_redirects=False,
    )

    response = client.get(
        f"/auth/oxaide-logout?token={_logout_token(secret)}",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "https://oxaide.com/auth/signin?logout=true"
    assert response.headers["cache-control"] == "no-store"
    secure_deletions = [
        cookie for cookie in response.headers.get_list("set-cookie")
        if cookie.startswith("__Host-")
    ]
    assert secure_deletions and all("Secure" in cookie for cookie in secure_deletions)


def test_oxaide_logout_command_is_tenant_bound(gated_app):
    client, secret = gated_app

    response = client.get(
        f"/auth/oxaide-logout?token={_logout_token(secret, workspace_id='workspace-other')}",
        follow_redirects=False,
    )

    assert response.status_code == 401


def test_invalid_launch_signature_is_rejected(gated_app):
    client, secret = gated_app
    token = _launch_token(secret)
    encoded, _signature = token.split(".", 1)

    response = client.get(
        f"/auth/oxaide-launch?token={encoded}.{'0' * 64}",
        follow_redirects=False,
    )

    assert response.status_code == 401


def test_signed_launch_url_is_single_use(gated_app):
    client, secret = gated_app
    token = _launch_token(secret)

    first = client.get(f"/auth/oxaide-launch?token={token}", follow_redirects=False)
    replay = client.get(f"/auth/oxaide-launch?token={token}", follow_redirects=False)

    assert first.status_code == 302
    assert replay.status_code == 401


def test_signed_nondict_payload_is_rejected_cleanly(gated_app):
    client, secret = gated_app
    encoded = base64.urlsafe_b64encode(b"[]").decode("ascii").rstrip("=")
    signature = hmac.new(
        secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    response = client.get(
        f"/auth/oxaide-launch?token={encoded}.{signature}",
        follow_redirects=False,
    )

    assert response.status_code == 400


def test_expired_launch_token_is_rejected(gated_app):
    client, secret = gated_app
    token = _launch_token(secret, expires_at=int(time.time()) - 1)

    response = client.get(
        f"/auth/oxaide-launch?token={token}",
        follow_redirects=False,
    )

    assert response.status_code == 401


def test_tenant_container_rejects_workspace_mismatch(gated_app, monkeypatch):
    client, secret = gated_app
    monkeypatch.setenv("HERMES_OXAIDE_WORKSPACE_ID", "different-workspace")

    response = client.get(
        f"/auth/oxaide-launch?token={_launch_token(secret)}",
        follow_redirects=False,
    )

    assert response.status_code == 401


def test_tenant_container_rejects_runtime_mismatch(gated_app, monkeypatch):
    client, secret = gated_app
    monkeypatch.setenv("HERMES_OXAIDE_WORKSPACE_ID", "workspace-1")
    monkeypatch.setenv("HERMES_OXAIDE_RUNTIME_KEY", "differentruntimekey1234567890")

    response = client.get(
        f"/auth/oxaide-launch?token={_launch_token(secret)}",
        follow_redirects=False,
    )

    assert response.status_code == 401


def test_tenant_container_requires_runtime_audience(gated_app, monkeypatch):
    client, secret = gated_app
    token = _launch_token(secret)
    encoded, _signature = token.split(".", 1)
    payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * ((4 - len(encoded) % 4) % 4)))
    payload["aud"] = "wrong-audience"
    tampered = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    signature = hmac.new(secret.encode(), tampered.encode(), hashlib.sha256).hexdigest()

    response = client.get(
        f"/auth/oxaide-launch?token={tampered}.{signature}",
        follow_redirects=False,
    )

    assert response.status_code == 401


def test_tenant_container_requires_pinned_runtime_identity(gated_app, monkeypatch):
    client, secret = gated_app
    monkeypatch.delenv("HERMES_OXAIDE_WORKSPACE_ID")

    response = client.get(
        f"/auth/oxaide-launch?token={_launch_token(secret)}",
        follow_redirects=False,
    )

    assert response.status_code == 503


def test_launch_token_lifetime_is_bounded(gated_app):
    client, secret = gated_app

    response = client.get(
        f"/auth/oxaide-launch?token={_launch_token(secret, expires_at=int(time.time()) + 3600)}",
        follow_redirects=False,
    )

    assert response.status_code == 401


def test_hosted_runtime_uses_its_own_shared_secret_on_gated_dashboard(gated_app):
    client, _secret = gated_app

    response = client.get(
        "/api/hosted/runtime/health",
        headers={"X-Hermes-Hosted-Secret": "test-hosted-runtime-secret-at-least-32-bytes"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_hosted_runtime_bridge_is_hidden_when_disabled(gated_app, monkeypatch):
    client, _secret = gated_app
    monkeypatch.setattr(
        hosted_runtime_bridge,
        "load_config",
        lambda: {"hosted_runtime_bridge": {"enabled": False}},
    )

    response = client.get(
        "/api/hosted/runtime/health",
        headers={"X-Hermes-Hosted-Secret": "test-hosted-runtime-secret-at-least-32-bytes"},
    )

    assert response.status_code == 404


def test_hosted_runtime_rejects_missing_shared_secret(gated_app):
    client, _secret = gated_app

    response = client.get("/api/hosted/runtime/health")

    assert response.status_code == 401


def test_placeholder_runtime_credentials_are_rejected(gated_app, monkeypatch):
    client, _secret = gated_app
    monkeypatch.setenv("HERMES_OXAIDE_DEMO_AUTH_SECRET", "replace-with-a-long-random-secret-value")
    monkeypatch.setenv("HERMES_HOSTED_RUNTIME_SHARED_SECRET", "__replace_with_a_separate_long_random_secret__")

    launch = client.get(
        f"/auth/oxaide-launch?token={_launch_token('replace-with-a-long-random-secret-value')}",
        follow_redirects=False,
    )
    hosted = client.get(
        "/api/hosted/runtime/health",
        headers={"X-Hermes-Hosted-Secret": "__replace_with_a_separate_long_random_secret__"},
    )

    assert launch.status_code == 503
    assert hosted.status_code == 401
