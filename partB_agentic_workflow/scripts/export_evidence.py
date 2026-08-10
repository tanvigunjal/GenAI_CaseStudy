"""Execute the sanitized scenario bundle and publish canonical presentation evidence."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from order_pipeline.demo import (
    run_binary_attachment_examples,
    run_demo_scenarios,
    run_failure_examples,
    run_human_review_example,
)
from order_pipeline.evidence import build_evidence_manifest

ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-commit-sha", required=True)
    parser.add_argument("--test-count", required=True, type=int)
    parser.add_argument("--core-coverage", required=True, type=int)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workspace = ROOT / "workspace" / "evidence_run"
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)

    manifest = build_evidence_manifest(
        source_commit_sha=args.source_commit_sha,
        demo=run_demo_scenarios(workspace / "workflow", include_auto_example=True),
        attachments=run_binary_attachment_examples(workspace / "attachments"),
        failures=run_failure_examples(workspace / "failures"),
        review=run_human_review_example(workspace / "review"),
        test_count=args.test_count,
        deterministic_core_branch_coverage=args.core_coverage,
    )
    serialized = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    destinations = (
        ROOT / "presentation_evidence.json",
        ROOT / "presentation" / "public" / "presentation_evidence.json",
    )
    for destination in destinations:
        destination.write_text(serialized, encoding="utf-8")
    print(
        json.dumps(
            {"status": manifest["status"], "destinations": [str(path.relative_to(ROOT)) for path in destinations]}
        )
    )


if __name__ == "__main__":
    main()
