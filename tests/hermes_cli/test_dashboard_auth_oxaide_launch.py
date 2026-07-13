"""End-to-end coverage for the Oxaide thin-client dashboard handoff."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from hermes_cli import web_server
from hermes_cli.dashboard_auth import clear_providers
from hermes_cli.dashboard_auth.ws_tickets import _reset_for_tests, consume_ticket
from hermes_cli.dashboard_auth.routes import _reset_oxaide_launch_tokens_for_tests


def _launch_token(secret: str, *, expires_at: int | None = None) -> str:
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
    monkeypatch.setenv("HERMES_HOSTED_RUNTIME_SHARED_SECRET", "test-hosted-runtime-secret")
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
    token = _launch_token(secret)

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
        "expires_at": pytest.approx(int(time.time()) + 600, abs=3),
    }

    ticket = client.post("/api/auth/ws-ticket")
    assert ticket.status_code == 200
    ticket_info = consume_ticket(ticket.json()["ticket"])
    assert ticket_info["trusted_context"] == {
        "workspace_id": "workspace-1",
        "runtime_session_id": "rt_workspace_1",
        "runtime_key": "runtimekey1234567890abcd",
        "user_id": "oxaide-user-1",
        "jti": "test-launch-jti",
        "expires_at": pytest.approx(int(time.time()) + 600, abs=3),
    }


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
    monkeypatch.setenv("HERMES_OXAIDE_RUNTIME_KEY", "different-runtime-key")

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
        headers={"X-Hermes-Hosted-Secret": "test-hosted-runtime-secret"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_hosted_runtime_rejects_missing_shared_secret(gated_app):
    client, _secret = gated_app

    response = client.get("/api/hosted/runtime/health")

    assert response.status_code == 401
