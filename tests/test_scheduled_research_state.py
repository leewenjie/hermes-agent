import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from hermes_state import SessionDB, ScheduledResearchOccurrenceConflict


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
    assert db.claim_scheduled_research_occurrence() is None
    assert not db.finish_scheduled_research_occurrence(
        payload["occurrence_id"], "wrong-token", "completed"
    )
    assert db.finish_scheduled_research_occurrence(
        payload["occurrence_id"], claim["lease_token"], "completed"
    )
    assert db.claim_scheduled_research_occurrence() is None


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


def test_expired_running_occurrence_is_reclaimed(tmp_path):
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
    assert reclaimed["attempt_count"] == 1


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
