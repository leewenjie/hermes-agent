"""Focused invariants for the Oxaide authorize/complete/release lifecycle."""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error

import pytest

from tui_gateway import server
from tui_gateway.oxaide_turns import (
    OxaideTurnClient,
    OxaideTurnDenied,
    OxaideTurnError,
)
from tui_gateway.transport import bind_transport, reset_transport


_CONTEXT_A = {
    "workspace_id": "workspace-a",
    "runtime_session_id": "runtime-a",
    "runtime_key": "runtime-key-a",
    "user_id": "user-a",
    "access_state": "active",
    "jti": "launch-a",
    "expires_at": 4_000_000_000,
}
_CONTEXT_B = {
    "workspace_id": "workspace-b",
    "runtime_session_id": "runtime-b",
    "runtime_key": "runtime-key-b",
    "user_id": "user-b",
    "access_state": "active",
    "jti": "launch-b",
    "expires_at": 4_000_000_000,
}


class _Transport:
    def __init__(self, context):
        self.trusted_context = context

    def write(self, _obj):
        return True

    def close(self):
        return None


class _Response:
    def __init__(self, body):
        self.status = 200
        self._body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=-1):
        return self._body


def _install_session(monkeypatch, sid="sid"):
    session = {
        "agent": None,
        "attached_images": ["must-not-be-touched.png"],
        "history": [],
        "history_lock": threading.Lock(),
        "history_version": 0,
        "inflight_turn": None,
        "last_active": 0,
        "lazy": False,
        "running": False,
        "session_key": "stored-session",
        "transport": _Transport({}),
        "trusted_launch_context": dict(_CONTEXT_A),
    }
    server._sessions[sid] = session
    monkeypatch.setattr(server, "_persist_branch_seed", lambda _session: None)
    return session


def test_transport_identity_isolated_and_rpc_params_cannot_override(monkeypatch):
    monkeypatch.setattr(server, "_new_session_key", lambda: "stored")
    monkeypatch.setattr(server, "_claim_active_session_slot", lambda *_a, **_k: (None, None))
    monkeypatch.setattr(server, "_schedule_agent_build", lambda _sid: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    monkeypatch.setattr(server, "_register_session_cwd", lambda _session: None)
    monkeypatch.setattr(server, "_enable_gateway_prompts", lambda: None)
    monkeypatch.setattr(server, "_git_branch_for_cwd", lambda _cwd: "")
    server._sessions.clear()

    try:
        created = []
        for context in (_CONTEXT_A, _CONTEXT_B):
            token = bind_transport(_Transport(dict(context)))
            try:
                response = server._methods["session.create"](
                    "rid",
                    {
                        "cwd": "/tmp/attacker-controlled",
                        "profile": "other-tenant",
                        "workspace_id": "rpc-spoof",
                        "runtime_session_id": "rpc-spoof",
                        "runtime_key": "rpc-spoof",
                        "user_id": "rpc-spoof",
                        "jti": "rpc-spoof",
                        "expires_at": 1,
                    },
                )
            finally:
                reset_transport(token)
            sid = response["result"]["session_id"]
            created.append(server._sessions[sid]["trusted_launch_context"])
            assert server._sessions[sid]["profile_home"] is None
            assert server._sessions[sid]["cwd"] != "/tmp/attacker-controlled"

        assert created == [_CONTEXT_A, _CONTEXT_B]
        assert created[0]["workspace_id"] != created[1]["workspace_id"]
    finally:
        server._sessions.clear()


def test_trusted_oxaide_owner_persists_and_binds_session_context(monkeypatch):
    created = []

    class _DB:
        def create_session(self, key, **kwargs):
            created.append((key, kwargs))

    session = {
        "session_key": "stored-session",
        "source": "tui",
        "cwd": "/tmp",
        "trusted_launch_context": dict(_CONTEXT_A),
    }
    server._sessions["sid"] = session
    monkeypatch.setattr(server, "_get_db", lambda: _DB())
    monkeypatch.setattr(server, "_resolve_model", lambda: "test-model")

    try:
        server._ensure_session_db_row(session)
        assert created[0][0] == "stored-session"
        assert created[0][1]["user_id"] == _CONTEXT_A["user_id"]

        from gateway.session_context import get_session_env

        tokens = server._set_session_context("stored-session")
        try:
            assert get_session_env("HERMES_SESSION_USER_ID") == _CONTEXT_A["user_id"]
        finally:
            server._clear_session_context(tokens)
    finally:
        server._sessions.clear()


def test_trusted_resume_repairs_ownerless_durable_session():
    class _DB:
        def __init__(self):
            self.row = {
                "id": "stored-session",
                "source": "tui",
                "model": "test-model",
                "user_id": None,
            }
            self.create_calls = []

        def create_session(self, session_id, **kwargs):
            self.create_calls.append((session_id, kwargs))
            if not str(self.row.get("user_id") or "").strip():
                self.row["user_id"] = kwargs.get("user_id")

        def get_session(self, _session_id):
            return dict(self.row)

    db = _DB()
    repaired = server._reconcile_trusted_session_owner(
        db, "stored-session", dict(db.row), _CONTEXT_A["user_id"]
    )

    assert repaired["user_id"] == _CONTEXT_A["user_id"]
    assert db.create_calls == [
        (
            "stored-session",
            {
                "source": "tui",
                "model": "test-model",
                "user_id": _CONTEXT_A["user_id"],
            },
        )
    ]


def test_trusted_resume_accepts_matching_owner_without_rewrite():
    class _DB:
        def create_session(self, *_args, **_kwargs):
            pytest.fail("matching ownership must not be rewritten")

    row = {"id": "stored-session", "user_id": _CONTEXT_A["user_id"]}
    assert (
        server._reconcile_trusted_session_owner(
            _DB(), "stored-session", row, _CONTEXT_A["user_id"]
        )
        is row
    )


def test_trusted_resume_rejects_established_other_owner():
    row = {"id": "stored-session", "user_id": _CONTEXT_B["user_id"]}

    with pytest.raises(
        server._SessionOwnershipMismatch,
        match="Session does not belong to this user",
    ):
        server._reconcile_trusted_session_owner(
            object(), "stored-session", row, _CONTEXT_A["user_id"]
        )


def test_live_session_reuse_is_fenced_by_trusted_owner():
    session = {
        "agent": None,
        "session_key": "stored-session",
        "trusted_launch_context": dict(_CONTEXT_A),
    }
    server._sessions["live-a"] = session
    try:
        assert server._find_live_session_by_key(
            "stored-session", expected_user_id=_CONTEXT_A["user_id"]
        ) == ("live-a", session)
        assert server._find_live_session_by_key(
            "stored-session", expected_user_id=_CONTEXT_B["user_id"]
        ) is None
        assert server._live_session_identity_conflict(
            "stored-session", _CONTEXT_B["user_id"]
        ) is True
    finally:
        server._sessions.clear()


def test_oxaide_session_create_does_not_prebuild_agent(monkeypatch):
    builds = []
    monkeypatch.setattr(server, "_new_session_key", lambda: "stored")
    monkeypatch.setattr(server, "_claim_active_session_slot", lambda *_a, **_k: (None, None))
    monkeypatch.setattr(server, "_schedule_agent_build", lambda sid: builds.append(sid))
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    monkeypatch.setattr(server, "_register_session_cwd", lambda _session: None)
    monkeypatch.setattr(server, "_enable_gateway_prompts", lambda: None)
    monkeypatch.setattr(server, "_git_branch_for_cwd", lambda _cwd: "")
    server._sessions.clear()

    try:
        token = bind_transport(_Transport(dict(_CONTEXT_A)))
        try:
            server._methods["session.create"]("rid", {})
        finally:
            reset_transport(token)
        assert builds == []

        token = bind_transport(_Transport({}))
        try:
            response = server._methods["session.create"]("rid-local", {})
        finally:
            reset_transport(token)
        assert builds == [response["result"]["session_id"]]
    finally:
        server._sessions.clear()


def test_alternate_rpc_cannot_build_first_oxaide_agent(monkeypatch):
    session = {
        "agent_ready": threading.Event(),
        "trusted_launch_context": dict(_CONTEXT_A),
    }
    server._start_agent_build("sid", session)
    assert session["agent_ready"].is_set() is False
    assert session.get("agent_build_started") is not True


def test_authorize_happens_before_agent_or_context_work(monkeypatch):
    session = _install_session(monkeypatch)
    calls = []

    class _Turn:
        def release(self):
            calls.append("release")

    monkeypatch.setattr(
        server, "_authorize_oxaide_user_turn", lambda _session: calls.append("authorize") or _Turn()
    )
    monkeypatch.setattr(server, "_ensure_session_db_row", lambda _session: calls.append("db"))
    monkeypatch.setattr(server, "_start_agent_build", lambda *_args: calls.append("agent"))
    monkeypatch.setattr(
        server,
        "_run_prompt_submit",
        lambda *_args, **_kwargs: calls.append("run"),
    )

    try:
        token = bind_transport(_Transport(dict(_CONTEXT_A)))
        try:
            response = server._methods["prompt.submit"](
                "rid", {"session_id": "sid", "text": "real user prompt"}
            )
        finally:
            reset_transport(token)
        assert response["result"]["status"] == "streaming"
        session["_run_thread"].join(timeout=2)
        assert calls[:4] == ["authorize", "db", "agent", "run"]
    finally:
        server._sessions.clear()


def test_authorization_denial_never_runs_agent_or_consumes_images(monkeypatch):
    session = _install_session(monkeypatch)
    calls = []

    def deny(_session):
        calls.append("authorize")
        raise OxaideTurnDenied("trial_turn_limit_reached")

    monkeypatch.setattr(server, "_authorize_oxaide_user_turn", deny)
    monkeypatch.setattr(server, "_ensure_session_db_row", lambda _session: calls.append("db"))
    monkeypatch.setattr(server, "_start_agent_build", lambda *_args: calls.append("agent"))
    monkeypatch.setattr(server, "_run_prompt_submit", lambda *_args, **_kwargs: calls.append("run"))

    try:
        token = bind_transport(_Transport(dict(_CONTEXT_A)))
        try:
            response = server._methods["prompt.submit"](
                "rid", {"session_id": "sid", "text": "must be denied"}
            )
        finally:
            reset_transport(token)
        assert response["error"]["code"] == 4020
        assert "trial_turn_limit_reached" in response["error"]["message"]
        assert calls == ["authorize"]
        assert session["attached_images"] == ["must-not-be-touched.png"]
        assert session["running"] is False
    finally:
        server._sessions.clear()


def test_same_event_id_authorizes_and_completes_once_without_content(monkeypatch):
    monkeypatch.setenv("HERMES_OXAIDE_USAGE_SIGNING_SECRET", "test-usage-signing-secret-at-least-32-bytes")
    monkeypatch.setenv(
        "OXAIDE_TURN_ENDPOINT",
        "https://oxaide.test/api/agents/billing/usage/record",
    )
    payloads = []

    def urlopen(request, timeout):
        assert timeout == 5.0
        assert request.get_header("Accept") == "application/json"
        assert request.get_header("User-agent") == "Oxaide-Hermes-Runtime/1"
        payload = json.loads(request.data)
        payloads.append(payload)
        code = {
            "authorize": "turn_authorized",
            "complete": "turn_completed",
        }[payload["phase"]]
        return _Response(
            {
                "ok": True,
                "phase": payload["phase"],
                "code": code,
                "workspace_id": "workspace-a",
            }
        )

    monkeypatch.setattr("tui_gateway.oxaide_turns.urllib.request.urlopen", urlopen)
    turn = OxaideTurnClient(dict(_CONTEXT_A)).authorize()
    turn.complete(
        {
            "schema_version": "2026-07-14-v1",
            "input_tokens": 120,
            "output_tokens": 30,
            "estimated_cost_usd": 0.0012,
        }
    )

    assert [payload["phase"] for payload in payloads] == ["authorize", "complete"]
    assert payloads[0]["hermes_event_id"] == payloads[1]["hermes_event_id"]
    assert len(payloads[0]["hermes_event_id"]) >= 8
    serialized = json.dumps(payloads)
    assert "real user prompt" not in serialized
    assert "assistant" not in serialized
    assert "runtime_key" not in serialized
    assert payloads[1]["details"] == {
        "schema_version": "2026-07-14-v1",
        "input_tokens": 120,
        "output_tokens": 30,
        "estimated_cost_usd": 0.0012,
    }


def test_heartbeat_renews_same_event_until_completion(monkeypatch):
    monkeypatch.setenv("HERMES_OXAIDE_USAGE_SIGNING_SECRET", "test-usage-signing-secret-at-least-32-bytes")
    monkeypatch.setenv(
        "OXAIDE_TURN_ENDPOINT",
        "https://oxaide.test/api/agents/billing/usage/record",
    )
    monkeypatch.setattr("tui_gateway.oxaide_turns._HEARTBEAT_INTERVAL_SECONDS", 0.01)
    heartbeat_seen = threading.Event()
    payloads = []

    def urlopen(request, timeout):
        payload = json.loads(request.data)
        payloads.append(payload)
        if payload["phase"] == "heartbeat":
            heartbeat_seen.set()
        code = {
            "authorize": "turn_authorized",
            "heartbeat": "turn_lease_renewed",
            "complete": "turn_completed",
        }[payload["phase"]]
        return _Response(
            {
                "ok": True,
                "phase": payload["phase"],
                "code": code,
                "workspace_id": "workspace-a",
            }
        )

    monkeypatch.setattr("tui_gateway.oxaide_turns.urllib.request.urlopen", urlopen)
    turn = OxaideTurnClient(dict(_CONTEXT_A)).authorize(
        on_lease_lost=lambda _code: pytest.fail("healthy heartbeat lost its lease")
    )
    assert heartbeat_seen.wait(timeout=1)
    turn.complete()

    phases = [payload["phase"] for payload in payloads]
    assert phases[0] == "authorize"
    assert "heartbeat" in phases
    assert phases[-1] == "complete"
    assert {payload["hermes_event_id"] for payload in payloads} == {turn.event_id}


def test_transient_heartbeat_failure_retries_within_active_lease(monkeypatch):
    monkeypatch.setenv("HERMES_OXAIDE_USAGE_SIGNING_SECRET", "test-usage-signing-secret-at-least-32-bytes")
    monkeypatch.setenv(
        "OXAIDE_TURN_ENDPOINT",
        "https://oxaide.test/api/agents/billing/usage/record",
    )
    monkeypatch.setattr("tui_gateway.oxaide_turns._HEARTBEAT_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr("tui_gateway.oxaide_turns._HEARTBEAT_RETRY_INTERVAL_SECONDS", 0.01)
    heartbeat_recovered = threading.Event()
    lost_codes = []
    heartbeat_attempts = 0

    def urlopen(request, timeout):
        nonlocal heartbeat_attempts
        payload = json.loads(request.data)
        if payload["phase"] == "authorize":
            return _Response(
                {
                    "ok": True,
                    "phase": "authorize",
                    "code": "turn_authorized",
                    "workspace_id": "workspace-a",
                }
            )
        if payload["phase"] == "heartbeat":
            heartbeat_attempts += 1
            if heartbeat_attempts == 1:
                raise urllib.error.URLError("temporary network outage")
            heartbeat_recovered.set()
            return _Response(
                {
                    "ok": True,
                    "phase": "heartbeat",
                    "code": "turn_lease_renewed",
                    "workspace_id": "workspace-a",
                }
            )
        return _Response(
            {
                "ok": True,
                "phase": "complete",
                "code": "turn_completed",
                "workspace_id": "workspace-a",
            }
        )

    monkeypatch.setattr("tui_gateway.oxaide_turns.urllib.request.urlopen", urlopen)
    turn = OxaideTurnClient(dict(_CONTEXT_A)).authorize(
        on_lease_lost=lost_codes.append
    )

    assert heartbeat_recovered.wait(timeout=1)
    turn.complete()
    assert heartbeat_attempts >= 2
    assert lost_codes == []


def test_persistent_transient_heartbeat_failure_stops_at_lease_deadline(monkeypatch):
    monkeypatch.setenv("HERMES_OXAIDE_USAGE_SIGNING_SECRET", "test-usage-signing-secret-at-least-32-bytes")
    monkeypatch.setenv(
        "OXAIDE_TURN_ENDPOINT",
        "https://oxaide.test/api/agents/billing/usage/record",
    )
    monkeypatch.setattr("tui_gateway.oxaide_turns._HEARTBEAT_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr("tui_gateway.oxaide_turns._HEARTBEAT_RETRY_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr("tui_gateway.oxaide_turns._LEGACY_LEASE_GRACE_SECONDS", 0.06)
    lease_lost = threading.Event()
    lost_codes = []
    heartbeat_attempts = 0

    def urlopen(request, timeout):
        nonlocal heartbeat_attempts
        payload = json.loads(request.data)
        if payload["phase"] == "authorize":
            return _Response(
                {
                    "ok": True,
                    "phase": "authorize",
                    "code": "turn_authorized",
                    "workspace_id": "workspace-a",
                }
            )
        heartbeat_attempts += 1
        raise urllib.error.URLError("persistent network outage")

    def on_lease_lost(code):
        lost_codes.append(code)
        lease_lost.set()

    monkeypatch.setattr("tui_gateway.oxaide_turns.urllib.request.urlopen", urlopen)
    started_at = time.monotonic()
    OxaideTurnClient(dict(_CONTEXT_A)).authorize(on_lease_lost=on_lease_lost)

    assert lease_lost.wait(timeout=1)
    assert time.monotonic() - started_at >= 0.05
    assert heartbeat_attempts >= 2
    assert lost_codes == ["turn_lease_renewal_failed"]


def test_heartbeat_failure_fails_closed(monkeypatch):
    monkeypatch.setenv("HERMES_OXAIDE_USAGE_SIGNING_SECRET", "test-usage-signing-secret-at-least-32-bytes")
    monkeypatch.setenv(
        "OXAIDE_TURN_ENDPOINT",
        "https://oxaide.test/api/agents/billing/usage/record",
    )
    monkeypatch.setattr("tui_gateway.oxaide_turns._HEARTBEAT_INTERVAL_SECONDS", 0.01)
    lease_lost = threading.Event()
    lost_codes = []

    def urlopen(request, timeout):
        payload = json.loads(request.data)
        if payload["phase"] == "authorize":
            return _Response(
                {
                    "ok": True,
                    "phase": "authorize",
                    "code": "turn_authorized",
                    "workspace_id": "workspace-a",
                }
            )
        return _Response(
            {
                "ok": False,
                "phase": "heartbeat",
                "code": "turn_reservation_not_active",
                "workspace_id": "workspace-a",
            }
        )

    def on_lease_lost(code):
        lost_codes.append(code)
        lease_lost.set()

    monkeypatch.setattr("tui_gateway.oxaide_turns.urllib.request.urlopen", urlopen)
    OxaideTurnClient(dict(_CONTEXT_A)).authorize(on_lease_lost=on_lease_lost)

    assert lease_lost.wait(timeout=1)
    assert lost_codes == ["turn_reservation_not_active"]


def test_each_successful_authorization_mints_a_fresh_execution_capability(
    monkeypatch,
):
    from hermes_cli import managed_scope

    managed_scope._reset_managed_runtime_terminal_failure_for_tests()
    monkeypatch.setenv(
        "HERMES_OXAIDE_USAGE_SIGNING_SECRET",
        "test-usage-signing-secret-at-least-32-bytes",
    )
    callbacks = []

    class _Turn:
        pass

    def authorize(self, on_lease_lost=None):
        callbacks.append(on_lease_lost)
        return _Turn()

    monkeypatch.setattr(OxaideTurnClient, "authorize", authorize)
    monkeypatch.setattr(server, "_schedule_oxaide_runtime_hard_stop", lambda _code: None)
    session = {
        "trusted_launch_context": dict(_CONTEXT_A),
        "history_lock": threading.Lock(),
    }

    first = server._authorize_oxaide_user_turn(session)
    second = server._authorize_oxaide_user_turn(session)

    assert first.execution_capability is not second.execution_capability
    assert first.execution_capability.is_active
    assert second.execution_capability.is_active

    callbacks[0]("turn_reservation_binding_fenced")
    assert first.execution_capability.is_active is False
    assert second.execution_capability.is_active
    managed_scope._reset_managed_runtime_terminal_failure_for_tests()


def test_lease_loss_interrupts_physical_agent_work_and_schedules_one_hard_stop(
    monkeypatch,
):
    from agent.execution_capability import ExecutionCapability
    from hermes_cli import managed_scope

    managed_scope._reset_managed_runtime_terminal_failure_for_tests()
    capability = ExecutionCapability()

    class _Agent:
        def __init__(self):
            self.interrupted = 0

        def interrupt(self):
            assert capability.is_active is False
            self.interrupted += 1

    agent = _Agent()
    session = {"agent": agent, "history_lock": threading.Lock()}
    scheduled = []
    monkeypatch.setattr(
        server,
        "_schedule_oxaide_runtime_hard_stop",
        lambda code: scheduled.append(code),
    )

    server._cancel_oxaide_turn_after_lease_loss(
        session,
        "turn_reservation_binding_fenced",
        execution_capability=capability,
    )
    server._cancel_oxaide_turn_after_lease_loss(
        session,
        "turn_reservation_binding_fenced",
        execution_capability=capability,
    )

    assert managed_scope.managed_runtime_terminal_failure() == "terminal_lease_loss"
    assert session["_turn_cancel_requested"] is True
    assert session["_oxaide_lease_lost"] == "turn_reservation_binding_fenced"
    assert capability.revoke_reason == "turn_reservation_binding_fenced"
    assert agent.interrupted == 2
    assert scheduled == ["turn_reservation_binding_fenced"]
    managed_scope._reset_managed_runtime_terminal_failure_for_tests()


def test_runtime_hard_stop_terminates_s6_service_tree(monkeypatch):
    calls = []
    monkeypatch.setenv(
        "HERMES_MANAGED_RUNTIME_SHUTDOWN_COMMAND",
        "/opt/runtime/bin/terminate",
    )
    monkeypatch.setattr(
        server.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )
    monkeypatch.setattr(
        os,
        "_exit",
        lambda code: (_ for _ in ()).throw(SystemExit(code)),
    )

    with pytest.raises(SystemExit, match="70"):
        server._terminate_oxaide_runtime("turn_reservation_binding_fenced")

    assert calls == [
        (
            ["/opt/runtime/bin/terminate"],
            {"check": True, "timeout": 10},
        )
    ]


def test_background_turn_authorizes_before_child_agent_construction(monkeypatch):
    from agent.execution_capability import ExecutionCapability

    session = _install_session(monkeypatch)
    session["agent"] = object()
    calls = []
    capability = ExecutionCapability()

    class _Turn:
        execution_capability = capability

        def complete(self, details=None):
            calls.append(("complete", details))

        def release(self):
            calls.append(("release", None))

    class _ChildAgent:
        model = "test-model"
        provider = "test-provider"

        def __init__(self, **kwargs):
            calls.append(("construct", None))
            assert kwargs["execution_capability"] is capability
            self.session_api_calls = 0
            self.session_input_tokens = 0
            self.session_output_tokens = 0
            self.session_cache_read_tokens = 0
            self.session_cache_write_tokens = 0
            self.session_reasoning_tokens = 0
            self.session_estimated_cost_usd = 0.0

        def run_conversation(self, **_kwargs):
            calls.append(("run", None))
            self.session_api_calls = 1
            self.session_input_tokens = 12
            self.session_output_tokens = 3
            return {"final_response": "background answer"}

    monkeypatch.setattr(
        server,
        "_authorize_oxaide_user_turn",
        lambda _session: calls.append(("authorize", None)) or _Turn(),
    )
    monkeypatch.setattr(
        server,
        "_background_agent_kwargs",
        lambda _agent, _task_id, execution_capability=None: {
            "execution_capability": execution_capability,
        },
    )
    monkeypatch.setattr("run_agent.AIAgent", _ChildAgent)
    monkeypatch.setattr(server, "_emit", lambda *_args: calls.append(("emit", None)))

    try:
        response = server._methods["prompt.background"](
            "rid", {"session_id": "sid", "text": "background prompt"}
        )
        assert "task_id" in response["result"]
        deadline = time.monotonic() + 2
        while not any(call[0] == "emit" for call in calls):
            if time.monotonic() >= deadline:
                pytest.fail("background turn did not complete")
            threading.Event().wait(0.01)

        assert [call[0] for call in calls[:4]] == [
            "authorize",
            "construct",
            "run",
            "complete",
        ]
        details = next(value for name, value in calls if name == "complete")
        assert details["origin"] == "background"
        assert details["api_calls"] == 1
        assert details["input_tokens"] == 12
        assert details["output_tokens"] == 3
        assert "prompt" not in details
        assert "response" not in details
    finally:
        server._sessions.clear()


def test_background_authorization_denial_never_constructs_agent(monkeypatch):
    _install_session(monkeypatch)

    monkeypatch.setattr(
        server,
        "_authorize_oxaide_user_turn",
        lambda _session: (_ for _ in ()).throw(
            OxaideTurnDenied("trial_turn_limit_reached")
        ),
    )

    try:
        response = server._methods["prompt.background"](
            "rid", {"session_id": "sid", "text": "must be denied"}
        )
        assert response["error"]["code"] == 4020
        assert "trial_turn_limit_reached" in response["error"]["message"]
    finally:
        server._sessions.clear()


def test_background_failure_hides_private_inference_identity(monkeypatch):
    session = _install_session(monkeypatch)
    session["agent"] = object()
    emitted = []
    private = "gpt-private via private-provider at /private/runtime"

    class _Turn:
        def release(self):
            return None

    class _ChildAgent:
        model = "private-model"
        provider = "private-provider"
        session_api_calls = 0
        session_input_tokens = 0
        session_output_tokens = 0
        session_cache_read_tokens = 0
        session_cache_write_tokens = 0
        session_reasoning_tokens = 0
        session_estimated_cost_usd = 0.0

        def __init__(self, **_kwargs):
            pass

        def run_conversation(self, **_kwargs):
            raise RuntimeError(private)

    class _InlineThread:
        def __init__(self, target=None, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setenv("HERMES_OXAIDE_WORKSPACE_ID", "workspace-a")
    monkeypatch.setenv("HERMES_OXAIDE_RUNTIME_KEY", "runtime-key-a")
    monkeypatch.setattr(server, "_authorize_oxaide_user_turn", lambda _session: _Turn())
    monkeypatch.setattr(server, "_background_agent_kwargs", lambda *_args: {})
    monkeypatch.setattr("run_agent.AIAgent", _ChildAgent)
    monkeypatch.setattr(server.threading, "Thread", _InlineThread)
    monkeypatch.setattr(
        server,
        "_emit",
        lambda event, _sid, payload=None: emitted.append((event, payload)),
    )

    try:
        token = bind_transport(_Transport(dict(_CONTEXT_A)))
        try:
            response = server._methods["prompt.background"](
                "rid", {"session_id": "sid", "text": "background prompt"}
            )
        finally:
            reset_transport(token)
        assert "task_id" in response["result"]
        deadline = time.monotonic() + 2
        while not emitted:
            if time.monotonic() >= deadline:
                pytest.fail("background failure did not complete")
            threading.Event().wait(0.01)

        assert emitted == [
            (
                "background.complete",
                {
                    "task_id": emitted[0][1]["task_id"],
                    "text": server._managed_inference_error(),
                },
            )
        ]
        assert private not in str(emitted)
    finally:
        server._sessions.clear()


def test_established_runtime_session_outlives_launch_token(monkeypatch):
    monkeypatch.setenv("HERMES_OXAIDE_USAGE_SIGNING_SECRET", "test-usage-signing-secret-at-least-32-bytes")
    monkeypatch.setenv(
        "OXAIDE_TURN_ENDPOINT",
        "https://oxaide.test/api/agents/billing/usage/record",
    )
    context = dict(_CONTEXT_A, expires_at=1)

    def urlopen(request, timeout):
        payload = json.loads(request.data)
        return _Response(
            {
                "ok": True,
                "phase": payload["phase"],
                "code": "turn_authorized",
                "workspace_id": "workspace-a",
            }
        )

    monkeypatch.setattr("tui_gateway.oxaide_turns.urllib.request.urlopen", urlopen)
    assert OxaideTurnClient(context).authorize().event_id


def test_success_completes_once_and_failed_turns_release():
    from agent.execution_capability import ExecutionCapability

    class _Turn:
        def __init__(self):
            self.completed = 0
            self.released = 0
            self.execution_capability = ExecutionCapability()

        def complete(self, details=None):
            assert self.execution_capability.is_active is False
            self.completed += 1
            self.details = details

        def release(self):
            assert self.execution_capability.is_active is False
            self.released += 1

    success = _Turn()
    details = {"input_tokens": 10, "output_tokens": 2}
    server._settle_oxaide_turn(
        success,
        {"final_response": "answer"},
        "answer",
        "complete",
        details=details,
    )
    assert (success.completed, success.released) == (1, 0)
    assert success.details == details
    assert success.execution_capability.revoke_reason == "turn_completed"

    for result, raw, status in (
        ({"failed": True}, "answer", "complete"),
        ({"partial": True}, "answer", "complete"),
        ({"interrupted": True}, "answer", "interrupted"),
        ({"error": "provider failed"}, "answer", "error"),
        ({}, "", "complete"),
    ):
        failed = _Turn()
        server._settle_oxaide_turn(failed, result, raw, status)
        assert (failed.completed, failed.released) == (0, 1)
        assert failed.execution_capability.revoke_reason == "turn_released"


def test_synthetic_turn_is_unmetered_noop():
    server._settle_oxaide_turn(None, {"failed": False}, "synthetic answer", "complete")


def test_hosted_full_turn_without_authorization_fails_closed(monkeypatch):
    monkeypatch.setenv("HERMES_OXAIDE_WORKSPACE_ID", "workspace-a")
    monkeypatch.setenv("HERMES_OXAIDE_RUNTIME_KEY", "runtime-key-a")
    monkeypatch.delenv("HERMES_INTERNAL_OXAIDE_LOOPBACK_DEV", raising=False)
    session = {"history": [], "history_lock": threading.Lock()}

    with pytest.raises(RuntimeError, match="require pre-runtime authorization"):
        server._run_prompt_submit("rid", "sid", session, "synthetic")


def test_hosted_answer_is_not_finalized_when_settlement_cannot_persist(monkeypatch):
    session = _install_session(monkeypatch)
    emitted = []

    class _Agent:
        model = "private-model"
        provider = "private-provider"
        session_api_calls = 0
        session_input_tokens = 0
        session_output_tokens = 0
        session_cache_read_tokens = 0
        session_cache_write_tokens = 0
        session_reasoning_tokens = 0
        session_estimated_cost_usd = 0.0
        session_cost_status = "unknown"
        session_cost_source = "none"

        def clear_interrupt(self):
            return None

        def run_conversation(self, *_args, **_kwargs):
            return {"final_response": "customer answer"}

    class _Turn:
        def complete(self, details=None):
            raise OxaideTurnError(
                "settlement could not be delivered or persisted",
                retryable=True,
            )

        def release(self):
            pytest.fail("failed completion must not send a contradictory release")

    class _InlineThread:
        def __init__(self, target=None, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    session["agent"] = _Agent()
    monkeypatch.setenv("HERMES_OXAIDE_WORKSPACE_ID", "workspace-a")
    monkeypatch.setenv("HERMES_OXAIDE_RUNTIME_KEY", "runtime-key-a")
    monkeypatch.setattr(server.threading, "Thread", _InlineThread)
    monkeypatch.setattr(server, "_set_session_context", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(server, "_clear_session_context", lambda _tokens: None)
    monkeypatch.setattr(server, "_wire_callbacks", lambda _sid: None)
    monkeypatch.setattr(server, "_sync_agent_model_with_config", lambda *_args: None)
    monkeypatch.setattr(server, "_register_session_cwd", lambda _session: None)
    monkeypatch.setattr(server, "make_stream_renderer", lambda _cols: None)
    monkeypatch.setattr(server, "render_message", lambda _raw, _cols: "")
    monkeypatch.setattr(server, "_get_usage", lambda _agent: {})
    monkeypatch.setattr(server, "_sync_session_key_after_compress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_session_info", lambda *_args: {})
    monkeypatch.setattr(server, "_drain_queued_prompt", lambda *_args: False)
    monkeypatch.setattr(
        server,
        "_emit",
        lambda event, _sid, payload=None: emitted.append((event, payload)),
    )

    try:
        server._run_prompt_submit(
            "rid", "sid", session, "research prompt", oxaide_turn=_Turn()
        )

        assert "message.complete" not in [event for event, _payload in emitted]
        assert "error" in [event for event, _payload in emitted]
    finally:
        server._sessions.clear()


def test_loopback_development_turn_does_not_require_hosted_authorization(monkeypatch):
    monkeypatch.setenv("HERMES_OXAIDE_WORKSPACE_ID", "workspace-a")
    monkeypatch.setenv("HERMES_OXAIDE_RUNTIME_KEY", "runtime-key-a")
    monkeypatch.setenv("HERMES_INTERNAL_OXAIDE_LOOPBACK_DEV", "1")
    session = {
        "agent": object(),
        "history": [],
        "history_lock": threading.Lock(),
    }

    class _StopAfterGuard(RuntimeError):
        pass

    monkeypatch.setattr(
        server,
        "_emit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(_StopAfterGuard()),
    )

    with pytest.raises(_StopAfterGuard):
        server._run_prompt_submit("rid", "sid", session, "local development")


def test_completion_details_are_private_per_turn_deltas():
    class _Agent:
        model = "gpt-5.4-mini"
        provider = "azure-foundry"
        session_api_calls = 8
        session_input_tokens = 1400
        session_output_tokens = 260
        session_cache_read_tokens = 900
        session_cache_write_tokens = 50
        session_reasoning_tokens = 40
        session_estimated_cost_usd = 0.01234567
        session_cost_status = "estimated"
        session_cost_source = "model_catalog"

    baseline = {
        "api_calls": 5,
        "input_tokens": 1000,
        "output_tokens": 200,
        "cache_read_tokens": 700,
        "cache_write_tokens": 20,
        "reasoning_tokens": 10,
        "estimated_cost_usd": 0.01,
    }
    details = server._oxaide_completion_details(
        _Agent(), baseline, time.monotonic()
    )

    assert details == {
        "schema_version": "2026-07-14-v1",
        "origin": "interactive",
        "model": "gpt-5.4-mini",
        "provider": "azure-foundry",
        "cost_status": "estimated",
        "cost_source": "model_catalog",
        "duration_ms": pytest.approx(0, abs=10),
        "api_calls": 3,
        "cache_read_tokens": 200,
        "cache_write_tokens": 30,
        "input_tokens": 400,
        "output_tokens": 60,
        "reasoning_tokens": 30,
        "estimated_cost_usd": 0.00234567,
    }
    assert "prompt" not in details
    assert "response" not in details


def test_usage_signing_does_not_fall_back_to_launch_secret(monkeypatch):
    monkeypatch.setenv("HERMES_OXAIDE_DEMO_AUTH_SECRET", "launch-only-secret")
    monkeypatch.delenv("HERMES_OXAIDE_USAGE_SIGNING_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="turn authorization is not configured"):
        OxaideTurnClient(dict(_CONTEXT_A))


@pytest.mark.parametrize("secret", [
    "short-secret",
    "__replace_with_a_long_usage_signing_secret__",
    "__RePlAcE_WiTh_A_LONG_USAGE_SIGNING_SECRET__",
])
def test_usage_signing_rejects_short_and_placeholder_secrets(monkeypatch, secret):
    monkeypatch.setenv("HERMES_OXAIDE_USAGE_SIGNING_SECRET", secret)

    with pytest.raises(RuntimeError, match="turn authorization is not configured"):
        OxaideTurnClient(dict(_CONTEXT_A))


def test_completion_delivery_failure_is_durable_and_non_raising(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_OXAIDE_USAGE_SIGNING_SECRET", "test-usage-signing-secret-at-least-32-bytes")
    monkeypatch.setenv(
        "OXAIDE_TURN_ENDPOINT",
        "https://oxaide.test/api/agents/billing/usage/record",
    )
    monkeypatch.setattr(
        "tui_gateway.oxaide_turns.urllib.request.urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(urllib.error.URLError("offline")),
    )

    client = OxaideTurnClient(dict(_CONTEXT_A))
    client.settle("complete", "immutable-event-123")

    pending = list((tmp_path / "oxaide-turn-outbox").glob("*.json"))
    assert len(pending) == 1
    payload = json.loads(pending[0].read_text())
    assert payload["hermes_event_id"] == "immutable-event-123"
    assert set(payload) == {
        "phase",
        "workspace_id",
        "runtime_session_id",
        "hermes_event_id",
        "completed_at",
    }


def test_settlement_raises_when_delivery_and_persistence_both_fail(monkeypatch):
    monkeypatch.setenv(
        "HERMES_OXAIDE_USAGE_SIGNING_SECRET",
        "test-usage-signing-secret-at-least-32-bytes",
    )
    client = OxaideTurnClient(dict(_CONTEXT_A))
    monkeypatch.setattr(
        client,
        "_write_outbox",
        lambda _payload: (_ for _ in ()).throw(OSError("disk unavailable")),
    )
    monkeypatch.setattr(
        client,
        "_request_payload",
        lambda _payload: (_ for _ in ()).throw(
            OxaideTurnError("offline", retryable=True)
        ),
    )

    with pytest.raises(
        OxaideTurnError,
        match="could not be delivered or persisted",
    ):
        client.settle("complete", "lost-event")


def test_settlement_retries_without_later_authorization(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv(
        "HERMES_OXAIDE_USAGE_SIGNING_SECRET",
        "test-usage-signing-secret-at-least-32-bytes",
    )
    monkeypatch.setattr(
        "tui_gateway.oxaide_turns._OUTBOX_RETRY_INITIAL_SECONDS", 0.01
    )
    monkeypatch.setattr(
        "tui_gateway.oxaide_turns._OUTBOX_RETRY_MAX_SECONDS", 0.01
    )
    client = OxaideTurnClient(dict(_CONTEXT_A))
    attempts = []

    def deliver(payload):
        attempts.append(payload)
        if len(attempts) == 1:
            raise OxaideTurnError("offline", retryable=True)
        return {"ok": True}

    monkeypatch.setattr(client, "_request_payload", deliver)
    client.settle("release", "retry-event")
    client._outbox_retry_thread.join(timeout=2)

    assert len(attempts) == 2
    assert list((tmp_path / "oxaide-turn-outbox").glob("*.json")) == []


def test_settlement_retry_worker_drains_multiple_pending_records(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv(
        "HERMES_OXAIDE_USAGE_SIGNING_SECRET",
        "test-usage-signing-secret-at-least-32-bytes",
    )
    monkeypatch.setattr(
        "tui_gateway.oxaide_turns._OUTBOX_RETRY_INITIAL_SECONDS", 1.0
    )
    monkeypatch.setattr(
        "tui_gateway.oxaide_turns._OUTBOX_RETRY_MAX_SECONDS", 1.0
    )
    client = OxaideTurnClient(dict(_CONTEXT_A))
    attempts = []

    def deliver(payload):
        attempts.append(payload["hermes_event_id"])
        if attempts.count(payload["hermes_event_id"]) == 1:
            raise OxaideTurnError("offline", retryable=True)
        return {"ok": True}

    monkeypatch.setattr(client, "_request_payload", deliver)
    client.settle("release", "retry-event-one")
    retry_thread = client._outbox_retry_thread
    assert retry_thread is not None
    client.settle("release", "retry-event-two")
    retry_thread.join(timeout=2)

    assert not retry_thread.is_alive()
    assert attempts.count("retry-event-one") == 2
    assert attempts.count("retry-event-two") == 2
    assert list((tmp_path / "oxaide-turn-outbox").glob("*.json")) == []


def test_outbox_rename_fsyncs_parent_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv(
        "HERMES_OXAIDE_USAGE_SIGNING_SECRET",
        "test-usage-signing-secret-at-least-32-bytes",
    )
    client = OxaideTurnClient(dict(_CONTEXT_A))
    synced = []
    monkeypatch.setattr(client, "_fsync_directory", synced.append)

    pending = client._write_outbox(
        client._payload("release", "durable-event")
    )

    assert pending.exists()
    assert synced == [tmp_path / "oxaide-turn-outbox"]


def test_terminal_settlement_failure_moves_record_to_dead_letter(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv(
        "HERMES_OXAIDE_USAGE_SIGNING_SECRET",
        "test-usage-signing-secret-at-least-32-bytes",
    )
    client = OxaideTurnClient(dict(_CONTEXT_A))
    monkeypatch.setattr(
        client,
        "_request_payload",
        lambda _payload: (_ for _ in ()).throw(
            OxaideTurnError(
                "scheduled_research_winner_committed",
                code="scheduled_research_winner_committed",
            )
        ),
    )

    client.settle("release", "terminal-event-123")

    outbox = tmp_path / "oxaide-turn-outbox"
    assert list(outbox.glob("*.json")) == []
    dead_letters = list((outbox / "dead-letter").glob("*.release.json"))
    assert len(dead_letters) == 1
    reason = json.loads(
        (outbox / "dead-letter" / f"{dead_letters[0].name}.reason.json").read_text()
    )
    assert reason["reason"] == "scheduled_research_winner_committed"
    assert reason["dead_lettered_at"]


def test_outbox_filters_workspace_before_batch_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv(
        "HERMES_OXAIDE_USAGE_SIGNING_SECRET",
        "test-usage-signing-secret-at-least-32-bytes",
    )
    outbox = tmp_path / "oxaide-turn-outbox"
    outbox.mkdir()
    for index in range(25):
        (outbox / f"00-other-{index:02}.json").write_text(
            json.dumps(
                {
                    "phase": "release",
                    "workspace_id": "workspace-b",
                    "runtime_session_id": "runtime-b",
                    "hermes_event_id": f"other-{index}",
                }
            )
        )
    target = outbox / "zz-current.json"
    target.write_text(
        json.dumps(
            {
                "phase": "release",
                "workspace_id": "workspace-a",
                "runtime_session_id": "runtime-a",
                "hermes_event_id": "current-event",
            }
        )
    )
    delivered = []
    client = OxaideTurnClient(dict(_CONTEXT_A))
    monkeypatch.setattr(client, "_request_payload", lambda payload: delivered.append(payload))

    client.flush_outbox()

    assert [payload["hermes_event_id"] for payload in delivered] == ["current-event"]
    assert not target.exists()
