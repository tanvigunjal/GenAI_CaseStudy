from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from passenger_forecast.evidence import (
    EXPECTED_DATASET_SHA256,
    EvidenceError,
    export_evidence_manifest,
    verify_evidence_manifest,
)


def _evaluation() -> dict[str, object]:
    counts = {str(station): (41 if station in (1, 8, 10) else 42) for station in range(1, 11)}
    return {
        "split": {
            "train_start": "2023-01-01",
            "train_end": "2026-01-26",
            "test_start": "2026-01-27",
            "test_end": "2026-02-02",
            "train_rows": 67_320,
            "test_rows": 420,
        },
        "coverage": {"prediction_count": 420, "scoreable_count": 417},
        "official_metrics": {"overall_rmse": 12.5},
        "per_station_results": [
            {"station_id": station, "sample_count": count, "rmse": 10.0 + int(station)}
            for station, count in counts.items()
        ],
    }


def _predictions() -> pd.DataFrame:
    timestamps = pd.date_range("2026-01-27 07:00", periods=42, freq="4h", tz="Europe/Berlin")
    return pd.DataFrame(
        [
            {
                "station_id": str(station),
                "target_timestamp": timestamp,
                "forecast_continuous": float(station + index),
            }
            for station in range(1, 11)
            for index, timestamp in enumerate(timestamps)
        ]
    )


def _export(repository: Path) -> Path:
    source = repository / "src/model.py"
    config = repository / "config/model.json"
    lock = repository / "uv.lock"
    model = repository / "evidence/model.bin"
    schema = repository / "evidence/feature_schema.json"
    predictions = repository / "evidence/predictions.csv"
    run_log = repository / "evidence/evaluate.jsonl"
    evaluation_path = repository / "evidence/evaluation_manifest.json"
    for path in (source, config, lock, model, schema, predictions, run_log, evaluation_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("MODEL = 'v1'\n", encoding="utf-8")
    config.write_text('{"seed": 20260808}\n', encoding="utf-8")
    lock.write_text("version = 1\n", encoding="utf-8")
    model.write_bytes(b"compact-model")
    schema.write_text('{"version": "1"}\n', encoding="utf-8")
    _predictions().to_csv(predictions, index=False)
    run_log.write_text('{"event":"evaluation_complete"}\n', encoding="utf-8")
    evaluation = _evaluation()
    evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")
    manifest = repository / "evidence/evidence.json"
    export_evidence_manifest(
        output_path=manifest,
        repository_root=repository,
        source_revision="a" * 40,
        source_files=[source],
        dependency_lock=lock,
        configuration_files=[config],
        artifact_files={
            "model": model,
            "feature_schema": schema,
            "predictions": predictions,
            "run_log": run_log,
            "evaluation_manifest": evaluation_path,
        },
        dataset_fingerprints=EXPECTED_DATASET_SHA256,
        split=evaluation["split"],
        evaluation=evaluation,
        host_profile={"hostname": "test-host", "cpu_cores": 8, "ram_gib": 15},
        resource_limits={"cpu_threads": 6, "max_rss_gib": 8, "max_tuning_seconds": 7200},
        resource_usage={"elapsed_seconds": 120, "peak_rss_gib": 4.2},
        tabpfn={"disposition": "skipped_resource_and_fit_gate"},
        model_candidates=[],
        ablations=[],
    )
    return manifest


def test_exported_manifest_verifies(tmp_path: Path) -> None:
    manifest = _export(tmp_path)
    verified = verify_evidence_manifest(
        manifest, repository_root=tmp_path, verify_source_revision=False
    )
    assert verified["evaluation"]["coverage"] == {
        "prediction_count": 420,
        "scoreable_count": 417,
    }


def test_source_or_artifact_change_fails_closed(tmp_path: Path) -> None:
    manifest = _export(tmp_path)
    (tmp_path / "src/model.py").write_text("MODEL = 'tampered'\n", encoding="utf-8")
    with pytest.raises(EvidenceError, match="hash verification failed"):
        verify_evidence_manifest(manifest, repository_root=tmp_path, verify_source_revision=False)


def test_manifest_change_fails_sidecar_before_parsing(tmp_path: Path) -> None:
    manifest = _export(tmp_path)
    manifest.write_text(manifest.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(EvidenceError, match="checksum verification failed"):
        verify_evidence_manifest(manifest, repository_root=tmp_path, verify_source_revision=False)


def test_wrong_official_coverage_cannot_be_exported(tmp_path: Path) -> None:
    evaluation = _evaluation()
    evaluation["coverage"] = {"prediction_count": 419, "scoreable_count": 417}
    with pytest.raises(EvidenceError, match="expected 420/417"):
        from passenger_forecast.evidence import validate_evaluation_contract

        validate_evaluation_contract(evaluation)
