"""Public schema exports plus compatibility models for the original notebook."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from order_pipeline.domain import (
    Actor,
    ActorRole,
    AttachmentKind,
    AttachmentRef,
    AuthenticationVerdict,
    CanonicalOrderLine,
    FieldEvidence,
    InboundEmail,
    IssueSeverity,
    OrderLineProposal,
    OrderProposal,
    ProcessingOutcome,
    ProcessingResult,
    ReviewRevision,
    ReviewState,
    SourceKind,
    SourceRef,
    ValidatedOrderCommand,
    ValidationIssue,
)


class ExtractedOrderLine(BaseModel):
    raw_description: str = ""
    sku: str | None = None
    quantity: float | None = None
    unit_price: float | None = None


class ExtractedOrder(BaseModel):
    customer_name: str | None = None
    customer_email: str | None = None
    company: str | None = None
    lines: list[ExtractedOrderLine] = Field(default_factory=list)
    currency: str | None = None
    requested_delivery_date: str | None = None
    notes: str | None = None
    llm_confidence: float = 0.0
    ambiguous_fields: list[str] = Field(default_factory=list)
    extraction_warnings: list[str] = Field(default_factory=list)

    @field_validator("llm_confidence")
    @classmethod
    def clamp_confidence(cls, value: float) -> float:
        return max(0.0, min(1.0, float(value)))


class LegacyValidationIssue(BaseModel):
    rule: str
    severity: str
    message: str


class ValidationResult(BaseModel):
    all_passed: bool
    issues: list[LegacyValidationIssue] = Field(default_factory=list)


class OrderDecision(StrEnum):
    AUTO_APPROVED = "AUTO_APPROVED"
    ESCALATED = "ESCALATED"
    BLOCKED_SECURITY = "BLOCKED_SECURITY"


@dataclass
class Email:
    email_id: str
    sender: str
    subject: str
    body: str
    attachments: list[str] = field(default_factory=list)
    received_at: str = ""


__all__ = [
    "Actor",
    "ActorRole",
    "AttachmentKind",
    "AttachmentRef",
    "AuthenticationVerdict",
    "CanonicalOrderLine",
    "Email",
    "ExtractedOrder",
    "ExtractedOrderLine",
    "FieldEvidence",
    "InboundEmail",
    "IssueSeverity",
    "LegacyValidationIssue",
    "OrderDecision",
    "OrderLineProposal",
    "OrderProposal",
    "ProcessingOutcome",
    "ProcessingResult",
    "ReviewRevision",
    "ReviewState",
    "SourceKind",
    "SourceRef",
    "ValidatedOrderCommand",
    "ValidationIssue",
    "ValidationResult",
]
