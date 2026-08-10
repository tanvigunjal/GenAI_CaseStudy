from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from passenger_forecast.batch import (
    CriticalSourceError,
    ForecastContractError,
    SourceState,
    build_batch_id,
    execute_batch,
)


def _grid() -> pd.DataFrame:
    rows = []
    timezone = ZoneInfo("Europe/Berlin")
    for day in range(27, 34):
        date = pd.Timestamp("2026-01-01") + pd.Timedelta(days=day - 1)
        for hour in range(7, 13):
            timestamp = datetime(date.year, date.month, date.day, hour, tzinfo=timezone)
            for station_id in range(1, 11):
                rows.append({"station_id": str(station_id), "target_timestamp": timestamp})
    return pd.DataFrame(rows)


def _states(*, weather_ok: bool = True) -> tuple[SourceState, ...]:
    return (
        SourceState("station_metadata", critical=True),
        SourceState("passenger_history", critical=True),
        SourceState("weather_forecast", critical=False, available=weather_ok),
        SourceState("traffic_history", critical=False),
    )


def _snapshots() -> dict[str, dict[str, object]]:
    return {
        "passenger_history": {"watermark": "2026-01-26T12:00:00+01:00", "rows": 67_320},
        "station_metadata": {"watermark": "2026-01-26T12:00:00+01:00", "rows": 10},
    }


def test_identical_inputs_publish_one_verified_version(tmp_path: Path) -> None:
    grid = _grid()
    kwargs = {
        "grid": grid,
        "origin": "2026-01-26T12:00:00+01:00",
        "source_snapshots": _snapshots(),
        "source_states": _states(),
        "champion_predict": lambda value: [10.5] * len(value),
        "fallback_predict": lambda value: [9.0] * len(value),
        "champion_model_hash": "champion-v1",
        "fallback_model_hash": "fallback-v1",
        "feature_config_hash": "features-v1",
        "output_dir": tmp_path,
        "expected_station_ids": [str(value) for value in range(1, 11)],
    }
    first = execute_batch(**kwargs)
    second = execute_batch(**kwargs)

    assert first.publication.batch_id == second.publication.batch_id
    assert not first.publication.idempotent_replay
    assert second.publication.idempotent_replay
    assert first.publication.csv_path.is_file()
    assert first.publication.parquet_path.is_file()
    assert len(list((tmp_path / "batches").iterdir())) == 1
    exported = pd.read_csv(first.publication.csv_path)
    assert len(exported) == 420
    assert set(exported["forecast_count"]) == {11}


def test_optional_outage_publishes_explicit_full_fallback(tmp_path: Path) -> None:
    result = execute_batch(
        grid=_grid(),
        origin="2026-01-26T12:00:00+01:00",
        source_snapshots=_snapshots(),
        source_states=_states(weather_ok=False),
        champion_predict=lambda value: [99.0] * len(value),
        fallback_predict=lambda value: [7.0] * len(value),
        champion_model_hash="champion-v1",
        fallback_model_hash="fallback-v1",
        feature_config_hash="features-v1",
        output_dir=tmp_path,
    )

    assert result.decision.model_role == "seasonal_naive_fallback"
    assert result.decision.batch_status == "degraded"
    frame = pd.read_parquet(result.publication.parquet_path)
    assert set(frame["model_role"]) == {"seasonal_naive_fallback"}
    assert set(frame["batch_status"]) == {"degraded"}
    assert set(frame["forecast_continuous"]) == {7.0}


def test_champion_invalid_output_falls_back_but_partial_fallback_blocks(tmp_path: Path) -> None:
    result = execute_batch(
        grid=_grid(),
        origin="2026-01-26T12:00:00+01:00",
        source_snapshots=_snapshots(),
        source_states=_states(),
        champion_predict=lambda value: [float("nan")] * len(value),
        fallback_predict=lambda value: [8.0] * len(value),
        champion_model_hash="champion-v1",
        fallback_model_hash="fallback-v1",
        feature_config_hash="features-v1",
        output_dir=tmp_path,
    )
    assert result.decision.model_role == "seasonal_naive_fallback"

    with pytest.raises(ForecastContractError):
        execute_batch(
            grid=_grid(),
            origin="2026-01-26T12:00:00+01:00",
            source_snapshots=_snapshots(),
            source_states=_states(weather_ok=False),
            champion_predict=lambda value: [99.0] * len(value),
            fallback_predict=lambda value: [8.0] * (len(value) - 1),
            champion_model_hash="champion-v1",
            fallback_model_hash="fallback-v2",
            feature_config_hash="features-v1",
            output_dir=tmp_path,
        )


def test_critical_failure_does_not_change_last_successful_pointer(tmp_path: Path) -> None:
    good = execute_batch(
        grid=_grid(),
        origin="2026-01-26T12:00:00+01:00",
        source_snapshots=_snapshots(),
        source_states=_states(),
        champion_predict=lambda value: [10.0] * len(value),
        fallback_predict=lambda value: [9.0] * len(value),
        champion_model_hash="champion-v1",
        fallback_model_hash="fallback-v1",
        feature_config_hash="features-v1",
        output_dir=tmp_path,
    )
    pointer_before = good.publication.current_pointer.read_bytes()
    failed_states = (
        SourceState("station_metadata", critical=True, valid=False),
        SourceState("passenger_history", critical=True),
    )
    with pytest.raises(CriticalSourceError):
        execute_batch(
            grid=_grid(),
            origin="2026-01-26T13:00:00+01:00",
            source_snapshots=_snapshots(),
            source_states=failed_states,
            champion_predict=lambda value: [10.0] * len(value),
            fallback_predict=lambda value: [9.0] * len(value),
            champion_model_hash="champion-v1",
            fallback_model_hash="fallback-v1",
            feature_config_hash="features-v1",
            output_dir=tmp_path,
        )
    assert good.publication.current_pointer.read_bytes() == pointer_before


def test_batch_id_binds_model_and_configuration() -> None:
    common = {
        "origin": "2026-01-26T12:00:00+01:00",
        "source_snapshots": _snapshots(),
        "feature_config_hash": "features-v1",
    }
    first = build_batch_id(model_artifact_hash="model-v1", **common)
    assert first == build_batch_id(model_artifact_hash="model-v1", **common)
    assert first != build_batch_id(model_artifact_hash="model-v2", **common)
