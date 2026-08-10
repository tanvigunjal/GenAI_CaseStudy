"""Command-line entrypoint for the passenger forecasting vertical slice."""

# ruff: noqa: B008

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import tomllib
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import typer

from passenger_forecast.backtest import (
    CandidateSpec,
    FrozenSelection,
    current_peak_rss_gib,
    require_remote_full_data_fit,
    rolling_folds,
    run_bounded_search,
    run_feature_ablations,
    tabpfn_feasibility_record,
)
from passenger_forecast.baselines import historical_median, seasonal_naive
from passenger_forecast.batch import (
    SourceState,
    build_batch_id,
    execute_batch,
)
from passenger_forecast.config import ForecastSettings, stable_hash
from passenger_forecast.contracts import CandidateResult
from passenger_forecast.data import (
    DatasetBundle,
    global_date_split,
    nearest_sensor_mapping,
    validate_datasets,
    verify_supplied_files,
)
from passenger_forecast.evidence import (
    EXPECTED_DATASET_SHA256,
    export_evidence_manifest,
    sha256_file,
    verify_evidence_manifest,
)
from passenger_forecast.features import CutoffFeatureBuilder
from passenger_forecast.logging import configure_logging, log_event
from passenger_forecast.metrics import evaluate_predictions
from passenger_forecast.models import create_model, load_model, save_model
from passenger_forecast.monitoring import monitor_forecasts

app = typer.Typer(
    name="forecast",
    help="Point-in-time-safe hourly passenger forecasting.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
LOGGER = logging.getLogger("passenger_forecast.cli")
OFFICIAL_ORIGIN = datetime(2026, 1, 26, 12, tzinfo=ZoneInfo("Europe/Berlin"))


def _json_default(value: object) -> object:
    if isinstance(value, datetime | Path):
        return value.isoformat() if isinstance(value, datetime) else value.as_posix()
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _atomic_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, sort_keys=True, indent=2, default=_json_default) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _settings(config: Path, data_dir: Path, output_dir: Path) -> ForecastSettings:
    if not config.is_file():
        raise FileNotFoundError(f"configuration file does not exist: {config}")
    with config.open("rb") as stream:
        values = tomllib.load(stream)
    values["raw_data_dir"] = data_dir
    values["output_dir"] = output_dir
    return ForecastSettings(**values)


@contextmanager
def _command_scope(name: str, settings: ForecastSettings) -> Iterator[None]:
    configure_logging(settings.log_level)
    started = datetime.now(UTC)
    log_event(
        LOGGER,
        "command_started",
        command=name,
        execution_target=settings.execution_target,
        config_hash=settings.stable_hash(),
    )
    try:
        yield
    except Exception as exc:
        log_event(
            LOGGER,
            "command_failed",
            command=name,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        typer.echo(
            json.dumps({"status": "failed", "command": name, "error": str(exc)}, sort_keys=True),
            err=True,
        )
        raise typer.Exit(code=1) from exc
    duration = (datetime.now(UTC) - started).total_seconds()
    log_event(LOGGER, "command_completed", command=name, duration_seconds=duration)


def _load_bundle(settings: ForecastSettings) -> DatasetBundle:
    verify_supplied_files(settings.raw_data_dir)
    return validate_datasets(
        pd.read_csv(settings.raw_data_dir / "station_metadata.csv"),
        pd.read_csv(settings.raw_data_dir / "timeseries_with_target.csv"),
        pd.read_csv(settings.raw_data_dir / "traffic_hourly.csv"),
        pd.read_csv(settings.raw_data_dir / "weather_hourly.csv"),
        supplied=True,
        timezone=settings.timezone,
    )


def _source_identity(bundle: DatasetBundle, data_dir: Path) -> dict[str, dict[str, Any]]:
    frames = {
        "station_metadata.csv": bundle.stations,
        "timeseries_with_target.csv": bundle.passengers,
        "traffic_hourly.csv": bundle.traffic,
        "weather_hourly.csv": bundle.weather,
    }
    result: dict[str, dict[str, Any]] = {}
    for name, frame in frames.items():
        watermark: str | None = None
        if "timestamp_utc" in frame and len(frame):
            watermark = pd.Timestamp(frame["timestamp_utc"].max()).isoformat()
        result[name] = {
            "digest": sha256_file(data_dir / name),
            "row_count": len(frame),
            "schema_version": "1.0.0",
            "watermark": watermark,
        }
    return result


def _source_revision(required: bool = True) -> str | None:
    revision = os.environ.get("SOURCE_REVISION")
    if revision:
        return revision
    try:
        found = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        found = ""
    if found:
        return found
    if required:
        raise RuntimeError("SOURCE_REVISION or a Git checkout is required")
    return None


def _host_profile() -> dict[str, Any]:
    memory_gib: float | None = None
    try:
        pages = int(os.sysconf("SC_PHYS_PAGES"))
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        memory_gib = pages * page_size / (1024**3)
    except (OSError, ValueError):
        pass
    return {
        "hostname": socket.gethostname(),
        "cpu_count": os.cpu_count() or 1,
        "memory_gib": memory_gib,
        "gpu": None,
        "python": sys.version.split()[0],
    }


def _candidate_payload(candidate: CandidateResult) -> dict[str, Any]:
    return candidate.model_dump(mode="json")


def _selection_payload(selection: FrozenSelection) -> dict[str, Any]:
    return {
        "candidate_id": selection.selected_spec.candidate_id,
        "family": selection.selected_spec.family,
        "parameters": dict(selection.selected_spec.parameters),
        "result": _candidate_payload(selection.selected_result),
        "locked": selection.locked,
    }


def _selection_from_payload(payload: dict[str, Any]) -> FrozenSelection:
    selected = payload.get("selection")
    candidates = payload.get("candidates")
    if not isinstance(selected, dict) or not isinstance(candidates, list):
        raise ValueError("backtest state is malformed")
    if selected.get("locked") is not True:
        raise ValueError("model selection is not frozen")
    spec = CandidateSpec.create(
        str(selected["candidate_id"]),
        str(selected["family"]),
        dict(selected["parameters"]),
    )
    results = tuple(CandidateResult.model_validate(item) for item in candidates)
    matching = [item for item in results if item.candidate_id == spec.candidate_id]
    if len(matching) != 1 or not matching[0].completed:
        raise ValueError("selected completed candidate is absent from backtest state")
    return FrozenSelection(spec, matching[0], results, locked=True)


def _split_payload(bundle: DatasetBundle) -> dict[str, Any]:
    split = global_date_split(bundle.passengers, holdout_days=7, require_rows_per_date=60)
    return {
        "train_start": str(split.train_dates[0]),
        "train_end": str(split.train_dates[-1]),
        "test_start": str(split.test_dates[0]),
        "test_end": str(split.test_dates[-1]),
        "train_rows": len(split.train),
        "test_rows": len(split.test),
    }


@app.command("data-check")
def data_check(
    config: Path = typer.Option(..., exists=True, dir_okay=False),
    data_dir: Path = typer.Option(..., exists=True, file_okay=False),
    output_dir: Path = typer.Option(..., file_okay=False),
) -> None:
    """Verify the supplied snapshot and emit a sanitized audit."""

    settings = _settings(config, data_dir, output_dir)
    with _command_scope("data-check", settings):
        bundle = _load_bundle(settings)
        mapping = nearest_sensor_mapping(bundle.stations, bundle.traffic)
        mapping_path = output_dir / "station_sensor_mapping.csv"
        output_dir.mkdir(parents=True, exist_ok=True)
        mapping.to_csv(mapping_path, index=False, lineterminator="\n")
        payload = {
            "schema_version": "1.0.0",
            "status": "passed",
            "audit": asdict(bundle.audit),
            "split": _split_payload(bundle),
            "source_snapshots": _source_identity(bundle, data_dir),
            "station_sensor_mapping_sha256": sha256_file(mapping_path),
        }
        path = _atomic_json(output_dir / "data_audit.json", payload)
        typer.echo(json.dumps({"status": "passed", "audit": path.as_posix()}))


@app.command()
def backtest(
    config: Path = typer.Option(..., exists=True, dir_okay=False),
    data_dir: Path = typer.Option(..., exists=True, file_okay=False),
    output_dir: Path = typer.Option(..., file_okay=False),
) -> None:
    """Run the locked remote-only rolling search and post-selection ablations."""

    settings = _settings(config, data_dir, output_dir)
    with _command_scope("backtest", settings):
        require_remote_full_data_fit(settings.execution_target)
        bundle = _load_bundle(settings)
        outcome = run_bounded_search(bundle, budget=settings.search_budget)
        ablations = run_feature_ablations(
            outcome.selection,
            bundle,
            execution_target=settings.execution_target,
            budget=settings.search_budget,
        )
        payload = {
            "schema_version": "1.0.0",
            "source_revision": _source_revision(),
            "selection": _selection_payload(outcome.selection),
            "candidates": [_candidate_payload(item) for item in outcome.candidates],
            "resource_usage": asdict(outcome.resource_usage),
            "resource_limits": {
                "cpu_threads": settings.cpu_threads,
                "max_rss_gib": settings.max_rss_gib,
                "max_tuning_seconds": settings.max_tuning_seconds,
            },
            "ablations": [item.model_dump(mode="json") for item in ablations],
            "tabpfn": {
                **tabpfn_feasibility_record().model_dump(mode="json"),
                "disposition": "skipped_resource_and_fit_gate",
            },
        }
        path = _atomic_json(output_dir / "backtest.json", payload)
        typer.echo(
            json.dumps(
                {
                    "status": "passed",
                    "selection": outcome.selection.selected_spec.candidate_id,
                    "backtest": path.as_posix(),
                },
                sort_keys=True,
            )
        )


@app.command()
def train(
    config: Path = typer.Option(..., exists=True, dir_okay=False),
    data_dir: Path = typer.Option(..., exists=True, file_okay=False),
    output_dir: Path = typer.Option(..., file_okay=False),
) -> None:
    """Refit the frozen champion on all labeled pre-holdout rows."""

    settings = _settings(config, data_dir, output_dir)
    with _command_scope("train", settings):
        require_remote_full_data_fit(settings.execution_target)
        bundle = _load_bundle(settings)
        backtest_state = _load_json(output_dir / "backtest.json")
        selection = _selection_from_payload(backtest_state)
        split = global_date_split(bundle.passengers, holdout_days=7, require_rows_per_date=60)
        builder = CutoffFeatureBuilder(
            timezone=settings.timezone, schema_version=settings.schema_version
        )
        built = builder.build(
            split.train,
            origin=OFFICIAL_ORIGIN,
            stations=bundle.stations,
            passenger_history=bundle.passengers,
            weather=bundle.weather,
            traffic=bundle.traffic,
        )
        columns = list(built.feature_columns)
        model = create_model(
            selection.selected_spec.family,
            dict(selection.selected_spec.parameters),
            categorical_columns=built.categorical_columns,
            seed=settings.seed,
            threads=settings.cpu_threads,
        )
        model.fit(built.frame[columns], split.train["target"].to_numpy(dtype=float))
        model_dir = output_dir / "model"
        model_path = save_model(model, model_dir / "champion.pkl")
        schema_path = built.persist_schema(model_dir / "feature_schema.json")
        mapping_path = model_dir / "station_sensor_mapping.csv"
        built.station_sensor_mapping.to_csv(mapping_path, index=False, lineterminator="\n")
        manifest = {
            "schema_version": "1.0.0",
            "source_revision": _source_revision(),
            "selected_candidate_id": selection.selected_spec.candidate_id,
            "family": selection.selected_spec.family,
            "parameters": dict(selection.selected_spec.parameters),
            "model_sha256": sha256_file(model_path),
            "feature_schema_sha256": sha256_file(schema_path),
            "feature_schema_hash": built.schema_hash,
            "mapping_sha256": sha256_file(mapping_path),
            "config_hash": settings.stable_hash(),
            "labeled_rows": int(split.train["target"].notna().sum()),
        }
        path = _atomic_json(model_dir / "training_manifest.json", manifest)
        typer.echo(json.dumps({"status": "passed", "training_manifest": path.as_posix()}))


def _official_prediction_frame(
    bundle: DatasetBundle,
    settings: ForecastSettings,
    output_dir: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    split = global_date_split(bundle.passengers, holdout_days=7, require_rows_per_date=60)
    model_dir = output_dir / "model"
    training = _load_json(model_dir / "training_manifest.json")
    model_path = model_dir / "champion.pkl"
    schema_path = model_dir / "feature_schema.json"
    if sha256_file(model_path) != training.get("model_sha256"):
        raise ValueError("champion model hash differs from training manifest")
    if sha256_file(schema_path) != training.get("feature_schema_sha256"):
        raise ValueError("feature schema hash differs from training manifest")
    model = load_model(model_path)
    builder = CutoffFeatureBuilder(
        timezone=settings.timezone, schema_version=settings.schema_version
    )
    built = builder.build(
        split.test,
        origin=OFFICIAL_ORIGIN,
        stations=bundle.stations,
        passenger_history=bundle.passengers,
        weather=bundle.weather,
        traffic=bundle.traffic,
        station_sensor_mapping=pd.read_csv(model_dir / "station_sensor_mapping.csv"),
    )
    if built.schema_hash != training.get("feature_schema_hash"):
        raise ValueError("evaluation features differ from frozen training schema")
    champion = np.clip(model.predict(built.frame[list(built.feature_columns)]), 0, None)
    seasonal = seasonal_naive(split.test, bundle.passengers, origin=OFFICIAL_ORIGIN).values
    median = historical_median(split.test, bundle.passengers, origin=OFFICIAL_ORIGIN)
    source_identity = _source_identity(bundle, settings.raw_data_dir)
    source_hash = stable_hash(source_identity)
    model_hash = str(training["model_sha256"])
    batch_id = build_batch_id(
        origin=OFFICIAL_ORIGIN,
        source_snapshots=source_identity,
        model_artifact_hash=model_hash,
        feature_config_hash=settings.stable_hash(),
    )
    frame = pd.DataFrame(
        {
            "batch_id": batch_id,
            "origin": OFFICIAL_ORIGIN.isoformat(),
            "target_timestamp": split.test["timestamp_utc"],
            "station_id": split.test["station_id"].astype(str),
            "lead_time_hours": (
                split.test["timestamp_utc"] - pd.Timestamp(OFFICIAL_ORIGIN).tz_convert("UTC")
            ).dt.total_seconds()
            / 3600,
            "forecast_continuous": champion,
            "forecast_count": np.floor(champion + 0.5).astype(int),
            "model_role": "champion",
            "batch_status": "healthy",
            "source_hash": source_hash,
            "model_hash": model_hash,
            "config_hash": settings.stable_hash(),
            "actual": split.test["target"],
            "seasonal_naive": seasonal,
            "historical_median": median,
        }
    )
    reports = {
        "champion": evaluate_predictions(frame["station_id"], frame["actual"], champion),
        "seasonal_naive": evaluate_predictions(frame["station_id"], frame["actual"], seasonal),
        "historical_median": evaluate_predictions(frame["station_id"], frame["actual"], median),
    }
    return frame, reports


@app.command()
def evaluate(
    config: Path = typer.Option(..., exists=True, dir_okay=False),
    data_dir: Path = typer.Option(..., exists=True, file_okay=False),
    output_dir: Path = typer.Option(..., file_okay=False),
) -> None:
    """Evaluate the frozen champion and baselines once on the official holdout."""

    settings = _settings(config, data_dir, output_dir)
    with _command_scope("evaluate", settings):
        require_remote_full_data_fit(settings.execution_target)
        bundle = _load_bundle(settings)
        frame, reports = _official_prediction_frame(bundle, settings, output_dir)
        frame.to_csv(output_dir / "predictions.csv", index=False, lineterminator="\n")
        frame.to_parquet(output_dir / "predictions.parquet", index=False, compression="zstd")
        backtest_state = _load_json(output_dir / "backtest.json")
        selection = _selection_from_payload(backtest_state)
        champion = reports["champion"]
        seasonal = reports["seasonal_naive"]
        median = reports["historical_median"]
        fold_windows = rolling_folds(bundle.passengers, timezone=settings.timezone)
        fold_rmse = selection.selected_result.fold_rmse
        evaluation = {
            "schema_version": "1.0.0",
            "status": "verified",
            "source_revision": _source_revision(),
            "selected_candidate_id": selection.selected_spec.candidate_id,
            "champion_family": selection.selected_spec.family,
            "champion_parameters": dict(selection.selected_spec.parameters),
            "prediction_count": champion.forecast_rows,
            "scoreable_count": champion.scoreable_rows,
            "overall_rmse": champion.overall_rmse,
            "overall_bias": champion.overall_bias,
            "seasonal_naive_rmse": seasonal.overall_rmse,
            "historical_median_rmse": median.overall_rmse,
            "per_station_results": [item.model_dump(mode="json") for item in champion.per_station],
            "seasonal_per_station": [item.model_dump(mode="json") for item in seasonal.per_station],
            "historical_median_per_station": [
                item.model_dump(mode="json") for item in median.per_station
            ],
            "split": _split_payload(bundle),
            "model_candidates": backtest_state["candidates"],
            "folds": [
                {
                    "fold": index,
                    "validation_start": str(fold.validation_dates[0]),
                    "validation_end": str(fold.validation_dates[-1]),
                    "rmse": fold_rmse[index - 1],
                }
                for index, fold in enumerate(fold_windows, start=1)
            ],
            "ablations": backtest_state["ablations"],
            "tabpfn": backtest_state["tabpfn"],
            "host_profile": _host_profile(),
            "resource_limits": backtest_state["resource_limits"],
            "resource_usage": {
                **dict(backtest_state["resource_usage"]),
                "peak_rss_gib": max(
                    float(backtest_state["resource_usage"]["peak_rss_gib"]),
                    current_peak_rss_gib(),
                ),
            },
            "artifact_digests": {
                "model": sha256_file(output_dir / "model/champion.pkl"),
                "feature_schema": sha256_file(output_dir / "model/feature_schema.json"),
                "predictions_csv": sha256_file(output_dir / "predictions.csv"),
                "predictions_parquet": sha256_file(output_dir / "predictions.parquet"),
            },
            "limitations": [
                "no ingestion timestamps",
                "same-horizon weather is a perfect-forecast proxy without vintages",
                "traffic mapping is inferred from nearest coordinates",
                "three official holdout labels are missing",
            ],
        }
        path = _atomic_json(output_dir / "evaluation_manifest.json", evaluation)
        typer.echo(
            json.dumps(
                {
                    "status": "verified",
                    "overall_rmse": champion.overall_rmse,
                    "coverage": f"{champion.scoreable_rows}/{champion.forecast_rows}",
                    "evaluation_manifest": path.as_posix(),
                },
                sort_keys=True,
            )
        )


def _forecast_grid(origin: datetime, bundle: DatasetBundle, timezone: str) -> pd.DataFrame:
    zone = ZoneInfo(timezone)
    local_origin = origin.astimezone(zone)
    rows: list[dict[str, Any]] = []
    station_ids = sorted(bundle.stations["station_id"].astype(str), key=lambda item: int(item))
    for offset in range(1, 8):
        service_date = local_origin.date() + timedelta(days=offset)
        for hour in (7, 8, 9, 10, 11, 12):
            target = datetime.combine(service_date, time(hour), tzinfo=zone)
            for station_id in station_ids:
                rows.append(
                    {
                        "station_id": station_id,
                        "target_timestamp": target.astimezone(UTC),
                        "timestamp_utc": target.astimezone(UTC),
                        "local_date": service_date,
                        "local_hour": hour,
                    }
                )
    return pd.DataFrame(rows)


@app.command()
def predict(
    as_of: str = typer.Option(..., "--as-of", help="Offset-aware forecast origin."),
    config: Path = typer.Option(..., exists=True, dir_okay=False),
    data_dir: Path = typer.Option(..., exists=True, file_okay=False),
    output_dir: Path = typer.Option(..., file_okay=False),
    model_dir: Path | None = typer.Option(None, file_okay=False),
) -> None:
    """Publish one complete, idempotent champion or fallback batch."""

    settings = _settings(config, data_dir, output_dir)
    with _command_scope("predict", settings):
        origin = datetime.fromisoformat(as_of)
        if origin.tzinfo is None or origin.utcoffset() is None:
            raise ValueError("--as-of must include an explicit UTC offset")
        bundle = _load_bundle(settings)
        grid = _forecast_grid(origin, bundle, settings.timezone)
        artifact_dir = model_dir or output_dir / "model"
        model_path = artifact_dir / "champion.pkl"
        training_path = artifact_dir / "training_manifest.json"
        mapping_path = artifact_dir / "station_sensor_mapping.csv"
        source_identity = _source_identity(bundle, data_dir)
        training: dict[str, Any] = {}
        model: Any = None
        champion_valid = False
        if model_path.is_file() and training_path.is_file() and mapping_path.is_file():
            try:
                training = _load_json(training_path)
                champion_valid = sha256_file(model_path) == training.get("model_sha256")
                if champion_valid:
                    model = load_model(model_path)
            except Exception:
                champion_valid = False

        def champion_predict(targets: pd.DataFrame) -> list[float]:
            if not champion_valid or model is None:
                raise ValueError("champion artifact is unavailable or invalid")
            built = CutoffFeatureBuilder(
                timezone=settings.timezone, schema_version=settings.schema_version
            ).build(
                targets,
                origin=origin,
                stations=bundle.stations,
                passenger_history=bundle.passengers,
                weather=bundle.weather,
                traffic=bundle.traffic,
                station_sensor_mapping=pd.read_csv(mapping_path),
            )
            if built.schema_hash != training.get("feature_schema_hash"):
                raise ValueError("prediction feature schema differs from champion")
            predictions = np.asarray(
                model.predict(built.frame[list(built.feature_columns)]), dtype=float
            )
            return [float(value) for value in predictions]

        def fallback_predict(targets: pd.DataFrame) -> list[float]:
            values = seasonal_naive(targets, bundle.passengers, origin=origin).values
            return [float(value) for value in values]

        result = execute_batch(
            grid=grid,
            origin=origin,
            source_snapshots=source_identity,
            source_states=(
                SourceState("station_metadata", critical=True),
                SourceState("passenger_history", critical=True),
                SourceState("weather", critical=False),
                SourceState("traffic", critical=False),
            ),
            champion_predict=champion_predict,
            fallback_predict=fallback_predict,
            champion_model_hash=str(training.get("model_sha256", "invalid_champion")),
            fallback_model_hash=stable_hash({"model": "seasonal_naive", "version": "1.0.0"}),
            feature_config_hash=settings.stable_hash(),
            output_dir=output_dir,
            expected_station_ids=tuple(bundle.stations["station_id"].astype(str)),
            champion_artifact_valid=champion_valid,
            run_id=uuid.uuid4().hex,
        )
        typer.echo(
            json.dumps(
                {
                    "status": result.decision.batch_status,
                    "model_role": result.decision.model_role,
                    "batch_id": result.publication.batch_id,
                    "records": result.publication.record_count,
                    "output": result.publication.batch_dir.as_posix(),
                    "idempotent_replay": result.publication.idempotent_replay,
                },
                sort_keys=True,
            )
        )


@app.command()
def monitor(
    config: Path = typer.Option(..., exists=True, dir_okay=False),
    data_dir: Path = typer.Option(..., exists=True, file_okay=False),
    output_dir: Path = typer.Option(..., file_okay=False),
    forecasts: Path = typer.Option(..., exists=True, dir_okay=False),
    labels: Path | None = typer.Option(None, exists=True, dir_okay=False),
    baseline: Path | None = typer.Option(None, exists=True, dir_okay=False),
) -> None:
    """Calculate aggregate batch and post-label monitoring."""

    settings = _settings(config, data_dir, output_dir)
    with _command_scope("monitor", settings):
        forecast_frame = (
            pd.read_parquet(forecasts) if forecasts.suffix == ".parquet" else pd.read_csv(forecasts)
        )
        labels_frame = None if labels is None else pd.read_csv(labels)
        baseline_frame = None if baseline is None else pd.read_csv(baseline)
        report = monitor_forecasts(
            forecast_frame,
            labels=labels_frame,
            baseline_forecasts=baseline_frame,
        )
        path = _atomic_json(output_dir / "monitoring.json", report.to_dict())
        typer.echo(
            json.dumps(
                {"status": report.severity, "action": report.action, "report": path.as_posix()},
                sort_keys=True,
            )
        )


@app.command("export-evidence")
def export_evidence(
    source_revision: str = typer.Option(...),
    artifact_dir: Path = typer.Option(..., exists=True, file_okay=False),
    config: Path = typer.Option(..., exists=True, dir_okay=False),
    data_dir: Path = typer.Option(..., exists=True, file_okay=False),
    output_dir: Path = typer.Option(..., file_okay=False),
) -> None:
    """Export a portable evidence manifest from a pulled remote run."""

    settings = _settings(config, data_dir, output_dir)
    with _command_scope("export-evidence", settings):
        evaluation = _load_json(artifact_dir / "evaluation_manifest.json")
        repository_root = Path(
            subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        project_root = repository_root / "partA_passenger_forecasting"
        log_path = artifact_dir / "logs/evaluate.jsonl"
        export_evidence_manifest(
            output_path=output_dir / "evaluation_evidence.json",
            repository_root=repository_root,
            source_revision=source_revision,
            source_files=sorted((project_root / "src").rglob("*.py")),
            dependency_lock=project_root / "uv.lock",
            configuration_files=(project_root / "pyproject.toml", config.resolve()),
            artifact_files={
                "model": artifact_dir / "model/champion.pkl",
                "feature_schema": artifact_dir / "model/feature_schema.json",
                "predictions": artifact_dir / "predictions.parquet",
                "run_log": log_path,
                "evaluation_manifest": artifact_dir / "evaluation_manifest.json",
            },
            dataset_fingerprints=EXPECTED_DATASET_SHA256,
            split=dict(evaluation["split"]),
            evaluation=evaluation,
            host_profile=dict(evaluation["host_profile"]),
            resource_limits=dict(evaluation["resource_limits"]),
            resource_usage=dict(evaluation["resource_usage"]),
            tabpfn=dict(evaluation["tabpfn"]),
            model_candidates=list(evaluation["model_candidates"]),
            ablations=list(evaluation["ablations"]),
        )
        typer.echo(
            json.dumps(
                {
                    "status": "exported",
                    "manifest": (output_dir / "evaluation_evidence.json").as_posix(),
                },
                sort_keys=True,
            )
        )


@app.command("verify-evidence")
def verify_evidence(
    manifest: Path = typer.Option(..., exists=True, dir_okay=False),
    config: Path = typer.Option(..., exists=True, dir_okay=False),
    data_dir: Path = typer.Option(..., exists=True, file_okay=False),
    output_dir: Path = typer.Option(..., file_okay=False),
) -> None:
    """Fail closed if evidence or any bound input has changed."""

    settings = _settings(config, data_dir, output_dir)
    with _command_scope("verify-evidence", settings):
        repository_root = Path(
            subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        verified = verify_evidence_manifest(
            manifest,
            repository_root=repository_root,
            raw_data_dir=data_dir,
        )
        path = _atomic_json(
            output_dir / "evidence_verification.json",
            {
                "status": "verified",
                "source_revision": verified["source"]["revision"],
                "manifest_sha256": sha256_file(manifest),
            },
        )
        typer.echo(json.dumps({"status": "verified", "report": path.as_posix()}))


if __name__ == "__main__":
    app()
