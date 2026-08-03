import hashlib
import json
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from hermes_state import SessionDB, ScheduledResearchOccurrenceConflict


_ARTIFACT_ID = "00000000-0000-4000-8000-000000000010"
_BILLING_ID = "00000000-0000-4000-8000-000000000011"
_LOSER_ARTIFACT_ID = "00000000-0000-4000-8000-000000000012"
_LOSER_BILLING_ID = "00000000-0000-4000-8000-000000000013"
_TERMINAL_BODY = '{"status":"completed"}'


def _payload(**updates):
    payload = {
        "occurrence_id": "00000000-0000-4000-8000-000000000001",
        "schedule_id": "00000000-0000-4000-8000-000000000002",
        "schedule_revision": 1,
        "workspace_id": "workspace-1",
        "user_id": "00000000-0000-4000-8000-000000000003",
        "runtime_key": "runtimekey1234567890abcd",
        "runtime_session_id": "rt_scheduled",
        "nominal_fire_at": "2026-07-18T10:00:00Z",
        "prompt": "Review overnight markets.",
    }
    payload.update(updates)
    return payload


def _digest(payload):
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _accept_and_authorize(db, payload):
    db.accept_scheduled_research_occurrence(payload, _digest(payload))
    db.authorize_scheduled_research_occurrence(payload)


def test_occurrence_acceptance_is_idempotent_and_immutable(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    payload = _payload()

    assert db.accept_scheduled_research_occurrence(payload, _digest(payload)) is False
    assert db.accept_scheduled_research_occurrence(payload, _digest(payload)) is True

    changed = _payload(prompt="Changed instructions")
    with pytest.raises(ScheduledResearchOccurrenceConflict):
        db.accept_scheduled_research_occurrence(changed, _digest(changed))


def test_occurrence_claim_and_terminal_state_are_lease_fenced(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    payload = _payload()
    db.accept_scheduled_research_occurrence(payload, _digest(payload))
    assert db.claim_scheduled_research_occurrence() is None
    db.authorize_scheduled_research_occurrence(payload)

    claim = db.claim_scheduled_research_occurrence()
    assert claim is not None
    assert claim["payload"]["occurrence_id"] == payload["occurrence_id"]
    assert claim["execution_phase"] == "authorizing"
    uuid.UUID(claim["billing_event_id"])
    assert not db.finish_scheduled_research_occurrence(
        payload["occurrence_id"], claim["lease_token"], "completed",
        result_artifact_id=_ARTIFACT_ID,
        billing_event_id=claim["billing_event_id"],
        terminal_event_id="premature-terminal-event",
        terminal_event_body=_TERMINAL_BODY,
    )
    assert db.begin_scheduled_research_execution(
        payload["occurrence_id"],
        claim["lease_token"],
        claim["billing_event_id"],
    )
    assert db.claim_scheduled_research_occurrence() is None
    assert not db.finish_scheduled_research_occurrence(
        payload["occurrence_id"], "wrong-token", "completed",
        result_artifact_id=_ARTIFACT_ID,
        billing_event_id=claim["billing_event_id"],
        terminal_event_id="terminal-event",
        terminal_event_body=_TERMINAL_BODY,
    )
    assert db.finish_scheduled_research_occurrence(
        payload["occurrence_id"], claim["lease_token"], "completed",
        result_artifact_id=_ARTIFACT_ID,
        billing_event_id=claim["billing_event_id"],
        terminal_event_id="terminal-event",
        terminal_event_body=_TERMINAL_BODY,
    )
    assert db.claim_scheduled_research_occurrence() is None


def test_completed_result_authorization_requires_exact_identity_and_final_session(
    tmp_path,
):
    db = SessionDB(tmp_path / "state.db")
    payload = _payload()
    _accept_and_authorize(db, payload)
    claim = db.claim_scheduled_research_occurrence()
    assert claim is not None
    assert db.begin_scheduled_research_execution(
        payload["occurrence_id"], claim["lease_token"], claim["billing_event_id"]
    )

    assert db.get_authorized_scheduled_research_result(
        payload["occurrence_id"],
        workspace_id=payload["workspace_id"],
        user_id=payload["user_id"],
        runtime_key=payload["runtime_key"],
        runtime_session_id=payload["runtime_session_id"],
    ) is None

    assert db.finish_scheduled_research_occurrence(
        payload["occurrence_id"],
        claim["lease_token"],
        "completed",
        result_artifact_id=_ARTIFACT_ID,
        billing_event_id=claim["billing_event_id"],
        hermes_session_id="cron_final_session",
        terminal_event_id="terminal-event",
        terminal_event_body=_TERMINAL_BODY,
    )

    expected = {
        "occurrence_id": payload["occurrence_id"],
        "workspace_id": payload["workspace_id"],
        "user_id": payload["user_id"],
        "runtime_key": payload["runtime_key"],
        "runtime_session_id": payload["runtime_session_id"],
        "hermes_session_id": "cron_final_session",
    }
    authorized = db.get_authorized_scheduled_research_result(
        payload["occurrence_id"],
        workspace_id=payload["workspace_id"],
        user_id=payload["user_id"],
        runtime_key=payload["runtime_key"],
        runtime_session_id=payload["runtime_session_id"],
    )
    assert authorized is not None
    assert {key: authorized[key] for key in expected} == expected
    assert authorized["completed_at"] is not None
    assert authorized["result_artifact_id"] == _ARTIFACT_ID
    assert authorized["billing_event_id"] == claim["billing_event_id"]

    for field in (
        "workspace_id",
        "user_id",
        "runtime_key",
        "runtime_session_id",
    ):
        identity = {
            "workspace_id": payload["workspace_id"],
            "user_id": payload["user_id"],
            "runtime_key": payload["runtime_key"],
            "runtime_session_id": payload["runtime_session_id"],
        }
        identity[field] = f"wrong-{field}"
        assert db.get_authorized_scheduled_research_result(
            payload["occurrence_id"], **identity
        ) is None

    assert db.get_authorized_scheduled_research_result(
        "00000000-0000-4000-8000-000000000099",
        workspace_id=payload["workspace_id"],
        user_id=payload["user_id"],
        runtime_key=payload["runtime_key"],
        runtime_session_id=payload["runtime_session_id"],
    ) is None


def test_completed_result_authorization_rejects_missing_final_session_identity(
    tmp_path,
):
    db = SessionDB(tmp_path / "state.db")
    payload = _payload()
    _accept_and_authorize(db, payload)
    claim = db.claim_scheduled_research_occurrence()
    assert claim is not None
    assert db.begin_scheduled_research_execution(
        payload["occurrence_id"], claim["lease_token"], claim["billing_event_id"]
    )
    assert db.finish_scheduled_research_occurrence(
        payload["occurrence_id"], claim["lease_token"], "completed",
        result_artifact_id=_ARTIFACT_ID,
        billing_event_id=claim["billing_event_id"],
        terminal_event_id="terminal-event",
        terminal_event_body=_TERMINAL_BODY,
    )

    assert db.get_authorized_scheduled_research_result(
        payload["occurrence_id"],
        workspace_id=payload["workspace_id"],
        user_id=payload["user_id"],
        runtime_key=payload["runtime_key"],
        runtime_session_id=payload["runtime_session_id"],
    ) is None


def test_event_outbox_preserves_occurrence_order(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    payload = _payload()
    db.accept_scheduled_research_occurrence(payload, _digest(payload))
    db.enqueue_scheduled_research_event(payload["occurrence_id"], 1, "event-1", '{"status":"accepted"}')
    db.enqueue_scheduled_research_event(payload["occurrence_id"], 2, "event-2", '{"status":"running"}')

    pending = db.list_pending_scheduled_research_events()
    assert [row["event_id"] for row in pending] == ["event-1"]

    db.settle_scheduled_research_event("event-1", delivered=True)
    pending = db.list_pending_scheduled_research_events()
    assert [row["event_id"] for row in pending] == ["event-2"]


def test_completed_winner_atomically_creates_billing_intent(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    payload = _payload()
    _accept_and_authorize(db, payload)
    claim = db.claim_scheduled_research_occurrence()
    assert claim is not None
    billing_event_id = claim["billing_event_id"]
    assert db.begin_scheduled_research_execution(
        payload["occurrence_id"], claim["lease_token"], billing_event_id
    )

    assert db.finish_scheduled_research_occurrence(
        payload["occurrence_id"],
        claim["lease_token"],
        "completed",
        result_artifact_id=_ARTIFACT_ID,
        billing_event_id=billing_event_id,
        terminal_event_id="terminal-event",
        terminal_event_body=json.dumps({"status": "completed"}),
    )

    billing = db._conn.execute(
        "SELECT occurrence_id, billing_event_id, status "
        "FROM scheduled_research_billing_outbox"
    ).fetchone()
    assert dict(billing) == {
        "occurrence_id": payload["occurrence_id"],
        "billing_event_id": billing_event_id,
        "status": "pending",
    }
    assert db.list_pending_scheduled_research_billing() == []

    db.settle_scheduled_research_event("terminal-event", delivered=True)
    pending = db.list_pending_scheduled_research_billing()
    assert [row["billing_event_id"] for row in pending] == [billing_event_id]
    assert db.settle_scheduled_research_billing(billing_event_id, delivered=True)
    assert db.list_pending_scheduled_research_billing() == []


def test_winner_transaction_conflict_rolls_back_occurrence_and_billing(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    payload = _payload()
    _accept_and_authorize(db, payload)
    claim = db.claim_scheduled_research_occurrence()
    assert claim is not None
    assert db.begin_scheduled_research_execution(
        payload["occurrence_id"], claim["lease_token"], claim["billing_event_id"]
    )
    db.enqueue_scheduled_research_event(
        payload["occurrence_id"],
        1,
        "duplicate-event-id",
        '{"status":"accepted"}',
    )

    with pytest.raises(sqlite3.IntegrityError):
        db.finish_scheduled_research_occurrence(
            payload["occurrence_id"],
            claim["lease_token"],
            "completed",
            result_artifact_id=_ARTIFACT_ID,
            billing_event_id=claim["billing_event_id"],
            terminal_event_id="duplicate-event-id",
            terminal_event_body=_TERMINAL_BODY,
        )

    occurrence = db._conn.execute(
        "SELECT status, lease_token, result_artifact_id, billing_event_id "
        "FROM scheduled_research_occurrences WHERE occurrence_id = ?",
        (payload["occurrence_id"],),
    ).fetchone()
    assert dict(occurrence) == {
        "status": "running",
        "lease_token": claim["lease_token"],
        "result_artifact_id": None,
        "billing_event_id": claim["billing_event_id"],
    }
    assert db._conn.execute(
        "SELECT COUNT(*) FROM scheduled_research_billing_outbox"
    ).fetchone()[0] == 0


def test_stale_attempt_cannot_commit_winner_or_billing_intent(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    payload = _payload()
    _accept_and_authorize(db, payload)
    stale = db.claim_scheduled_research_occurrence(lease_seconds=30)
    assert stale is not None
    with db._lock:
        db._conn.execute(
            "UPDATE scheduled_research_occurrences SET lease_expires_at = 0 "
            "WHERE occurrence_id = ?",
            (payload["occurrence_id"],),
        )
    winner = db.claim_scheduled_research_occurrence(lease_seconds=30)
    assert winner is not None
    assert db.begin_scheduled_research_execution(
        payload["occurrence_id"], winner["lease_token"], winner["billing_event_id"]
    )

    assert not db.finish_scheduled_research_occurrence(
        payload["occurrence_id"],
        stale["lease_token"],
        "completed",
        result_artifact_id=_LOSER_ARTIFACT_ID,
        billing_event_id=stale["billing_event_id"],
        terminal_event_id="loser-terminal-event",
        terminal_event_body=_TERMINAL_BODY,
    )
    assert db._conn.execute(
        "SELECT COUNT(*) FROM scheduled_research_billing_outbox"
    ).fetchone()[0] == 0

    assert db.finish_scheduled_research_occurrence(
        payload["occurrence_id"],
        winner["lease_token"],
        "completed",
        result_artifact_id=_ARTIFACT_ID,
        billing_event_id=winner["billing_event_id"],
        terminal_event_id="winner-terminal-event",
        terminal_event_body=_TERMINAL_BODY,
    )
    occurrence = db._conn.execute(
        "SELECT result_artifact_id, billing_event_id "
        "FROM scheduled_research_occurrences WHERE occurrence_id = ?",
        (payload["occurrence_id"],),
    ).fetchone()
    billing = db._conn.execute(
        "SELECT billing_event_id FROM scheduled_research_billing_outbox"
    ).fetchall()
    assert dict(occurrence) == {
        "result_artifact_id": _ARTIFACT_ID,
        "billing_event_id": winner["billing_event_id"],
    }
    assert [row["billing_event_id"] for row in billing] == [winner["billing_event_id"]]


def test_billing_reconciliation_survives_each_crash_boundary(tmp_path):
    path = tmp_path / "state.db"
    db = SessionDB(path)
    payload = _payload()
    _accept_and_authorize(db, payload)
    claim = db.claim_scheduled_research_occurrence()
    assert claim is not None
    billing_event_id = claim["billing_event_id"]
    assert db.begin_scheduled_research_execution(
        payload["occurrence_id"], claim["lease_token"], billing_event_id
    )
    assert db.finish_scheduled_research_occurrence(
        payload["occurrence_id"],
        claim["lease_token"],
        "completed",
        result_artifact_id=_ARTIFACT_ID,
        billing_event_id=billing_event_id,
        terminal_event_id="terminal-event",
        terminal_event_body=_TERMINAL_BODY,
    )
    db.close()

    # Crash after local winner commit: settlement remains gated until the
    # sequence-three lifecycle event has been acknowledged by Oxaide.
    db = SessionDB(path)
    assert db.list_pending_scheduled_research_billing() == []
    db.settle_scheduled_research_event("terminal-event", delivered=True)
    db.close()

    # Crash after lifecycle acknowledgement: the exact winner settlement is
    # recoverable and remains pending until its own acknowledgement is saved.
    db = SessionDB(path)
    pending = db.list_pending_scheduled_research_billing()
    assert [row["billing_event_id"] for row in pending] == [billing_event_id]
    db.close()

    db = SessionDB(path)
    assert db.settle_scheduled_research_billing(billing_event_id, delivered=True)
    assert not db.settle_scheduled_research_billing(billing_event_id, delivered=True)
    assert db.list_pending_scheduled_research_billing() == []


def test_concurrent_duplicate_receipt_has_one_acceptance_and_one_replay(tmp_path):
    path = tmp_path / "state.db"
    first = SessionDB(path)
    second = SessionDB(path)
    payload = _payload()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda db: db.accept_scheduled_research_occurrence(payload, _digest(payload)),
            (first, second),
        ))

    assert sorted(results) == [False, True]


def test_expired_authorizing_occurrence_reuses_stable_billing_identity(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    payload = _payload()
    _accept_and_authorize(db, payload)
    first = db.claim_scheduled_research_occurrence(lease_seconds=30)
    assert first is not None
    with db._lock:
        db._conn.execute(
            "UPDATE scheduled_research_occurrences SET lease_expires_at = 0 WHERE occurrence_id = ?",
            (payload["occurrence_id"],),
        )
        db._conn.commit()

    reclaimed = db.claim_scheduled_research_occurrence(lease_seconds=30)
    assert reclaimed is not None
    assert reclaimed["lease_token"] != first["lease_token"]
    assert reclaimed["attempt_count"] == 2
    assert reclaimed["billing_event_id"] == first["billing_event_id"]


def test_expired_executing_occurrence_is_not_automatically_replayed(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    payload = _payload()
    _accept_and_authorize(db, payload)
    claim = db.claim_scheduled_research_occurrence(lease_seconds=30)
    assert claim is not None
    assert db.begin_scheduled_research_execution(
        payload["occurrence_id"],
        claim["lease_token"],
        claim["billing_event_id"],
    )
    with db._lock:
        db._conn.execute(
            "UPDATE scheduled_research_occurrences SET lease_expires_at = 0 "
            "WHERE occurrence_id = ?",
            (payload["occurrence_id"],),
        )
        db._conn.commit()

    assert db.claim_scheduled_research_occurrence(lease_seconds=30) is None

    health = db.get_scheduled_research_health()
    assert health["running_count"] == 1
    assert health["execution_phases"] == {
        "authorizing": 0,
        "executing": 1,
        "unknown": 0,
    }
    assert health["expired_leases"] == {"authorizing": 0, "executing": 1}
    assert health["oldest_running_age_seconds"] is not None


def test_scheduled_research_health_reports_outbox_settlement_state(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    payload = _payload()
    _accept_and_authorize(db, payload)
    claim = db.claim_scheduled_research_occurrence()
    assert claim is not None
    assert db.begin_scheduled_research_execution(
        payload["occurrence_id"], claim["lease_token"], claim["billing_event_id"]
    )
    assert db.finish_scheduled_research_occurrence(
        payload["occurrence_id"],
        claim["lease_token"],
        "completed",
        result_artifact_id=_ARTIFACT_ID,
        billing_event_id=claim["billing_event_id"],
        terminal_event_id="terminal-event",
        terminal_event_body=_TERMINAL_BODY,
    )

    health = db.get_scheduled_research_health()
    assert health["lifecycle_outbox"]["pending_count"] == 1
    assert health["billing_outbox"]["pending_count"] == 1
    assert health["billing_outbox"]["ready_to_settle_count"] == 0
    assert health["completed_without_terminal_event_count"] == 0
    assert health["completed_without_billing_count"] == 0

    db.settle_scheduled_research_event("terminal-event", delivered=True)
    health = db.get_scheduled_research_health()
    assert health["lifecycle_outbox"]["pending_count"] == 0
    assert health["billing_outbox"]["ready_to_settle_count"] == 1


def test_running_occurrence_lease_renewal_is_fenced(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    payload = _payload()
    _accept_and_authorize(db, payload)
    claim = db.claim_scheduled_research_occurrence(lease_seconds=30)
    assert claim is not None

    assert not db.renew_scheduled_research_occurrence_lease(
        payload["occurrence_id"], "wrong-token", lease_seconds=1800
    )
    assert db.renew_scheduled_research_occurrence_lease(
        payload["occurrence_id"], claim["lease_token"], lease_seconds=1800
    )
    assert db.claim_scheduled_research_occurrence() is None


def test_dead_lettered_prior_event_does_not_block_later_occurrence_events(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    payload = _payload()
    db.accept_scheduled_research_occurrence(payload, _digest(payload))
    db.enqueue_scheduled_research_event(payload["occurrence_id"], 1, "event-1", "{}")
    db.enqueue_scheduled_research_event(payload["occurrence_id"], 2, "event-2", "{}")
    db.settle_scheduled_research_event(
        "event-1", delivered=False, dead_letter=True, error="invalid transition"
    )

    assert [
        event["event_id"] for event in db.list_pending_scheduled_research_events()
    ] == ["event-2"]
