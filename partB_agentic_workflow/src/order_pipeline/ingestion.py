"""Deterministic transport checks and bounded attachment ingestion."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .attachments import (
    AttachmentLimits,
    AttachmentScanner,
    AttachmentStore,
    ParsedAttachment,
    scan_and_parse_attachment,
)
from .domain import AuthenticationVerdict, InboundEmail


@dataclass(frozen=True)
class IngestedContent:
    email: InboundEmail
    input_digest: str
    parsed_attachments: tuple[ParsedAttachment, ...]


def message_input_digest(email: InboundEmail) -> str:
    payload = {
        "mailbox_id": email.mailbox_id,
        "provider_message_id": email.provider_message_id,
        "envelope_sender": email.envelope_sender.lower(),
        "subject": email.subject,
        "body": email.body,
        "received_at": email.received_at.isoformat(),
        "webhook_verified": email.webhook_verified,
        "spf": email.spf.value,
        "dkim": email.dkim.value,
        "dmarc": email.dmarc.value,
        "attachments": [
            {
                "attachment_id": ref.attachment_id,
                "digest": ref.digest,
                "byte_size": ref.byte_size,
                "detected_kind": ref.detected_kind.value,
            }
            for ref in email.attachments
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def transport_security_issues(email: InboundEmail) -> tuple[str, ...]:
    issues: list[str] = []
    if not email.webhook_verified:
        issues.append("WEBHOOK_UNVERIFIED")
    verdicts = {"SPF": email.spf, "DKIM": email.dkim, "DMARC": email.dmarc}
    issues.extend(
        f"{name}_{verdict.value.upper()}"
        for name, verdict in verdicts.items()
        if verdict is not AuthenticationVerdict.PASS
    )
    return tuple(issues)


def ingest_attachments(
    email: InboundEmail,
    *,
    store: AttachmentStore,
    scanner: AttachmentScanner,
    limits: AttachmentLimits | None = None,
) -> IngestedContent:
    limits = limits or AttachmentLimits()
    if len(email.attachments) > limits.max_attachments:
        raise ValueError("ATTACHMENT_COUNT_LIMIT")
    if sum(reference.byte_size for reference in email.attachments) > limits.max_total_bytes:
        raise ValueError("TOTAL_ATTACHMENT_SIZE_LIMIT")
    parsed: list[ParsedAttachment] = []
    for reference in email.attachments:
        content = store.read(
            reference.storage_ref,
            expected_attachment_id=reference.attachment_id,
            expected_digest=reference.digest,
        )
        if len(content) != reference.byte_size:
            raise ValueError("ATTACHMENT_SIZE_MISMATCH")
        parsed.append(
            scan_and_parse_attachment(
                attachment_id=reference.attachment_id,
                content=content,
                original_filename=reference.original_filename,
                declared_media_type=reference.declared_content_type,
                expected_digest=reference.digest,
                scanner=scanner,
                limits=limits,
            )
        )
    return IngestedContent(
        email=email,
        input_digest=message_input_digest(email),
        parsed_attachments=tuple(parsed),
    )
