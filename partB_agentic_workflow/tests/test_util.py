from __future__ import annotations

import hashlib

import pytest

from order_pipeline.attachments import (
    AttachmentBoundaryError,
    AttachmentStore,
    normalize_untrusted_text,
    validate_original_filename,
)


def test_nfkc_normalization_is_deterministic():
    fullwidth_sku = "\uff33\uff34\uff2c\uff0d\uff22\uff25\uff21\uff2d\x00"
    assert normalize_untrusted_text(fullwidth_sku) == "STL-BEAM"


@pytest.mark.parametrize("filename", ["../order.pdf", "/tmp/order.pdf", "folder/order.xlsx", "..\\order.pptx"])
def test_filename_paths_are_rejected(filename: str):
    with pytest.raises(AttachmentBoundaryError, match="path"):
        validate_original_filename(filename)


def test_server_storage_is_content_bound_and_contained(tmp_path):
    store = AttachmentStore(tmp_path / "attachments")
    content = b"%PDF-synthetic"
    attachment_id, storage_ref = store.put(
        mailbox_id="orders", provider_message_id="message-1", ordinal=0, content=content
    )
    digest = hashlib.sha256(content).hexdigest()
    assert store.read(storage_ref, expected_attachment_id=attachment_id, expected_digest=digest) == content
    with pytest.raises(AttachmentBoundaryError, match="digest"):
        store.read(storage_ref, expected_attachment_id=attachment_id, expected_digest="0" * 64)


def test_storage_symlink_is_rejected_before_resolution(tmp_path):
    root = tmp_path / "attachments"
    store = AttachmentStore(root)
    message_key = "a" * 64
    attachment_id = "b" * 64
    directory = root / message_key
    directory.mkdir()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-outside")
    (directory / attachment_id).symlink_to(outside)
    with pytest.raises(AttachmentBoundaryError, match="symlink"):
        store.read(
            f"{message_key}/{attachment_id}",
            expected_attachment_id=attachment_id,
            expected_digest=hashlib.sha256(outside.read_bytes()).hexdigest(),
        )
