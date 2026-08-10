"""Append-only, redacted, hash-chained SQLite audit events."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import uuid
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict

from order_pipeline.config import Settings
from order_pipeline.domain import Actor, ActorRole

_GENESIS_HASH = "0" * 64
_REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "body",
    "content",
    "cookie",
    "customer_data",
    "password",
    "prompt",
    "raw_email",
    "secret",
    "stack_trace",
    "token",
    "traceback",
}
_EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sanitize_value(value: Any, key: str | None = None) -> Any:
    normalized_key = (key or "").lower().replace("-", "_")
    if normalized_key in _SENSITIVE_KEYS or any(
        marker in normalized_key for marker in ("password", "secret", "token", "authorization", "api_key")
    ):
        return _REDACTED
    if isinstance(value, Mapping):
        return {
            str(child_key): _sanitize_value(child_value, str(child_key)) for child_key, child_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, str):
        sanitized = _BEARER_RE.sub("Bearer [REDACTED]", value)
        sanitized = _EMAIL_RE.sub("[REDACTED_EMAIL]", sanitized)
        if len(sanitized) > 512 or sanitized.count("\n") > 4:
            digest = hashlib.sha256(sanitized.encode()).hexdigest()
            return f"[REDACTED_TEXT sha256={digest}]"
        return sanitized
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


class AuditStatus(StrEnum):
    INTENT = "intent"
    SUCCESS = "success"
    DENIED = "denied"
    TIMEOUT = "timeout"
    FAILURE = "failure"


class AuditEvent(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    event_id: str
    trace_id: str
    sequence: int
    occurred_at: datetime
    event_type: str
    status: AuditStatus
    actor: Actor
    code_version: str
    prompt_version: str
    policy_version: str
    model_versions: dict[str, Any]
    input_digest: str | None
    artifact_digest: str | None
    idempotency_key: str | None
    outcome: str | None
    payload: dict[str, Any]
    intent_event_id: str | None
    previous_hash: str
    event_hash: str


class AuditPersistenceError(RuntimeError):
    """A required audit event could not be committed; callers must fail closed."""


class AuditSink:
    def __init__(self, db_path: Path | str, *, versions: Mapping[str, Any] | None = None):
        self.db_path = Path(db_path) if str(db_path) != ":memory:" else Path(":memory:")
        if self.db_path != Path(":memory:"):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._versions = dict(versions or Settings().version_manifest())
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(str(self.db_path), timeout=30, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._configure()
        self._create_schema()

    def _configure(self) -> None:
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 30000")
        if self.db_path != Path(":memory:"):
            mode = self._connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if str(mode).lower() != "wal":
                raise RuntimeError(f"SQLite refused WAL mode for {self.db_path}")
        self._connection.execute("PRAGMA synchronous = FULL")

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                event_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                sequence INTEGER NOT NULL CHECK (sequence >= 1),
                occurred_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                status TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                actor_role TEXT NOT NULL,
                actor_display_name TEXT NOT NULL,
                code_version TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                policy_version TEXT NOT NULL,
                model_versions_json TEXT NOT NULL,
                input_digest TEXT,
                artifact_digest TEXT,
                idempotency_key TEXT,
                outcome TEXT,
                payload_json TEXT NOT NULL,
                intent_event_id TEXT,
                previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL,
                UNIQUE (trace_id, sequence),
                UNIQUE (trace_id, event_hash)
            );

            CREATE INDEX IF NOT EXISTS idx_audit_events_trace
            ON audit_events (trace_id, sequence);

            CREATE TRIGGER IF NOT EXISTS audit_events_no_update
            BEFORE UPDATE ON audit_events
            BEGIN
                SELECT RAISE(ABORT, 'audit events are append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
            BEFORE DELETE ON audit_events
            BEGIN
                SELECT RAISE(ABORT, 'audit events are append-only');
            END;
            """
        )
        self._connection.commit()

    @staticmethod
    def _system_actor() -> Actor:
        return Actor(actor_id="workflow", role=ActorRole.SYSTEM, display_name="Workflow service")

    def _version_fields(self) -> tuple[str, str, str, dict[str, Any]]:
        return (
            str(self._versions.get("code_version", "unknown")),
            str(self._versions.get("prompt_version", "unknown")),
            str(self._versions.get("policy_version", "unknown")),
            dict(self._versions.get("model_versions", {})),
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> AuditEvent:
        return AuditEvent.model_validate(
            {
                "event_id": row["event_id"],
                "trace_id": row["trace_id"],
                "sequence": row["sequence"],
                "occurred_at": datetime.fromisoformat(row["occurred_at"]),
                "event_type": row["event_type"],
                "status": AuditStatus(row["status"]),
                "actor": Actor(
                    actor_id=row["actor_id"],
                    role=ActorRole(row["actor_role"]),
                    display_name=row["actor_display_name"],
                ),
                "code_version": row["code_version"],
                "prompt_version": row["prompt_version"],
                "policy_version": row["policy_version"],
                "model_versions": json.loads(row["model_versions_json"]),
                "input_digest": row["input_digest"],
                "artifact_digest": row["artifact_digest"],
                "idempotency_key": row["idempotency_key"],
                "outcome": row["outcome"],
                "payload": json.loads(row["payload_json"]),
                "intent_event_id": row["intent_event_id"],
                "previous_hash": row["previous_hash"],
                "event_hash": row["event_hash"],
            }
        )

    def append(
        self,
        *,
        trace_id: str,
        event_type: str,
        status: AuditStatus,
        actor: Actor | None = None,
        input_digest: str | None = None,
        artifact_digest: str | None = None,
        idempotency_key: str | None = None,
        outcome: str | None = None,
        payload: Mapping[str, Any] | None = None,
        intent_event_id: str | None = None,
    ) -> AuditEvent:
        actor = actor or self._system_actor()
        sanitized_payload = _sanitize_value(dict(payload or {}))
        code_version, prompt_version, policy_version, model_versions = self._version_fields()
        event_id = str(uuid.uuid4())
        occurred_at = _utc_now()
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                previous = self._connection.execute(
                    "SELECT sequence, event_hash FROM audit_events WHERE trace_id = ? ORDER BY sequence DESC LIMIT 1",
                    (trace_id,),
                ).fetchone()
                sequence = 1 if previous is None else int(previous["sequence"]) + 1
                previous_hash = _GENESIS_HASH if previous is None else str(previous["event_hash"])
                hash_material = {
                    "event_id": event_id,
                    "trace_id": trace_id,
                    "sequence": sequence,
                    "occurred_at": occurred_at.isoformat(),
                    "event_type": event_type,
                    "status": status.value,
                    "actor": actor.model_dump(mode="json"),
                    "code_version": code_version,
                    "prompt_version": prompt_version,
                    "policy_version": policy_version,
                    "model_versions": model_versions,
                    "input_digest": input_digest,
                    "artifact_digest": artifact_digest,
                    "idempotency_key": idempotency_key,
                    "outcome": outcome,
                    "payload": sanitized_payload,
                    "intent_event_id": intent_event_id,
                    "previous_hash": previous_hash,
                }
                event_hash = hashlib.sha256(_canonical_json(hash_material).encode()).hexdigest()
                self._connection.execute(
                    """
                    INSERT INTO audit_events (
                        event_id, trace_id, sequence, occurred_at, event_type, status,
                        actor_id, actor_role, actor_display_name, code_version, prompt_version,
                        policy_version, model_versions_json, input_digest, artifact_digest,
                        idempotency_key, outcome, payload_json, intent_event_id, previous_hash, event_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        trace_id,
                        sequence,
                        occurred_at.isoformat(),
                        event_type,
                        status.value,
                        actor.actor_id,
                        actor.role.value,
                        actor.display_name,
                        code_version,
                        prompt_version,
                        policy_version,
                        _canonical_json(model_versions),
                        input_digest,
                        artifact_digest,
                        idempotency_key,
                        outcome,
                        _canonical_json(sanitized_payload),
                        intent_event_id,
                        previous_hash,
                        event_hash,
                    ),
                )
                self._connection.commit()
            except BaseException as error:
                with suppress(sqlite3.Error):
                    self._connection.rollback()
                raise AuditPersistenceError("required audit event could not be persisted") from error
        return AuditEvent(
            event_id=event_id,
            trace_id=trace_id,
            sequence=sequence,
            occurred_at=occurred_at,
            event_type=event_type,
            status=status,
            actor=actor,
            code_version=code_version,
            prompt_version=prompt_version,
            policy_version=policy_version,
            model_versions=model_versions,
            input_digest=input_digest,
            artifact_digest=artifact_digest,
            idempotency_key=idempotency_key,
            outcome=outcome,
            payload=sanitized_payload,
            intent_event_id=intent_event_id,
            previous_hash=previous_hash,
            event_hash=event_hash,
        )

    def record_intent(self, trace_id: str, event_type: str, **kwargs: Any) -> AuditEvent:
        return self.append(trace_id=trace_id, event_type=event_type, status=AuditStatus.INTENT, **kwargs)

    def record_outcome(
        self,
        trace_id: str,
        intent_event_id: str,
        status: AuditStatus,
        *,
        outcome: str | None = None,
        payload: Mapping[str, Any] | None = None,
        actor: Actor | None = None,
        artifact_digest: str | None = None,
    ) -> AuditEvent:
        if status is AuditStatus.INTENT:
            raise ValueError("record_outcome requires success, denial, timeout, or failure")
        with self._lock:
            intent = self._connection.execute(
                "SELECT * FROM audit_events WHERE event_id = ? AND trace_id = ?",
                (intent_event_id, trace_id),
            ).fetchone()
        if intent is None or intent["status"] != AuditStatus.INTENT.value:
            raise ValueError("intent_event_id must reference an intent on the same trace")
        return self.append(
            trace_id=trace_id,
            event_type=f"{intent['event_type']}.outcome",
            status=status,
            actor=actor,
            input_digest=intent["input_digest"],
            artifact_digest=artifact_digest,
            idempotency_key=intent["idempotency_key"],
            outcome=outcome,
            payload=payload,
            intent_event_id=intent_event_id,
        )

    def events_for_trace(self, trace_id: str) -> list[AuditEvent]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM audit_events WHERE trace_id = ? ORDER BY sequence",
                (trace_id,),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def verify_trace(self, trace_id: str) -> bool:
        events = self.events_for_trace(trace_id)
        previous_hash = _GENESIS_HASH
        for expected_sequence, event in enumerate(events, start=1):
            hash_material = {
                "event_id": event.event_id,
                "trace_id": event.trace_id,
                "sequence": event.sequence,
                "occurred_at": event.occurred_at.isoformat(),
                "event_type": event.event_type,
                "status": event.status.value,
                "actor": event.actor.model_dump(mode="json"),
                "code_version": event.code_version,
                "prompt_version": event.prompt_version,
                "policy_version": event.policy_version,
                "model_versions": event.model_versions,
                "input_digest": event.input_digest,
                "artifact_digest": event.artifact_digest,
                "idempotency_key": event.idempotency_key,
                "outcome": event.outcome,
                "payload": event.payload,
                "intent_event_id": event.intent_event_id,
                "previous_hash": event.previous_hash,
            }
            expected_hash = hashlib.sha256(_canonical_json(hash_material).encode()).hexdigest()
            if (
                event.sequence != expected_sequence
                or event.previous_hash != previous_hash
                or event.event_hash != expected_hash
            ):
                return False
            previous_hash = event.event_hash
        return True

    def log_llm_call(self, trace_id: str, **kwargs: Any) -> None:
        self.append(
            trace_id=trace_id,
            event_type="llm_call",
            status=AuditStatus.SUCCESS,
            payload=kwargs,
        )

    def log_tool_call(self, trace_id: str, name: str, args: dict[str, Any], result: Any, **kwargs: Any) -> None:
        self.append(
            trace_id=trace_id,
            event_type="tool_call",
            status=AuditStatus.SUCCESS,
            payload={"tool": name, "args": args, "result": result, **kwargs},
        )

    def log_event(self, trace_id: str, event_type: str, payload: dict[str, Any]) -> None:
        self.append(trace_id=trace_id, event_type=event_type, status=AuditStatus.SUCCESS, payload=payload)

    def trace_df(self, trace_id: str) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "sequence": event.sequence,
                    "ts": event.occurred_at.isoformat(),
                    "event_type": event.event_type,
                    "status": event.status.value,
                    "payload": _canonical_json(event.payload),
                }
                for event in self.events_for_trace(trace_id)
            ]
        )

    def all_df(self) -> pd.DataFrame:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM audit_events ORDER BY occurred_at, sequence").fetchall()
        return pd.DataFrame([dict(row) for row in rows])

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> AuditSink:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


AuditLogger = AuditSink
