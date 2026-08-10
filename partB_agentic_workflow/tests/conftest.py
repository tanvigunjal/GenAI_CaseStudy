from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from order_pipeline.domain import (
    AuthenticationVerdict,
    FieldEvidence,
    InboundEmail,
    OrderLineProposal,
    OrderProposal,
    SourceKind,
    SourceRef,
)


@pytest.fixture
def digest() -> str:
    return hashlib.sha256(b"synthetic-input").hexdigest()


@pytest.fixture
def body_evidence() -> FieldEvidence:
    return FieldEvidence(
        field="lines.0",
        source=SourceRef(kind=SourceKind.BODY),
        excerpt="10 x STL-BEAM-200 at EUR 184.50",
    )


@pytest.fixture
def clean_proposal(body_evidence: FieldEvidence) -> OrderProposal:
    return OrderProposal(
        detected_language="en",
        lines=(
            OrderLineProposal(
                description="Steel beam",
                candidate_sku="STL-BEAM-200",
                quantity=Decimal("10"),
                quoted_unit_price=Decimal("184.50"),
                evidence=(body_evidence,),
            ),
        ),
        provenance=(body_evidence,),
        model_confidence=Decimal("0.20"),
    )


def make_email(
    *, message_id: str = "provider-1", subject: str = "Order", body: str = "10 x STL-BEAM-200"
) -> InboundEmail:
    return InboundEmail(
        mailbox_id="orders-eu",
        provider_message_id=message_id,
        envelope_sender="anna.keller@nordbau.de",
        subject=subject,
        body=body,
        received_at=datetime(2026, 8, 8, 12, tzinfo=UTC),
        webhook_verified=True,
        spf=AuthenticationVerdict.PASS,
        dkim=AuthenticationVerdict.PASS,
        dmarc=AuthenticationVerdict.PASS,
        attachments=(),
    )
