"""Transactional SQLite workflow state, duplicate reservation, and review history."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from order_pipeline.domain import InboundEmail, ProcessingOutcome, ProcessingResult, ReviewRevision


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def derive_idempotency_key(mailbox_id: str, provider_message_id: str) -> str:
    """Derive an unambiguous stable key from provider identity, not content."""

    material = _canonical_json(
        {
            "domain": "order-idempotency-v1",
            "mailbox_id": mailbox_id,
            "provider_message_id": provider_message_id,
        }
    )
    return hashlib.sha256(material.encode()).hexdigest()


def compute_content_digest(email: InboundEmail) -> str:
    material = _canonical_json(email.model_dump(mode="json"))
    return hashlib.sha256(material.encode()).hexdigest()


class ReservationDisposition(StrEnum):
    NEW = "new"
    DUPLICATE = "duplicate"
    CONTENT_CONFLICT = "content_conflict"


class WorkflowJob(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    job_id: str
    mailbox_id: str
    provider_message_id: str
    idempotency_key: str
    content_digest: str
    trace_id: str
    outcome: ProcessingOutcome
    reason_codes: tuple[str, ...] = ()
    order_id: str | None = None
    delivery_count: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class MessageReservation(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    disposition: ReservationDisposition
    job: WorkflowJob

    @property
    def deduplicated(self) -> bool:
        return self.disposition is not ReservationDisposition.NEW


class StateConflictError(RuntimeError):
    """Raised when an optimistic transition or terminal result is stale."""


class StateStore:
    """Durable state store with one stable job/trace per mailbox message."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path) if str(db_path) != ":memory:" else Path(":memory:")
        if self.db_path != Path(":memory:"):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
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
            CREATE TABLE IF NOT EXISTS workflow_jobs (
                job_id TEXT PRIMARY KEY,
                mailbox_id TEXT NOT NULL,
                provider_message_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                content_digest TEXT NOT NULL,
                trace_id TEXT NOT NULL UNIQUE,
                outcome TEXT NOT NULL,
                reason_codes_json TEXT NOT NULL DEFAULT '[]',
                order_id TEXT,
                delivery_count INTEGER NOT NULL DEFAULT 1 CHECK (delivery_count >= 1),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (mailbox_id, provider_message_id)
            );

            CREATE TABLE IF NOT EXISTS message_content_conflicts (
                conflict_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES workflow_jobs(job_id),
                observed_digest TEXT NOT NULL,
                observed_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS review_revisions (
                review_id TEXT NOT NULL,
                trace_id TEXT NOT NULL REFERENCES workflow_jobs(trace_id),
                version INTEGER NOT NULL CHECK (version >= 1),
                state TEXT NOT NULL,
                actor_json TEXT NOT NULL,
                before_json TEXT NOT NULL,
                after_json TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (review_id, version)
            );

            CREATE TRIGGER IF NOT EXISTS review_revisions_no_update
            BEFORE UPDATE ON review_revisions
            BEGIN
                SELECT RAISE(ABORT, 'review revisions are append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS review_revisions_no_delete
            BEFORE DELETE ON review_revisions
            BEGIN
                SELECT RAISE(ABORT, 'review revisions are append-only');
            END;
            """
        )
        self._connection.commit()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                yield self._connection
            except BaseException:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    @staticmethod
    def _job_from_row(row: sqlite3.Row) -> WorkflowJob:
        return WorkflowJob(
            job_id=row["job_id"],
            mailbox_id=row["mailbox_id"],
            provider_message_id=row["provider_message_id"],
            idempotency_key=row["idempotency_key"],
            content_digest=row["content_digest"],
            trace_id=row["trace_id"],
            outcome=ProcessingOutcome(row["outcome"]),
            reason_codes=tuple(json.loads(row["reason_codes_json"])),
            order_id=row["order_id"],
            delivery_count=row["delivery_count"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def reserve_message(self, email: InboundEmail) -> MessageReservation:
        return self.reserve(
            mailbox_id=email.mailbox_id,
            provider_message_id=email.provider_message_id,
            content_digest=compute_content_digest(email),
        )

    def reserve(self, *, mailbox_id: str, provider_message_id: str, content_digest: str) -> MessageReservation:
        idempotency_key = derive_idempotency_key(mailbox_id, provider_message_id)
        now = _utc_now().isoformat()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_jobs WHERE mailbox_id = ? AND provider_message_id = ?",
                (mailbox_id, provider_message_id),
            ).fetchone()
            if row is None:
                job_id = str(uuid.uuid4())
                trace_id = f"trace-{uuid.uuid4()}"
                connection.execute(
                    """
                    INSERT INTO workflow_jobs (
                        job_id, mailbox_id, provider_message_id, idempotency_key, content_digest,
                        trace_id, outcome, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        mailbox_id,
                        provider_message_id,
                        idempotency_key,
                        content_digest,
                        trace_id,
                        ProcessingOutcome.RECEIVED.value,
                        now,
                        now,
                    ),
                )
                row = connection.execute("SELECT * FROM workflow_jobs WHERE job_id = ?", (job_id,)).fetchone()
                assert row is not None
                return MessageReservation(disposition=ReservationDisposition.NEW, job=self._job_from_row(row))

            disposition = ReservationDisposition.DUPLICATE
            outcome = row["outcome"]
            reason_codes_json = row["reason_codes_json"]
            if row["content_digest"] != content_digest:
                disposition = ReservationDisposition.CONTENT_CONFLICT
                outcome = ProcessingOutcome.SECURITY_REVIEW.value
                reason_codes = set(json.loads(reason_codes_json))
                reason_codes.add("MESSAGE_ID_CONTENT_CONFLICT")
                reason_codes_json = _canonical_json(sorted(reason_codes))
                connection.execute(
                    """
                    INSERT INTO message_content_conflicts (conflict_id, job_id, observed_digest, observed_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (str(uuid.uuid4()), row["job_id"], content_digest, now),
                )
            connection.execute(
                """
                UPDATE workflow_jobs
                SET delivery_count = delivery_count + 1, outcome = ?, reason_codes_json = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (outcome, reason_codes_json, now, row["job_id"]),
            )
            refreshed = connection.execute("SELECT * FROM workflow_jobs WHERE job_id = ?", (row["job_id"],)).fetchone()
            assert refreshed is not None
            return MessageReservation(disposition=disposition, job=self._job_from_row(refreshed))

    def get_job(self, trace_id: str) -> WorkflowJob | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM workflow_jobs WHERE trace_id = ?", (trace_id,)).fetchone()
        return None if row is None else self._job_from_row(row)

    def get_job_by_idempotency_key(self, idempotency_key: str) -> WorkflowJob | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM workflow_jobs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return None if row is None else self._job_from_row(row)

    def transition_job(
        self,
        trace_id: str,
        outcome: ProcessingOutcome,
        *,
        expected_outcome: ProcessingOutcome | None = None,
        reason_codes: tuple[str, ...] = (),
        order_id: str | None = None,
    ) -> WorkflowJob:
        now = _utc_now().isoformat()
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM workflow_jobs WHERE trace_id = ?", (trace_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown trace_id {trace_id}")
            current = ProcessingOutcome(row["outcome"])
            if expected_outcome is not None and current is not expected_outcome:
                raise StateConflictError(f"expected {expected_outcome.value}, found {current.value}")
            if current is ProcessingOutcome.ERP_CREATED and outcome is not ProcessingOutcome.ERP_CREATED:
                raise StateConflictError("ERP_CREATED is terminal")
            connection.execute(
                """
                UPDATE workflow_jobs
                SET outcome = ?, reason_codes_json = ?, order_id = COALESCE(?, order_id), updated_at = ?
                WHERE trace_id = ?
                """,
                (outcome.value, _canonical_json(reason_codes), order_id, now, trace_id),
            )
            refreshed = connection.execute("SELECT * FROM workflow_jobs WHERE trace_id = ?", (trace_id,)).fetchone()
            assert refreshed is not None
            return self._job_from_row(refreshed)

    def record_result(self, result: ProcessingResult) -> WorkflowJob:
        return self.transition_job(
            result.trace_id,
            result.outcome,
            reason_codes=result.reason_codes,
            order_id=result.order_id,
        )

    def append_review_revision(
        self,
        revision: ReviewRevision,
        *,
        expected_previous_version: int | None,
    ) -> ReviewRevision:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT MAX(version) AS version FROM review_revisions WHERE review_id = ?",
                (revision.review_id,),
            ).fetchone()
            latest = row["version"] if row is not None else None
            if latest != expected_previous_version:
                raise StateConflictError(f"expected review version {expected_previous_version}, found {latest}")
            if revision.version != (latest or 0) + 1:
                raise StateConflictError(f"revision.version must be {(latest or 0) + 1}")
            connection.execute(
                """
                INSERT INTO review_revisions (
                    review_id, trace_id, version, state, actor_json, before_json,
                    after_json, evidence_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision.review_id,
                    revision.trace_id,
                    revision.version,
                    revision.state.value,
                    revision.actor.model_dump_json(),
                    _canonical_json(revision.before),
                    _canonical_json(revision.after),
                    _canonical_json([item.model_dump(mode="json") for item in revision.evidence]),
                    revision.created_at.isoformat(),
                ),
            )
        return revision

    def review_revisions(self, review_id: str) -> list[ReviewRevision]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM review_revisions WHERE review_id = ? ORDER BY version",
                (review_id,),
            ).fetchall()
        return [
            ReviewRevision.model_validate(
                {
                    "review_id": row["review_id"],
                    "trace_id": row["trace_id"],
                    "version": row["version"],
                    "state": row["state"],
                    "actor": json.loads(row["actor_json"]),
                    "before": json.loads(row["before_json"]),
                    "after": json.loads(row["after_json"]),
                    "evidence": json.loads(row["evidence_json"]),
                    "created_at": datetime.fromisoformat(row["created_at"]),
                },
                strict=False,
            )
            for row in rows
        ]

    def job_count(self) -> int:
        with self._lock:
            return int(self._connection.execute("SELECT COUNT(*) FROM workflow_jobs").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
