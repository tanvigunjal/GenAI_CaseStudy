"""Strict domain contracts separating untrusted proposals from write commands."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
Currency = Literal["EUR"]


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", validate_assignment=True)


class FrozenStrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class AuthenticationVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class AttachmentKind(StrEnum):
    PDF = "pdf"
    PPTX = "pptx"
    XLSX = "xlsx"


class SourceKind(StrEnum):
    SUBJECT = "subject"
    BODY = "body"
    ATTACHMENT = "attachment"


class IssueSeverity(StrEnum):
    WARNING = "warning"
    ERROR = "error"
    SECURITY = "security"


class ProcessingOutcome(StrEnum):
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    SECURITY_REVIEW = "SECURITY_REVIEW"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    SHADOW_APPROVED = "SHADOW_APPROVED"
    ERP_CREATED = "ERP_CREATED"
    FAILED = "FAILED"


class ActorRole(StrEnum):
    SYSTEM = "SYSTEM"
    ORDER_REVIEWER = "ORDER_REVIEWER"
    SECURITY_REVIEWER = "SECURITY_REVIEWER"


class ReviewState(StrEnum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    CORRECTED = "CORRECTED"
    REVALIDATED = "REVALIDATED"
    WRITE_PENDING = "WRITE_PENDING"
    ERP_CREATED = "ERP_CREATED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class AttachmentRef(FrozenStrictModel):
    attachment_id: NonEmpty
    original_filename: NonEmpty
    detected_kind: AttachmentKind
    declared_content_type: NonEmpty | None = None
    detected_content_type: NonEmpty
    byte_size: int = Field(ge=0)
    digest: Digest
    storage_ref: NonEmpty

    @field_validator("storage_ref")
    @classmethod
    def storage_ref_is_opaque(cls, value: str) -> str:
        if value.startswith(("/", "\\")) or ".." in value.replace("\\", "/").split("/"):
            raise ValueError("storage_ref must be an opaque server-issued reference, not a filesystem path")
        return value


class InboundEmail(FrozenStrictModel):
    mailbox_id: NonEmpty
    provider_message_id: NonEmpty
    envelope_sender: NonEmpty
    subject: str
    body: str
    received_at: datetime
    webhook_verified: bool
    spf: AuthenticationVerdict
    dkim: AuthenticationVerdict
    dmarc: AuthenticationVerdict
    attachments: tuple[AttachmentRef, ...] = ()

    @field_validator("received_at")
    @classmethod
    def received_at_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("received_at must be timezone-aware")
        return value


class SourceRef(FrozenStrictModel):
    kind: SourceKind
    attachment_id: NonEmpty | None = None
    page: int | None = Field(default=None, ge=1)
    slide: int | None = Field(default=None, ge=1)
    sheet: NonEmpty | None = None
    row: int | None = Field(default=None, ge=1)
    cell: NonEmpty | None = None

    @model_validator(mode="after")
    def locator_matches_source(self) -> SourceRef:
        attachment_locators = (self.page, self.slide, self.sheet, self.row, self.cell)
        if self.kind is SourceKind.ATTACHMENT and self.attachment_id is None:
            raise ValueError("attachment sources require attachment_id")
        if self.kind is not SourceKind.ATTACHMENT and (
            self.attachment_id or any(x is not None for x in attachment_locators)
        ):
            raise ValueError("subject/body sources cannot carry attachment locators")
        return self


class FieldEvidence(FrozenStrictModel):
    field: NonEmpty
    source: SourceRef
    excerpt: str = Field(max_length=1_000)


class OrderLineProposal(StrictModel):
    description: NonEmpty
    candidate_sku: NonEmpty | None = None
    quantity: Decimal | None = Field(default=None, allow_inf_nan=False)
    quoted_unit_price: Decimal | None = Field(default=None, allow_inf_nan=False)
    evidence: tuple[FieldEvidence, ...] = ()


class OrderProposal(StrictModel):
    detected_language: NonEmpty
    lines: tuple[OrderLineProposal, ...] = ()
    requested_delivery_date: date | None = None
    notes: str | None = None
    warnings: tuple[NonEmpty, ...] = ()
    ambiguities: tuple[NonEmpty, ...] = ()
    provenance: tuple[FieldEvidence, ...] = ()
    model_confidence: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("1"), allow_inf_nan=False)


class CanonicalOrderLine(FrozenStrictModel):
    sku: NonEmpty
    quantity: int = Field(gt=0)
    authoritative_unit_price: Decimal = Field(gt=Decimal("0"), allow_inf_nan=False)


class ValidationIssue(FrozenStrictModel):
    code: NonEmpty
    severity: IssueSeverity
    explanation: NonEmpty
    field: NonEmpty | None = None


class ValidatedOrderCommand(FrozenStrictModel):
    customer_id: NonEmpty
    lines: tuple[CanonicalOrderLine, ...] = Field(min_length=1)
    currency: Currency = "EUR"
    total: Decimal = Field(gt=Decimal("0"), allow_inf_nan=False)
    catalog_version: NonEmpty
    input_digest: Digest
    policy_version: NonEmpty
    idempotency_key: Digest

    @model_validator(mode="after")
    def total_matches_lines(self) -> ValidatedOrderCommand:
        expected = sum(
            (line.authoritative_unit_price * line.quantity for line in self.lines),
            start=Decimal("0"),
        )
        if self.total != expected:
            raise ValueError(f"total must equal canonical line total {expected}")
        return self


class ProcessingResult(FrozenStrictModel):
    trace_id: NonEmpty
    outcome: ProcessingOutcome
    reason_codes: tuple[NonEmpty, ...] = ()
    order_id: NonEmpty | None = None
    deduplicated: bool = False


class Actor(FrozenStrictModel):
    actor_id: NonEmpty
    role: ActorRole
    display_name: NonEmpty


class ReviewRevision(FrozenStrictModel):
    review_id: NonEmpty
    trace_id: NonEmpty
    version: int = Field(ge=1)
    state: ReviewState
    actor: Actor
    before: dict[str, Any]
    after: dict[str, Any]
    evidence: tuple[FieldEvidence, ...] = ()
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def created_at_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value
