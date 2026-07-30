"""HTTP routes for the dashboard-auth OAuth round trip.

Mounted at root (no prefix) by ``web_server.py``. The router does not
auto-gate; gating is performed by ``gated_auth_middleware``, which
allowlists everything under ``/auth/*`` and ``/api/auth/providers``.

The routes:

  GET  /login              → server-rendered login page
  GET  /auth/login?provider=N → 302 to IDP, sets PKCE cookie
  GET  /auth/callback?code,state → completes login, sets session cookies
  POST /auth/logout        → clears cookies, best-effort revoke
  GET  /api/auth/providers → list registered providers (login bootstrap)
  GET  /api/auth/me        → current Session as JSON (auth-required)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
import re
from collections import defaultdict, deque
from typing import Any, Deque, Dict, Tuple
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from hermes_cli.dashboard_auth import (
    get_provider,
    list_providers,
    list_session_providers,
)
from hermes_cli.dashboard_auth.audit import AuditEvent, audit_log
from hermes_cli.dashboard_auth.base import (
    InvalidCodeError,
    InvalidCredentialsError,
    ProviderError,
    RefreshExpiredError,
)
from hermes_cli.dashboard_auth.cookies import (
    clear_pkce_cookie,
    clear_session_cookies,
    clear_sso_attempt_cookie,
    detect_https,
    read_pkce_cookie,
    read_session_cookies,
    set_pkce_cookie,
    set_session_cookies,
)
from hermes_cli.dashboard_auth.login_page import render_login_html
from hermes_cli.dashboard_auth.base import Session
from hermes_constants import get_hermes_home

_log = logging.getLogger(__name__)

router = APIRouter()
_consumed_oxaide_launch_tokens_lock = threading.Lock()
_OXAIDE_LAUNCH_AUDIENCE = "oxaide-hermes-runtime"
_OXAIDE_LAUNCH_MAX_TTL_SECONDS = 15 * 60
_OXAIDE_SESSION_AUDIENCE = "oxaide-hermes-session"
_OXAIDE_SESSION_TTL_SECONDS = _OXAIDE_LAUNCH_MAX_TTL_SECONDS
_OXAIDE_REFRESH_TTL_SECONDS = 30 * 24 * 60 * 60
_OXAIDE_REFRESH_PREFIX = "oxa_rt_"
_OXAIDE_REFRESH_ENDPOINT = "https://oxaide.com/api/agents/runtime-session/refresh"
_OXAIDE_LOGOUT_AUDIENCE = "oxaide-runtime-logout"
_OXAIDE_LOGOUT_TTL_SECONDS = 2 * 60
_OXAIDE_CUSTOMER_LOGIN_URL = "https://oxaide.com/agents"
_OXAIDE_WORKSPACE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,128}$")
_OXAIDE_RUNTIME_PATTERN = re.compile(r"^[a-z0-9]{20,64}$")
_OXAIDE_ACCESS_STATES = frozenset({"active", "frozen"})


def _oxaide_customer_login_url() -> str:
    """Return the Oxaide entry URL scoped to this pinned tenant runtime."""
    workspace_id, _runtime_key = _required_oxaide_runtime_identity()
    return f"{_OXAIDE_CUSTOMER_LOGIN_URL}?{urlencode({'workspace': workspace_id})}"


def _oxaide_access_state(payload: dict) -> str:
    value = payload.get("access_state")
    if not isinstance(value, str) or value not in _OXAIDE_ACCESS_STATES:
        raise HTTPException(status_code=401, detail="Invalid Oxaide access state")
    return value


def _valid_oxaide_value(value: object, *, minimum_length: int = 1) -> bool:
    normalized = str(value or "").strip()
    lowered = normalized.lower()
    return bool(
        len(normalized) >= minimum_length
        and not lowered.startswith("replace-with-")
        and not lowered.startswith("__replace_with_")
    )


def _oxaide_launch_secret() -> str:
    value = str(os.environ.get("HERMES_OXAIDE_DEMO_AUTH_SECRET", "") or "").strip()
    return value if _valid_oxaide_value(value, minimum_length=32) else ""


def is_oxaide_native_auth_configured() -> bool:
    """Return true when native launch auth can safely gate a public runtime."""
    workspace_id = str(os.environ.get("HERMES_OXAIDE_WORKSPACE_ID", "") or "").strip()
    runtime_key = str(os.environ.get("HERMES_OXAIDE_RUNTIME_KEY", "") or "").strip()
    return bool(
        _oxaide_launch_secret()
        and _OXAIDE_WORKSPACE_PATTERN.fullmatch(workspace_id)
        and _OXAIDE_RUNTIME_PATTERN.fullmatch(runtime_key)
    )


def _is_oxaide_tenant_runtime() -> bool:
    """Return true only when the runtime is fully pinned to one Oxaide tenant."""
    return is_oxaide_native_auth_configured()


def _required_oxaide_runtime_identity() -> tuple[str, str]:
    workspace_id = str(
        os.environ.get("HERMES_OXAIDE_WORKSPACE_ID", "") or ""
    ).strip()
    runtime_key = str(
        os.environ.get("HERMES_OXAIDE_RUNTIME_KEY", "") or ""
    ).strip()
    if (
        not _OXAIDE_WORKSPACE_PATTERN.fullmatch(workspace_id)
        or not _OXAIDE_RUNTIME_PATTERN.fullmatch(runtime_key)
    ):
        raise HTTPException(
            status_code=503,
            detail="Oxaide workspace runtime identity is not configured",
        )
    return workspace_id, runtime_key


def _decode_oxaide_launch_token(token: str) -> dict:
    secret = _oxaide_launch_secret()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="Oxaide demo auth secret is not configured",
        )

    try:
        encoded, signature = token.split('.', 1)
    except ValueError:
        raise HTTPException(status_code=400, detail="Malformed launch token")

    expected = hmac.new(secret.encode('utf-8'), encoded.encode('utf-8'), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid launch token signature")

    try:
        payload_raw = base64.urlsafe_b64decode(encoded + '=' * ((4 - len(encoded) % 4) % 4))
        payload = json.loads(payload_raw.decode('utf-8'))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid launch token payload")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid launch token payload")
    try:
        exp = int(payload.get('exp') or 0)
        iat = int(payload.get('iat') or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid launch token timestamps")
    if iat <= 0 or iat > int(time.time()) + 60:
        raise HTTPException(status_code=401, detail="Invalid launch token issue time")
    if exp <= int(time.time()):
        raise HTTPException(status_code=401, detail="Launch token expired")
    if exp - iat > _OXAIDE_LAUNCH_MAX_TTL_SECONDS:
        raise HTTPException(status_code=401, detail="Launch token lifetime is too long")
    if str(payload.get("aud") or "").strip() != _OXAIDE_LAUNCH_AUDIENCE:
        raise HTTPException(status_code=401, detail="Invalid launch token audience")

    expected_workspace, expected_runtime_key = _required_oxaide_runtime_identity()
    if str(payload.get("workspace_id") or "").strip() != expected_workspace:
        raise HTTPException(status_code=401, detail="Launch token workspace mismatch")
    if str(payload.get("runtime_key") or "").strip() != expected_runtime_key:
        raise HTTPException(status_code=401, detail="Launch token runtime mismatch")

    _oxaide_access_state(payload)

    return payload


def _encode_oxaide_session_token(session_payload: dict) -> str:
    """Mint a short-lived access token independent of the launch assertion."""
    secret = _oxaide_launch_secret()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="Oxaide demo auth secret is not configured",
        )
    now = int(time.time())
    payload = dict(session_payload)
    payload.update(
        {
            "aud": _OXAIDE_SESSION_AUDIENCE,
            "jti": uuid.uuid4().hex,
            "iat": now,
            "exp": now + _OXAIDE_SESSION_TTL_SECONDS,
        }
    )
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(
        secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{encoded}.{signature}"


def _decode_oxaide_session_token(token: str) -> dict:
    """Verify the post-exchange dashboard session and tenant binding."""
    secret = _oxaide_launch_secret()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="Oxaide demo auth secret is not configured",
        )
    try:
        encoded, signature = token.split(".", 1)
    except ValueError:
        raise HTTPException(status_code=400, detail="Malformed Oxaide session token")
    expected = hmac.new(
        secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid Oxaide session signature")
    try:
        payload_raw = base64.urlsafe_b64decode(
            encoded + "=" * ((4 - len(encoded) % 4) % 4)
        )
        payload = json.loads(payload_raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Oxaide session payload")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid Oxaide session payload")
    try:
        exp = int(payload.get("exp") or 0)
        iat = int(payload.get("iat") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid Oxaide session timestamps")
    now = int(time.time())
    if iat <= 0 or iat > now + 60:
        raise HTTPException(status_code=401, detail="Invalid Oxaide session issue time")
    if exp <= now or exp - iat > _OXAIDE_SESSION_TTL_SECONDS:
        raise HTTPException(status_code=401, detail="Oxaide dashboard session expired")
    if str(payload.get("aud") or "").strip() != _OXAIDE_SESSION_AUDIENCE:
        raise HTTPException(status_code=401, detail="Invalid Oxaide session audience")
    expected_workspace, expected_runtime_key = _required_oxaide_runtime_identity()
    if str(payload.get("workspace_id") or "").strip() != expected_workspace:
        raise HTTPException(status_code=401, detail="Oxaide session workspace mismatch")
    if str(payload.get("runtime_key") or "").strip() != expected_runtime_key:
        raise HTTPException(status_code=401, detail="Oxaide session runtime mismatch")
    _oxaide_access_state(payload)
    return payload


def _consume_oxaide_launch_token(token: str, expires_at: int) -> None:
    """Reject launch-URL replay while preserving cookie-session verification."""
    digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
    now = int(time.time())
    with _consumed_oxaide_launch_tokens_lock:
        db_path = get_hermes_home() / "oxaide-launch-tokens.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path, timeout=5.0) as connection:
            connection.execute(
                "create table if not exists consumed_tokens (digest text primary key, expires_at integer not null)"
            )
            connection.execute("delete from consumed_tokens where expires_at <= ?", (now,))
            try:
                connection.execute(
                    "insert into consumed_tokens(digest, expires_at) values (?, ?)",
                    (digest, expires_at),
                )
                connection.commit()
            except sqlite3.IntegrityError:
                raise HTTPException(status_code=401, detail="Launch token already used")


def _oxaide_auth_db() -> sqlite3.Connection:
    db_path = get_hermes_home() / "oxaide-launch-tokens.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=5.0)
    connection.execute(
        "create table if not exists refresh_sessions ("
        "digest text primary key, payload text not null, expires_at integer not null)"
    )
    return connection


def _create_oxaide_refresh_session(payload: dict) -> str:
    """Persist an opaque renewable session and return its browser credential."""
    token = f"{_OXAIDE_REFRESH_PREFIX}{secrets.token_urlsafe(48)}"
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires_at = int(time.time()) + _OXAIDE_REFRESH_TTL_SECONDS
    stored = dict(payload)
    stored.pop("exp", None)
    stored.pop("iat", None)
    stored.pop("aud", None)
    with _consumed_oxaide_launch_tokens_lock, _oxaide_auth_db() as connection:
        connection.execute(
            "delete from refresh_sessions where expires_at <= ?", (int(time.time()),)
        )
        connection.execute(
            "insert into refresh_sessions(digest, payload, expires_at) values (?, ?, ?)",
            (digest, json.dumps(stored, separators=(",", ":")), expires_at),
        )
    return token


def _load_oxaide_refresh_session(token: str) -> dict | None:
    if not token.startswith(_OXAIDE_REFRESH_PREFIX):
        return None
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = int(time.time())
    with _consumed_oxaide_launch_tokens_lock, _oxaide_auth_db() as connection:
        row = connection.execute(
            "select payload, expires_at from refresh_sessions where digest = ?",
            (digest,),
        ).fetchone()
        if row is None:
            raise RefreshExpiredError("Oxaide refresh session is unknown")
        if int(row[1]) <= now:
            connection.execute("delete from refresh_sessions where digest = ?", (digest,))
            raise RefreshExpiredError("Oxaide refresh session expired")
    try:
        payload = json.loads(str(row[0]))
    except json.JSONDecodeError as exc:
        raise RefreshExpiredError("Oxaide refresh session is invalid") from exc
    if not isinstance(payload, dict):
        raise RefreshExpiredError("Oxaide refresh session is invalid")
    return payload


def _revoke_oxaide_refresh_session(token: str) -> None:
    if not token.startswith(_OXAIDE_REFRESH_PREFIX):
        return
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _consumed_oxaide_launch_tokens_lock, _oxaide_auth_db() as connection:
        connection.execute("delete from refresh_sessions where digest = ?", (digest,))


def _revalidate_oxaide_session(payload: dict) -> str:
    """Revalidate durable access with Oxaide before renewing browser access."""
    request_payload = {
        "workspace_id": str(payload.get("workspace_id") or "").strip(),
        "runtime_session_id": str(payload.get("runtime_session_id") or "").strip(),
        "runtime_key": str(payload.get("runtime_key") or "").strip(),
        "user_id": str(payload.get("sub") or payload.get("user_id") or "").strip(),
        "iat": int(time.time()),
        "nonce": uuid.uuid4().hex,
    }
    if not all(request_payload.values()):
        raise RefreshExpiredError("Oxaide refresh identity is incomplete")
    body = json.dumps(
        request_payload, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    signature = hmac.new(
        _oxaide_launch_secret().encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    request = urllib.request.Request(
        _OXAIDE_REFRESH_ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Oxaide-Runtime-Key": request_payload["runtime_key"],
            "X-Oxaide-Signature": signature,
        },
    )
    try:
        from hermes_cli.urllib_security import open_credentialed_url

        with open_credentialed_url(request, timeout=10.0) as response:
            response_body = response.read(16_385)
            response_signature = response.headers.get("X-Oxaide-Signature", "")
    except urllib.error.HTTPError as exc:
        response_body = exc.read(16_385)
        response_signature = exc.headers.get("X-Oxaide-Signature", "")
        expected_signature = hmac.new(
            _oxaide_launch_secret().encode("utf-8"), response_body, hashlib.sha256
        ).hexdigest()
        if (
            len(response_body) <= 16_384
            and hmac.compare_digest(response_signature, expected_signature)
        ):
            try:
                error_result = json.loads(response_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                error_result = None
            if (
                isinstance(error_result, dict)
                and error_result.get("nonce") == request_payload["nonce"]
                and exc.code in {403, 404, 409, 410}
            ):
                raise RefreshExpiredError(
                    "Oxaide runtime access is no longer active"
                ) from exc
        raise ProviderError(f"Oxaide session revalidation returned HTTP {exc.code}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise ProviderError("Oxaide session revalidation is unavailable") from exc
    if len(response_body) > 16_384:
        raise ProviderError("Oxaide session revalidation response is too large")
    expected_signature = hmac.new(
        _oxaide_launch_secret().encode("utf-8"), response_body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(response_signature, expected_signature):
        raise ProviderError("Oxaide session revalidation response is untrusted")
    try:
        result = json.loads(response_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderError("Oxaide session revalidation response is invalid") from exc
    if not isinstance(result, dict) or result.get("nonce") != request_payload["nonce"]:
        raise ProviderError("Oxaide session revalidation response is stale")
    access_state = str(result.get("access_state") or "")
    if access_state not in _OXAIDE_ACCESS_STATES:
        raise RefreshExpiredError("Oxaide runtime access is no longer active")
    return access_state


def _refresh_oxaide_session(refresh_token: str) -> Session | None:
    """Renew a recognised Oxaide refresh session after control-plane validation."""
    payload = _load_oxaide_refresh_session(refresh_token)
    if payload is None:
        return None
    payload["access_state"] = _revalidate_oxaide_session(payload)
    access_token = _encode_oxaide_session_token(payload)
    session = _oxaide_session_from_token(access_token)
    return Session(
        **{
            **session.__dict__,
            "refresh_token": refresh_token,
        }
    )


def _reset_oxaide_launch_tokens_for_tests() -> None:
    with _consumed_oxaide_launch_tokens_lock:
        db_path = get_hermes_home() / "oxaide-launch-tokens.db"
        if db_path.exists():
            db_path.unlink()


def _oxaide_session_from_token(token: str) -> Session:
    """Verify an exchanged Oxaide dashboard token and return its session."""
    payload = _decode_oxaide_session_token(token)
    expires_at = int(payload.get('exp') or 0)
    user_id = str(payload.get('sub') or payload.get('user_id') or '').strip() or 'oxaide-user'
    email = str(payload.get('email') or '').strip()
    workspace_id = str(payload.get('workspace_id') or '').strip()
    display_name = str(payload.get('name') or email or user_id).strip() or user_id

    return Session(
        user_id=user_id,
        email=email,
        display_name=display_name,
        org_id=workspace_id,
        provider='oxaide-demo',
        expires_at=expires_at,
        access_token=token,
        refresh_token="",
        access_state=_oxaide_access_state(payload),
    )


def _trusted_oxaide_context(payload: dict) -> dict:
    """Return the minimal server-trusted identity needed by the live runtime."""
    def bounded(name: str, value: object, maximum: int) -> str:
        text = str(value or "").strip()
        if not text or len(text) > maximum or "\x00" in text:
            raise HTTPException(
                status_code=401, detail=f"Launch token {name} is invalid"
            )
        return text

    context = {
        "workspace_id": bounded("workspace", payload.get("workspace_id"), 128),
        "runtime_session_id": bounded(
            "runtime session", payload.get("runtime_session_id"), 200
        ),
        "runtime_key": bounded("runtime key", payload.get("runtime_key"), 200),
        "user_id": bounded(
            "user", payload.get("sub") or payload.get("user_id"), 200
        ),
        "jti": bounded("jti", payload.get("jti"), 200),
        "expires_at": int(payload.get("exp") or 0),
        "access_state": _oxaide_access_state(payload),
    }
    if not all(context.values()):
        raise HTTPException(status_code=401, detail="Launch token context is incomplete")
    return context


def _oxaide_logout_url(payload: dict) -> str:
    raw = str(payload.get("logout_url") or "").strip()
    try:
        parsed = urlsplit(raw)
    except ValueError:
        parsed = None
    if (
        parsed is None
        or parsed.scheme != "https"
        or parsed.hostname not in {"oxaide.com", "www.oxaide.com"}
        or parsed.path != "/auth/runtime-logout"
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise HTTPException(status_code=401, detail="Invalid Oxaide logout URL")
    return raw


def _oxaide_logout_continuation(launch_token: str) -> tuple[str, str]:
    launch = _decode_oxaide_session_token(launch_token)
    logout_url = _oxaide_logout_url(launch)
    now = int(time.time())
    payload = {
        "sub": str(launch.get("sub") or launch.get("user_id") or "").strip(),
        "workspace_id": str(launch.get("workspace_id") or "").strip(),
        "runtime_key": str(launch.get("runtime_key") or "").strip(),
        "aud": _OXAIDE_LOGOUT_AUDIENCE,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + _OXAIDE_LOGOUT_TTL_SECONDS,
    }
    if not payload["sub"] or not payload["workspace_id"] or not payload["runtime_key"]:
        raise HTTPException(status_code=401, detail="Incomplete Oxaide logout identity")
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(
        _oxaide_launch_secret().encode("utf-8"),
        encoded.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return logout_url, f"{encoded}.{signature}"


def _decode_oxaide_logout_token(token: str) -> dict:
    secret = _oxaide_launch_secret()
    if not secret:
        raise HTTPException(status_code=503, detail="Oxaide auth secret is not configured")
    try:
        encoded, signature = token.split(".", 1)
    except ValueError:
        raise HTTPException(status_code=400, detail="Malformed logout token")
    expected = hmac.new(
        secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid logout token signature")
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * ((4 - len(encoded) % 4) % 4))
        payload = json.loads(raw.decode("utf-8"))
        iat = int(payload.get("iat") or 0)
        exp = int(payload.get("exp") or 0)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid logout token payload")
    now = int(time.time())
    if not isinstance(payload, dict) or iat <= 0 or iat > now + 60:
        raise HTTPException(status_code=401, detail="Invalid logout token timestamps")
    if exp <= now or exp - iat > 180:
        raise HTTPException(status_code=401, detail="Logout token expired")
    if str(payload.get("aud") or "") != _OXAIDE_LOGOUT_AUDIENCE:
        raise HTTPException(status_code=401, detail="Invalid logout token audience")
    expected_workspace, expected_runtime = _required_oxaide_runtime_identity()
    if str(payload.get("workspace_id") or "").strip() != expected_workspace:
        raise HTTPException(status_code=401, detail="Logout token workspace mismatch")
    if str(payload.get("runtime_key") or "").strip() != expected_runtime:
        raise HTTPException(status_code=401, detail="Logout token runtime mismatch")
    if not str(payload.get("sub") or "").strip() or not str(payload.get("jti") or "").strip():
        raise HTTPException(status_code=401, detail="Logout token identity is incomplete")
    return payload


def _oxaide_logout_return_url(payload: dict) -> str:
    raw = str(payload.get("return_url") or "").strip()
    try:
        parsed = urlsplit(raw)
    except ValueError:
        parsed = None
    if (
        parsed is None
        or parsed.scheme != "https"
        or parsed.hostname not in {"oxaide.com", "www.oxaide.com"}
        or parsed.path != "/auth/signin"
        or parsed.query != "logout=true"
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise HTTPException(status_code=401, detail="Invalid logout return URL")
    return raw


@router.get('/auth/oxaide-launch', name='auth_oxaide_launch')
async def auth_oxaide_launch(request: Request, token: str = '', next: str = ''):
    if not token:
        raise HTTPException(status_code=400, detail='Missing launch token')

    launch_payload = _decode_oxaide_launch_token(token)
    _consume_oxaide_launch_token(token, int(launch_payload.get("exp") or 0))
    access_token = _encode_oxaide_session_token(launch_payload)
    refresh_token = _create_oxaide_refresh_session(launch_payload)
    access_session = _oxaide_session_from_token(access_token)
    session = Session(
        **{
            **access_session.__dict__,
            "refresh_token": refresh_token,
        }
    )

    audit_log(
        AuditEvent.LOGIN_SUCCESS,
        provider='oxaide-demo',
        user_id=session.user_id,
        email=session.email,
        org_id=session.org_id,
        ip=_client_ip(request),
    )

    landing = _validate_post_login_target(next) or '/'
    resp = RedirectResponse(url=landing, status_code=302)
    set_session_cookies(
        resp,
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        access_token_expires_in=max(60, session.expires_at - int(time.time())),
        use_https=detect_https(request),
        prefix=_prefix(request),
    )
    return resp


@router.get('/auth/oxaide-logout', name='auth_oxaide_logout')
async def auth_oxaide_logout(request: Request, token: str = ''):
    if not token:
        raise HTTPException(status_code=400, detail='Missing logout token')
    payload = _decode_oxaide_logout_token(token)
    destination = _oxaide_logout_return_url(payload)
    audit_log(
        AuditEvent.LOGOUT,
        provider='oxaide-demo',
        user_id=str(payload.get('sub') or ''),
        reason='oxaide_logout_command',
        ip=_client_ip(request),
    )
    prefix = _prefix(request)
    response = RedirectResponse(url=destination, status_code=302)
    clear_session_cookies(response, prefix=prefix)
    clear_pkce_cookie(response, prefix=prefix)
    clear_sso_attempt_cookie(response, prefix=prefix)
    response.headers['Cache-Control'] = 'no-store'
    return response


def _redirect_uri(request: Request) -> str:
    """Reconstruct the absolute callback URL the IDP redirects back to.

    Three resolution tiers:

      1. ``HERMES_DASHBOARD_PUBLIC_URL`` env var or
         ``dashboard.public_url`` in config.yaml — when set, this is
         the complete authority (scheme + host + optional path prefix)
         and we append ``/auth/callback`` verbatim. ``X-Forwarded-Prefix``
         is IGNORED on this code path because the operator has declared
         the public URL — we no longer need to guess from proxy headers,
         and stacking the prefix on top would double-prefix the common
         case where the prefix is already baked into ``public_url``.
         Relief valve for deploys behind reverse proxies whose forwarded
         headers aren't reliable.

      2. ``X-Forwarded-Prefix: /hermes`` (Mission Control deploys) — we
         prepend the prefix to the path FastAPI's ``url_for`` produces
         (it doesn't natively honour this header — it isn't part of the
         Starlette/uvicorn proxy_headers set).

      3. Bare ``request.url_for("auth_callback")`` — under uvicorn's
         ``proxy_headers=True`` this picks up the public https URL from
         ``X-Forwarded-Host`` plus ``X-Forwarded-Proto``. Fly.io's
         default path.
    """
    from urllib.parse import urlparse, urlunparse

    from hermes_cli.dashboard_auth.prefix import (
        prefix_from_request,
        resolve_public_url,
    )

    # Tier 1: operator-declared public URL.
    public_url = resolve_public_url()
    if public_url:
        # ``public_url`` is the complete authority (possibly with a
        # path prefix already baked in). Append the auth callback path
        # verbatim. ``resolve_public_url`` already stripped any trailing
        # slash so we don't produce ``//auth/callback`` double-slashes.
        return f"{public_url}/auth/callback"

    # Tier 2 + 3: reconstruct from the request URL, optionally with
    # X-Forwarded-Prefix layered on top of the path.
    base = str(request.url_for("auth_callback"))
    prefix = prefix_from_request(request)
    if not prefix:
        return base
    parsed = urlparse(base)
    return urlunparse(parsed._replace(path=f"{prefix}{parsed.path}"))


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def _prefix(request: Request) -> str:
    """Resolve the X-Forwarded-Prefix header for the active request.

    Local indirection so the routes pass a consistent value to the
    cookie helpers (cookie name + Path attribute) and the gate's
    redirect builders (login_url construction). See
    ``hermes_cli.dashboard_auth.prefix`` for the normalisation rules.
    """
    from hermes_cli.dashboard_auth.prefix import prefix_from_request
    return prefix_from_request(request)


# ---------------------------------------------------------------------------
# Public: login page (server-rendered HTML, no SPA bundle)
# ---------------------------------------------------------------------------


@router.get("/login", name="login_page")
async def login_page(request: Request):
    # Oxaide owns customer authentication and workspace selection. A fully
    # pinned tenant runtime therefore sends ordinary visitors back through the
    # Oxaide account entry, which returns with a signed, one-time launch token.
    # The password provider remains available only through the explicit
    # operator recovery URL; breakglass is a route selector, not a secret.
    if (
        _is_oxaide_tenant_runtime()
        and request.query_params.get("breakglass", "") != "1"
    ):
        return RedirectResponse(
            url=_oxaide_customer_login_url(),
            status_code=302,
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
            },
        )

    # Read the ``next=`` query the gate's ``_unauth_response`` set on
    # the redirect URL. Validate against the same same-origin rules the
    # callback applies (defence in depth — the gate already filters,
    # but /login is reachable directly too).
    next_path = _validate_post_login_target(
        request.query_params.get("next", "")
    )
    return HTMLResponse(
        render_login_html(next_path=next_path),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


# ---------------------------------------------------------------------------
# Public: provider list for the login-page bootstrap
# ---------------------------------------------------------------------------


@router.get("/api/auth/providers", name="auth_providers")
async def api_auth_providers() -> Any:
    # Advertise only interactive providers; a token-only credential (e.g. drain)
    # is not a sign-in option.
    providers = list_session_providers()
    if not providers:
        # Q13: fail-closed when zero providers are registered.
        return JSONResponse(
            {"detail": "no auth providers registered"},
            status_code=503,
        )
    return {
        "providers": [
            {
                "name": p.name,
                "display_name": p.display_name,
                "supports_password": bool(
                    getattr(p, "supports_password", False)
                ),
            }
            for p in providers
        ],
    }


# ---------------------------------------------------------------------------
# Public: OAuth round trip
# ---------------------------------------------------------------------------


@router.get("/auth/login", name="auth_login")
async def auth_login(request: Request, provider: str, next: str = ""):
    p = get_provider(provider)
    if p is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown provider: {provider!r}",
        )
    if not getattr(p, "supports_session", True):
        raise HTTPException(
            status_code=404,
            detail=f"Provider does not support interactive login: {provider!r}",
        )
    if getattr(p, "supports_password", False):
        from urllib.parse import quote

        safe_next = _validate_post_login_target(next)
        login_url = f"{_prefix(request)}/login"
        if safe_next:
            login_url = f"{login_url}?next={quote(safe_next, safe='')}"
        return RedirectResponse(url=login_url, status_code=302)

    try:
        ls = p.start_login(redirect_uri=_redirect_uri(request))
    except ProviderError as e:
        audit_log(
            AuditEvent.LOGIN_FAILURE,
            provider=provider,
            reason="provider_unreachable",
            ip=_client_ip(request),
        )
        raise HTTPException(
            status_code=503,
            detail=f"Provider unreachable: {e}",
        )

    audit_log(
        AuditEvent.LOGIN_START,
        provider=provider,
        ip=_client_ip(request),
    )

    resp = RedirectResponse(url=ls.redirect_url, status_code=302)
    # Pack the provider name into the PKCE cookie so the callback can
    # find it without a separate cookie. Provider may or may not have
    # already included a ``provider=`` segment.
    pkce = ls.cookie_payload.get("hermes_session_pkce", "")
    if "provider=" not in pkce:
        pkce = f"provider={provider};{pkce}" if pkce else f"provider={provider}"
    # Carry ``next=`` through the round trip in the PKCE cookie. Real
    # IDPs only echo back ``code`` + ``state`` on the callback URL, so
    # query-string transport would lose the value — the cookie is the
    # only server-controlled channel that survives. Validate before we
    # store it so an attacker who reaches /auth/login directly with
    # ``next=//evil.example`` can't poison the cookie.
    safe_next = _validate_post_login_target(next)
    if safe_next:
        from urllib.parse import quote
        pkce = f"{pkce};next={quote(safe_next, safe='')}"
    set_pkce_cookie(
        resp, payload=pkce, use_https=detect_https(request),
        prefix=_prefix(request),
    )
    return resp


@router.get("/auth/callback", name="auth_callback")
async def auth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
):
    pkce_raw = read_pkce_cookie(request)
    if not pkce_raw:
        audit_log(
            AuditEvent.LOGIN_FAILURE,
            reason="missing_pkce_cookie",
            ip=_client_ip(request),
        )
        raise HTTPException(
            status_code=400,
            detail="Missing PKCE state cookie",
        )

    # Parse ``provider=...;state=...;verifier=...;next=...`` — the
    # ``next`` segment is optional (only present when /auth/login was
    # given a next= query). All keys live in the same flat namespace;
    # ``next`` carries a URL-encoded path so it never contains ``;``.
    parts = dict(
        seg.split("=", 1) for seg in pkce_raw.split(";") if "=" in seg
    )
    provider_name = parts.get("provider", "")
    expected_state = parts.get("state", "")
    verifier = parts.get("verifier", "")
    # Read next= from the cookie ONLY. The IDP doesn't echo next= back
    # on the callback URL (it only carries ``code`` + ``state``), so any
    # next= query parameter on the callback URL is attacker-controlled
    # and MUST be ignored.
    next_from_cookie = parts.get("next", "")

    p = get_provider(provider_name)
    if p is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider in cookie: {provider_name!r}",
        )

    if error:
        audit_log(
            AuditEvent.LOGIN_FAILURE,
            provider=provider_name,
            reason="idp_error",
            error=error,
            ip=_client_ip(request),
        )
        raise HTTPException(
            status_code=400,
            detail=f"OAuth error from provider: {error} ({error_description})",
        )

    if not state or state != expected_state:
        audit_log(
            AuditEvent.LOGIN_FAILURE,
            provider=provider_name,
            reason="state_mismatch",
            ip=_client_ip(request),
        )
        raise HTTPException(
            status_code=400,
            detail="OAuth state mismatch (CSRF check failed)",
        )

    try:
        session = p.complete_login(
            code=code,
            state=state,
            code_verifier=verifier,
            redirect_uri=_redirect_uri(request),
        )
    except InvalidCodeError as e:
        audit_log(
            AuditEvent.LOGIN_FAILURE,
            provider=provider_name,
            reason="invalid_code",
            ip=_client_ip(request),
        )
        raise HTTPException(status_code=400, detail=f"Invalid code: {e}")
    except ProviderError as e:
        audit_log(
            AuditEvent.LOGIN_FAILURE,
            provider=provider_name,
            reason="provider_unreachable",
            ip=_client_ip(request),
        )
        raise HTTPException(
            status_code=503,
            detail=f"Provider unreachable: {e}",
        )

    audit_log(
        AuditEvent.LOGIN_SUCCESS,
        provider=provider_name,
        user_id=session.user_id,
        email=session.email,
        org_id=session.org_id,
        ip=_client_ip(request),
    )

    expires_in = max(60, session.expires_at - int(time.time()))
    # Honour the ``next=`` value the gate's _unauth_response set in the
    # /login redirect URL and that /auth/login persisted into the PKCE
    # cookie. We re-validate against the same-origin rules here — the
    # cookie is server-set so this is defence in depth, but a regression
    # that lets attacker-controlled bytes into the cookie would otherwise
    # produce an open redirect.
    landing = _validate_post_login_target(next_from_cookie) or "/"
    resp = RedirectResponse(url=landing, status_code=302)
    set_session_cookies(
        resp,
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        access_token_expires_in=expires_in,
        use_https=detect_https(request),
        prefix=_prefix(request),
    )
    clear_pkce_cookie(resp, prefix=_prefix(request))
    # Clear the one-shot auto-SSO loop-guard marker now that login succeeded,
    # so it never lingers to suppress a future silent attempt after logout.
    clear_sso_attempt_cookie(resp, prefix=_prefix(request))
    return resp


def _validate_post_login_target(raw: str) -> str:
    """Return ``raw`` if it's a safe same-origin path, else empty string.

    The ``next`` query param survives a full OAuth round trip — the gate
    encodes it into the /login redirect, the login page emits it back into
    /auth/login, and the IDP preserves it across /authorize/callback. We
    have to re-validate here because the value came back in via the
    URL (an attacker could craft a /auth/callback URL with their own
    ``next=https://evil.example``).
    """
    if not raw:
        return ""
    from urllib.parse import unquote
    if "\\" in raw or any(ord(char) < 32 or ord(char) == 127 for char in raw):
        return ""
    decoded = unquote(raw)
    if "\\" in decoded or any(ord(char) < 32 or ord(char) == 127 for char in decoded):
        return ""
    if not decoded.startswith("/") or decoded.startswith("//"):
        return ""
    # Don't loop back to login pages or auth flow.
    if any(
        decoded == p or decoded.startswith(p)
        for p in ("/login", "/auth/", "/api/auth/")
    ):
        return ""
    # Reject any ``/api/*`` target. The gate's ``_safe_next_target``
    # already filters these out before they reach the cookie, but a
    # malicious or stale ``next=`` value that re-enters via the
    # callback URL must not be honoured: a successful redirect to an
    # API endpoint renders raw JSON in the browser address bar — never
    # a useful post-login destination, and indistinguishable from an
    # attacker trying to weaponise the redirect.
    if decoded == "/api" or decoded.startswith("/api/"):
        return ""
    return decoded


# ---------------------------------------------------------------------------
# Public: password (non-redirect) login
# ---------------------------------------------------------------------------
#
# Brute-force throttle. The OAuth flow has no guessable secret on our side
# (the IDP owns credentials), but ``/auth/password-login`` accepts a
# password we verify locally, so it's a credential-stuffing target. A
# simple in-process sliding-window limiter per client IP raises the cost
# of online guessing without any external dependency. It is intentionally
# best-effort: process-local (resets on restart), and behind a trusting
# proxy the IP is the proxy's unless X-Forwarded-For is set — which is why
# this is defence-in-depth on top of the provider's own constant-time
# verify, not the only line of defence.

_PW_RATE_MAX_ATTEMPTS = 10
_PW_RATE_WINDOW_SEC = 60.0
_pw_attempts: Dict[str, Deque[float]] = defaultdict(deque)
_pw_attempts_lock = threading.Lock()


def _password_rate_limited(ip: str) -> bool:
    """True if ``ip`` has exceeded the password-login attempt budget.

    Sliding window: prune attempts older than the window, then check the
    count. Records the attempt timestamp when allowed. An empty IP (no
    discernible client) shares a single bucket — fail-safe toward
    throttling rather than letting unattributable traffic through
    unmetered.
    """
    now = time.monotonic()
    cutoff = now - _PW_RATE_WINDOW_SEC
    key = ip or "_unknown_"
    with _pw_attempts_lock:
        bucket = _pw_attempts[key]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= _PW_RATE_MAX_ATTEMPTS:
            return True
        bucket.append(now)
        return False


def _reset_password_rate_limit() -> None:
    """Test-only: clear all rate-limit buckets."""
    with _pw_attempts_lock:
        _pw_attempts.clear()


class _PasswordLoginBody(BaseModel):
    provider: str
    username: str
    password: str
    next: str = ""


@router.post("/auth/password-login", name="auth_password_login")
async def auth_password_login(request: Request, body: _PasswordLoginBody):
    """Authenticate a username/password against a password provider.

    Mirrors the cookie-minting tail of ``/auth/callback`` but skips the
    PKCE/state/code machinery (those are OAuth-only). On success sets the
    session cookies and returns JSON ``{"ok": true, "next": <path>}`` —
    the credential form POSTs via fetch and navigates client-side, so a
    302 (which fetch follows opaquely) is the wrong shape here.

    Failure modes, all deliberately generic so the endpoint can't be used
    as a username oracle or a provider-enumeration oracle:
      * unknown provider / provider lacks password support → 404
      * bad credentials → 401 ("Invalid credentials")
      * backing store unreachable → 503
      * too many attempts from this IP → 429
    """
    ip = _client_ip(request)
    if _password_rate_limited(ip):
        audit_log(
            AuditEvent.LOGIN_FAILURE,
            provider=body.provider,
            reason="rate_limited",
            ip=ip,
        )
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Try again shortly.",
        )

    p = get_provider(body.provider)
    if p is None or not getattr(p, "supports_password", False):
        # Don't leak which providers exist or which support passwords —
        # same 404 whether the provider is unknown or OAuth-only.
        audit_log(
            AuditEvent.LOGIN_FAILURE,
            provider=body.provider,
            reason="unknown_password_provider",
            ip=ip,
        )
        raise HTTPException(status_code=404, detail="Unknown provider")

    try:
        session = p.complete_password_login(
            username=body.username, password=body.password
        )
    except InvalidCredentialsError:
        audit_log(
            AuditEvent.LOGIN_FAILURE,
            provider=body.provider,
            reason="invalid_credentials",
            ip=ip,
        )
        # Generic message — never distinguish unknown-user from wrong-password.
        raise HTTPException(status_code=401, detail="Invalid credentials")
    except NotImplementedError:
        # supports_password was True but the method isn't actually
        # implemented — a provider bug, not a client error.
        raise HTTPException(status_code=500, detail="Provider misconfigured")
    except ProviderError as e:
        audit_log(
            AuditEvent.LOGIN_FAILURE,
            provider=body.provider,
            reason="provider_unreachable",
            ip=ip,
        )
        raise HTTPException(status_code=503, detail=f"Provider unreachable: {e}")

    audit_log(
        AuditEvent.LOGIN_SUCCESS,
        provider=body.provider,
        user_id=session.user_id,
        email=session.email,
        org_id=session.org_id,
        ip=ip,
    )

    expires_in = max(60, session.expires_at - int(time.time()))
    landing = _validate_post_login_target(body.next) or "/"
    resp = JSONResponse({"ok": True, "next": landing})
    set_session_cookies(
        resp,
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        access_token_expires_in=expires_in,
        use_https=detect_https(request),
        prefix=_prefix(request),
    )
    return resp


@router.post("/auth/logout", name="auth_logout")
async def auth_logout(request: Request):
    at, rt = read_session_cookies(request)
    sess = getattr(request.state, "session", None)
    if sess is None and at:
        try:
            sess = _oxaide_session_from_token(at)
        except HTTPException:
            sess = None
    if rt:
        _revoke_oxaide_refresh_session(rt)
        # Best-effort revoke. Try every provider so a session minted by
        # any registered provider is revoked correctly. Failures are
        # logged but never raised.
        for provider in list_providers():
            try:
                provider.revoke_session(refresh_token=rt)
            except Exception as e:  # noqa: BLE001 — best-effort
                _log.warning(
                    "dashboard-auth: revoke on %r failed: %s",
                    provider.name, e,
                )

    audit_log(
        AuditEvent.LOGOUT,
        provider=(sess.provider if sess else "unknown"),
        user_id=(sess.user_id if sess else ""),
        ip=_client_ip(request),
    )

    prefix = _prefix(request)
    redirect_to = f"{prefix}/login"
    logout_token = None
    if sess is not None and sess.provider == "oxaide-demo" and at:
        try:
            redirect_to, logout_token = _oxaide_logout_continuation(at)
        except HTTPException:
            _log.warning("dashboard-auth: could not create Oxaide logout continuation")

    wants_json = "application/json" in request.headers.get("accept", "").lower()
    if wants_json:
        payload: dict[str, Any] = {"ok": True, "redirect_to": redirect_to}
        if logout_token:
            payload["logout_token"] = logout_token
        resp = JSONResponse(payload)
    else:
        location = redirect_to
        if logout_token:
            location = f"{redirect_to}?{urlencode({'token': logout_token})}"
        resp = RedirectResponse(url=location, status_code=302)
    clear_session_cookies(resp, prefix=prefix)
    clear_pkce_cookie(resp, prefix=prefix)
    clear_sso_attempt_cookie(resp, prefix=prefix)
    return resp


# ---------------------------------------------------------------------------
# Auth-required: identity probe for the SPA
# ---------------------------------------------------------------------------


@router.get("/api/auth/me", name="auth_me")
async def api_auth_me(request: Request):
    """Return the verified session as JSON. Auth-required (gate enforces)."""
    sess = getattr(request.state, "session", None)
    if sess is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {
        "user_id": sess.user_id,
        "email": sess.email,
        "display_name": sess.display_name,
        "org_id": sess.org_id,
        "provider": sess.provider,
        "expires_at": sess.expires_at,
        "access_state": sess.access_state,
    }


# ---------------------------------------------------------------------------
# Auth-required: WS upgrade ticket (Phase 5)
# ---------------------------------------------------------------------------


@router.post("/api/auth/ws-ticket", name="auth_ws_ticket")
async def api_auth_ws_ticket(request: Request):
    """Mint a short-lived single-use ticket for the authenticated session.

    Browsers cannot set ``Authorization`` on a WebSocket upgrade, so in
    gated mode the SPA POSTs this endpoint to get a ``?ticket=`` value to
    append to ``/api/pty``, ``/api/console``, ``/api/ws``, ``/api/pub``, or
    ``/api/events``.

    The ticket has a 30-second TTL and is single-use. Calling this endpoint
    multiple times in quick succession (e.g. one ticket per WS) is the
    expected pattern.
    """
    sess = getattr(request.state, "session", None)
    if sess is None:
        # Middleware should already have rejected, but check defensively.
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Import here so the routes module stays usable in test contexts that
    # don't load the ticket store.
    from hermes_cli.dashboard_auth.ws_tickets import TTL_SECONDS, mint_ticket

    trusted_context = None
    if sess.provider == "oxaide-demo":
        trusted_context = _trusted_oxaide_context(
            _decode_oxaide_session_token(sess.access_token)
        )
    ticket = mint_ticket(
        user_id=sess.user_id,
        provider=sess.provider,
        trusted_context=trusted_context,
    )
    audit_log(
        AuditEvent.WS_TICKET_MINTED,
        provider=sess.provider,
        user_id=sess.user_id,
        ip=_client_ip(request),
    )
    return {"ticket": ticket, "ttl_seconds": TTL_SECONDS}
