"""Aggregate-only monitoring for forecast freshness, completeness, and performance."""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import pandas as pd

from passenger_forecast.batch import EXPECTED_RECORDS

Severity = Literal["info", "warning", "critical"]
Action = Literal["none", "alert", "fallback", "block_publication"]


@dataclass(frozen=True)
class MonitoringThresholds:
    """Explicit alert thresholds; source freshness is supplied per source contract."""

    expected_records: int = EXPECTED_RECORDS
    rolling_window_days: int = 28
    warning_completeness: float = 1.0
    critical_completeness: float = 0.99
    maximum_absolute_bias: float | None = None
    maximum_rmse: float | None = None


@dataclass(frozen=True)
class MonitoringFinding:
    """One aggregate monitoring fact safe for structured logs and alerts."""

    metric: str
    scope: str
    severity: Severity
    value: float | int | str | None
    threshold: float | int | str | None
    message: str


@dataclass(frozen=True)
class StationPerformance:
    """Rolling score summary at station granularity."""

    station_id: str
    sample_count: int
    rmse: float
    bias: float
    baseline_rmse: float | None


@dataclass(frozen=True)
class MonitoringReport:
    """Serializable monitoring result matching the system contract."""

    schema_version: str
    monitoring_id: str
    batch_id: str | None
    evaluated_at: str
    rolling_window_days: int
    expected_records: int
    complete_records: int
    completeness: float
    freshness_hours: Mapping[str, float | None]
    scoreable_records: int
    overall_rmse: float | None
    overall_bias: float | None
    baseline_rmse: float | None
    champion_relative_lift: float | None
    per_station: tuple[StationPerformance, ...]
    findings: tuple[MonitoringFinding, ...]
    severity: Severity
    action: Action

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _severity_max(findings: Sequence[MonitoringFinding]) -> Severity:
    rank: dict[Severity, int] = {"info": 0, "warning": 1, "critical": 2}
    return max((finding.severity for finding in findings), key=rank.__getitem__, default="info")


def _parse_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("monitoring timestamps must include an explicit UTC offset")
    return parsed


def _normalise_forecasts(frame: pd.DataFrame) -> pd.DataFrame:
    aliases = {
        "station": "station_id",
        "timestamp": "target_timestamp",
        "prediction": "forecast_continuous",
        "forecast": "forecast_continuous",
    }
    normalised = frame.rename(
        columns={key: value for key, value in aliases.items() if key in frame.columns}
    )
    required = {"station_id", "target_timestamp", "forecast_continuous"}
    missing = sorted(required - set(normalised.columns))
    if missing:
        raise ValueError(f"forecasts missing columns: {missing}")
    result = normalised.copy()
    result["station_id"] = result["station_id"].astype(str)
    result["target_timestamp"] = pd.to_datetime(result["target_timestamp"], utc=True)
    result["forecast_continuous"] = pd.to_numeric(result["forecast_continuous"], errors="coerce")
    return result


def _normalise_labels(frame: pd.DataFrame) -> pd.DataFrame:
    aliases = {
        "station": "station_id",
        "timestamp": "target_timestamp",
        "passengers": "actual",
        "target": "actual",
        "passenger_count": "actual",
    }
    normalised = frame.rename(
        columns={key: value for key, value in aliases.items() if key in frame.columns}
    )
    required = {"station_id", "target_timestamp", "actual"}
    missing = sorted(required - set(normalised.columns))
    if missing:
        raise ValueError(f"labels missing columns: {missing}")
    result = normalised.loc[:, ["station_id", "target_timestamp", "actual"]].copy()
    result["station_id"] = result["station_id"].astype(str)
    result["target_timestamp"] = pd.to_datetime(result["target_timestamp"], utc=True)
    result["actual"] = pd.to_numeric(result["actual"], errors="coerce")
    if result[["station_id", "target_timestamp"]].duplicated().any():
        raise ValueError("labels contain duplicate station/timestamp keys")
    return result


def _normalise_baseline(frame: pd.DataFrame) -> pd.DataFrame:
    aliases = {
        "station": "station_id",
        "timestamp": "target_timestamp",
        "prediction": "baseline_forecast",
        "forecast_continuous": "baseline_forecast",
        "forecast": "baseline_forecast",
    }
    normalised = frame.rename(
        columns={key: value for key, value in aliases.items() if key in frame.columns}
    )
    required = {"station_id", "target_timestamp", "baseline_forecast"}
    missing = sorted(required - set(normalised.columns))
    if missing:
        raise ValueError(f"baseline forecasts missing columns: {missing}")
    result = normalised.loc[:, ["station_id", "target_timestamp", "baseline_forecast"]].copy()
    result["station_id"] = result["station_id"].astype(str)
    result["target_timestamp"] = pd.to_datetime(result["target_timestamp"], utc=True)
    result["baseline_forecast"] = pd.to_numeric(result["baseline_forecast"], errors="coerce")
    if result[["station_id", "target_timestamp"]].duplicated().any():
        raise ValueError("baseline forecasts contain duplicate station/timestamp keys")
    return result


def _rmse(errors: pd.Series[Any]) -> float:
    return float(math.sqrt(float((errors.astype(float) ** 2).mean())))


def _is_finite(value: Any) -> bool:
    return bool(pd.notna(value) and math.isfinite(float(value)))


def _freshness(
    source_watermarks: Mapping[str, datetime | str | None], *, now: datetime
) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for source_name, watermark in sorted(source_watermarks.items()):
        if watermark is None:
            result[source_name] = None
            continue
        age = (
            now.astimezone(UTC) - _parse_time(watermark).astimezone(UTC)
        ).total_seconds() / 3600.0
        result[source_name] = max(0.0, age)
    return result


def monitor_forecasts(
    forecasts: pd.DataFrame,
    *,
    labels: pd.DataFrame | None = None,
    baseline_forecasts: pd.DataFrame | None = None,
    source_watermarks: Mapping[str, datetime | str | None] | None = None,
    source_freshness_limits_hours: Mapping[str, float] | None = None,
    critical_sources: Sequence[str] = ("station_metadata", "passenger_history"),
    thresholds: MonitoringThresholds | None = None,
    now: datetime | None = None,
) -> MonitoringReport:
    """Calculate aggregate monitoring and an explicit alert/fallback/block action.

    When labels span more than the configured window, performance is calculated on the most
    recent target-time window.  Null labels are excluded and coverage remains visible.
    """

    limits = source_freshness_limits_hours or {}
    settings = thresholds or MonitoringThresholds()
    evaluated_at = now or datetime.now(UTC)
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("now must include an explicit UTC offset")
    predictions = _normalise_forecasts(forecasts)
    finite_mask = predictions["forecast_continuous"].map(
        lambda value: _is_finite(value) and float(value) >= 0
    )
    unique_mask = ~predictions[["station_id", "target_timestamp"]].duplicated(keep=False)
    complete_records = int((finite_mask & unique_mask).sum())
    completeness = min(1.0, complete_records / settings.expected_records)
    findings: list[MonitoringFinding] = []
    if completeness < settings.critical_completeness:
        findings.append(
            MonitoringFinding(
                "completeness",
                "batch",
                "critical",
                completeness,
                settings.critical_completeness,
                "Batch is incomplete or contains invalid/duplicate predictions.",
            )
        )
    elif completeness < settings.warning_completeness:
        findings.append(
            MonitoringFinding(
                "completeness",
                "batch",
                "warning",
                completeness,
                settings.warning_completeness,
                "Batch completeness is below the publication contract.",
            )
        )

    watermarks = source_watermarks or {}
    freshness = _freshness(watermarks, now=evaluated_at)
    critical_set = set(critical_sources)
    for source_name, age in freshness.items():
        limit = limits.get(source_name)
        stale = age is None or (limit is not None and age > limit)
        if not stale:
            continue
        severity: Severity = "critical" if source_name in critical_set else "warning"
        findings.append(
            MonitoringFinding(
                "freshness_hours",
                source_name,
                severity,
                age,
                limit,
                "Critical source is unavailable or stale."
                if severity == "critical"
                else "Optional source is unavailable or stale; use declared fallback.",
            )
        )

    scoreable_records = 0
    overall_rmse: float | None = None
    overall_bias: float | None = None
    baseline_rmse: float | None = None
    champion_relative_lift: float | None = None
    per_station: list[StationPerformance] = []
    if labels is not None:
        actuals = _normalise_labels(labels)
        scored = predictions.merge(
            actuals, on=["station_id", "target_timestamp"], how="inner", validate="one_to_one"
        )
        maximum_target = scored.loc[scored["actual"].notna(), "target_timestamp"].max()
        if pd.notna(maximum_target):
            cutoff = maximum_target - pd.Timedelta(days=settings.rolling_window_days)
            scored = scored.loc[scored["target_timestamp"] > cutoff].copy()
        scored = scored.loc[
            scored["actual"].notna() & scored["forecast_continuous"].map(_is_finite)
        ].copy()
        if baseline_forecasts is not None:
            baseline = _normalise_baseline(baseline_forecasts)
            scored = scored.merge(
                baseline,
                on=["station_id", "target_timestamp"],
                how="left",
                validate="one_to_one",
            )
        scoreable_records = len(scored)
        if scoreable_records:
            scored["error"] = scored["forecast_continuous"] - scored["actual"]
            overall_rmse = _rmse(scored["error"])
            overall_bias = float(scored["error"].mean())
            if "baseline_forecast" in scored:
                valid_baseline = scored["baseline_forecast"].notna() & scored[
                    "baseline_forecast"
                ].map(_is_finite)
                if valid_baseline.any():
                    baseline_errors = (
                        scored.loc[valid_baseline, "baseline_forecast"]
                        - scored.loc[valid_baseline, "actual"]
                    )
                    baseline_rmse = _rmse(baseline_errors)
                    if baseline_rmse > 0:
                        champion_relative_lift = (baseline_rmse - overall_rmse) / baseline_rmse
                    if overall_rmse >= baseline_rmse:
                        findings.append(
                            MonitoringFinding(
                                "champion_vs_baseline_rmse",
                                "overall",
                                "warning",
                                overall_rmse - baseline_rmse,
                                0.0,
                                "Champion is not outperforming the fixed baseline on "
                                "available labels.",
                            )
                        )
            for station_id, group in scored.groupby("station_id", sort=True, observed=True):
                station_baseline_rmse: float | None = None
                if "baseline_forecast" in group:
                    valid = group["baseline_forecast"].notna()
                    if valid.any():
                        station_baseline_rmse = _rmse(
                            group.loc[valid, "baseline_forecast"] - group.loc[valid, "actual"]
                        )
                per_station.append(
                    StationPerformance(
                        station_id=str(station_id),
                        sample_count=len(group),
                        rmse=_rmse(group["error"]),
                        bias=float(group["error"].mean()),
                        baseline_rmse=station_baseline_rmse,
                    )
                )
            if settings.maximum_rmse is not None and overall_rmse > settings.maximum_rmse:
                findings.append(
                    MonitoringFinding(
                        "rmse",
                        "overall",
                        "warning",
                        overall_rmse,
                        settings.maximum_rmse,
                        "Rolling overall RMSE exceeded the approved monitoring threshold.",
                    )
                )
            if (
                settings.maximum_absolute_bias is not None
                and abs(overall_bias) > settings.maximum_absolute_bias
            ):
                findings.append(
                    MonitoringFinding(
                        "absolute_bias",
                        "overall",
                        "warning",
                        abs(overall_bias),
                        settings.maximum_absolute_bias,
                        "Rolling absolute bias exceeded the approved monitoring threshold.",
                    )
                )

    severity = _severity_max(findings)
    critical_freshness = any(
        finding.severity == "critical" and finding.metric == "freshness_hours"
        for finding in findings
    )
    critical_completeness = any(
        finding.severity == "critical" and finding.metric == "completeness" for finding in findings
    )
    optional_freshness = any(
        finding.severity == "warning" and finding.metric == "freshness_hours"
        for finding in findings
    )
    if critical_freshness or critical_completeness:
        action: Action = "block_publication"
    elif optional_freshness:
        action = "fallback"
    elif severity == "warning":
        action = "alert"
    else:
        action = "none"
    batch_id = None
    if "batch_id" in predictions and predictions["batch_id"].nunique(dropna=False) == 1:
        batch_id = str(predictions["batch_id"].iloc[0])
    return MonitoringReport(
        schema_version="1.0.0",
        monitoring_id=uuid.uuid4().hex,
        batch_id=batch_id,
        evaluated_at=evaluated_at.isoformat(),
        rolling_window_days=settings.rolling_window_days,
        expected_records=settings.expected_records,
        complete_records=complete_records,
        completeness=completeness,
        freshness_hours=freshness,
        scoreable_records=scoreable_records,
        overall_rmse=overall_rmse,
        overall_bias=overall_bias,
        baseline_rmse=baseline_rmse,
        champion_relative_lift=champion_relative_lift,
        per_station=tuple(per_station),
        findings=tuple(findings),
        severity=severity,
        action=action,
    )
