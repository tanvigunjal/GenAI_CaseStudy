from datetime import UTC, datetime

import pytest

from order_pipeline.evidence import build_evidence_manifest


def test_manifest_is_sanitized_and_records_control_invariants() -> None:
    outcome = {
        "trace_id": "random-trace-that-must-not-escape",
        "outcome": "SHADOW_APPROVED",
        "reason_codes": [],
        "order_id": "random-order-that-must-not-escape",
        "deduplicated": False,
    }
    manifest = build_evidence_manifest(
        source_commit_sha="a" * 40,
        demo={
            "shadow": {
                "scenario_outcomes": [outcome],
                "audit_event_count": 8,
                "audit_verified": True,
                "write_count": 0,
            },
            "auto": {
                "scenario_outcomes": [outcome, {**outcome, "deduplicated": True}],
                "audit_event_count": 10,
                "audit_verified": True,
                "write_count": 1,
            },
        },
        attachments={"pdf": {"outcome": "SHADOW_APPROVED", "write_count": 0, "locators": [{"page": 1}]}},
        failures={
            "injection": {
                "outcome": "SECURITY_REVIEW",
                "reason_codes": ["PROMPT_INJECTION"],
                "model_calls": 0,
                "write_count": 0,
            }
        },
        review={
            "states": ["PENDING", "APPROVED"],
            "correction_changes": ["lines"],
            "validated_total": "1845.00",
            "write_count": 1,
        },
        generated_at=datetime(2026, 8, 8, tzinfo=UTC),
        test_count=73,
        deterministic_core_branch_coverage=91,
    )

    serialized = str(manifest)
    assert "random-trace" not in serialized
    assert "random-order" not in serialized
    assert manifest["sourceCommitSha"] == "a" * 40
    assert manifest["quality"]["blockedPathWriteCount"] == 0


def test_manifest_requires_full_source_commit_sha() -> None:
    with pytest.raises(ValueError, match="40-character"):
        build_evidence_manifest(
            source_commit_sha="short",
            demo={},
            attachments={},
            failures={},
            review={},
            test_count=0,
            deterministic_core_branch_coverage=0,
        )
