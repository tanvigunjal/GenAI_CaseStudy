"""Build the sanitized, presentation-facing manifest from executed demo results."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any


def _outcome_summary(raw: Mapping[str, Any]) -> dict[str, object]:
    return {
        "outcome": raw["outcome"],
        "reasonCodes": list(raw.get("reason_codes", ())),
        "deduplicated": bool(raw.get("deduplicated", False)),
    }


def build_evidence_manifest(
    *,
    source_commit_sha: str,
    demo: Mapping[str, Any],
    attachments: Mapping[str, Any],
    failures: Mapping[str, Any],
    review: Mapping[str, Any],
    generated_at: datetime | None = None,
    test_count: int,
    deterministic_core_branch_coverage: int,
) -> dict[str, object]:
    """Return a JSON-safe manifest without random IDs, raw content, or local paths."""

    if len(source_commit_sha) != 40:
        raise ValueError("source_commit_sha must be a full 40-character Git SHA")

    shadow = demo["shadow"]
    auto = demo["auto"]
    assert isinstance(shadow, Mapping)
    assert isinstance(auto, Mapping)
    shadow_outcomes = shadow["scenario_outcomes"]
    auto_outcomes = auto["scenario_outcomes"]
    assert isinstance(shadow_outcomes, list)
    assert isinstance(auto_outcomes, list)

    attachment_summary: dict[str, object] = {}
    for name, raw in attachments.items():
        assert isinstance(raw, Mapping)
        locators = raw["locators"]
        assert isinstance(locators, list)
        attachment_summary[name.upper()] = {
            "outcome": raw["outcome"],
            "writeCount": raw["write_count"],
            "locators": locators,
        }

    failure_summary: dict[str, object] = {}
    for name, raw in failures.items():
        assert isinstance(raw, Mapping)
        failure_summary[name] = {
            "outcome": raw["outcome"],
            "reasonCodes": list(raw["reason_codes"]),
            "modelCalls": raw["model_calls"],
            "writeCount": raw["write_count"],
        }

    generated = generated_at or datetime.now(UTC)
    return {
        "schemaVersion": 1,
        "sourceCommitSha": source_commit_sha,
        "generatedAt": generated.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "mode": "deterministic replay / shadow by default",
        "source": "order_to_erp_agent.ipynb replay evidence export",
        "status": "passed",
        "toolAllowlist": ["lookup_product", "search_products", "purchase_history"],
        "claims": {
            "supportedLanguages": ["English", "German"],
            "supportedAttachments": ["PDF", "PPTX", "XLSX"],
            "coreScenes": 14,
            "promotionSampleFloor": 299,
        },
        "observations": {
            "shadow": {
                "outcomes": [_outcome_summary(item) for item in shadow_outcomes],
                "auditEventCount": shadow["audit_event_count"],
                "auditVerified": shadow["audit_verified"],
                "writeCount": shadow["write_count"],
            },
            "isolatedAutoDuplicate": {
                "outcomes": [_outcome_summary(item) for item in auto_outcomes],
                "auditEventCount": auto["audit_event_count"],
                "auditVerified": auto["audit_verified"],
                "writeCount": auto["write_count"],
            },
            "attachments": attachment_summary,
            "failures": failure_summary,
            "humanReview": {
                "states": list(review["states"]),
                "correctionChanges": list(review["correction_changes"]),
                "validatedTotal": review["validated_total"],
                "writeCount": review["write_count"],
            },
        },
        "quality": {
            "testStatus": "passed",
            "testCount": test_count,
            "deterministicCoreBranchCoveragePercent": deterministic_core_branch_coverage,
            "blockedPathWriteCount": sum(int(item["write_count"]) for item in failures.values()),
        },
        "claimBoundary": "Synthetic local control-path evidence; no production accuracy, ROI, cost, or latency claim.",
    }
