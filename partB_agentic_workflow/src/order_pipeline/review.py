"""Role-scoped, append-only human review state machine."""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from .audit import AuditSink, AuditStatus
from .domain import (
    Actor,
    ActorRole,
    FieldEvidence,
    OrderProposal,
    ProcessingOutcome,
    ReviewRevision,
    ReviewState,
    ValidatedOrderCommand,
    ValidationIssue,
)
from .erp_client import ERPClient, ERPError
from .state import StateConflictError, StateStore
from .validation import OrderPolicy, PolicyDecision


class ReviewActionError(RuntimeError):
    pass


_SYSTEM_ACTOR = Actor(actor_id="workflow", role=ActorRole.SYSTEM, display_name="Workflow service")


def _json_proposal(value: dict[str, Any]) -> OrderProposal:
    return OrderProposal.model_validate_json(json.dumps(value))


def _changed_fields(before: Any, after: Any, prefix: str = "") -> list[str]:
    if isinstance(before, dict) and isinstance(after, dict):
        changed: list[str] = []
        for key in sorted(set(before) | set(after)):
            path = f"{prefix}.{key}" if prefix else key
            if key not in before or key not in after:
                changed.append(path)
            else:
                changed.extend(_changed_fields(before[key], after[key], path))
        return changed
    if before != after:
        return [prefix]
    return []


class ReviewService:
    def __init__(self, state: StateStore) -> None:
        self._state = state

    def _latest(self, review_id: str, *, expected_version: int) -> ReviewRevision:
        revisions = self._state.review_revisions(review_id)
        if not revisions:
            raise KeyError(review_id)
        latest = revisions[-1]
        if latest.version != expected_version:
            raise StateConflictError(f"expected review version {expected_version}, found {latest.version}")
        return latest

    def _append(
        self,
        latest: ReviewRevision,
        *,
        state: ReviewState,
        actor: Actor,
        before: dict[str, Any],
        after: dict[str, Any],
        evidence: Sequence[FieldEvidence] = (),
    ) -> ReviewRevision:
        revision = ReviewRevision(
            review_id=latest.review_id,
            trace_id=latest.trace_id,
            version=latest.version + 1,
            state=state,
            actor=actor,
            before=before,
            after=after,
            evidence=tuple(evidence),
            created_at=datetime.now(UTC),
        )
        return self._state.append_review_revision(revision, expected_previous_version=latest.version)

    def create_order_review(
        self,
        *,
        trace_id: str,
        proposal: OrderProposal,
        issues: Sequence[ValidationIssue],
        authenticated_customer_id: str,
        input_digest: str,
        review_id: str | None = None,
    ) -> ReviewRevision:
        review_id = review_id or f"review-{uuid.uuid4()}"
        revision = ReviewRevision(
            review_id=review_id,
            trace_id=trace_id,
            version=1,
            state=ReviewState.PENDING,
            actor=_SYSTEM_ACTOR,
            before={},
            after={
                "review_type": "order",
                "proposal": proposal.model_dump(mode="json"),
                "issues": [issue.model_dump(mode="json") for issue in issues],
                "authenticated_customer_id": authenticated_customer_id,
                "input_digest": input_digest,
            },
            evidence=(),
            created_at=datetime.now(UTC),
        )
        return self._state.append_review_revision(revision, expected_previous_version=None)

    def create_security_review(
        self,
        *,
        trace_id: str,
        reason_codes: Sequence[str],
        review_id: str | None = None,
    ) -> ReviewRevision:
        review_id = review_id or f"review-{uuid.uuid4()}"
        revision = ReviewRevision(
            review_id=review_id,
            trace_id=trace_id,
            version=1,
            state=ReviewState.PENDING,
            actor=_SYSTEM_ACTOR,
            before={},
            after={"review_type": "security", "reason_codes": list(reason_codes)},
            evidence=(),
            created_at=datetime.now(UTC),
        )
        return self._state.append_review_revision(revision, expected_previous_version=None)

    def create_processing_review(
        self,
        *,
        trace_id: str,
        reason_codes: Sequence[str],
        review_id: str | None = None,
    ) -> ReviewRevision:
        """Persist a non-correctable ingestion/model failure for manual disposition."""

        review_id = review_id or f"review-{uuid.uuid4()}"
        revision = ReviewRevision(
            review_id=review_id,
            trace_id=trace_id,
            version=1,
            state=ReviewState.PENDING,
            actor=_SYSTEM_ACTOR,
            before={},
            after={"review_type": "order", "reason_codes": list(reason_codes), "reprocessable": True},
            evidence=(),
            created_at=datetime.now(UTC),
        )
        return self._state.append_review_revision(revision, expected_previous_version=None)

    def claim(self, review_id: str, *, actor: Actor, expected_version: int) -> ReviewRevision:
        latest = self._latest(review_id, expected_version=expected_version)
        if latest.state is not ReviewState.PENDING:
            raise ReviewActionError("Only pending reviews can be claimed")
        review_type = str(latest.after.get("review_type"))
        required_role = ActorRole.SECURITY_REVIEWER if review_type == "security" else ActorRole.ORDER_REVIEWER
        if actor.role is not required_role:
            raise ReviewActionError(f"{required_role.value} role is required")
        after = {**latest.after, "claimed_by": actor.actor_id}
        return self._append(
            latest,
            state=ReviewState.CLAIMED,
            actor=actor,
            before=latest.after,
            after=after,
        )

    def correct(
        self,
        review_id: str,
        *,
        actor: Actor,
        expected_version: int,
        corrected_proposal: OrderProposal,
        evidence: Sequence[FieldEvidence],
    ) -> ReviewRevision:
        latest = self._latest(review_id, expected_version=expected_version)
        if latest.state is not ReviewState.CLAIMED or actor.role is not ActorRole.ORDER_REVIEWER:
            raise ReviewActionError("A claimed order review is required for correction")
        if latest.after.get("review_type") != "order" or latest.after.get("claimed_by") != actor.actor_id:
            raise ReviewActionError("Reviewer does not own this order review")
        previous_proposal = _json_proposal(latest.after["proposal"])
        if corrected_proposal.model_confidence != previous_proposal.model_confidence:
            raise ReviewActionError("Human corrections cannot rewrite model confidence")
        before_json = previous_proposal.model_dump(mode="json")
        after_json = corrected_proposal.model_dump(mode="json")
        changes = _changed_fields(before_json, after_json)
        if not changes:
            raise ReviewActionError("A correction must change at least one proposal field")
        if not evidence:
            raise ReviewActionError("Corrections require source evidence")
        if "ambiguities" in changes and not any(change != "ambiguities" for change in changes):
            raise ReviewActionError("Ambiguities cannot be cleared without a concrete field correction")
        after = {**latest.after, "proposal": after_json, "changes": changes}
        return self._append(
            latest,
            state=ReviewState.CORRECTED,
            actor=actor,
            before=latest.after,
            after=after,
            evidence=evidence,
        )

    def revalidate(
        self,
        review_id: str,
        *,
        actor: Actor,
        expected_version: int,
        policy: OrderPolicy,
    ) -> tuple[ReviewRevision, PolicyDecision]:
        latest = self._latest(review_id, expected_version=expected_version)
        if latest.state is not ReviewState.CORRECTED or actor.role is not ActorRole.ORDER_REVIEWER:
            raise ReviewActionError("Correction is required before revalidation")
        if latest.after.get("claimed_by") != actor.actor_id:
            raise ReviewActionError("Reviewer does not own this order review")
        revisions = self._state.review_revisions(review_id)
        anchor = revisions[0].after
        job = self._state.get_job(latest.trace_id)
        if job is None:
            raise ReviewActionError("Review is not anchored to a reserved workflow job")
        customer_id = anchor.get("authenticated_customer_id")
        input_digest = anchor.get("input_digest")
        if not isinstance(customer_id, str) or not isinstance(input_digest, str):
            raise ReviewActionError("Review authorization anchors are missing")
        proposal = _json_proposal(latest.after["proposal"])
        decision = policy.validate(
            proposal,
            customer_id=customer_id,
            input_digest=input_digest,
            idempotency_key=job.idempotency_key,
        )
        if not decision.approved or decision.command is None:
            raise ReviewActionError("Corrected order still fails deterministic validation")
        after = {**latest.after, "command": decision.command.model_dump(mode="json")}
        revision = self._append(
            latest,
            state=ReviewState.REVALIDATED,
            actor=actor,
            before=latest.after,
            after=after,
        )
        return revision, decision

    def mark_write_pending(self, review_id: str, *, actor: Actor, expected_version: int) -> ReviewRevision:
        latest = self._latest(review_id, expected_version=expected_version)
        if latest.state is not ReviewState.REVALIDATED or actor.role is not ActorRole.ORDER_REVIEWER:
            raise ReviewActionError("A revalidated order is required before writing")
        if latest.after.get("claimed_by") != actor.actor_id:
            raise ReviewActionError("Reviewer does not own this order review")
        return self._append(
            latest,
            state=ReviewState.WRITE_PENDING,
            actor=actor,
            before=latest.after,
            after=latest.after,
        )

    def _mark_verified_erp_created(
        self, latest: ReviewRevision, *, order_id: str, write_audit_event_id: str
    ) -> ReviewRevision:
        if latest.state is not ReviewState.WRITE_PENDING:
            raise ReviewActionError("Write-pending state is required")
        return self._append(
            latest,
            state=ReviewState.ERP_CREATED,
            actor=_SYSTEM_ACTOR,
            before=latest.after,
            after={
                **latest.after,
                "order_id": order_id,
                "write_audit_event_id": write_audit_event_id,
                "verified_receipt": True,
            },
        )

    def approve_after_write(
        self, review_id: str, *, actor: Actor, expected_version: int, audit: AuditSink
    ) -> ReviewRevision:
        latest = self._latest(review_id, expected_version=expected_version)
        if latest.state is not ReviewState.ERP_CREATED or actor.role is not ActorRole.ORDER_REVIEWER:
            raise ReviewActionError("ERP creation is required before approval")
        if latest.after.get("claimed_by") != actor.actor_id:
            raise ReviewActionError("Reviewer does not own this order review")
        audit_event_id = latest.after.get("write_audit_event_id")
        verified = any(
            event.event_id == audit_event_id
            and event.status is AuditStatus.SUCCESS
            and event.outcome == ProcessingOutcome.ERP_CREATED.value
            for event in audit.events_for_trace(latest.trace_id)
        )
        if not latest.after.get("verified_receipt") or not verified:
            raise ReviewActionError("Approval requires a verified ERP receipt and successful write audit")
        return self._append(
            latest,
            state=ReviewState.APPROVED,
            actor=actor,
            before=latest.after,
            after=latest.after,
        )

    def reject(self, review_id: str, *, actor: Actor, expected_version: int, reason: str) -> ReviewRevision:
        latest = self._latest(review_id, expected_version=expected_version)
        review_type = latest.after.get("review_type")
        required_role = ActorRole.SECURITY_REVIEWER if review_type == "security" else ActorRole.ORDER_REVIEWER
        if actor.role is not required_role or latest.state in {ReviewState.APPROVED, ReviewState.REJECTED}:
            raise ReviewActionError("Reviewer is not authorized to reject this item")
        if latest.state is not ReviewState.PENDING and latest.after.get("claimed_by") != actor.actor_id:
            raise ReviewActionError("Reviewer does not own this review")
        return self._append(
            latest,
            state=ReviewState.REJECTED,
            actor=actor,
            before=latest.after,
            after={**latest.after, "rejection_reason": reason},
        )

    def _command_for_write(self, review_id: str, *, expected_version: int) -> ValidatedOrderCommand:
        latest = self._latest(review_id, expected_version=expected_version)
        if latest.state is not ReviewState.WRITE_PENDING or "command" not in latest.after:
            raise ReviewActionError("A revalidated write-pending command is required")
        return ValidatedOrderCommand.model_validate(latest.after["command"], strict=False)

    def write_revalidated(
        self,
        review_id: str,
        *,
        actor: Actor,
        expected_version: int,
        erp: ERPClient,
        audit: AuditSink,
    ) -> tuple[ReviewRevision, dict[str, object]]:
        """Write only the stored command, then reconcile before minting ERP_CREATED."""

        latest = self._latest(review_id, expected_version=expected_version)
        if latest.state is ReviewState.REVALIDATED:
            latest = self.mark_write_pending(review_id, actor=actor, expected_version=expected_version)
        elif latest.state is not ReviewState.WRITE_PENDING:
            raise ReviewActionError("A revalidated or write-pending order is required")
        if latest.after.get("claimed_by") != actor.actor_id:
            raise ReviewActionError("Reviewer does not own this order review")
        command = self._command_for_write(review_id, expected_version=latest.version)
        intent = audit.record_intent(
            latest.trace_id,
            "review_erp_create_order",
            input_digest=command.input_digest,
            idempotency_key=command.idempotency_key,
            actor=actor,
            payload={"review_id": review_id, "line_count": len(command.lines)},
        )
        try:
            order = erp.create_validated_order(command)
            reconciled = erp.lookup_by_idempotency_key(command.idempotency_key)
            if reconciled is None or reconciled.get("order_id") != order.get("order_id"):
                raise ReviewActionError("ERP write receipt could not be reconciled")
        except (ERPError, ReviewActionError):
            audit.record_outcome(latest.trace_id, intent.event_id, AuditStatus.FAILURE, actor=actor)
            raise
        order_id = str(order["order_id"])
        outcome = audit.record_outcome(
            latest.trace_id,
            intent.event_id,
            AuditStatus.SUCCESS,
            actor=actor,
            outcome=ProcessingOutcome.ERP_CREATED.value,
            payload={"review_id": review_id, "order_id": order_id},
        )
        return (
            self._mark_verified_erp_created(
                latest,
                order_id=order_id,
                write_audit_event_id=outcome.event_id,
            ),
            order,
        )
