"""Production-shaped fixture mailbox boundary with server-owned attachments."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Sequence
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from .attachments import AttachmentLimits, AttachmentStore, inspect_attachment
from .domain import AttachmentKind, AttachmentRef, AuthenticationVerdict, InboundEmail


class FixtureAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    original_filename: str
    content: bytes
    declared_content_type: str | None = None


class FixtureMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mailbox_id: str
    provider_message_id: str
    envelope_sender: str
    subject: str
    body: str
    received_at: datetime
    webhook_verified: bool = True
    spf: AuthenticationVerdict = AuthenticationVerdict.PASS
    dkim: AuthenticationVerdict = AuthenticationVerdict.PASS
    dmarc: AuthenticationVerdict = AuthenticationVerdict.PASS
    attachments: tuple[FixtureAttachment, ...] = ()


class MailboxAdapter(Protocol):
    def messages(self) -> Iterator[InboundEmail]: ...


class FixtureMailboxAdapter:
    """Converts binary fixtures into the same opaque-reference contract as a webhook."""

    def __init__(
        self,
        fixtures: Sequence[FixtureMessage],
        *,
        attachment_store: AttachmentStore,
        limits: AttachmentLimits | None = None,
    ) -> None:
        self._fixtures = tuple(fixtures)
        self._store = attachment_store
        self._limits = limits or AttachmentLimits()

    def messages(self) -> Iterator[InboundEmail]:
        for fixture in self._fixtures:
            yield self.materialize(fixture)

    def materialize(self, fixture: FixtureMessage) -> InboundEmail:
        if len(fixture.attachments) > self._limits.max_attachments:
            raise ValueError("ATTACHMENT_COUNT_LIMIT")
        total_size = sum(len(attachment.content) for attachment in fixture.attachments)
        if total_size > self._limits.max_total_bytes:
            raise ValueError("TOTAL_ATTACHMENT_SIZE_LIMIT")
        refs: list[AttachmentRef] = []
        for ordinal, attachment in enumerate(fixture.attachments):
            extension, media_type = inspect_attachment(
                attachment.content,
                original_filename=attachment.original_filename,
                declared_media_type=attachment.declared_content_type,
                limits=self._limits,
            )
            attachment_id, storage_reference = self._store.put(
                mailbox_id=fixture.mailbox_id,
                provider_message_id=fixture.provider_message_id,
                ordinal=ordinal,
                content=attachment.content,
            )
            kind = AttachmentKind(extension.removeprefix("."))
            refs.append(
                AttachmentRef(
                    attachment_id=attachment_id,
                    original_filename=attachment.original_filename,
                    detected_kind=kind,
                    declared_content_type=attachment.declared_content_type,
                    detected_content_type=media_type,
                    byte_size=len(attachment.content),
                    digest=hashlib.sha256(attachment.content).hexdigest(),
                    storage_ref=storage_reference,
                )
            )
        return InboundEmail(
            mailbox_id=fixture.mailbox_id,
            provider_message_id=fixture.provider_message_id,
            envelope_sender=fixture.envelope_sender,
            subject=fixture.subject,
            body=fixture.body,
            received_at=fixture.received_at,
            webhook_verified=fixture.webhook_verified,
            spf=fixture.spf,
            dkim=fixture.dkim,
            dmarc=fixture.dmarc,
            attachments=tuple(refs),
        )
