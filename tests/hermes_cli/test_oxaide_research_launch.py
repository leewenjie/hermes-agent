import hashlib
import json
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from hermes_cli import oxaide_research_launch as launch
from hermes_cli import web_server
from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from hermes_state import OxaideResearchLaunchConflict, SessionDB
from tui_gateway.oxaide_turns import OxaideTurnClient


@pytest.fixture
def launch_env(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    token = set_hermes_home_override(str(home))
    monkeypatch.setenv("HERMES_OXAIDE_WORKSPACE_ID", "workspace-1")
    monkeypatch.setenv("HERMES_OXAIDE_RUNTIME_KEY", "runtimekey1234567890abcd")
    monkeypatch.setenv(
        "HERMES_OXAIDE_RESEARCH_LAUNCH_SIGNING_SECRET",
        "research-launch-test-secret-at-least-32-bytes",
    )
    web_server.app.state.auth_required = False
    web_server.app.state.bound_host = None
    monkeypatch.setattr(web_server, "_dashboard_branding_settings", lambda: {"product": "oxaide"})
    try:
        yield home
    finally:
        reset_hermes_home_override(token)


def _payload(**updates):
    value = {
        "schema_version": "research-launch.v1",
        "dispatch_id": "00000000-0000-4000-8000-000000000001",
        "workspace_id": "workspace-1",
        "user_id": "00000000-0000-4000-8000-000000000002",
        "runtime_key": "runtimekey1234567890abcd",
        "runtime_session_id": "runtime_session_1",
        "prompt": "Compare the strongest evidence for two investments.",
    }
    value.update(updates)
    return value


def _raw(payload=None):
    return json.dumps(payload or _payload(), separators=(",", ":")).encode()


def test_signature_is_bound_to_exact_body_and_fresh_timestamp(launch_env):
    raw = _raw()
    timestamp = str(int(time.time()))
    signature = launch.sign_research_launch_dispatch(timestamp, raw.decode())

    assert launch.verify_research_launch_dispatch(raw, timestamp, signature)
    assert not launch.verify_research_launch_dispatch(raw + b" ", timestamp, signature)
    assert not launch.verify_research_launch_dispatch(raw, str(int(timestamp) - 301), signature)


def test_parser_enforces_runtime_identity_pins(launch_env):
    assert launch.parse_research_launch_dispatch(_raw())["prompt"].startswith("Compare")
    with pytest.raises(launch.InvalidResearchLaunchDispatch, match="runtime_identity_mismatch"):
        launch.parse_research_launch_dispatch(_raw(_payload(workspace_id="workspace-2")))


def test_dispatch_ledger_replays_and_rejects_changed_immutable_payload(launch_env):
    db = SessionDB(launch_env / "state.db")
    payload = _payload()
    digest = hashlib.sha256(_raw(payload)).hexdigest()
    try:
        first = db.accept_oxaide_research_launch_dispatch(payload, digest)
        assert first["replayed"] is False
        replay = db.accept_oxaide_research_launch_dispatch(payload, digest)
        assert replay["replayed"] is True
        with pytest.raises(OxaideResearchLaunchConflict):
            changed = _payload(prompt="Changed prompt")
            db.accept_oxaide_research_launch_dispatch(
                changed, hashlib.sha256(_raw(changed)).hexdigest()
            )
    finally:
        db.close()


def test_dispatch_lease_recovers_only_after_expiry(launch_env):
    db = SessionDB(launch_env / "state.db")
    payload = _payload()
    try:
        db.accept_oxaide_research_launch_dispatch(
            payload, hashlib.sha256(_raw(payload)).hexdigest()
        )
        first = db.claim_oxaide_research_launch_dispatch(payload["dispatch_id"], lease_seconds=30)
        assert first
        assert db.claim_oxaide_research_launch_dispatch(payload["dispatch_id"], lease_seconds=30) is None
        assert db.release_oxaide_research_launch_dispatch(payload["dispatch_id"], first, "retry")
        assert db.claim_oxaide_research_launch_dispatch(payload["dispatch_id"], lease_seconds=30)
    finally:
        db.close()


def test_machine_transport_declares_a_complete_turn_context(launch_env, monkeypatch):
    monkeypatch.setenv(
        "HERMES_OXAIDE_USAGE_SIGNING_SECRET",
        "usage-signing-test-secret-at-least-32-bytes",
    )

    transport = launch._MachineTransport(_payload())
    assert transport.trusted_context["context_kind"] == "machine_launch"
    assert transport.trusted_context["dispatch_id"] == _payload()["dispatch_id"]

    client = OxaideTurnClient(transport.trusted_context)
    assert client.context_kind == "machine_launch"


def test_session_attachment_rolls_back_when_dispatch_lease_is_lost(launch_env):
    db = SessionDB(launch_env / "state.db")
    payload = _payload()
    try:
        db.accept_oxaide_research_launch_dispatch(
            payload, hashlib.sha256(_raw(payload)).hexdigest()
        )
        expired_lease = db.claim_oxaide_research_launch_dispatch(
            payload["dispatch_id"], lease_seconds=-1
        )
        assert expired_lease
        with pytest.raises(
            OxaideResearchLaunchConflict,
            match="research_launch_session_lease_lost",
        ):
            db.attach_oxaide_research_launch_session(
                payload["dispatch_id"],
                expired_lease,
                "orphan-session",
                payload["user_id"],
            )

        assert db.get_session("orphan-session") is None
        assert db.get_oxaide_research_launch_dispatch(payload["dispatch_id"])[
            "hermes_session_id"
        ] is None
    finally:
        db.close()


def test_replay_returns_one_durable_ordinary_user_turn(launch_env, monkeypatch):
    payload = _payload()
    raw = _raw(payload)
    prompt_submissions = []

    def fake_gateway(request):
        method = request["method"]
        if method == "session.create":
            return {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {
                    "session_id": "live-session-1",
                    "stored_session_id": "durable-session-1",
                },
            }
        if method == "session.resume":
            return {
                "jsonrpc": "2.0", "id": request["id"],
                "result": {"session_id": "live-session-2"},
            }
        if method == "prompt.submit":
            prompt_submissions.append(request["params"]["text"])
            db = SessionDB(launch_env / "state.db")
            try:
                db.append_message(
                    "durable-session-1", "user", request["params"]["text"]
                )
            finally:
                db.close()
            return {
                "jsonrpc": "2.0", "id": request["id"],
                "result": {"status": "streaming"},
            }
        raise AssertionError(f"unexpected gateway method: {method}")

    monkeypatch.setattr(launch, "handle_request", fake_gateway)
    first = launch.accept_research_launch_dispatch(payload, raw)
    replay = launch.accept_research_launch_dispatch(payload, raw)

    assert first["session_id"] == "durable-session-1"
    assert replay == {
        "dispatch_id": payload["dispatch_id"],
        "session_id": "durable-session-1",
        "replayed": True,
    }
    assert prompt_submissions == [payload["prompt"]]
    db = SessionDB(launch_env / "state.db")
    try:
        assert db.get_session("durable-session-1")["user_id"] == payload["user_id"]
    finally:
        db.close()


def test_machine_route_bypasses_browser_auth_after_valid_signature(launch_env, monkeypatch):
    monkeypatch.setattr(
        launch,
        "accept_research_launch_dispatch",
        lambda payload, raw_body: {
            "dispatch_id": payload["dispatch_id"],
            "session_id": "durable-session-1",
            "replayed": False,
        },
    )
    raw = _raw()
    timestamp = str(int(time.time()))
    signature = launch.sign_research_launch_dispatch(timestamp, raw.decode())
    response = TestClient(web_server.app).post(
        "/api/oxaide/turn-dispatch",
        content=raw,
        headers={
            "content-type": "application/json",
            "x-oxaide-research-launch-timestamp": timestamp,
            "x-oxaide-research-launch-signature": signature,
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "dispatch_id": _payload()["dispatch_id"],
        "session_id": "durable-session-1",
        "replayed": False,
    }


def test_machine_route_rejects_signature_before_parsing(launch_env):
    response = TestClient(web_server.app).post(
        "/api/oxaide/turn-dispatch",
        content=b"not-json",
        headers={
            "x-oxaide-research-launch-timestamp": str(int(time.time())),
            "x-oxaide-research-launch-signature": "0" * 64,
        },
    )
    assert response.status_code == 401
    assert response.json()["code"] == "research_launch_signature_invalid"


def test_standalone_oxaide_compose_requires_dispatch_signing_secret():
    compose = (Path(__file__).parents[2] / "docker-compose.oxaide-workspace.yml").read_text()

    assert "HERMES_OXAIDE_RESEARCH_LAUNCH_SIGNING_SECRET:" in compose
    assert "${HERMES_OXAIDE_RESEARCH_LAUNCH_SIGNING_SECRET:?" in compose
