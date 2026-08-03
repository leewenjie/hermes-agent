import hashlib
import json
import threading
import time
import uuid

import pytest
from starlette.testclient import TestClient

from hermes_cli import oxaide_scheduled_research as managed
from hermes_cli import hosted_runtime_bridge
from hermes_cli import web_server
from hermes_cli.scheduled_research_results import (
    RESULT_MAX_BYTES,
    ScheduledResearchResultTooLarge,
    ScheduledResearchResultUnavailable,
)
from hermes_constants import reset_hermes_home_override, set_hermes_home_override


_ARTIFACT_ID = "00000000-0000-4000-8000-000000000010"
_BILLING_ID = "00000000-0000-4000-8000-000000000011"


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


def test_completed_result_is_persisted_as_opaque_managed_artifact(monkeypatch, tmp_path):
    managed_root = tmp_path / "workspace"
    monkeypatch.setenv("HERMES_DASHBOARD_FILES_ROOT", str(managed_root))
    occurrence_id = "00000000-0000-4000-8000-000000000001"

    reference = managed._persist_occurrence_result(
        _ARTIFACT_ID,
        "# Research result\n\nEvidence changed.",
        "fallback",
    )

    assert reference == _ARTIFACT_ID
    assert reference != occurrence_id
    result = managed.scheduled_research_result_path(reference)
    assert result.read_text(encoding="utf-8") == "# Research result\n\nEvidence changed."
    assert result.stat().st_mode & 0o777 == 0o600


def test_result_persistence_enforces_utf8_byte_limit_before_writing(monkeypatch, tmp_path):
    managed_root = tmp_path / "workspace"
    monkeypatch.setenv("HERMES_DASHBOARD_FILES_ROOT", str(managed_root))
    managed._persist_occurrence_result(_ARTIFACT_ID, "x" * RESULT_MAX_BYTES, None)
    target = managed.scheduled_research_result_path(_ARTIFACT_ID)
    assert target.stat().st_size == RESULT_MAX_BYTES

    with pytest.raises(ScheduledResearchResultTooLarge):
        managed._persist_occurrence_result(
            _ARTIFACT_ID,
            "x" * (RESULT_MAX_BYTES + 1),
            None,
        )
    assert target.stat().st_size == RESULT_MAX_BYTES
    assert not list(target.parent.glob("*.tmp"))

    with pytest.raises(UnicodeEncodeError):
        managed._persist_occurrence_result(_ARTIFACT_ID, "invalid-\ud800", None)
    assert target.stat().st_size == RESULT_MAX_BYTES


def test_result_persistence_rejects_symlink_parent(monkeypatch, tmp_path):
    managed_root = tmp_path / "workspace"
    managed_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    results = managed_root / "research-results"
    try:
        results.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("filesystem does not allow directory symlinks")
    monkeypatch.setenv("HERMES_DASHBOARD_FILES_ROOT", str(managed_root))

    with pytest.raises(OSError):
        managed._persist_occurrence_result(
            "00000000-0000-4000-8000-000000000001",
            "must not escape",
            None,
        )

    assert list(outside.iterdir()) == []


def test_result_persistence_rejects_existing_leaf_without_following_it(
    monkeypatch,
    tmp_path,
):
    managed_root = tmp_path / "workspace"
    results = managed_root / "research-results"
    results.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("outside secret")
    target = results / f"{_ARTIFACT_ID}.md"
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("filesystem does not allow file symlinks")
    monkeypatch.setenv("HERMES_DASHBOARD_FILES_ROOT", str(managed_root))

    with pytest.raises(ScheduledResearchResultUnavailable):
        managed._persist_occurrence_result(_ARTIFACT_ID, "private result", None)

    assert target.is_symlink()
    assert outside.read_text() == "outside secret"


def _result_headers(payload, secret="hosted-runtime-test-secret-at-least-32-bytes", **overrides):
    identity = {
        "workspace_id": payload["workspace_id"],
        "user_id": payload["user_id"],
        "runtime_key": payload["runtime_key"],
        "runtime_session_id": payload["runtime_session_id"],
        **overrides,
    }
    headers = {"X-Hermes-Hosted-Secret": secret}
    headers.update({
        web_server._SCHEDULED_RESEARCH_RESULT_IDENTITY_HEADERS[field]: value
        for field, value in identity.items()
    })
    return headers


def _complete_occurrence(db, payload, *, hermes_session_id="cron_final_session"):
    claim = _claim_occurrence(db, payload)
    assert claim is not None
    assert db.begin_scheduled_research_execution(
        payload["occurrence_id"],
        claim["lease_token"],
        claim["billing_event_id"],
    )
    assert db.finish_scheduled_research_occurrence(
        payload["occurrence_id"],
        claim["lease_token"],
        "completed",
        result_artifact_id=_ARTIFACT_ID,
        billing_event_id=claim["billing_event_id"],
        hermes_session_id=hermes_session_id,
        terminal_event_id="terminal-event",
        terminal_event_body='{"status":"completed"}',
    )


def test_completed_result_is_only_available_through_protected_route(
    occurrence_client, monkeypatch, tmp_path
):
    managed_root = tmp_path / "workspace"
    monkeypatch.setenv("HERMES_DASHBOARD_FILES_ROOT", str(managed_root))
    monkeypatch.setenv(
        "HERMES_HOSTED_RUNTIME_SHARED_SECRET",
        "hosted-runtime-test-secret-at-least-32-bytes",
    )
    monkeypatch.setattr(
        hosted_runtime_bridge,
        "load_config",
        lambda: {"hosted_runtime_bridge": {"enabled": True}},
    )
    payload = _payload()
    occurrence_id = "00000000-0000-4000-8000-000000000001"
    db = managed._session_db()
    try:
        _complete_occurrence(db, payload)
    finally:
        db.close()
    managed._persist_occurrence_result(_ARTIFACT_ID, "# Routed research result", None)

    response = occurrence_client.get(
        f"/api/hosted/runtime/scheduled-research/results/{occurrence_id}",
        headers=_result_headers(payload),
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert response.text == "# Routed research result"
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert "location" not in response.headers


def test_protected_result_route_rejects_oversize_invalid_and_aliased_artifacts(
    occurrence_client,
    monkeypatch,
    tmp_path,
):
    managed_root = tmp_path / "workspace"
    results = managed_root / "research-results"
    results.mkdir(parents=True)
    monkeypatch.setenv("HERMES_DASHBOARD_FILES_ROOT", str(managed_root))
    monkeypatch.setenv(
        "HERMES_HOSTED_RUNTIME_SHARED_SECRET",
        "hosted-runtime-test-secret-at-least-32-bytes",
    )
    monkeypatch.setattr(
        hosted_runtime_bridge,
        "load_config",
        lambda: {"hosted_runtime_bridge": {"enabled": True}},
    )
    payload = _payload()
    occurrence_id = payload["occurrence_id"]
    target = results / f"{_ARTIFACT_ID}.md"
    db = managed._session_db()
    try:
        _complete_occurrence(db, payload)
    finally:
        db.close()
    url = f"/api/hosted/runtime/scheduled-research/results/{occurrence_id}"
    headers = _result_headers(payload)

    target.write_bytes(b"x" * (RESULT_MAX_BYTES + 1))
    oversized = occurrence_client.get(url, headers=headers, follow_redirects=False)
    target.write_bytes(b"invalid-utf8-\xff")
    invalid_utf8 = occurrence_client.get(url, headers=headers, follow_redirects=False)
    target.unlink()
    target.mkdir()
    non_regular = occurrence_client.get(url, headers=headers, follow_redirects=False)
    target.rmdir()

    outside = tmp_path / "outside.md"
    outside.write_text("outside secret")
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("filesystem does not allow file symlinks")
    aliased_leaf = occurrence_client.get(url, headers=headers, follow_redirects=False)
    target.unlink()
    results.rmdir()
    outside_results = tmp_path / "outside-results"
    outside_results.mkdir()
    (outside_results / target.name).write_text("parent alias secret")
    try:
        results.symlink_to(outside_results, target_is_directory=True)
    except OSError:
        pytest.skip("filesystem does not allow directory symlinks")
    aliased_parent = occurrence_client.get(url, headers=headers, follow_redirects=False)

    responses = (
        oversized,
        invalid_utf8,
        non_regular,
        aliased_leaf,
        aliased_parent,
    )
    assert {response.status_code for response in responses} == {404}
    assert len({response.text for response in responses}) == 1
    for response in responses:
        assert "outside secret" not in response.text
        assert "parent alias secret" not in response.text


def test_protected_result_route_rejects_bad_auth_and_identity(
    occurrence_client, monkeypatch
):
    monkeypatch.setenv(
        "HERMES_HOSTED_RUNTIME_SHARED_SECRET",
        "hosted-runtime-test-secret-at-least-32-bytes",
    )
    monkeypatch.setattr(
        hosted_runtime_bridge,
        "load_config",
        lambda: {"hosted_runtime_bridge": {"enabled": True}},
    )
    payload = _payload()
    url = f"/api/hosted/runtime/scheduled-research/results/{payload['occurrence_id']}"

    missing_secret = occurrence_client.get(
        url, headers=_result_headers(payload, secret=""), follow_redirects=False
    )
    assert missing_secret.status_code == 401

    invalid_secret = occurrence_client.get(
        url,
        headers=_result_headers(payload, secret="wrong-secret-at-least-32-bytes"),
        follow_redirects=False,
    )
    assert invalid_secret.status_code == 401

    missing_identity = occurrence_client.get(
        url,
        headers={"X-Hermes-Hosted-Secret": "hosted-runtime-test-secret-at-least-32-bytes"},
        follow_redirects=False,
    )
    assert missing_identity.status_code == 400

    malformed_uuid = occurrence_client.get(
        "/api/hosted/runtime/scheduled-research/results/not-a-uuid",
        headers=_result_headers(payload),
        follow_redirects=False,
    )
    assert malformed_uuid.status_code == 400


@pytest.mark.parametrize(
    "identity_field",
    ["workspace_id", "user_id", "runtime_key", "runtime_session_id"],
)
def test_protected_result_route_rejects_identity_mismatch(
    occurrence_client, monkeypatch, tmp_path, identity_field
):
    managed_root = tmp_path / "workspace"
    monkeypatch.setenv("HERMES_DASHBOARD_FILES_ROOT", str(managed_root))
    monkeypatch.setenv(
        "HERMES_HOSTED_RUNTIME_SHARED_SECRET",
        "hosted-runtime-test-secret-at-least-32-bytes",
    )
    monkeypatch.setattr(
        hosted_runtime_bridge,
        "load_config",
        lambda: {"hosted_runtime_bridge": {"enabled": True}},
    )
    payload = _payload()
    db = managed._session_db()
    try:
        _complete_occurrence(db, payload)
    finally:
        db.close()
    managed._persist_occurrence_result(_ARTIFACT_ID, "private result", None)

    mismatched = {
        "workspace_id": "workspace-other",
        "user_id": "user-other",
        "runtime_key": "otherruntimekey1234567890abcd",
        "runtime_session_id": "rt_other",
    }
    response = occurrence_client.get(
        f"/api/hosted/runtime/scheduled-research/results/{payload['occurrence_id']}",
        headers=_result_headers(payload, **{identity_field: mismatched[identity_field]}),
        follow_redirects=False,
    )
    assert response.status_code == 404


def test_protected_result_route_requires_completed_identity_and_artifact(
    occurrence_client, monkeypatch, tmp_path
):
    managed_root = tmp_path / "workspace"
    monkeypatch.setenv("HERMES_DASHBOARD_FILES_ROOT", str(managed_root))
    monkeypatch.setenv(
        "HERMES_HOSTED_RUNTIME_SHARED_SECRET",
        "hosted-runtime-test-secret-at-least-32-bytes",
    )
    monkeypatch.setattr(
        hosted_runtime_bridge,
        "load_config",
        lambda: {"hosted_runtime_bridge": {"enabled": True}},
    )
    payload = _payload()
    db = managed._session_db()
    try:
        _complete_occurrence(db, payload)
    finally:
        db.close()

    url = f"/api/hosted/runtime/scheduled-research/results/{payload['occurrence_id']}"
    missing_artifact = occurrence_client.get(
        url, headers=_result_headers(payload), follow_redirects=False
    )
    assert missing_artifact.status_code == 404


def test_protected_result_route_hides_unknown_and_incomplete_occurrences(
    occurrence_client, monkeypatch
):
    monkeypatch.setenv(
        "HERMES_HOSTED_RUNTIME_SHARED_SECRET",
        "hosted-runtime-test-secret-at-least-32-bytes",
    )
    monkeypatch.setattr(
        hosted_runtime_bridge,
        "load_config",
        lambda: {"hosted_runtime_bridge": {"enabled": True}},
    )
    payload = _payload()
    db = managed._session_db()
    try:
        raw = json.dumps(payload, separators=(",", ":")).encode()
        db.accept_scheduled_research_occurrence(
            payload, hashlib.sha256(raw).hexdigest()
        )
        db.authorize_scheduled_research_occurrence(payload)
    finally:
        db.close()

    base_url = "/api/hosted/runtime/scheduled-research/results/"
    incomplete = occurrence_client.get(
        base_url + payload["occurrence_id"],
        headers=_result_headers(payload),
        follow_redirects=False,
    )
    unknown_payload = _payload(
        occurrence_id="00000000-0000-4000-8000-000000000099"
    )
    unknown = occurrence_client.get(
        base_url + unknown_payload["occurrence_id"],
        headers=_result_headers(unknown_payload),
        follow_redirects=False,
    )
    assert incomplete.status_code == 404
    assert unknown.status_code == 404
    assert incomplete.json() == unknown.json()


def test_protected_result_route_is_disabled_without_bridge(
    occurrence_client, monkeypatch
):
    monkeypatch.setenv(
        "HERMES_HOSTED_RUNTIME_SHARED_SECRET",
        "hosted-runtime-test-secret-at-least-32-bytes",
    )
    monkeypatch.setattr(
        hosted_runtime_bridge,
        "load_config",
        lambda: {"hosted_runtime_bridge": {"enabled": False}},
    )
    payload = _payload()
    response = occurrence_client.get(
        f"/api/hosted/runtime/scheduled-research/results/{payload['occurrence_id']}",
        headers=_result_headers(payload),
        follow_redirects=False,
    )
    assert response.status_code == 404


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
    delivered_statuses = []

    def deliver_event(raw_body):
        delivered_statuses.append(json.loads(raw_body)["status"])
        return True, False, ""

    monkeypatch.setattr(managed, "_deliver_event", deliver_event)

    class FakeTurn:
        def __init__(self):
            self.released = False
            self.release_entered = threading.Event()
            self.release_gate = threading.Event()

        def release(self):
            self.release_entered.set()
            self.release_gate.wait(timeout=5.0)
            self.released = True

        def complete(self, _metadata):
            raise AssertionError("timed-out work must not complete billing")

    turn = FakeTurn()

    class FakeClient:
        @classmethod
        def from_scheduled_occurrence(cls, **kwargs):
            assert kwargs["occurrence_id"] == payload["occurrence_id"]
            return cls()

        def authorize(self, _on_revoke, *, event_id):
            uuid.UUID(event_id)
            assert event_id == claim["billing_event_id"]
            return turn

    monkeypatch.setattr(
        "tui_gateway.oxaide_turns.OxaideTurnClient", FakeClient
    )

    close_entered = threading.Event()
    release_close = threading.Event()

    class BlockingCloseAgent:
        def close(self):
            close_entered.set()
            release_close.wait(timeout=5.0)

    def timed_out_job(_job, *, cancel_requested, defer_agent_teardown):
        assert cancel_requested() is False
        defer_agent_teardown.append(BlockingCloseAgent())
        return False, "", "", "TimeoutError: managed_scheduled_research_timeout"

    monkeypatch.setattr("cron.scheduler.run_job", timed_out_job)

    managed._run_occurrence(db, claim)

    # Teardown starts only after the terminal row/outbox transaction commits;
    # even a close hook that now blocks cannot hold the occurrence at running.
    assert close_entered.wait(timeout=1.0)
    assert turn.release_entered.wait(timeout=1.0)

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
    assert "failed" in delivered_statuses
    assert turn.released is False
    turn.release_gate.set()
    release_close.set()


def test_successful_occurrence_emits_result_and_final_session_evidence(
    occurrence_client,
    monkeypatch,
    tmp_path,
):
    del occurrence_client
    managed_root = tmp_path / "workspace"
    monkeypatch.setenv("HERMES_DASHBOARD_FILES_ROOT", str(managed_root))
    payload = _payload()
    db = managed._session_db()
    claim = _claim_occurrence(db, payload)
    assert claim is not None
    monkeypatch.setattr(managed, "flush_occurrence_events", lambda db=None: None)

    class FakeTurn:
        def __init__(self):
            self.completed = None

        def release(self):
            raise AssertionError("successful work must complete billing")

        def complete(self, metadata):
            self.completed = metadata

    turn = FakeTurn()

    class FakeClient:
        def __init__(self):
            self.stopped = False

        @classmethod
        def from_scheduled_occurrence(cls, **kwargs):
            assert kwargs["occurrence_id"] == payload["occurrence_id"]
            return cls()

        def authorize(self, _on_revoke, *, event_id):
            uuid.UUID(event_id)
            assert event_id == claim["billing_event_id"]
            return turn

        def stop_heartbeat(self):
            self.stopped = True

    monkeypatch.setattr("tui_gateway.oxaide_turns.OxaideTurnClient", FakeClient)

    def successful_job(
        _job,
        *,
        cancel_requested,
        final_session_id,
    ):
        assert cancel_requested() is False
        final_session_id.append("cron_final_session")
        return True, "# Completed research", "fallback", None

    monkeypatch.setattr("cron.scheduler.run_job", successful_job)

    managed._run_occurrence(db, claim)

    row = db._conn.execute(
        "SELECT status, hermes_session_id, result_artifact_id, billing_event_id "
        "FROM scheduled_research_occurrences "
        "WHERE occurrence_id = ?",
        (payload["occurrence_id"],),
    ).fetchone()
    terminal_event = db._conn.execute(
        "SELECT raw_body FROM scheduled_research_event_outbox "
        "WHERE occurrence_id = ? AND sequence = 3",
        (payload["occurrence_id"],),
    ).fetchone()
    billing = db._conn.execute(
        "SELECT billing_event_id, status FROM scheduled_research_billing_outbox "
        "WHERE occurrence_id = ?",
        (payload["occurrence_id"],),
    ).fetchone()
    db.close()

    assert row["status"] == "completed"
    assert row["hermes_session_id"] == "cron_final_session"
    uuid.UUID(row["result_artifact_id"])
    uuid.UUID(row["billing_event_id"])
    assert row["result_artifact_id"] != payload["occurrence_id"]
    assert row["billing_event_id"] != payload["occurrence_id"]
    event = json.loads(terminal_event["raw_body"])
    assert event["status"] == "completed"
    assert event["result_artifact_ref"] == (
        f"/research-results/{row['result_artifact_id']}.md"
    )
    assert event["billing_event_id"] == row["billing_event_id"]
    assert event["result_session_id"] == "cron_final_session"
    assert dict(billing) == {
        "billing_event_id": row["billing_event_id"],
        "status": "pending",
    }
    assert turn.completed is None
    assert managed.scheduled_research_result_path(
        row["result_artifact_id"]
    ).read_text() == "# Completed research"


def test_successful_attempt_cleans_artifact_and_releases_turn_on_commit_failure(
    occurrence_client,
    monkeypatch,
    tmp_path,
):
    del occurrence_client
    managed_root = tmp_path / "workspace"
    monkeypatch.setenv("HERMES_DASHBOARD_FILES_ROOT", str(managed_root))
    payload = _payload()
    db = managed._session_db()
    claim = _claim_occurrence(db, payload)
    assert claim is not None
    monkeypatch.setattr(managed, "flush_occurrence_events", lambda db=None: None)

    class FakeTurn:
        def __init__(self):
            self.released = False

        def release(self):
            self.released = True

    turn = FakeTurn()

    class FakeClient:
        @classmethod
        def from_scheduled_occurrence(cls, **_kwargs):
            return cls()

        def authorize(self, _on_revoke, *, event_id):
            uuid.UUID(event_id)
            return turn

        def stop_heartbeat(self):
            raise AssertionError("an uncommitted attempt cannot become the winner")

    monkeypatch.setattr("tui_gateway.oxaide_turns.OxaideTurnClient", FakeClient)

    def successful_job(_job, *, cancel_requested, final_session_id):
        assert cancel_requested() is False
        final_session_id.append("cron_final_session")
        return True, "private result", None, None

    monkeypatch.setattr("cron.scheduler.run_job", successful_job)
    monkeypatch.setattr(
        db,
        "finish_scheduled_research_occurrence",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("simulated transaction failure")
        ),
    )

    with pytest.raises(RuntimeError, match="simulated transaction failure"):
        managed._run_occurrence(db, claim)

    occurrence = db._conn.execute(
        "SELECT status, result_artifact_id, billing_event_id "
        "FROM scheduled_research_occurrences WHERE occurrence_id = ?",
        (payload["occurrence_id"],),
    ).fetchone()
    billing_count = db._conn.execute(
        "SELECT COUNT(*) FROM scheduled_research_billing_outbox"
    ).fetchone()[0]
    db.close()

    assert dict(occurrence) == {
        "status": "running",
        "result_artifact_id": None,
        "billing_event_id": claim["billing_event_id"],
    }
    assert billing_count == 0
    assert turn.released is True
    results = managed_root / "research-results"
    assert not results.exists() or list(results.iterdir()) == []


def test_remote_billing_ack_is_retried_after_local_ack_crash(
    occurrence_client,
    monkeypatch,
):
    del occurrence_client
    payload = _payload()
    db = managed._session_db()
    _complete_occurrence(db, payload)
    db.settle_scheduled_research_event("terminal-event", delivered=True)
    remote_calls = []

    class FakeClient:
        @classmethod
        def from_scheduled_occurrence(cls, **kwargs):
            assert kwargs["occurrence_id"] == payload["occurrence_id"]
            return cls()

        def complete_scheduled_occurrence(
            self,
            billing_event_id,
            *,
            completed_at,
            details,
        ):
            remote_calls.append((billing_event_id, completed_at, details))

        @staticmethod
        def _is_terminal_settlement_error(_exc):
            return False

    monkeypatch.setattr("tui_gateway.oxaide_turns.OxaideTurnClient", FakeClient)
    settle_locally = db.settle_scheduled_research_billing

    def crash_before_local_ack(*_args, **_kwargs):
        raise RuntimeError("simulated crash after remote acknowledgement")

    monkeypatch.setattr(
        db,
        "settle_scheduled_research_billing",
        crash_before_local_ack,
    )
    with pytest.raises(RuntimeError, match="after remote acknowledgement"):
        managed.flush_occurrence_billing(db)
    persisted_billing_id = db._conn.execute(
        "SELECT billing_event_id FROM scheduled_research_occurrences "
        "WHERE occurrence_id = ?",
        (payload["occurrence_id"],),
    ).fetchone()["billing_event_id"]
    assert [call[0] for call in remote_calls] == [persisted_billing_id]

    monkeypatch.setattr(db, "settle_scheduled_research_billing", settle_locally)
    managed.flush_occurrence_billing(db)
    billing = db._conn.execute(
        "SELECT status FROM scheduled_research_billing_outbox "
        "WHERE billing_event_id = ?",
        (persisted_billing_id,),
    ).fetchone()
    db.close()

    assert [call[0] for call in remote_calls] == [
        persisted_billing_id,
        persisted_billing_id,
    ]
    assert billing["status"] == "delivered"


def test_expired_authorizing_occurrence_is_reclaimed_with_stable_billing_identity(
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
    assert recovered["billing_event_id"] == first_claim["billing_event_id"]

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
