from __future__ import annotations

import hashlib
from decimal import Decimal

import pytest
from conftest import make_email
from fastapi.testclient import TestClient

from order_pipeline.audit import AuditSink
from order_pipeline.config import Settings
from order_pipeline.domain import (
    Actor,
    ActorRole,
    IssueSeverity,
    ValidationIssue,
)
from order_pipeline.erp_client import RestERPClient
from order_pipeline.erp_sandbox import SandboxDatabase, create_erp_app
from order_pipeline.review import ReviewActionError, ReviewService
from order_pipeline.state import StateConflictError, StateStore
from order_pipeline.validation import OrderPolicy


def test_order_review_requires_claim_correction_revalidation_and_write(tmp_path, clean_proposal, body_evidence):
    state = StateStore(tmp_path / "state.sqlite3")
    audit = AuditSink(tmp_path / "audit.sqlite3", versions=Settings().version_manifest())
    reservation = state.reserve_message(make_email())
    service = ReviewService(state)
    reviewer = Actor(actor_id="reviewer-1", role=ActorRole.ORDER_REVIEWER, display_name="Synthetic Reviewer")
    issue = ValidationIssue(
        code="UNRESOLVED_AMBIGUITY",
        severity=IssueSeverity.ERROR,
        explanation="Quantity is ambiguous.",
    )
    ambiguous = clean_proposal.model_copy(update={"ambiguities": ("quantity",)})
    pending = service.create_order_review(
        trace_id=reservation.job.trace_id,
        proposal=ambiguous,
        issues=(issue,),
        authenticated_customer_id="CUST-1001",
        input_digest=hashlib.sha256(b"anchored-input").hexdigest(),
        review_id="r1",
    )

    with pytest.raises(ReviewActionError, match="Correction"):
        service.revalidate(
            "r1",
            actor=reviewer,
            expected_version=pending.version,
            policy=None,  # type: ignore[arg-type]
        )
    claimed = service.claim("r1", actor=reviewer, expected_version=1)
    with pytest.raises(StateConflictError):
        service.claim("r1", actor=reviewer, expected_version=1)
    with pytest.raises(ReviewActionError, match="evidence"):
        service.correct(
            "r1", actor=reviewer, expected_version=claimed.version, corrected_proposal=clean_proposal, evidence=()
        )
    changed = clean_proposal.model_copy(
        update={"lines": (clean_proposal.lines[0].model_copy(update={"quantity": Decimal("9")}),)}
    )
    corrected = service.correct(
        "r1",
        actor=reviewer,
        expected_version=claimed.version,
        corrected_proposal=changed,
        evidence=(body_evidence,),
    )

    database = SandboxDatabase(tmp_path / "erp.sqlite3")
    with TestClient(create_erp_app(database)) as http:
        policy = OrderPolicy(RestERPClient(http).for_customer("CUST-1001"))
        revalidated, decision = service.revalidate(
            "r1",
            actor=reviewer,
            expected_version=corrected.version,
            policy=policy,
        )
    assert decision.command is not None
    assert decision.command.customer_id == "CUST-1001"
    assert decision.command.input_digest == hashlib.sha256(b"anchored-input").hexdigest()
    assert decision.command.idempotency_key == reservation.job.idempotency_key
    with pytest.raises(ReviewActionError, match="ERP creation"):
        service.approve_after_write("r1", actor=reviewer, expected_version=revalidated.version, audit=audit)
    with TestClient(create_erp_app(database)) as http:
        erp = RestERPClient(http).for_customer("CUST-1001")
        erp_created, order = service.write_revalidated(
            "r1",
            actor=reviewer,
            expected_version=revalidated.version,
            erp=erp,
            audit=audit,
        )
    assert order["order_id"] == erp_created.after["order_id"]
    assert not hasattr(service, "mark_erp_created")
    assert not hasattr(service, "command_for_write")
    unrelated_audit = AuditSink(tmp_path / "unrelated-audit.sqlite3", versions=Settings().version_manifest())
    with pytest.raises(ReviewActionError, match="verified ERP receipt"):
        service.approve_after_write("r1", actor=reviewer, expected_version=erp_created.version, audit=unrelated_audit)
    unrelated_audit.close()
    approved = service.approve_after_write("r1", actor=reviewer, expected_version=erp_created.version, audit=audit)
    assert approved.state.value == "APPROVED"
    assert len(state.review_revisions("r1")) == 7
    audit.close()
    state.close()


def test_security_review_is_role_scoped_and_cannot_be_corrected(tmp_path, clean_proposal, body_evidence):
    state = StateStore(tmp_path / "state.sqlite3")
    reservation = state.reserve_message(make_email())
    service = ReviewService(state)
    order_reviewer = Actor(actor_id="order", role=ActorRole.ORDER_REVIEWER, display_name="Order Reviewer")
    security_reviewer = Actor(actor_id="security", role=ActorRole.SECURITY_REVIEWER, display_name="Security Reviewer")
    service.create_security_review(trace_id=reservation.job.trace_id, reason_codes=("INJECTION",), review_id="s1")
    with pytest.raises(ReviewActionError, match="SECURITY_REVIEWER"):
        service.claim("s1", actor=order_reviewer, expected_version=1)
    claimed = service.claim("s1", actor=security_reviewer, expected_version=1)
    with pytest.raises(ReviewActionError, match="order review"):
        service.correct(
            "s1",
            actor=order_reviewer,
            expected_version=claimed.version,
            corrected_proposal=clean_proposal,
            evidence=(body_evidence,),
        )
    rejected = service.reject("s1", actor=security_reviewer, expected_version=claimed.version, reason="quarantine")
    assert rejected.state.value == "REJECTED"
    state.close()
