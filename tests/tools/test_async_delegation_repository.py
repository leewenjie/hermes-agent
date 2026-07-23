"""Contract tests for native async-delegation state repositories."""

from __future__ import annotations

import os
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tools import async_delegation as ad
from tools.async_delegation_repository import (
    PostgresAsyncDelegationRepository,
    SQLiteAsyncDelegationRepository,
    oxaide_namespace_id,
)


def _dispatch(delegation_id: str, *, session: str = "owner") -> dict:
    return {
        "delegation_id": delegation_id,
        "session_key": session,
        "origin_ui_session_id": "tab-1",
        "parent_session_id": "parent-1",
        "goal": "research",
        "context": "context",
        "toolsets": ["web"],
        "role": "leaf",
        "model": "model",
        "is_batch": False,
        "dispatched_at": 10.0,
        "updated_at": 10.0,
    }


def _event(delegation_id: str) -> dict:
    return {
        "type": "async_delegation",
        "delegation_id": delegation_id,
        "session_key": "owner",
        "status": "completed",
        "summary": "done",
        "completed_at": 20.0,
    }


def _exercise_lifecycle(repository) -> None:
    repository.persist_dispatch(_dispatch("deleg_contract"), owner_started_at=123)
    inflight = repository.list_inflight()
    assert len(inflight) == 1
    assert inflight[0]["task"]["goal"] == "research"
    assert inflight[0]["owner_started_at"] == 123

    repository.persist_completion(
        _event("deleg_contract"),
        {"status": "completed", "summary": "done"},
        20.0,
    )
    assert repository.list_inflight() == []
    assert repository.list_pending_events()[0]["summary"] == "done"
    durable = repository.get("deleg_contract")
    assert durable["result"]["summary"] == "done"
    assert durable["delivery_state"] == "pending"

    assert repository.claim_delivery("deleg_contract", "consumer-a", 21.0, 0.0)
    assert not repository.claim_delivery("deleg_contract", "consumer-b", 21.0, 0.0)
    assert repository.release_delivery("deleg_contract", "consumer-a", 22.0)
    assert repository.claim_delivery("deleg_contract", "consumer-b", 23.0, 0.0)
    assert repository.complete_delivery("deleg_contract", "consumer-b", 24.0)
    assert repository.get("deleg_contract")["delivery_state"] == "delivered"
    assert repository.list_pending_events() == []


def test_sqlite_repository_contract(tmp_path: Path):
    repository = SQLiteAsyncDelegationRepository(tmp_path / "state.db")
    _exercise_lifecycle(repository)


def test_sqlite_abandoned_recovery_is_compare_and_set(tmp_path: Path):
    repository = SQLiteAsyncDelegationRepository(tmp_path / "state.db")
    repository.persist_dispatch(_dispatch("deleg_abandoned"), owner_started_at=None)
    event = {**_event("deleg_abandoned"), "status": "unknown"}
    result = {"status": "unknown", "error": "owner exited"}
    assert repository.mark_abandoned_unknown(
        "deleg_abandoned", event, result, 20.0
    )
    assert not repository.mark_abandoned_unknown(
        "deleg_abandoned", event, result, 21.0
    )
    assert repository.get("deleg_abandoned")["state"] == "unknown"


def test_namespace_id_is_stable_and_scoped():
    value = oxaide_namespace_id("workspace-a", "runtime-a")
    assert len(value) == 64
    assert value == oxaide_namespace_id("workspace-a", "runtime-a")
    assert value != oxaide_namespace_id("workspace-a", "runtime-b")
    assert "workspace-a" not in value


def test_repository_factory_defaults_to_sqlite(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_STATE_DATABASE_URL", raising=False)
    ad._reset_for_tests()
    try:
        assert isinstance(ad._repository(), SQLiteAsyncDelegationRepository)
    finally:
        ad._reset_for_tests()


def test_repository_factory_fails_closed_without_trusted_identity(monkeypatch):
    monkeypatch.setenv(
        "HERMES_STATE_DATABASE_URL", "postgresql://example.invalid/hermes"
    )
    monkeypatch.delenv("HERMES_OXAIDE_WORKSPACE_ID", raising=False)
    monkeypatch.delenv("HERMES_OXAIDE_RUNTIME_KEY", raising=False)
    ad._reset_for_tests()
    try:
        with pytest.raises(RuntimeError, match="trusted Oxaide workspace"):
            ad._repository()
    finally:
        ad._reset_for_tests()


def _postgres_url() -> str:
    value = os.environ.get("HERMES_TEST_POSTGRES_URL", "").strip()
    if not value:
        pytest.skip("HERMES_TEST_POSTGRES_URL is not configured")
    return value


@pytest.mark.integration
def test_postgres_repository_contract_and_namespace_isolation():
    url = _postgres_url()
    namespace_a = oxaide_namespace_id("workspace-contract", "runtime-a")
    namespace_b = oxaide_namespace_id("workspace-contract", "runtime-b")
    repository_a = PostgresAsyncDelegationRepository(url, namespace_a)
    repository_b = PostgresAsyncDelegationRepository(url, namespace_b)
    try:
        repository_a.delete_all_for_tests()
        repository_b.delete_all_for_tests()
        _exercise_lifecycle(repository_a)
        assert repository_b.get("deleg_contract") is None

        repository_b.persist_dispatch(
            _dispatch("deleg_contract", session="other-owner"),
            owner_started_at=456,
        )
        assert repository_b.get("deleg_contract")["origin_session"] == "other-owner"
        assert repository_a.get("deleg_contract")["origin_session"] == "owner"
    finally:
        repository_a.delete_all_for_tests()
        repository_b.delete_all_for_tests()
        repository_a.close()
        repository_b.close()


@pytest.mark.integration
def test_postgres_delivery_claim_is_exclusive_across_pools():
    url = _postgres_url()
    namespace = oxaide_namespace_id("workspace-contention", "runtime")
    writer = PostgresAsyncDelegationRepository(url, namespace)
    claimant_a = PostgresAsyncDelegationRepository(url, namespace)
    claimant_b = PostgresAsyncDelegationRepository(url, namespace)
    try:
        writer.delete_all_for_tests()
        writer.persist_dispatch(_dispatch("deleg_race"), owner_started_at=1)
        writer.persist_completion(
            _event("deleg_race"),
            {"status": "completed", "summary": "done"},
            20.0,
        )
        barrier = threading.Barrier(2)

        def claim(repository, claim_id: str) -> bool:
            barrier.wait(timeout=5)
            return repository.claim_delivery(
                "deleg_race", claim_id, 21.0, 0.0
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = [
                executor.submit(claim, claimant_a, "consumer-a"),
                executor.submit(claim, claimant_b, "consumer-b"),
            ]
        assert sorted(future.result() for future in results) == [False, True]
        assert writer.get("deleg_race")["delivery_attempts"] == 1
    finally:
        writer.delete_all_for_tests()
        writer.close()
        claimant_a.close()
        claimant_b.close()


@pytest.mark.integration
def test_async_delegation_seam_uses_postgres_without_creating_sqlite(
    tmp_path: Path, monkeypatch
):
    url = _postgres_url()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_STATE_DATABASE_URL", url)
    monkeypatch.setenv("HERMES_OXAIDE_WORKSPACE_ID", "workspace-seam")
    monkeypatch.setenv("HERMES_OXAIDE_RUNTIME_KEY", "runtime-seam")
    ad._reset_for_tests()
    repository = ad._repository()
    try:
        repository.delete_all_for_tests()
        ad._persist_dispatch(_dispatch("deleg_seam"))
        ad._persist_completion(
            _event("deleg_seam"),
            {"status": "completed", "summary": "postgres-only"},
        )

        restored = queue.Queue()
        assert ad.restore_undelivered_completions(restored) == 1
        assert restored.get_nowait()["delegation_id"] == "deleg_seam"
        assert ad.claim_completion_delivery("deleg_seam", "consumer")
        assert ad.complete_completion_delivery("deleg_seam", "consumer")
        assert ad.get_durable_delegation("deleg_seam")["result"]["summary"] == (
            "postgres-only"
        )
        assert not (tmp_path / "state.db").exists()
    finally:
        repository.delete_all_for_tests()
        ad._reset_for_tests()
