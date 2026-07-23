"""Durable repositories for background-delegation lifecycle state.

SQLite remains the default for local Hermes profiles. Hosted Oxaide runtimes
may select PostgreSQL with ``HERMES_STATE_DATABASE_URL``; those rows are scoped
to a namespace derived from the trusted workspace and runtime pins.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol


_POSTGRES_MIGRATION_ID = "0001_async_delegations"
_POSTGRES_TABLE = "hermes_async_delegations"
_POSTGRES_MIGRATIONS_TABLE = "hermes_state_schema_migrations"


class AsyncDelegationRepository(Protocol):
    def persist_dispatch(self, record: Dict[str, Any], owner_started_at: Optional[int]) -> None: ...
    def delete(self, delegation_id: str) -> None: ...
    def prune(self, *, cutoff: float, retained_completed: int, max_pending: int) -> None: ...
    def persist_completion(self, event: Dict[str, Any], result: Dict[str, Any], now: float) -> None: ...
    def note_delivery_attempt(self, delegation_id: str, now: float) -> None: ...
    def list_inflight(self) -> List[Dict[str, Any]]: ...
    def mark_abandoned_unknown(
        self, delegation_id: str, event: Dict[str, Any], result: Dict[str, Any], now: float
    ) -> bool: ...
    def list_pending_events(self) -> List[Dict[str, Any]]: ...
    def mark_delivered(self, delegation_id: str, now: float) -> bool: ...
    def claim_delivery(
        self, delegation_id: str, claim_id: str, now: float, stale_before: float
    ) -> bool: ...
    def release_delivery(self, delegation_id: str, claim_id: str, now: float) -> bool: ...
    def complete_delivery(self, delegation_id: str, claim_id: str, now: float) -> bool: ...
    def get(self, delegation_id: str) -> Optional[Dict[str, Any]]: ...
    def close(self) -> None: ...


def _task_payload(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: record.get(key)
        for key in ("goal", "goals", "context", "toolsets", "role", "model", "is_batch")
        if key in record
    }


def _decode_json(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    return json.loads(value)


class SQLiteAsyncDelegationRepository:
    """SQLite implementation preserving the existing local profile behavior."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS async_delegations (
                delegation_id TEXT PRIMARY KEY,
                origin_session TEXT NOT NULL,
                origin_ui_session_id TEXT NOT NULL DEFAULT '',
                parent_session_id TEXT,
                state TEXT NOT NULL,
                dispatched_at REAL NOT NULL,
                completed_at REAL,
                updated_at REAL NOT NULL,
                event_json TEXT,
                result_json TEXT,
                delivery_state TEXT NOT NULL DEFAULT 'pending',
                delivery_attempts INTEGER NOT NULL DEFAULT 0,
                delivered_at REAL,
                owner_pid INTEGER,
                owner_started_at INTEGER,
                task_json TEXT,
                delivery_claim TEXT,
                delivery_claimed_at REAL
            )"""
        )
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(async_delegations)")
        }
        for name, sql_type in (
            ("owner_pid", "INTEGER"),
            ("owner_started_at", "INTEGER"),
            ("task_json", "TEXT"),
            ("delivery_claim", "TEXT"),
            ("delivery_claimed_at", "REAL"),
        ):
            if name not in columns:
                conn.execute(
                    f"ALTER TABLE async_delegations ADD COLUMN {name} {sql_type}"
                )
        return conn

    def persist_dispatch(self, record: Dict[str, Any], owner_started_at: Optional[int]) -> None:
        now = record["updated_at"]
        with self._lock, self.connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO async_delegations
                   (delegation_id, origin_session, origin_ui_session_id,
                    parent_session_id, state, dispatched_at, updated_at,
                    delivery_state, delivery_attempts, owner_pid,
                    owner_started_at, task_json)
                   VALUES (?, ?, ?, ?, 'running', ?, ?, 'pending', 0, ?, ?, ?)""",
                (
                    record["delegation_id"],
                    record.get("session_key", ""),
                    record.get("origin_ui_session_id", ""),
                    record.get("parent_session_id"),
                    record["dispatched_at"],
                    now,
                    os.getpid(),
                    owner_started_at,
                    json.dumps(_task_payload(record)),
                ),
            )

    def delete(self, delegation_id: str) -> None:
        with self._lock, self.connect() as conn:
            conn.execute(
                "DELETE FROM async_delegations WHERE delegation_id=?", (delegation_id,)
            )

    def prune(self, *, cutoff: float, retained_completed: int, max_pending: int) -> None:
        with self._lock, self.connect() as conn:
            conn.execute(
                "DELETE FROM async_delegations WHERE delivery_state='delivered' AND updated_at < ?",
                (cutoff,),
            )
            terminal_count = conn.execute(
                "SELECT COUNT(*) FROM async_delegations WHERE state NOT IN ('running','finalizing')"
            ).fetchone()[0]
            excess = max(0, terminal_count - retained_completed)
            if excess:
                conn.execute(
                    """DELETE FROM async_delegations WHERE delegation_id IN (
                         SELECT delegation_id FROM async_delegations
                         WHERE state NOT IN ('running','finalizing')
                         ORDER BY CASE delivery_state WHEN 'delivered' THEN 0 ELSE 1 END,
                                  updated_at ASC LIMIT ?
                       )""",
                    (excess,),
                )
            pending_count = conn.execute(
                """SELECT COUNT(*) FROM async_delegations
                   WHERE state NOT IN ('running','finalizing') AND delivery_state='pending'"""
            ).fetchone()[0]
            overflow = max(0, pending_count - max_pending)
            if overflow:
                conn.execute(
                    """DELETE FROM async_delegations WHERE delegation_id IN (
                         SELECT delegation_id FROM async_delegations
                         WHERE state NOT IN ('running','finalizing') AND delivery_state='pending'
                         ORDER BY updated_at ASC LIMIT ?
                       )""",
                    (overflow,),
                )

    def persist_completion(self, event: Dict[str, Any], result: Dict[str, Any], now: float) -> None:
        with self._lock, self.connect() as conn:
            conn.execute(
                """UPDATE async_delegations SET state=?, completed_at=?, updated_at=?,
                   event_json=?, result_json=?, delivery_state='pending'
                   WHERE delegation_id=?""",
                (
                    event.get("status", "completed"),
                    event.get("completed_at", now),
                    now,
                    json.dumps(event),
                    json.dumps(result),
                    event["delegation_id"],
                ),
            )

    def note_delivery_attempt(self, delegation_id: str, now: float) -> None:
        with self._lock, self.connect() as conn:
            conn.execute(
                """UPDATE async_delegations
                   SET delivery_attempts=delivery_attempts+1, updated_at=?
                   WHERE delegation_id=?""",
                (now, delegation_id),
            )

    def list_inflight(self) -> List[Dict[str, Any]]:
        with self._lock, self.connect() as conn:
            rows = conn.execute(
                """SELECT delegation_id, origin_session, origin_ui_session_id,
                          parent_session_id, dispatched_at, owner_pid,
                          owner_started_at, task_json
                   FROM async_delegations WHERE state IN ('running','finalizing')"""
            ).fetchall()
        return [
            {
                "delegation_id": row[0],
                "session_key": row[1],
                "origin_ui_session_id": row[2],
                "parent_session_id": row[3],
                "dispatched_at": row[4],
                "owner_pid": row[5],
                "owner_started_at": row[6],
                "task": _decode_json(row[7]) or {},
            }
            for row in rows
        ]

    def mark_abandoned_unknown(
        self, delegation_id: str, event: Dict[str, Any], result: Dict[str, Any], now: float
    ) -> bool:
        with self._lock, self.connect() as conn:
            cur = conn.execute(
                """UPDATE async_delegations SET state='unknown', completed_at=?,
                   updated_at=?, event_json=?, result_json=?, delivery_state='pending'
                   WHERE delegation_id=? AND state IN ('running','finalizing')""",
                (now, now, json.dumps(event), json.dumps(result), delegation_id),
            )
            return cur.rowcount == 1

    def list_pending_events(self) -> List[Dict[str, Any]]:
        with self._lock, self.connect() as conn:
            rows = conn.execute(
                """SELECT event_json FROM async_delegations
                   WHERE state != 'running' AND delivery_state='pending'
                     AND event_json IS NOT NULL
                   ORDER BY completed_at, delegation_id"""
            ).fetchall()
        return [_decode_json(row[0]) for row in rows]

    def mark_delivered(self, delegation_id: str, now: float) -> bool:
        with self._lock, self.connect() as conn:
            cur = conn.execute(
                """UPDATE async_delegations
                   SET delivery_state='delivered', delivered_at=?, updated_at=?
                   WHERE delegation_id=? AND delivery_state!='delivered'""",
                (now, now, delegation_id),
            )
            return cur.rowcount == 1

    def claim_delivery(
        self, delegation_id: str, claim_id: str, now: float, stale_before: float
    ) -> bool:
        with self._lock, self.connect() as conn:
            row = conn.execute(
                "SELECT delivery_state FROM async_delegations WHERE delegation_id=?",
                (delegation_id,),
            ).fetchone()
            if row is None:
                return True
            cur = conn.execute(
                """UPDATE async_delegations SET delivery_claim=?, delivery_claimed_at=?,
                          delivery_attempts=delivery_attempts+1, updated_at=?
                   WHERE delegation_id=? AND delivery_state='pending'
                     AND (delivery_claim IS NULL OR delivery_claimed_at < ?)""",
                (claim_id, now, now, delegation_id, stale_before),
            )
            return cur.rowcount == 1

    def release_delivery(self, delegation_id: str, claim_id: str, now: float) -> bool:
        with self._lock, self.connect() as conn:
            cur = conn.execute(
                """UPDATE async_delegations SET delivery_claim=NULL,
                          delivery_claimed_at=NULL, updated_at=?
                   WHERE delegation_id=? AND delivery_state='pending'
                     AND delivery_claim=?""",
                (now, delegation_id, claim_id),
            )
            return cur.rowcount == 1

    def complete_delivery(self, delegation_id: str, claim_id: str, now: float) -> bool:
        with self._lock, self.connect() as conn:
            cur = conn.execute(
                """UPDATE async_delegations SET delivery_state='delivered',
                          delivered_at=?, updated_at=?, delivery_claim=NULL,
                          delivery_claimed_at=NULL
                   WHERE delegation_id=? AND delivery_state='pending'
                     AND delivery_claim=?""",
                (now, now, delegation_id, claim_id),
            )
            return cur.rowcount == 1

    def get(self, delegation_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self.connect() as conn:
            row = conn.execute(
                """SELECT origin_session, state, dispatched_at, completed_at,
                          result_json, delivery_state, delivery_attempts
                   FROM async_delegations WHERE delegation_id=?""",
                (delegation_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "delegation_id": delegation_id,
            "origin_session": row[0],
            "state": row[1],
            "dispatched_at": row[2],
            "completed_at": row[3],
            "result": _decode_json(row[4]),
            "delivery_state": row[5],
            "delivery_attempts": row[6],
        }

    def set_owner_for_tests(
        self, delegation_id: str, owner_pid: int, owner_started_at: Optional[int]
    ) -> None:
        with self._lock, self.connect() as conn:
            conn.execute(
                """UPDATE async_delegations SET owner_pid=?, owner_started_at=?
                   WHERE delegation_id=?""",
                (owner_pid, owner_started_at, delegation_id),
            )

    def count_for_tests(self) -> int:
        with self._lock, self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM async_delegations").fetchone()[0])

    def delete_all_for_tests(self) -> None:
        with self._lock, self.connect() as conn:
            conn.execute("DELETE FROM async_delegations")

    def close(self) -> None:
        return None


class PostgresAsyncDelegationRepository:
    """PostgreSQL authority for one trusted Oxaide runtime namespace."""

    def __init__(self, database_url: str, namespace_id: str):
        try:
            from psycopg_pool import ConnectionPool
        except ImportError as exc:  # pragma: no cover - exercised without extra installed
            raise RuntimeError(
                "PostgreSQL Hermes state requires the 'postgres' package extra"
            ) from exc
        self.namespace_id = namespace_id
        self._pool = ConnectionPool(
            conninfo=database_url,
            min_size=1,
            max_size=4,
            open=False,
            timeout=10,
            kwargs={"autocommit": False},
        )
        self._pool.open(wait=True, timeout=10)
        self._migrate()

    def _migrate(self) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                f"""CREATE TABLE IF NOT EXISTS {_POSTGRES_MIGRATIONS_TABLE} (
                    migration_id TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )"""
            )
            conn.execute(
                f"""CREATE TABLE IF NOT EXISTS {_POSTGRES_TABLE} (
                    namespace_id TEXT NOT NULL,
                    delegation_id TEXT NOT NULL,
                    origin_session TEXT NOT NULL,
                    origin_ui_session_id TEXT NOT NULL DEFAULT '',
                    parent_session_id TEXT,
                    state TEXT NOT NULL,
                    dispatched_at DOUBLE PRECISION NOT NULL,
                    completed_at DOUBLE PRECISION,
                    updated_at DOUBLE PRECISION NOT NULL,
                    event_json JSONB,
                    result_json JSONB,
                    delivery_state TEXT NOT NULL DEFAULT 'pending',
                    delivery_attempts INTEGER NOT NULL DEFAULT 0,
                    delivered_at DOUBLE PRECISION,
                    owner_pid BIGINT,
                    owner_started_at BIGINT,
                    task_json JSONB,
                    delivery_claim TEXT,
                    delivery_claimed_at DOUBLE PRECISION,
                    PRIMARY KEY (namespace_id, delegation_id)
                )"""
            )
            conn.execute(
                f"""CREATE INDEX IF NOT EXISTS idx_hermes_async_delegations_delivery
                    ON {_POSTGRES_TABLE}
                    (namespace_id, delivery_state, completed_at)"""
            )
            conn.execute(
                f"""INSERT INTO {_POSTGRES_MIGRATIONS_TABLE} (migration_id)
                    VALUES (%s) ON CONFLICT (migration_id) DO NOTHING""",
                (_POSTGRES_MIGRATION_ID,),
            )

    def persist_dispatch(self, record: Dict[str, Any], owner_started_at: Optional[int]) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                f"""INSERT INTO {_POSTGRES_TABLE}
                   (namespace_id, delegation_id, origin_session,
                    origin_ui_session_id, parent_session_id, state,
                    dispatched_at, updated_at, delivery_state,
                    delivery_attempts, owner_pid, owner_started_at, task_json)
                   VALUES (%s, %s, %s, %s, %s, 'running', %s, %s,
                           'pending', 0, %s, %s, %s::jsonb)
                   ON CONFLICT (namespace_id, delegation_id) DO UPDATE SET
                       origin_session=EXCLUDED.origin_session,
                       origin_ui_session_id=EXCLUDED.origin_ui_session_id,
                       parent_session_id=EXCLUDED.parent_session_id,
                       state='running', dispatched_at=EXCLUDED.dispatched_at,
                       completed_at=NULL, updated_at=EXCLUDED.updated_at,
                       event_json=NULL, result_json=NULL,
                       delivery_state='pending', delivery_attempts=0,
                       delivered_at=NULL, owner_pid=EXCLUDED.owner_pid,
                       owner_started_at=EXCLUDED.owner_started_at,
                       task_json=EXCLUDED.task_json, delivery_claim=NULL,
                       delivery_claimed_at=NULL""",
                (
                    self.namespace_id,
                    record["delegation_id"],
                    record.get("session_key", ""),
                    record.get("origin_ui_session_id", ""),
                    record.get("parent_session_id"),
                    record["dispatched_at"],
                    record["updated_at"],
                    os.getpid(),
                    owner_started_at,
                    json.dumps(_task_payload(record)),
                ),
            )

    def delete(self, delegation_id: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                f"DELETE FROM {_POSTGRES_TABLE} WHERE namespace_id=%s AND delegation_id=%s",
                (self.namespace_id, delegation_id),
            )

    def prune(self, *, cutoff: float, retained_completed: int, max_pending: int) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                f"""DELETE FROM {_POSTGRES_TABLE}
                    WHERE namespace_id=%s AND delivery_state='delivered'
                      AND updated_at < %s""",
                (self.namespace_id, cutoff),
            )
            terminal_count = conn.execute(
                f"""SELECT COUNT(*) FROM {_POSTGRES_TABLE}
                    WHERE namespace_id=%s AND state NOT IN ('running','finalizing')""",
                (self.namespace_id,),
            ).fetchone()[0]
            excess = max(0, terminal_count - retained_completed)
            if excess:
                conn.execute(
                    f"""DELETE FROM {_POSTGRES_TABLE}
                        WHERE namespace_id=%s AND delegation_id IN (
                            SELECT delegation_id FROM {_POSTGRES_TABLE}
                            WHERE namespace_id=%s
                              AND state NOT IN ('running','finalizing')
                            ORDER BY CASE delivery_state WHEN 'delivered' THEN 0 ELSE 1 END,
                                     updated_at ASC
                            LIMIT %s
                        )""",
                    (self.namespace_id, self.namespace_id, excess),
                )
            pending_count = conn.execute(
                f"""SELECT COUNT(*) FROM {_POSTGRES_TABLE}
                    WHERE namespace_id=%s AND state NOT IN ('running','finalizing')
                      AND delivery_state='pending'""",
                (self.namespace_id,),
            ).fetchone()[0]
            overflow = max(0, pending_count - max_pending)
            if overflow:
                conn.execute(
                    f"""DELETE FROM {_POSTGRES_TABLE}
                        WHERE namespace_id=%s AND delegation_id IN (
                            SELECT delegation_id FROM {_POSTGRES_TABLE}
                            WHERE namespace_id=%s
                              AND state NOT IN ('running','finalizing')
                              AND delivery_state='pending'
                            ORDER BY updated_at ASC
                            LIMIT %s
                        )""",
                    (self.namespace_id, self.namespace_id, overflow),
                )

    def persist_completion(self, event: Dict[str, Any], result: Dict[str, Any], now: float) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                f"""UPDATE {_POSTGRES_TABLE}
                    SET state=%s, completed_at=%s, updated_at=%s,
                        event_json=%s::jsonb, result_json=%s::jsonb,
                        delivery_state='pending'
                    WHERE namespace_id=%s AND delegation_id=%s""",
                (
                    event.get("status", "completed"),
                    event.get("completed_at", now),
                    now,
                    json.dumps(event),
                    json.dumps(result),
                    self.namespace_id,
                    event["delegation_id"],
                ),
            )

    def note_delivery_attempt(self, delegation_id: str, now: float) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                f"""UPDATE {_POSTGRES_TABLE}
                    SET delivery_attempts=delivery_attempts+1, updated_at=%s
                    WHERE namespace_id=%s AND delegation_id=%s""",
                (now, self.namespace_id, delegation_id),
            )

    def list_inflight(self) -> List[Dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"""SELECT delegation_id, origin_session, origin_ui_session_id,
                           parent_session_id, dispatched_at, owner_pid,
                           owner_started_at, task_json
                    FROM {_POSTGRES_TABLE}
                    WHERE namespace_id=%s AND state IN ('running','finalizing')""",
                (self.namespace_id,),
            ).fetchall()
        return [
            {
                "delegation_id": row[0],
                "session_key": row[1],
                "origin_ui_session_id": row[2],
                "parent_session_id": row[3],
                "dispatched_at": row[4],
                "owner_pid": row[5],
                "owner_started_at": row[6],
                "task": _decode_json(row[7]) or {},
            }
            for row in rows
        ]

    def mark_abandoned_unknown(
        self, delegation_id: str, event: Dict[str, Any], result: Dict[str, Any], now: float
    ) -> bool:
        with self._pool.connection() as conn:
            cur = conn.execute(
                f"""UPDATE {_POSTGRES_TABLE}
                    SET state='unknown', completed_at=%s, updated_at=%s,
                        event_json=%s::jsonb, result_json=%s::jsonb,
                        delivery_state='pending'
                    WHERE namespace_id=%s AND delegation_id=%s
                      AND state IN ('running','finalizing')""",
                (
                    now,
                    now,
                    json.dumps(event),
                    json.dumps(result),
                    self.namespace_id,
                    delegation_id,
                ),
            )
            return cur.rowcount == 1

    def list_pending_events(self) -> List[Dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"""SELECT event_json FROM {_POSTGRES_TABLE}
                    WHERE namespace_id=%s AND state != 'running'
                      AND delivery_state='pending' AND event_json IS NOT NULL
                    ORDER BY completed_at, delegation_id""",
                (self.namespace_id,),
            ).fetchall()
        return [_decode_json(row[0]) for row in rows]

    def mark_delivered(self, delegation_id: str, now: float) -> bool:
        with self._pool.connection() as conn:
            cur = conn.execute(
                f"""UPDATE {_POSTGRES_TABLE}
                    SET delivery_state='delivered', delivered_at=%s, updated_at=%s
                    WHERE namespace_id=%s AND delegation_id=%s
                      AND delivery_state!='delivered'""",
                (now, now, self.namespace_id, delegation_id),
            )
            return cur.rowcount == 1

    def claim_delivery(
        self, delegation_id: str, claim_id: str, now: float, stale_before: float
    ) -> bool:
        with self._pool.connection() as conn:
            row = conn.execute(
                f"""SELECT delivery_state FROM {_POSTGRES_TABLE}
                    WHERE namespace_id=%s AND delegation_id=%s""",
                (self.namespace_id, delegation_id),
            ).fetchone()
            if row is None:
                return True
            cur = conn.execute(
                f"""UPDATE {_POSTGRES_TABLE}
                    SET delivery_claim=%s, delivery_claimed_at=%s,
                        delivery_attempts=delivery_attempts+1, updated_at=%s
                    WHERE namespace_id=%s AND delegation_id=%s
                      AND delivery_state='pending'
                      AND (delivery_claim IS NULL OR delivery_claimed_at < %s)""",
                (
                    claim_id,
                    now,
                    now,
                    self.namespace_id,
                    delegation_id,
                    stale_before,
                ),
            )
            return cur.rowcount == 1

    def release_delivery(self, delegation_id: str, claim_id: str, now: float) -> bool:
        with self._pool.connection() as conn:
            cur = conn.execute(
                f"""UPDATE {_POSTGRES_TABLE}
                    SET delivery_claim=NULL, delivery_claimed_at=NULL, updated_at=%s
                    WHERE namespace_id=%s AND delegation_id=%s
                      AND delivery_state='pending' AND delivery_claim=%s""",
                (now, self.namespace_id, delegation_id, claim_id),
            )
            return cur.rowcount == 1

    def complete_delivery(self, delegation_id: str, claim_id: str, now: float) -> bool:
        with self._pool.connection() as conn:
            cur = conn.execute(
                f"""UPDATE {_POSTGRES_TABLE}
                    SET delivery_state='delivered', delivered_at=%s,
                        updated_at=%s, delivery_claim=NULL,
                        delivery_claimed_at=NULL
                    WHERE namespace_id=%s AND delegation_id=%s
                      AND delivery_state='pending' AND delivery_claim=%s""",
                (now, now, self.namespace_id, delegation_id, claim_id),
            )
            return cur.rowcount == 1

    def get(self, delegation_id: str) -> Optional[Dict[str, Any]]:
        with self._pool.connection() as conn:
            row = conn.execute(
                f"""SELECT origin_session, state, dispatched_at, completed_at,
                           result_json, delivery_state, delivery_attempts
                    FROM {_POSTGRES_TABLE}
                    WHERE namespace_id=%s AND delegation_id=%s""",
                (self.namespace_id, delegation_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "delegation_id": delegation_id,
            "origin_session": row[0],
            "state": row[1],
            "dispatched_at": row[2],
            "completed_at": row[3],
            "result": _decode_json(row[4]),
            "delivery_state": row[5],
            "delivery_attempts": row[6],
        }

    def set_owner_for_tests(
        self, delegation_id: str, owner_pid: int, owner_started_at: Optional[int]
    ) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                f"""UPDATE {_POSTGRES_TABLE}
                    SET owner_pid=%s, owner_started_at=%s
                    WHERE namespace_id=%s AND delegation_id=%s""",
                (owner_pid, owner_started_at, self.namespace_id, delegation_id),
            )

    def count_for_tests(self) -> int:
        with self._pool.connection() as conn:
            return int(
                conn.execute(
                    f"SELECT COUNT(*) FROM {_POSTGRES_TABLE} WHERE namespace_id=%s",
                    (self.namespace_id,),
                ).fetchone()[0]
            )

    def delete_all_for_tests(self) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                f"DELETE FROM {_POSTGRES_TABLE} WHERE namespace_id=%s",
                (self.namespace_id,),
            )

    def close(self) -> None:
        self._pool.close()


def oxaide_namespace_id(workspace_id: str, runtime_key: str) -> str:
    """Return a non-reversible stable namespace for one hosted runtime."""
    material = f"oxaide\0{workspace_id.strip()}\0{runtime_key.strip()}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()
