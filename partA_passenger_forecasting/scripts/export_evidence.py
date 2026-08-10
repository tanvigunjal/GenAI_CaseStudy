#!/usr/bin/env python3
"""Export the pulled remote run as a checksummed immutable evidence manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from passenger_forecast.evidence import EXPECTED_DATASET_SHA256, export_evidence_manifest


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--feature-schema", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--run-log", type=Path, required=True)
    parser.add_argument("--dependency-lock", type=Path, required=True)
    parser.add_argument("--configuration-file", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    repository_root = args.repository_root.resolve()
    evaluation = _json(args.evaluation)
    source_files = sorted((repository_root / "partA_passenger_forecasting/src").rglob("*.py"))
    split = evaluation.get("split")
    if not isinstance(split, dict):
        split = {
            "train_start": evaluation.get("train_start_date"),
            "train_end": evaluation.get("train_end_date"),
            "test_start": evaluation.get("test_start_date"),
            "test_end": evaluation.get("test_end_date"),
            "train_rows": evaluation.get("train_rows", 67_320),
            "test_rows": evaluation.get("forecast_rows"),
        }
    export_evidence_manifest(
        output_path=args.output,
        repository_root=repository_root,
        source_revision=args.source_revision,
        source_files=source_files,
        dependency_lock=args.dependency_lock,
        configuration_files=args.configuration_file,
        artifact_files={
            "model": args.model,
            "feature_schema": args.feature_schema,
            "predictions": args.predictions,
            "run_log": args.run_log,
            "evaluation_manifest": args.evaluation,
        },
        dataset_fingerprints=EXPECTED_DATASET_SHA256,
        split=split,
        evaluation=evaluation,
        host_profile=evaluation["host_profile"],
        resource_limits=evaluation.get(
            "resource_limits",
            {"cpu_threads": 6, "max_rss_gib": 8, "max_tuning_seconds": 7200},
        ),
        resource_usage=evaluation["resource_usage"],
        tabpfn=evaluation.get(
            "tabpfn", {"disposition": "skipped_resource_and_fit_gate", "version": "v2"}
        ),
        model_candidates=evaluation.get(
            "model_candidates", evaluation.get("candidate_results", [])
        ),
        ablations=evaluation.get("ablations", []),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
