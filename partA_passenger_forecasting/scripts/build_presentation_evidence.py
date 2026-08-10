#!/usr/bin/env python3
"""Convert verified remote artifacts into the presentation's strict evidence schema."""

from __future__ import annotations

import argparse
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--station-metadata", type=Path, required=True)
    parser.add_argument("--traffic-mapping", type=Path, required=True)
    parser.add_argument("--evidence-manifest", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _finite_or_none(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    numeric = float(value)
    if not math.isfinite(numeric):
        return None
    return numeric


def _best_candidates(evaluation: dict[str, Any]) -> list[dict[str, Any]]:
    raw = evaluation["model_candidates"]
    if not isinstance(raw, list):
        raise ValueError("model_candidates must be a list")
    completed = [
        item
        for item in raw
        if isinstance(item, dict)
        and item.get("completed") is True
        and item.get("mean_rmse") is not None
    ]
    chosen: list[dict[str, Any]] = []
    selected_id = str(evaluation["selected_candidate_id"])
    for family in ("lightgbm", "catboost", "tweedie"):
        family_rows = [item for item in completed if item.get("model_family") == family]
        if not family_rows:
            continue
        selected = next((item for item in family_rows if item["candidate_id"] == selected_id), None)
        best = selected or min(family_rows, key=lambda item: float(item["mean_rmse"]))
        chosen.append(
            {
                "model": family if family != "tweedie" else "tweedie_glm",
                "role": "challenger",
                "meanValidationRmse": float(best["mean_rmse"]),
                "worstStationRmse": float(best["worst_station_rmse"]),
                "latencyMs": float(best["inference_latency_ms"]),
                "artifactBytes": int(best["artifact_size_bytes"]),
                "selected": best["candidate_id"] == selected_id,
                "parameters": json.loads(str(best["parameters_json"])),
            }
        )
    return chosen


def _station_series(
    evaluation: dict[str, Any], predictions: pd.DataFrame, labels: dict[str, str]
) -> list[dict[str, Any]]:
    metrics = evaluation["per_station_results"]
    if not isinstance(metrics, list) or len(metrics) != 10:
        raise ValueError("verified evaluation must contain ten station results")
    by_station = {str(item["station_id"]): item for item in metrics}
    rows: list[dict[str, Any]] = []
    for station_id in sorted(by_station, key=int):
        metric = by_station[station_id]
        station = predictions.loc[predictions["station_id"].astype(str) == station_id].copy()
        station = station.sort_values("target_timestamp")
        series = []
        for item in station.itertuples(index=False):
            timestamp = pd.Timestamp(item.target_timestamp)
            series.append(
                {
                    "timestamp": timestamp.isoformat(),
                    "actual": _finite_or_none(item.actual),
                    "forecast": float(item.forecast_continuous),
                    "hour": int(timestamp.tz_convert("Europe/Berlin").hour),
                    "leadHours": float(item.lead_time_hours),
                }
            )
        rows.append(
            {
                "stationId": station_id,
                "stationLabel": labels[station_id],
                "sampleCount": int(metric["sample_count"]),
                "rmse": float(metric["rmse"]),
                "bias": float(metric["bias"]),
                "series": series,
            }
        )
    return rows


def main() -> int:
    args = _arguments()
    evaluation = _object(args.evaluation)
    integrity = _object(args.evidence_manifest)
    result = _object(args.template)
    if evaluation.get("status") != "verified":
        raise ValueError("evaluation is not verified")
    predictions = pd.read_parquet(args.predictions)
    if len(predictions) != 420 or int(predictions["actual"].notna().sum()) != 417:
        raise ValueError("prediction coverage is not 420/417")
    metadata = pd.read_csv(args.station_metadata)
    labels = {
        str(row.station_id): str(row.station_name).replace("_", " ")
        for row in metadata.itertuples(index=False)
    }
    source_revision = str(evaluation["source_revision"])
    artifacts = integrity["artifacts"]
    lock = integrity["dependency_lock"]
    configuration = integrity["configuration"]
    candidates = [
        {
            "model": "seasonal_naive",
            "role": "fixed baseline",
            "meanValidationRmse": None,
            "worstStationRmse": None,
            "latencyMs": None,
            "artifactBytes": None,
            "selected": False,
            "parameters": {"lag_days": 7, "fallback": "historical_median"},
        },
        {
            "model": "historical_median",
            "role": "fixed baseline",
            "meanValidationRmse": None,
            "worstStationRmse": None,
            "latencyMs": None,
            "artifactBytes": None,
            "selected": False,
            "parameters": {"grain": "station_weekday_hour"},
        },
        *_best_candidates(evaluation),
    ]
    result.update(
        {
            "status": "verified",
            "nonFinal": False,
            "generatedAt": datetime.now(UTC).isoformat(),
            "sourceRevision": source_revision,
            "claimBoundary": (
                "Model results and operational diagnostics are loaded from the verified remote "
                "evaluation and integrity manifests bound to the recorded source revision."
            ),
        }
    )
    result["evaluation"] = {
        "status": "verified",
        "champion": evaluation["champion_family"],
        "officialRmse": float(evaluation["overall_rmse"]),
        "seasonalNaiveRmse": float(evaluation["seasonal_naive_rmse"]),
        "historicalMedianRmse": float(evaluation["historical_median_rmse"]),
        "stationMetrics": _station_series(evaluation, predictions, labels),
        "folds": [
            {
                "fold": int(item["fold"]),
                "validationStart": item["validation_start"],
                "validationEnd": item["validation_end"],
                "rmse": float(item["rmse"]),
                "scoreableRows": None,
            }
            for item in evaluation["folds"]
        ],
        "candidates": candidates,
        "ablations": [
            {
                "featureFamily": item["feature_family"],
                "rmseWithout": float(item["overall_rmse"]),
                "deltaRmse": float(item["delta_rmse"]),
            }
            for item in evaluation["ablations"]
        ],
    }
    mapping = pd.read_csv(args.traffic_mapping)
    result["dataQuality"]["trafficMapping"] = [
        {
            "stationId": str(item.station_id),
            "sensorId": str(item.sensor_id),
            "distanceKm": float(item.sensor_distance_km),
        }
        for item in mapping.itertuples(index=False)
    ]
    result["artifacts"] = {
        "modelSha256": artifacts["model"]["sha256"],
        "featureSchemaSha256": artifacts["feature_schema"]["sha256"],
        "configSha256": configuration["configuration_sha256"],
        "dependencyLockSha256": lock["sha256"],
        "predictionsSha256": artifacts["predictions"]["sha256"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
