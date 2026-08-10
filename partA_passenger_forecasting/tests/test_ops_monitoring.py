from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from passenger_forecast.monitoring import monitor_forecasts


def _frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    timestamps = pd.date_range("2026-01-27 07:00", periods=42, freq="4h", tz="Europe/Berlin")
    rows = [
        {
            "station_id": str(station),
            "target_timestamp": timestamp,
            "actual": float(station + index),
        }
        for station in range(1, 11)
        for index, timestamp in enumerate(timestamps)
    ]
    labels = pd.DataFrame(rows)
    for station in ("1", "8", "10"):
        index = labels.index[labels["station_id"] == station][0]
        labels.loc[index, "actual"] = None
    forecasts = labels[["station_id", "target_timestamp"]].copy()
    forecasts["forecast_continuous"] = labels["actual"].fillna(10.0) + 1.0
    forecasts["batch_id"] = "batch-a"
    baseline = labels[["station_id", "target_timestamp"]].copy()
    baseline["baseline_forecast"] = labels["actual"].fillna(10.0) + 2.0
    return forecasts, labels, baseline


def test_monitoring_scores_417_labels_and_all_stations() -> None:
    forecasts, labels, baseline = _frames()
    report = monitor_forecasts(forecasts, labels=labels, baseline_forecasts=baseline)

    assert report.complete_records == 420
    assert report.completeness == 1.0
    assert report.scoreable_records == 417
    assert report.overall_rmse == 1.0
    assert report.overall_bias == 1.0
    assert report.baseline_rmse == 2.0
    assert report.champion_relative_lift == 0.5
    assert len(report.per_station) == 10
    counts = {item.station_id: item.sample_count for item in report.per_station}
    assert counts["1"] == counts["8"] == counts["10"] == 41
    assert all(counts[str(station)] == 42 for station in (2, 3, 4, 5, 6, 7, 9))


def test_optional_stale_source_requests_fallback() -> None:
    forecasts, _, _ = _frames()
    now = datetime(2026, 1, 27, 12, tzinfo=UTC)
    report = monitor_forecasts(
        forecasts,
        source_watermarks={"weather_forecast": now - timedelta(hours=9)},
        source_freshness_limits_hours={"weather_forecast": 6},
        now=now,
    )
    assert report.severity == "warning"
    assert report.action == "fallback"


def test_incomplete_batch_or_stale_critical_source_blocks_publication() -> None:
    forecasts, _, _ = _frames()
    now = datetime(2026, 1, 27, 12, tzinfo=UTC)
    report = monitor_forecasts(
        forecasts.iloc[:-5],
        source_watermarks={"passenger_history": now - timedelta(hours=30)},
        source_freshness_limits_hours={"passenger_history": 24},
        now=now,
    )
    assert report.severity == "critical"
    assert report.action == "block_publication"
