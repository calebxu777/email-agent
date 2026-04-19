from __future__ import annotations

from contextlib import contextmanager
import json
import sqlite3
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from .models import AuditEvent, DraftResponse, NormalizedEmail, OrchestrationResult, RawEmail, TrackingLookupResult, TrackingWorksheet


class SQLiteRepository:
    def __init__(self, path: str | Path = "email_agent.db") -> None:
        self.path = str(path)
        self._init_schema()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS raw_emails (
                    message_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    sender_email TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    body_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS normalized_emails (
                    message_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    sender_email TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    body_hash TEXT NOT NULL,
                    body_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS worksheet_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS backend_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    response_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS outbound_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    lane TEXT NOT NULL,
                    should_send INTEGER NOT NULL,
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    old_state TEXT NOT NULL,
                    new_state TEXT NOT NULL,
                    actor_type TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sender_activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender_email TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    body_hash TEXT NOT NULL,
                    received_at TEXT NOT NULL
                );
                """
            )

    def store_raw_email(self, raw_email: RawEmail, trace_id: str) -> None:
        payload = json.dumps(_serialize(asdict(raw_email)), default=str)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO raw_emails(message_id, thread_id, sender_email, subject, received_at, trace_id, body_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    raw_email.message_id,
                    raw_email.thread_id,
                    raw_email.sender_email.lower(),
                    raw_email.subject,
                    raw_email.received_at.astimezone(timezone.utc).isoformat(),
                    trace_id,
                    payload,
                ),
            )

    def store_normalized_email(self, email: NormalizedEmail, trace_id: str) -> None:
        payload = json.dumps(_serialize(asdict(email)), default=str)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO normalized_emails(message_id, thread_id, sender_email, subject, received_at, trace_id, body_hash, body_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    email.message_id,
                    email.thread_id,
                    email.sender_email,
                    email.subject,
                    email.received_at.isoformat(),
                    trace_id,
                    email.body_hash,
                    payload,
                ),
            )

    def store_worksheet(self, worksheet: TrackingWorksheet, trace_id: str) -> None:
        payload = json.dumps(_serialize(asdict(worksheet)), default=str)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO worksheet_snapshots(message_id, trace_id, created_at, snapshot_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    worksheet.message_id,
                    trace_id,
                    datetime.now(timezone.utc).isoformat(),
                    payload,
                ),
            )

    def record_backend_call(
        self,
        message_id: str,
        trace_id: str,
        request_payload: dict,
        response_payload: TrackingLookupResult,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO backend_calls(message_id, trace_id, created_at, request_json, response_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    trace_id,
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps(_serialize(request_payload), default=str),
                    json.dumps(_serialize(asdict(response_payload)), default=str),
                ),
            )

    def record_outbound(self, message_id: str, trace_id: str, draft: DraftResponse) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO outbound_messages(message_id, trace_id, created_at, lane, should_send, subject, body)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    trace_id,
                    datetime.now(timezone.utc).isoformat(),
                    draft.lane.value,
                    int(draft.should_send),
                    draft.subject,
                    draft.body,
                ),
            )

    def record_audit_events(self, events: list[AuditEvent]) -> None:
        if not events:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO audit_events(message_id, thread_id, trace_id, old_state, new_state, actor_type, detail, policy_version, model_version, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        event.message_id,
                        event.thread_id,
                        event.trace_id,
                        event.old_state.value,
                        event.new_state.value,
                        event.actor_type,
                        event.detail,
                        event.policy_version,
                        event.model_version,
                        event.timestamp.isoformat(),
                    )
                    for event in events
                ],
            )

    def record_sender_activity(self, sender_email: str, message_id: str, trace_id: str, body_hash: str, received_at: datetime) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sender_activity(sender_email, message_id, trace_id, body_hash, received_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (sender_email.lower(), message_id, trace_id, body_hash, received_at.astimezone(timezone.utc).isoformat()),
            )

    def recent_sender_volume(self, sender_email: str, since: datetime) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM sender_activity
                WHERE sender_email = ? AND received_at >= ?
                """,
                (sender_email.lower(), since.astimezone(timezone.utc).isoformat()),
            ).fetchone()
        return int(row["count"] if row else 0)

    def seen_recent_duplicate(self, sender_email: str, body_hash: str, since: datetime) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM sender_activity
                WHERE sender_email = ? AND body_hash = ? AND received_at >= ?
                LIMIT 1
                """,
                (sender_email.lower(), body_hash, since.astimezone(timezone.utc).isoformat()),
            ).fetchone()
        return row is not None

    def metrics_snapshot(self) -> dict[str, int]:
        queries = {
            "raw_emails": "SELECT COUNT(*) AS count FROM raw_emails",
            "normalized_emails": "SELECT COUNT(*) AS count FROM normalized_emails",
            "audit_events": "SELECT COUNT(*) AS count FROM audit_events",
            "backend_calls": "SELECT COUNT(*) AS count FROM backend_calls",
            "outbound_messages": "SELECT COUNT(*) AS count FROM outbound_messages",
        }
        metrics: dict[str, int] = {}
        with self._connect() as conn:
            for key, query in queries.items():
                row = conn.execute(query).fetchone()
                metrics[key] = int(row["count"] if row else 0)
        return metrics

    def persist_result(self, raw_email: RawEmail, result: OrchestrationResult) -> None:
        self.store_raw_email(raw_email, result.trace_id)
        self.store_normalized_email(result.normalized_email, result.trace_id)
        self.store_worksheet(result.worksheet, result.trace_id)
        self.record_audit_events(list(result.audit_events))
        self.record_sender_activity(
            sender_email=result.normalized_email.sender_email,
            message_id=result.normalized_email.message_id,
            trace_id=result.trace_id,
            body_hash=result.normalized_email.body_hash,
            received_at=result.normalized_email.received_at,
        )
        if result.backend_result:
            self.record_backend_call(
                message_id=result.normalized_email.message_id,
                trace_id=result.trace_id,
                request_payload={
                    "sender_email": result.normalized_email.sender_email,
                    "order_id": result.worksheet.order_id.value,
                    "thread_id": result.normalized_email.thread_id,
                },
                response_payload=result.backend_result,
            )
        if result.draft_response:
            self.record_outbound(result.normalized_email.message_id, result.trace_id, result.draft_response)


def _serialize(value):
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize(item) for item in value]
    if isinstance(value, set):
        return sorted(_serialize(item) for item in value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _serialize(asdict(value))
    return value
