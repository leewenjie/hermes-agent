import asyncio
import hashlib
import json
import threading
import time
from types import SimpleNamespace

import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

from hermes_cli import oxaide_scheduled_research as managed
from hermes_cli import web_server
from hermes_constants import reset_hermes_home_override, set_hermes_home_override


@pytest.fixture
def occurrence_client(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    home_token = set_hermes_home_override(str(home))
    monkeypatch.setenv("HERMES_OXAIDE_WORKSPACE_ID", "workspace-1")
    monkeypatch.setenv("HERMES_OXAIDE_RUNTIME_KEY", "runtimekey1234567890abcd")
    monkeypatch.setenv(
        "HERMES_OXAIDE_SCHEDULED_RESEARCH_SIGNING_SECRET",
        "scheduled-research-test-secret-at-least-32-bytes",
    )
    monkeypatch.setattr(managed, "wake_occurrence_worker", lambda: None)
    monkeypatch.setattr(
        web_server, "_dashboard_branding_settings", lambda: {"product": "oxaide"}
    )
    web_server.app.state.auth_required = False
    web_server.app.state.bound_host = None
    try:
        yield TestClient(web_server.app)
    finally:
        reset_hermes_home_override(home_token)


def _payload(**updates):
    payload = {
        "schema_version": "scheduled-research.v1",
        "occurrence_id": "00000000-0000-4000-8000-000000000001",
        "schedule_id": "00000000-0000-4000-8000-000000000002",
        "schedule_revision": 1,
        "workspace_id": "workspace-1",
        "user_id": "00000000-0000-4000-8000-000000000003",
        "runtime_key": "runtimekey1234567890abcd",
        "runtime_session_id": "rt_scheduled",
        "nominal_fire_at": "2026-07-18T10:00:00Z",
        "dispatched_at": "2026-07-18T09:59:00Z",
        "name": "Market review",
        "prompt": "Review overnight markets.",
        "schedule": {"kind": "interval", "minutes": 60, "display": "Every hour"},
        "timezone": "UTC",
    }
    payload.update(updates)
    return payload


def _signed_request(client, payload, *, signature=None):
    raw = json.dumps(payload, separators=(",", ":"))
    timestamp = str(int(time.time()))
    signature = signature or managed.sign_request("dispatch", timestamp, raw)
    return client.post(
        "/api/research-schedules/occurrences",
        content=raw,
        headers={
            "content-type": "application/json",
            "x-oxaide-scheduled-research-timestamp": timestamp,
            "x-oxaide-scheduled-research-signature": signature,
        },
    )


def _start_payload(payload):
    return {
        "schema_version": "scheduled-research.v1",
        "command": "start",
        "occurrence_id": payload["occurrence_id"],
        "schedule_id": payload["schedule_id"],
        "workspace_id": payload["workspace_id"],
        "user_id": payload["user_id"],
        "runtime_key": payload["runtime_key"],
        "runtime_session_id": payload["runtime_session_id"],
    }


def _managed_file_request() -> Request:
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/files/read",
        "raw_path": b"/api/files/read",
        "query_string": b"",
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "app": SimpleNamespace(state=SimpleNamespace(auth_required=False)),
    })


def test_completed_result_is_persisted_as_opaque_managed_artifact(monkeypatch, tmp_path):
    managed_root = tmp_path / "workspace"
    monkeypatch.setenv("HERMES_DASHBOARD_FILES_ROOT", str(managed_root))
    occurrence_id = "00000000-0000-4000-8000-000000000001"

    reference = managed._persist_occurrence_result(
        occurrence_id,
        "# Research result\n\nEvidence changed.",
        "fallback",
    )

    assert reference == f"/research-results/{occurrence_id}.md"
    result = managed_root / reference.removeprefix("/")
    assert result.read_text(encoding="utf-8") == "# Research result\n\nEvidence changed."
    assert result.stat().st_mode & 0o777 == 0o600


def test_completed_result_reference_resolves_through_managed_files_route(
    monkeypatch, tmp_path
):
    managed_root = tmp_path / "workspace"
    monkeypatch.setenv("HERMES_DASHBOARD_FILES_ROOT", str(managed_root))
    occurrence_id = "00000000-0000-4000-8000-000000000001"
    reference = managed._persist_occurrence_result(
        occurrence_id,
        "# Routed research result",
        None,
    )

    response = asyncio.run(
        web_server.read_managed_file(_managed_file_request(), reference)
    )

    assert response["path"] == reference
    assert response["name"] == f"{occurrence_id}.md"


def test_occurrence_ingress_accepts_without_browser_session_and_replays(occurrence_client):
    first = _signed_request(occurrence_client, _payload())
    assert first.status_code == 202
    assert first.json() == {
        "ok": True,
        "accepted": True,
        "occurrence_id": _payload()["occurrence_id"],
        "runtime_session_id": "rt_scheduled",
        "replayed": False,
    }

    replay = _signed_request(occurrence_client, _payload())
    assert replay.status_code == 202
    assert replay.json()["replayed"] is True


def test_occurrence_requires_durable_start_authorization(occurrence_client):
    payload = _payload()
    assert _signed_request(occurrence_client, payload).status_code == 202

    db = managed._session_db()
    try:
        assert db.claim_scheduled_research_occurrence() is None
    finally:
        db.close()

    start = _signed_request(occurrence_client, _start_payload(payload))
    assert start.status_code == 202
    assert start.json() == {
        "ok": True,
        "started": True,
        "occurrence_id": payload["occurrence_id"],
        "runtime_session_id": payload["runtime_session_id"],
        "replayed": False,
    }
    replay = _signed_request(occurrence_client, _start_payload(payload))
    assert replay.status_code == 202
    assert replay.json()["replayed"] is True

    db = managed._session_db()
    try:
        assert db.claim_scheduled_research_occurrence() is not None
    finally:
        db.close()


def test_occurrence_ingress_rejects_bad_signature_before_schema(occurrence_client):
    response = occurrence_client.post(
        "/api/research-schedules/occurrences",
        content=b"not-json",
        headers={
            "x-oxaide-scheduled-research-timestamp": str(int(time.time())),
            "x-oxaide-scheduled-research-signature": "0" * 64,
        },
    )
    assert response.status_code == 401
    assert response.json()["code"] == "scheduled_research_signature_invalid"


def test_occurrence_ingress_conflicts_on_changed_payload(occurrence_client):
    assert _signed_request(occurrence_client, _payload()).status_code == 202
    changed = _payload(prompt="Different immutable instructions")
    response = _signed_request(occurrence_client, changed)
    assert response.status_code == 409
    assert response.json()["code"] == "scheduled_research_occurrence_reused"


def test_occurrence_ingress_enforces_runtime_pins(occurrence_client):
    response = _signed_request(occurrence_client, _payload(workspace_id="workspace-2"))
    assert response.status_code == 422
    assert response.json()["code"] == "runtime_identity_mismatch"


def _claim_occurrence(db, payload):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    db.accept_scheduled_research_occurrence(payload, hashlib.sha256(raw).hexdigest())
    db.authorize_scheduled_research_occurrence(payload)
    return db.claim_scheduled_research_occurrence(lease_seconds=30)


def test_managed_occurrence_has_hard_deadline_and_terminal_callback(
    occurrence_client, monkeypatch
):
    del occurrence_client
    payload = _payload()
    db = managed._session_db()
    claim = _claim_occurrence(db, payload)
    assert claim is not None

    # Simulate sequence 2 surviving a process interruption. Re-emitting
    # `running` during recovery must not block the terminal sequence 3 event.
    managed.enqueue_occurrence_event(db, payload, 2, "running")
    monkeypatch.setattr(managed, "flush_occurrence_events", lambda db=None: None)

    class FakeTurn:
        def __init__(self):
            self.released = False

        def release(self):
            self.released = True

        def complete(self, _metadata):
            raise AssertionError("timed-out work must not complete billing")

    turn = FakeTurn()

    class FakeClient:
        @classmethod
        def from_scheduled_occurrence(cls, **_kwargs):
            return cls()

        def authorize(self, _on_revoke, *, event_id):
            assert event_id == payload["occurrence_id"]
            return turn

    monkeypatch.setattr(
        "tui_gateway.oxaide_turns.OxaideTurnClient", FakeClient
    )

    def timed_out_job(_job, *, cancel_requested):
        assert cancel_requested() is False
        return False, "", "", "TimeoutError: managed_scheduled_research_timeout"

    monkeypatch.setattr("cron.scheduler.run_job", timed_out_job)

    managed._run_occurrence(db, claim)

    row = db._conn.execute(
        "SELECT status, last_error_code FROM scheduled_research_occurrences "
        "WHERE occurrence_id = ?", (payload["occurrence_id"],)
    ).fetchone()
    events = db._conn.execute(
        "SELECT sequence, raw_body FROM scheduled_research_event_outbox "
        "WHERE occurrence_id = ? ORDER BY sequence", (payload["occurrence_id"],)
    ).fetchall()
    db.close()

    assert dict(row) == {"status": "failed", "last_error_code": "execution_timeout"}
    assert [(event["sequence"], json.loads(event["raw_body"])["status"]) for event in events] == [
        (2, "running"),
        (3, "failed"),
    ]
    assert json.loads(events[-1]["raw_body"])["error_code"] == "execution_timeout"
    assert turn.released is True


def test_expired_running_occurrence_is_reclaimed_without_duplicate_running_event(
    occurrence_client,
):
    del occurrence_client
    payload = _payload()
    db = managed._session_db()
    first_claim = _claim_occurrence(db, payload)
    assert first_claim is not None
    managed.enqueue_occurrence_event(db, payload, 2, "running")

    db._conn.execute(
        "UPDATE scheduled_research_occurrences SET lease_expires_at = ? "
        "WHERE occurrence_id = ?", (time.time() - 1, payload["occurrence_id"])
    )
    recovered = db.claim_scheduled_research_occurrence(lease_seconds=30)
    assert recovered is not None
    assert recovered["lease_token"] != first_claim["lease_token"]

    managed.enqueue_occurrence_event(db, payload, 2, "running")
    assert db.finish_scheduled_research_occurrence(
        payload["occurrence_id"], recovered["lease_token"], "failed",
        error_code="execution_timeout", error="deadline",
    )
    managed.enqueue_occurrence_event(
        db, payload, 3, "failed",
        error_code="execution_timeout", error_message="deadline",
    )
    sequences = db._conn.execute(
        "SELECT sequence FROM scheduled_research_event_outbox "
        "WHERE occurrence_id = ? ORDER BY sequence", (payload["occurrence_id"],)
    ).fetchall()
    db.close()

    assert [row["sequence"] for row in sequences] == [2, 3]
