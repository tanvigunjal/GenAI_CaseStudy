"""Fail-closed orchestration and immutable publication for hourly forecast batches.

The module intentionally accepts prediction callables rather than importing a concrete model
adapter.  This keeps the publication boundary usable by the selected champion and by the fixed
seasonal fallback without giving either implementation permission to write outputs directly.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import pandas as pd

EXPECTED_RECORDS = 420
EXPECTED_STATIONS = 10
HORIZON_DAYS = 7
SERVICE_HOURS = (7, 8, 9, 10, 11, 12)
TIMEZONE = "Europe/Berlin"

REQUIRED_COLUMNS = (
    "batch_id",
    "origin",
    "target_timestamp",
    "station_id",
    "lead_time_hours",
    "forecast_continuous",
    "forecast_count",
    "model_role",
    "batch_status",
    "source_hash",
    "model_hash",
    "config_hash",
)


class BatchError(RuntimeError):
    """Base class for errors which must not update the current-batch pointer."""


class CriticalSourceError(BatchError):
    """A source required for any trustworthy forecast failed its contract."""


class ForecastContractError(BatchError):
    """The candidate output is partial, malformed, or otherwise unsafe to publish."""


class ChampionArtifactError(BatchError):
    """A champion model cannot be safely loaded or used and should trigger fallback."""


@dataclass(frozen=True)
class SourceState:
    """Availability and validation state used by the source-criticality policy."""

    name: str
    critical: bool
    available: bool = True
    valid: bool = True
    detail: str | None = None

    @property
    def healthy(self) -> bool:
        return self.available and self.valid


@dataclass(frozen=True)
class ExecutionDecision:
    """Selected serving role and the non-sensitive reasons for it."""

    model_role: Literal["champion", "seasonal_naive_fallback"]
    batch_status: Literal["healthy", "degraded"]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class PublicationResult:
    """Locations and lineage of one successful immutable publication."""

    batch_id: str
    batch_dir: Path
    csv_path: Path
    parquet_path: Path
    manifest_path: Path
    current_pointer: Path
    record_count: int
    model_role: str
    batch_status: str
    idempotent_replay: bool


@dataclass(frozen=True)
class BatchExecutionResult:
    """Decision plus the publication resulting from an end-to-end batch attempt."""

    decision: ExecutionDecision
    publication: PublicationResult


PredictionFunction = Callable[[pd.DataFrame], Sequence[float]]


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(item)
            for key, item in sorted(value.items(), key=lambda x: str(x[0]))
        }
    if isinstance(value, list | tuple | set | frozenset):
        items = [_jsonable(item) for item in value]
        if isinstance(value, set | frozenset):
            return sorted(items, key=lambda item: json.dumps(item, sort_keys=True))
        return items
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return value.as_posix()
    return value


def _stable_digest(value: Any) -> str:
    payload = json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _aware_datetime(value: datetime | str, *, field: str) -> datetime:
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ForecastContractError(f"{field} must include an explicit UTC offset")
    return parsed


def build_batch_id(
    *,
    origin: datetime | str,
    source_snapshots: Mapping[str, Any] | Sequence[Any],
    model_artifact_hash: str,
    feature_config_hash: str,
) -> str:
    """Return the deterministic identity of the four inputs locked by the plan."""

    parsed_origin = _aware_datetime(origin, field="origin")
    if not model_artifact_hash or not feature_config_hash:
        raise ForecastContractError("model and feature-configuration hashes are required")
    return _stable_digest(
        {
            "origin": parsed_origin.isoformat(),
            "source_snapshots": source_snapshots,
            "model_artifact_hash": model_artifact_hash,
            "feature_config_hash": feature_config_hash,
        }
    )


def select_execution_mode(
    source_states: Sequence[SourceState], *, champion_artifact_valid: bool = True
) -> ExecutionDecision:
    """Apply the explicit source-criticality and artifact-fallback policy.

    Critical state is assigned by the caller's source adapter contract.  In the production
    mapping, station metadata and passenger history/target are critical; weather and traffic are
    optional because the fixed seasonal hierarchy remains usable without them.
    """

    critical_failures = sorted(
        state.name for state in source_states if state.critical and not state.healthy
    )
    if critical_failures:
        joined = ", ".join(critical_failures)
        raise CriticalSourceError(f"critical source contract failed: {joined}")

    reasons = [
        f"optional_source_unavailable:{state.name}"
        for state in sorted(source_states, key=lambda item: item.name)
        if not state.critical and not state.healthy
    ]
    if not champion_artifact_valid:
        reasons.append("champion_artifact_invalid")
    if reasons:
        return ExecutionDecision("seasonal_naive_fallback", "degraded", tuple(reasons))
    return ExecutionDecision("champion", "healthy", ())


def _normalise_grid(grid: pd.DataFrame) -> pd.DataFrame:
    aliases = {"station": "station_id", "timestamp": "target_timestamp"}
    normalised = grid.rename(
        columns={key: value for key, value in aliases.items() if key in grid.columns}
    )
    required = {"station_id", "target_timestamp"}
    missing = sorted(required - set(normalised.columns))
    if missing:
        raise ForecastContractError(f"forecast grid missing columns: {missing}")
    result = normalised.loc[:, ["station_id", "target_timestamp"]].copy()
    result["station_id"] = result["station_id"].astype(str)
    try:
        for value in result["target_timestamp"]:
            timestamp = pd.Timestamp(value)
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ForecastContractError(
                    "target_timestamp contains invalid or offset-free values"
                )
        result["target_timestamp"] = pd.to_datetime(result["target_timestamp"], utc=True)
    except (TypeError, ValueError) as exc:
        raise ForecastContractError(
            "target_timestamp contains invalid or offset-free values"
        ) from exc
    return result


def build_forecast_records(
    *,
    grid: pd.DataFrame,
    predictions: Sequence[float],
    batch_id: str,
    origin: datetime | str,
    decision: ExecutionDecision,
    source_snapshots: Mapping[str, Any] | Sequence[Any],
    model_hash: str,
    config_hash: str,
) -> pd.DataFrame:
    """Build the operational record contract, including clipping and half-up rounding."""

    parsed_origin = _aware_datetime(origin, field="origin")
    records = _normalise_grid(grid)
    if len(predictions) != len(records):
        raise ForecastContractError(
            f"predictor returned {len(predictions)} rows for a {len(records)}-row grid"
        )
    numeric = pd.to_numeric(pd.Series(predictions, index=records.index), errors="coerce")
    if numeric.isna().any() or not numeric.map(math.isfinite).all():
        raise ForecastContractError("predictions must all be finite numbers")
    continuous = numeric.clip(lower=0.0).astype(float)
    records.insert(0, "batch_id", batch_id)
    records.insert(1, "origin", parsed_origin.isoformat())
    origin_utc = pd.Timestamp(parsed_origin).tz_convert("UTC")
    records["lead_time_hours"] = (
        records["target_timestamp"] - origin_utc
    ).dt.total_seconds() / 3600.0
    records["forecast_continuous"] = continuous
    records["forecast_count"] = continuous.map(lambda value: int(math.floor(value + 0.5)))
    records["model_role"] = decision.model_role
    records["batch_status"] = decision.batch_status
    records["source_hash"] = _stable_digest(source_snapshots)
    records["model_hash"] = model_hash
    records["config_hash"] = config_hash
    return records.loc[:, list(REQUIRED_COLUMNS)]


def validate_forecast_frame(
    frame: pd.DataFrame,
    *,
    expected_station_ids: Sequence[str] | None = None,
    timezone: str = TIMEZONE,
    service_hours: Sequence[int] = SERVICE_HOURS,
    horizon_days: int = HORIZON_DAYS,
) -> None:
    """Fail closed unless the complete fixed-horizon publication contract is satisfied."""

    missing = sorted(set(REQUIRED_COLUMNS) - set(frame.columns))
    if missing:
        raise ForecastContractError(f"forecast output missing columns: {missing}")
    expected_rows = (
        horizon_days
        * len(service_hours)
        * (len(expected_station_ids) if expected_station_ids is not None else EXPECTED_STATIONS)
    )
    if len(frame) != expected_rows or (
        expected_station_ids is None and len(frame) != EXPECTED_RECORDS
    ):
        raise ForecastContractError(
            f"forecast output has {len(frame)} rows; expected {expected_rows}"
        )
    if frame[["station_id", "target_timestamp"]].duplicated().any():
        raise ForecastContractError("forecast output contains duplicate station/timestamp keys")
    station_values = {str(value) for value in frame["station_id"]}
    if expected_station_ids is None:
        if len(station_values) != EXPECTED_STATIONS:
            raise ForecastContractError(
                f"forecast output covers {len(station_values)} stations; expected 10"
            )
    elif station_values != {str(value) for value in expected_station_ids}:
        raise ForecastContractError("forecast station set differs from the active-station contract")

    numeric = pd.to_numeric(frame["forecast_continuous"], errors="coerce")
    if numeric.isna().any() or not numeric.map(math.isfinite).all() or (numeric < 0).any():
        raise ForecastContractError("continuous forecasts must be finite and non-negative")
    rounded = pd.to_numeric(frame["forecast_count"], errors="coerce")
    expected_rounded = numeric.map(lambda value: int(math.floor(float(value) + 0.5)))
    if rounded.isna().any() or not rounded.astype(int).equals(expected_rounded.astype(int)):
        raise ForecastContractError("forecast_count must use deterministic round-half-up")

    if frame["batch_id"].nunique(dropna=False) != 1:
        raise ForecastContractError("all output rows must share one batch_id")
    if frame["origin"].nunique(dropna=False) != 1:
        raise ForecastContractError("all output rows must share one origin")
    if (
        frame["model_role"].nunique(dropna=False) != 1
        or frame["batch_status"].nunique(dropna=False) != 1
    ):
        raise ForecastContractError("a batch cannot mix model roles or health statuses")

    origin = _aware_datetime(str(frame["origin"].iloc[0]), field="origin")
    timestamps = pd.to_datetime(frame["target_timestamp"], utc=True).dt.tz_convert(timezone)
    local_dates = timestamps.dt.date
    expected_dates = {
        (origin.astimezone(timestamps.dt.tz).date() + timedelta(days=offset))
        for offset in range(1, horizon_days + 1)
    }
    if set(local_dates) != expected_dates:
        raise ForecastContractError("targets must cover the next seven complete local dates")
    if set(timestamps.dt.hour) != set(service_hours):
        raise ForecastContractError("targets must contain only all configured service hours")
    counts = (
        frame.assign(_date=local_dates, _hour=timestamps.dt.hour)
        .groupby(["station_id", "_date"], observed=True)["_hour"]
        .nunique()
    )
    if (
        len(counts) != len(station_values) * horizon_days
        or not (counts == len(service_hours)).all()
    ):
        raise ForecastContractError(
            "each station/date must contain every service hour exactly once"
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write_text(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _verify_existing_publication(batch_dir: Path, batch_id: str) -> PublicationResult:
    manifest_path = batch_dir / "publication.json"
    if not manifest_path.is_file():
        raise ForecastContractError(f"existing batch {batch_id} is incomplete")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ForecastContractError(f"existing batch {batch_id} has an invalid manifest") from exc
    csv_path = batch_dir / "forecasts.csv"
    parquet_path = batch_dir / "forecasts.parquet"
    for key, path in (("csv_sha256", csv_path), ("parquet_sha256", parquet_path)):
        if not path.is_file() or _sha256_file(path) != manifest.get(key):
            raise ForecastContractError(f"existing batch {batch_id} failed artifact verification")
    return PublicationResult(
        batch_id=batch_id,
        batch_dir=batch_dir,
        csv_path=csv_path,
        parquet_path=parquet_path,
        manifest_path=manifest_path,
        current_pointer=batch_dir.parent.parent / "current.json",
        record_count=int(manifest["record_count"]),
        model_role=str(manifest["model_role"]),
        batch_status=str(manifest["batch_status"]),
        idempotent_replay=True,
    )


def publish_forecast_batch(
    frame: pd.DataFrame,
    *,
    output_dir: Path,
    expected_station_ids: Sequence[str] | None = None,
    run_id: str | None = None,
) -> PublicationResult:
    """Atomically publish CSV and Parquet, then update the last-successful pointer.

    History is immutable.  Replaying a verified batch is a no-op apart from making its current
    pointer explicit again.  Any validation or write failure leaves the previous pointer intact.
    """

    validate_forecast_frame(frame, expected_station_ids=expected_station_ids)
    batch_id = str(frame["batch_id"].iloc[0])
    batches_dir = output_dir / "batches"
    batches_dir.mkdir(parents=True, exist_ok=True)
    batch_dir = batches_dir / batch_id
    current_pointer = output_dir / "current.json"
    if batch_dir.exists():
        existing = _verify_existing_publication(batch_dir, batch_id)
        _atomic_write_text(
            current_pointer,
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "batch_id": batch_id,
                    "relative_path": f"batches/{batch_id}",
                    "publication_sha256": _sha256_file(existing.manifest_path),
                },
                sort_keys=True,
                indent=2,
            )
            + "\n",
        )
        return existing

    staging = batches_dir / f".staging-{batch_id}-{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        ordered = frame.sort_values(["target_timestamp", "station_id"], kind="stable").reset_index(
            drop=True
        )
        csv_path = staging / "forecasts.csv"
        parquet_path = staging / "forecasts.parquet"
        ordered.to_csv(csv_path, index=False, lineterminator="\n")
        ordered.to_parquet(parquet_path, index=False, engine="pyarrow", compression="zstd")
        manifest = {
            "schema_version": "1.0.0",
            "batch_id": batch_id,
            "run_id": run_id,
            "origin": str(ordered["origin"].iloc[0]),
            "record_count": len(ordered),
            "model_role": str(ordered["model_role"].iloc[0]),
            "batch_status": str(ordered["batch_status"].iloc[0]),
            "source_hash": str(ordered["source_hash"].iloc[0]),
            "model_hash": str(ordered["model_hash"].iloc[0]),
            "config_hash": str(ordered["config_hash"].iloc[0]),
            "csv_sha256": _sha256_file(csv_path),
            "parquet_sha256": _sha256_file(parquet_path),
        }
        manifest_path = staging / "publication.json"
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(staging, batch_dir)
        final_manifest = batch_dir / "publication.json"
        _atomic_write_text(
            current_pointer,
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "batch_id": batch_id,
                    "relative_path": f"batches/{batch_id}",
                    "publication_sha256": _sha256_file(final_manifest),
                },
                sort_keys=True,
                indent=2,
            )
            + "\n",
        )
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    return PublicationResult(
        batch_id=batch_id,
        batch_dir=batch_dir,
        csv_path=batch_dir / "forecasts.csv",
        parquet_path=batch_dir / "forecasts.parquet",
        manifest_path=batch_dir / "publication.json",
        current_pointer=current_pointer,
        record_count=len(frame),
        model_role=str(frame["model_role"].iloc[0]),
        batch_status=str(frame["batch_status"].iloc[0]),
        idempotent_replay=False,
    )


def execute_batch(
    *,
    grid: pd.DataFrame,
    origin: datetime | str,
    source_snapshots: Mapping[str, Any] | Sequence[Any],
    source_states: Sequence[SourceState],
    champion_predict: PredictionFunction,
    fallback_predict: PredictionFunction,
    champion_model_hash: str,
    fallback_model_hash: str,
    feature_config_hash: str,
    output_dir: Path,
    expected_station_ids: Sequence[str] | None = None,
    champion_artifact_valid: bool = True,
    run_id: str | None = None,
) -> BatchExecutionResult:
    """Select a role, predict all rows, validate, and publish exactly one complete batch."""

    decision = select_execution_mode(source_states, champion_artifact_valid=champion_artifact_valid)
    predictor = champion_predict if decision.model_role == "champion" else fallback_predict
    model_hash = champion_model_hash if decision.model_role == "champion" else fallback_model_hash
    try:
        predictions = predictor(grid.copy())
        batch_id = build_batch_id(
            origin=origin,
            source_snapshots=source_snapshots,
            model_artifact_hash=model_hash,
            feature_config_hash=feature_config_hash,
        )
        records = build_forecast_records(
            grid=grid,
            predictions=predictions,
            batch_id=batch_id,
            origin=origin,
            decision=decision,
            source_snapshots=source_snapshots,
            model_hash=model_hash,
            config_hash=feature_config_hash,
        )
        validate_forecast_frame(records, expected_station_ids=expected_station_ids)
    except (ChampionArtifactError, ForecastContractError, ValueError, TypeError) as exc:
        if decision.model_role != "champion":
            raise ForecastContractError(
                "fallback could not produce a complete valid batch"
            ) from exc
        decision = ExecutionDecision(
            "seasonal_naive_fallback",
            "degraded",
            (f"champion_prediction_failed:{type(exc).__name__}",),
        )
        predictions = fallback_predict(grid.copy())
        batch_id = build_batch_id(
            origin=origin,
            source_snapshots=source_snapshots,
            model_artifact_hash=fallback_model_hash,
            feature_config_hash=feature_config_hash,
        )
        records = build_forecast_records(
            grid=grid,
            predictions=predictions,
            batch_id=batch_id,
            origin=origin,
            decision=decision,
            source_snapshots=source_snapshots,
            model_hash=fallback_model_hash,
            config_hash=feature_config_hash,
        )
    publication = publish_forecast_batch(
        records,
        output_dir=output_dir,
        expected_station_ids=expected_station_ids,
        run_id=run_id,
    )
    return BatchExecutionResult(decision, publication)
