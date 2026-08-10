from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from order_pipeline.audit import AuditPersistenceError, AuditSink, AuditStatus
from order_pipeline.config import ModelMode, RuntimeEnvironment, Settings, WriteMode
from order_pipeline.domain import (
    Actor,
    ActorRole,
    AttachmentKind,
    AttachmentRef,
    AuthenticationVerdict,
    CanonicalOrderLine,
    InboundEmail,
    ProcessingOutcome,
    ReviewRevision,
    ReviewState,
    ValidatedOrderCommand,
)
from order_pipeline.state import ReservationDisposition, StateConflictError, StateStore, derive_idempotency_key

DIGEST_A = hashlib.sha256(b"a").hexdigest()
DIGEST_B = hashlib.sha256(b"b").hexdigest()


def inbound_email(*, body: str = "Please order 2 beams") -> InboundEmail:
    return InboundEmail(
        mailbox_id="orders-eu",
        provider_message_id="provider-123",
        envelope_sender="buyer@example.test",
        subject="Order",
        body=body,
        received_at=datetime(2026, 8, 8, 12, tzinfo=UTC),
        webhook_verified=True,
        spf=AuthenticationVerdict.PASS,
        dkim=AuthenticationVerdict.PASS,
        dmarc=AuthenticationVerdict.PASS,
        attachments=(),
    )


def test_replay_shadow_defaults_are_keyless_and_workspace_scoped(tmp_path):
    settings = Settings(workspace_dir=tmp_path)

    assert settings.model_mode is ModelMode.REPLAY
    assert settings.write_mode is WriteMode.SHADOW
    assert settings.google_api_key is None
    assert settings.writes_enabled is False
    assert settings.database_path == tmp_path / "workflow.sqlite3"
    assert settings.version_manifest()["model_versions"] == {"provider": "replay", "transcript": "replay-v1"}


def test_live_mode_requires_credentials_and_explicit_models():
    with pytest.raises(ValidationError, match="GOOGLE_API_KEY"):
        Settings(model_mode=ModelMode.LIVE, google_api_key=None)

    with pytest.raises(ValidationError, match="GEMINI_EXTRACTION_MODEL"):
        Settings(
            model_mode=ModelMode.LIVE,
            google_api_key="synthetic-test-key",
            gemini_extraction_model=None,
        )


def test_auto_write_is_limited_to_local_sandbox_and_honors_kill_switch():
    with pytest.raises(ValidationError, match="local_sandbox"):
        Settings(write_mode=WriteMode.AUTO, runtime_environment=RuntimeEnvironment.PRODUCTION)

    settings = Settings(write_mode=WriteMode.AUTO, kill_switch=True)
    assert settings.writes_enabled is False


def test_core_contracts_are_strict_and_forbid_caller_paths():
    with pytest.raises(ValidationError, match="storage_ref"):
        AttachmentRef(
            attachment_id="att-1",
            original_filename="order.pdf",
            detected_kind=AttachmentKind.PDF,
            detected_content_type="application/pdf",
            byte_size=10,
            digest=DIGEST_A,
            storage_ref="../customer/order.pdf",
        )

    with pytest.raises(ValidationError):
        inbound_email().model_copy(update={"unexpected": "field"}).model_validate(
            {**inbound_email().model_dump(), "unexpected": "field"}
        )


def test_validated_command_is_frozen_and_total_is_deterministic():
    line = CanonicalOrderLine(
        sku="STL-BEAM-200",
        quantity=2,
        authoritative_unit_price=Decimal("184.50"),
    )
    command = ValidatedOrderCommand(
        customer_id="CUST-1001",
        lines=(line,),
        total=Decimal("369.00"),
        catalog_version="catalog-v1",
        input_digest=DIGEST_A,
        policy_version="policy-v1",
        idempotency_key=DIGEST_B,
    )

    assert command.currency == "EUR"
    with pytest.raises(ValidationError):
        command.total = Decimal("1")
    with pytest.raises(ValidationError, match="canonical line total"):
        ValidatedOrderCommand(
            customer_id="CUST-1001",
            lines=(line,),
            total=Decimal("1.00"),
            catalog_version="catalog-v1",
            input_digest=DIGEST_A,
            policy_version="policy-v1",
            idempotency_key=DIGEST_B,
        )


def test_sequential_duplicate_returns_one_job_and_stable_trace(tmp_path):
    with StateStore(tmp_path / "state.sqlite3") as store:
        first = store.reserve_message(inbound_email())
        second = store.reserve_message(inbound_email())

        assert first.disposition is ReservationDisposition.NEW
        assert second.disposition is ReservationDisposition.DUPLICATE
        assert second.job.trace_id == first.job.trace_id
        assert second.job.idempotency_key == derive_idempotency_key("orders-eu", "provider-123")
        assert second.job.delivery_count == 2
        assert store.job_count() == 1


def test_message_id_reuse_with_changed_content_routes_same_trace_to_security_review(tmp_path):
    with StateStore(tmp_path / "state.sqlite3") as store:
        first = store.reserve_message(inbound_email())
        collision = store.reserve_message(inbound_email(body="Different content"))

        assert collision.disposition is ReservationDisposition.CONTENT_CONFLICT
        assert collision.job.trace_id == first.job.trace_id
        assert collision.job.outcome is ProcessingOutcome.SECURITY_REVIEW
        assert collision.job.reason_codes == ("MESSAGE_ID_CONTENT_CONFLICT",)
        assert store.job_count() == 1


def test_duplicate_is_stable_after_restart(tmp_path):
    db_path = tmp_path / "state.sqlite3"
    first_store = StateStore(db_path)
    first = first_store.reserve_message(inbound_email())
    first_store.close()

    with StateStore(db_path) as restarted:
        duplicate = restarted.reserve_message(inbound_email())
        assert duplicate.job.job_id == first.job.job_id
        assert duplicate.job.trace_id == first.job.trace_id
        assert duplicate.disposition is ReservationDisposition.DUPLICATE


def test_concurrent_duplicate_reservation_creates_one_job(tmp_path):
    db_path = tmp_path / "state.sqlite3"

    def reserve_once(_: int):
        with StateStore(db_path) as store:
            return store.reserve_message(inbound_email())

    with ThreadPoolExecutor(max_workers=8) as pool:
        reservations = list(pool.map(reserve_once, range(8)))

    assert sum(item.disposition is ReservationDisposition.NEW for item in reservations) == 1
    assert len({item.job.trace_id for item in reservations}) == 1
    with StateStore(db_path) as store:
        assert store.job_count() == 1
        assert store.reserve_message(inbound_email()).job.delivery_count == 9


def test_review_revisions_are_optimistic_and_append_only(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    reservation = store.reserve_message(inbound_email())
    revision = ReviewRevision(
        review_id="review-1",
        trace_id=reservation.job.trace_id,
        version=1,
        state=ReviewState.CLAIMED,
        actor=Actor(actor_id="reviewer-1", role=ActorRole.ORDER_REVIEWER, display_name="Synthetic Reviewer"),
        before={},
        after={"claimed": True},
        created_at=datetime.now(UTC),
    )
    store.append_review_revision(revision, expected_previous_version=None)

    with pytest.raises(StateConflictError):
        store.append_review_revision(revision, expected_previous_version=None)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store._connection.execute("UPDATE review_revisions SET state='REJECTED'")

    assert store.review_revisions("review-1") == [revision]
    store.close()


def test_audit_records_intent_before_outcome_and_verifies_hash_chain(tmp_path):
    with AuditSink(tmp_path / "audit.sqlite3") as audit:
        intent = audit.record_intent(
            "trace-1",
            "erp.create_order",
            input_digest=DIGEST_A,
            idempotency_key=DIGEST_B,
            payload={"customer_id": "CUST-1001"},
        )
        outcome = audit.record_outcome(
            "trace-1",
            intent.event_id,
            AuditStatus.SUCCESS,
            outcome="ERP_CREATED",
            payload={"order_id": "ORD-1"},
        )

        assert (intent.sequence, outcome.sequence) == (1, 2)
        assert outcome.intent_event_id == intent.event_id
        assert audit.verify_trace("trace-1") is True


def test_audit_payloads_are_redacted_and_mutations_fail(tmp_path):
    audit = AuditSink(tmp_path / "audit.sqlite3")
    event = audit.record_intent(
        "trace-redaction",
        "model.invoke",
        payload={
            "api_key": "super-secret",
            "body": "raw email body",
            "sender": "buyer@example.test",
            "authorization": "Bearer abc.123",
            "safe": "catalog-v1",
        },
    )

    assert event.payload == {
        "api_key": "[REDACTED]",
        "body": "[REDACTED]",
        "sender": "[REDACTED_EMAIL]",
        "authorization": "[REDACTED]",
        "safe": "catalog-v1",
    }
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        audit._connection.execute("DELETE FROM audit_events")
    assert audit.verify_trace("trace-redaction") is True
    audit.close()


def test_audit_persistence_failure_is_explicit_for_fail_closed_callers(tmp_path):
    audit = AuditSink(tmp_path / "audit.sqlite3")
    audit.close()

    with pytest.raises(AuditPersistenceError, match="could not be persisted"):
        audit.record_intent("trace-closed", "erp.create_order")
