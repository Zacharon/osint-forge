"""SQLite persistence and resume support for OSINT Forge missions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ResumeBundle:
    mission: dict[str, Any]
    state: dict[str, Any]
    artifacts: list[dict[str, Any]]
    reviews: list[dict[str, Any]]


class MissionStore:
    """Authoritative SQLite store for mission state, artifacts, reviews, and audit events."""

    def __init__(self, db_path: str | Path = "osint_forge.db") -> None:
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.initialize_schema()

    def initialize_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS missions (
                mission_id TEXT PRIMARY KEY,
                user_query_hash TEXT NOT NULL,
                created_at REAL NOT NULL,
                status TEXT NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mission_state (
                mission_id TEXT PRIMARY KEY,
                state_json TEXT NOT NULL,
                budget_json TEXT NOT NULL,
                manifest_hash TEXT NOT NULL,
                completed_tools_json TEXT NOT NULL,
                pending_reviews_json TEXT NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(mission_id) REFERENCES missions(mission_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS artifacts (
                mission_id TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                artifact_path TEXT NOT NULL,
                artifact_hash TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY(mission_id, artifact_path, artifact_hash),
                FOREIGN KEY(mission_id) REFERENCES missions(mission_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS review_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                artifact_path TEXT NOT NULL,
                artifact_hash TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                requested_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                resolved_at REAL,
                reviewed_by TEXT,
                decision_reason TEXT,
                review_method TEXT,
                created_at REAL NOT NULL DEFAULT (strftime('%s','now')),
                FOREIGN KEY(mission_id) REFERENCES missions(mission_id) ON DELETE CASCADE
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_review_unique
                ON review_requests(mission_id, artifact_type, artifact_path, artifact_hash);
            CREATE INDEX IF NOT EXISTS idx_review_mission_status
                ON review_requests(mission_id, status);
            CREATE INDEX IF NOT EXISTS idx_review_expires
                ON review_requests(expires_at);

            CREATE TABLE IF NOT EXISTS governance_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_key TEXT,
                metadata_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                FOREIGN KEY(mission_id) REFERENCES missions(mission_id) ON DELETE CASCADE
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_governance_event_once
                ON governance_events(mission_id, event_type, event_key)
                WHERE event_key IS NOT NULL;

            CREATE TABLE IF NOT EXISTS tool_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mission_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                status TEXT NOT NULL,
                input_hash TEXT NOT NULL,
                output_hash TEXT,
                error TEXT,
                started_at REAL NOT NULL,
                ended_at REAL NOT NULL,
                FOREIGN KEY(mission_id) REFERENCES missions(mission_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS tool_outputs (
                mission_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                input_hash TEXT NOT NULL,
                output_hash TEXT NOT NULL,
                output_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY(mission_id, tool_name, input_hash),
                FOREIGN KEY(mission_id) REFERENCES missions(mission_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS tool_executions (
                mission_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                input_hash TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'completed', 'failed', 'blocked')),
                retryable INTEGER NOT NULL DEFAULT 0,
                output_hash TEXT,
                error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(mission_id, tool_name, input_hash),
                FOREIGN KEY(mission_id) REFERENCES missions(mission_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS ingested_records (
                mission_id TEXT NOT NULL,
                source_name TEXT NOT NULL,
                record_fingerprint TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                fixture_name TEXT NOT NULL,
                record_json TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(mission_id, source_name, record_fingerprint),
                FOREIGN KEY(mission_id) REFERENCES missions(mission_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS raw_payloads (
                mission_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY(mission_id, source_id, payload_hash),
                FOREIGN KEY(mission_id) REFERENCES missions(mission_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS normalized_events (
                mission_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event_fingerprint TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                raw_payload_hash TEXT,
                event_json TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                published_at TEXT,
                confidence REAL NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(mission_id, source_id, event_fingerprint),
                FOREIGN KEY(mission_id) REFERENCES missions(mission_id) ON DELETE CASCADE
            );
            """
        )
        self._ensure_column("governance_events", "event_key", "TEXT")
        self.conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_governance_event_once
                ON governance_events(mission_id, event_type, event_key)
                WHERE event_key IS NOT NULL
            """
        )
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def close(self) -> None:
        self.conn.close()

    def _json(self, value: Any) -> str:
        return json.dumps(value, sort_keys=True, default=str)

    def _hash_text(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def payload_hash(self, value: Any) -> str:
        return self._hash_text(self._json(value))

    def tool_idempotency_key(self, mission_id: str, tool_name: str, input_payload: Any) -> str:
        payload = {
            "mission_id": mission_id,
            "tool_name": tool_name,
            "input_hash": self.payload_hash(input_payload),
        }
        return self.payload_hash(payload)

    def mission_exists(self, mission_id: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM missions WHERE mission_id = ? LIMIT 1", (mission_id,)).fetchone()
        return row is not None

    def create_mission(self, mission_id: str, user_query: str, status: str = "running") -> None:
        now = time.time()
        self.conn.execute(
            """
            INSERT OR IGNORE INTO missions(mission_id, user_query_hash, created_at, status, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (mission_id, self._hash_text(user_query), now, status, now),
        )
        self.conn.commit()

    def update_mission_status(self, mission_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE missions SET status = ?, updated_at = ? WHERE mission_id = ?",
            (status, time.time(), mission_id),
        )
        self.conn.commit()

    def save_state(
        self,
        *,
        mission_id: str,
        state: dict[str, Any],
        budget: dict[str, Any],
        manifest_hash: str,
        completed_tools: list[str],
        pending_reviews: list[dict[str, Any]],
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO mission_state(
                mission_id, state_json, budget_json, manifest_hash,
                completed_tools_json, pending_reviews_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(mission_id) DO UPDATE SET
                state_json=excluded.state_json,
                budget_json=excluded.budget_json,
                manifest_hash=excluded.manifest_hash,
                completed_tools_json=excluded.completed_tools_json,
                pending_reviews_json=excluded.pending_reviews_json,
                updated_at=excluded.updated_at
            """,
            (
                mission_id,
                self._json(state),
                self._json(budget),
                manifest_hash,
                self._json(completed_tools),
                self._json(pending_reviews),
                time.time(),
            ),
        )
        self.conn.commit()

    def record_artifact(self, mission_id: str, artifact_type: str, artifact_path: str, artifact_hash: str) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO artifacts(mission_id, artifact_type, artifact_path, artifact_hash, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (mission_id, artifact_type, artifact_path, artifact_hash, time.time()),
            )
            self.conn.commit()
        self.log_event(
            mission_id,
            "ARTIFACT_RECORDED",
            {"artifact_type": artifact_type, "artifact_path": artifact_path, "artifact_hash": artifact_hash},
            event_key=f"{artifact_path}:{artifact_hash}",
        )

    def request_review(
        self,
        mission_id: str,
        artifact_type: str,
        artifact_path: str,
        artifact_hash: str,
        expires_in_seconds: int = 86_400,
    ) -> bool:
        now = time.time()
        cur = self.conn.execute(
            """
            INSERT OR IGNORE INTO review_requests(
                mission_id, artifact_type, artifact_path, artifact_hash,
                status, requested_at, expires_at
            )
            VALUES (?, ?, ?, ?, 'PENDING', ?, ?)
            """,
            (mission_id, artifact_type, artifact_path, artifact_hash, now, now + expires_in_seconds),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def expire_reviews(self) -> int:
        cur = self.conn.execute(
            """
            UPDATE review_requests
            SET status = 'TIMEOUT', resolved_at = ?
            WHERE status = 'PENDING' AND expires_at <= ?
            """,
            (time.time(), time.time()),
        )
        self.conn.commit()
        return cur.rowcount

    def update_review_status(
        self,
        *,
        mission_id: str,
        artifact_path: str,
        artifact_hash: str,
        status: str,
        reviewer: str,
        reason: str,
        review_method: str = "cli",
    ) -> bool:
        if status not in {"APPROVED", "REJECTED"}:
            raise ValueError("status must be APPROVED or REJECTED")
        now = time.time()
        cur = self.conn.execute(
            """
            UPDATE review_requests
            SET status = ?, reviewed_by = ?, decision_reason = ?, review_method = ?, resolved_at = ?
            WHERE mission_id = ? AND artifact_path = ? AND artifact_hash = ?
              AND status = 'PENDING' AND expires_at > ?
            """,
            (status, reviewer, reason, review_method, now, mission_id, artifact_path, artifact_hash, now),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def verify_approved_artifact(self, mission_id: str, artifact_path: str, artifact_hash: str) -> bool:
        self.expire_reviews()
        row = self.conn.execute(
            """
            SELECT 1 FROM review_requests
            WHERE mission_id = ? AND artifact_path = ? AND artifact_hash = ?
              AND status = 'APPROVED'
            LIMIT 1
            """,
            (mission_id, artifact_path, artifact_hash),
        ).fetchone()
        return row is not None

    def log_event(self, mission_id: str, event_type: str, metadata: dict[str, Any], event_key: str | None = None) -> bool:
        with self._lock:
            cur = self.conn.execute(
                """
                INSERT OR IGNORE INTO governance_events(mission_id, event_type, event_key, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (mission_id, event_type, event_key, self._json(metadata), time.time()),
            )
            self.conn.commit()
            return cur.rowcount == 1

    def get_tool_output(self, mission_id: str, tool_name: str, input_payload: Any) -> dict[str, Any] | None:
        input_hash = self.payload_hash(input_payload)
        row = self.conn.execute(
            """
            SELECT output_json FROM tool_outputs
            WHERE mission_id = ? AND tool_name = ? AND input_hash = ?
            """,
            (mission_id, tool_name, input_hash),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["output_json"])

    def get_tool_execution(self, mission_id: str, tool_name: str, input_payload: Any) -> dict[str, Any] | None:
        input_hash = self.payload_hash(input_payload)
        row = self.conn.execute(
            """
            SELECT * FROM tool_executions
            WHERE mission_id = ? AND tool_name = ? AND input_hash = ?
            """,
            (mission_id, tool_name, input_hash),
        ).fetchone()
        return dict(row) if row else None

    def mark_tool_execution(self, mission_id: str, tool_name: str, input_payload: Any, status: str, **fields: Any) -> str:
        if status not in {"pending", "running", "completed", "failed", "blocked"}:
            raise ValueError(f"unsupported tool execution status: {status}")
        input_hash = self.payload_hash(input_payload)
        idempotency_key = self.tool_idempotency_key(mission_id, tool_name, input_payload)
        now = time.time()
        retryable = 1 if fields.get("retryable") else 0
        output_hash = fields.get("output_hash")
        error = fields.get("error")
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO tool_executions(
                    mission_id, tool_name, input_hash, idempotency_key, status,
                    retryable, output_hash, error, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mission_id, tool_name, input_hash) DO UPDATE SET
                    status=excluded.status,
                    retryable=excluded.retryable,
                    output_hash=COALESCE(excluded.output_hash, tool_executions.output_hash),
                    error=excluded.error,
                    updated_at=excluded.updated_at
                """,
                (
                    mission_id,
                    tool_name,
                    input_hash,
                    idempotency_key,
                    status,
                    retryable,
                    output_hash,
                    error,
                    now,
                    now,
                ),
            )
            self.conn.commit()
        return idempotency_key

    def save_tool_output(self, mission_id: str, tool_name: str, input_payload: Any, output_payload: Any) -> None:
        input_hash = self.payload_hash(input_payload)
        output_json = self._json(output_payload)
        output_hash = self._hash_text(output_json)
        self.conn.execute(
            """
            INSERT OR REPLACE INTO tool_outputs(
                mission_id, tool_name, input_hash, output_hash, output_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (mission_id, tool_name, input_hash, output_hash, output_json, time.time()),
        )
        self.conn.commit()
        self.mark_tool_execution(
            mission_id,
            tool_name,
            input_payload,
            "completed",
            output_hash=output_hash,
            retryable=False,
        )

    def record_tool_run(
        self,
        *,
        mission_id: str,
        tool_name: str,
        status: str,
        input_payload: Any,
        output_payload: Any | None = None,
        error: str | None = None,
        started_at: float | None = None,
    ) -> None:
        started = started_at or time.time()
        output_hash = self._hash_text(self._json(output_payload)) if output_payload is not None else None
        self.conn.execute(
            """
            INSERT INTO tool_runs(
                mission_id, tool_name, status, input_hash, output_hash, error, started_at, ended_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mission_id,
                tool_name,
                status,
                self._hash_text(self._json(input_payload)),
                output_hash,
                error,
                started,
                time.time(),
            ),
        )
        self.conn.commit()

    def upsert_ingested_record(
        self,
        *,
        mission_id: str,
        source_name: str,
        record_fingerprint: str,
        content_hash: str,
        fixture_name: str,
        record: dict[str, Any],
        collected_at: str,
    ) -> bool:
        now = time.time()
        with self._lock:
            cur = self.conn.execute(
                """
                INSERT INTO ingested_records(
                    mission_id, source_name, record_fingerprint, content_hash,
                    fixture_name, record_json, collected_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mission_id, source_name, record_fingerprint) DO UPDATE SET
                    content_hash=excluded.content_hash,
                    fixture_name=excluded.fixture_name,
                    record_json=excluded.record_json,
                    collected_at=excluded.collected_at,
                    updated_at=excluded.updated_at
                WHERE ingested_records.content_hash != excluded.content_hash
                """,
                (
                    mission_id,
                    source_name,
                    record_fingerprint,
                    content_hash,
                    fixture_name,
                    self._json(record),
                    collected_at,
                    now,
                    now,
                ),
            )
            self.conn.commit()
            return cur.rowcount == 1

    def record_raw_payload(
        self,
        *,
        mission_id: str,
        source_id: str,
        payload_hash: str,
        payload: dict[str, Any],
        observed_at: str,
    ) -> bool:
        now = time.time()
        with self._lock:
            cur = self.conn.execute(
                """
                INSERT OR IGNORE INTO raw_payloads(
                    mission_id, source_id, payload_hash, payload_json, observed_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (mission_id, source_id, payload_hash, self._json(payload), observed_at, now),
            )
            self.conn.commit()
            return cur.rowcount == 1

    def upsert_normalized_event(
        self,
        *,
        mission_id: str,
        source_id: str,
        event_id: str,
        event_fingerprint: str,
        content_hash: str,
        raw_payload_hash: str | None,
        event: dict[str, Any],
        observed_at: str,
        published_at: str | None,
        confidence: float,
    ) -> bool:
        now = time.time()
        with self._lock:
            cur = self.conn.execute(
                """
                INSERT INTO normalized_events(
                    mission_id, source_id, event_id, event_fingerprint, content_hash,
                    raw_payload_hash, event_json, observed_at, published_at, confidence, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mission_id, source_id, event_fingerprint) DO UPDATE SET
                    content_hash=excluded.content_hash,
                    raw_payload_hash=excluded.raw_payload_hash,
                    event_json=excluded.event_json,
                    observed_at=excluded.observed_at,
                    published_at=excluded.published_at,
                    confidence=excluded.confidence,
                    updated_at=excluded.updated_at
                WHERE normalized_events.content_hash != excluded.content_hash
                """
                ,
                (
                    mission_id,
                    source_id,
                    event_id,
                    event_fingerprint,
                    content_hash,
                    raw_payload_hash,
                    self._json(event),
                    observed_at,
                    published_at,
                    confidence,
                    now,
                    now,
                ),
            )
            self.conn.commit()
            return cur.rowcount == 1

    def list_normalized_events(
        self,
        *,
        mission_id: str | None = None,
        source_id: str | None = None,
        min_confidence: float | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if mission_id is not None:
            clauses.append("mission_id = ?")
            params.append(mission_id)
        if source_id is not None:
            clauses.append("source_id = ?")
            params.append(source_id)
        if min_confidence is not None:
            clauses.append("confidence >= ?")
            params.append(float(min_confidence))
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.conn.execute(
            f"""
            SELECT * FROM normalized_events
            {where}
            ORDER BY COALESCE(published_at, observed_at) DESC, updated_at DESC
            LIMIT ?
            """,
            (*params, int(limit)),
        ).fetchall()
        return [
            {
                **dict(row),
                "event": json.loads(row["event_json"]),
            }
            for row in rows
        ]

    def count_rows(self, table: str, mission_id: str) -> int:
        allowed = {
            "artifacts",
            "governance_events",
            "ingested_records",
            "normalized_events",
            "raw_payloads",
            "review_requests",
            "tool_executions",
            "tool_runs",
            "tool_outputs",
        }
        if table not in allowed:
            raise ValueError(f"unsupported table: {table}")
        row = self.conn.execute(f"SELECT COUNT(*) AS count FROM {table} WHERE mission_id = ?", (mission_id,)).fetchone()
        return int(row["count"])

    def has_event(self, mission_id: str, event_type: str, event_key: str) -> bool:
        row = self.conn.execute(
            """
            SELECT 1 FROM governance_events
            WHERE mission_id = ? AND event_type = ? AND event_key = ?
            LIMIT 1
            """,
            (mission_id, event_type, event_key),
        ).fetchone()
        return row is not None

    def load_resume_bundle(self, mission_id: str) -> ResumeBundle:
        mission = self.conn.execute("SELECT * FROM missions WHERE mission_id = ?", (mission_id,)).fetchone()
        if mission is None:
            raise KeyError(f"mission not found: {mission_id}")

        state_row = self.conn.execute("SELECT * FROM mission_state WHERE mission_id = ?", (mission_id,)).fetchone()
        state = {}
        if state_row is not None:
            state = {
                "state": json.loads(state_row["state_json"]),
                "budget": json.loads(state_row["budget_json"]),
                "manifest_hash": state_row["manifest_hash"],
                "completed_tools": json.loads(state_row["completed_tools_json"]),
                "pending_reviews": json.loads(state_row["pending_reviews_json"]),
                "updated_at": state_row["updated_at"],
            }

        artifacts = [dict(row) for row in self.conn.execute("SELECT * FROM artifacts WHERE mission_id = ?", (mission_id,))]
        reviews = [dict(row) for row in self.conn.execute("SELECT * FROM review_requests WHERE mission_id = ?", (mission_id,))]
        return ResumeBundle(mission=dict(mission), state=state, artifacts=artifacts, reviews=reviews)
