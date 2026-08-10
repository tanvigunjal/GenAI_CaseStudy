from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from passenger_forecast.baselines import seasonal_naive
from passenger_forecast.data import (
    DataContractError,
    DatasetBundle,
    global_date_split,
    nearest_sensor_mapping,
    normalize_passengers,
    validate_datasets,
)
from passenger_forecast.features import CutoffFeatureBuilder


@pytest.fixture(scope="module")
def synthetic_bundle() -> DatasetBundle:
    timezone = ZoneInfo("Europe/Berlin")
    hours = pd.date_range(
        "2025-03-01 00:00",
        "2025-04-11 00:00",
        freq="h",
        inclusive="left",
        tz=timezone,
    )
    stations = pd.DataFrame(
        {
            "station_id": range(1, 11),
            "station_name": [f"Station {index}" for index in range(1, 11)],
            "lat": np.linspace(48.10, 48.19, 10),
            "lon": np.linspace(11.50, 11.59, 10),
            "station_capacity": [50] * 10,
            "is_interchange": [index % 2 == 0 for index in range(10)],
            "connectivity_score": range(10),
        }
    )
    passenger_rows: list[dict[str, object]] = []
    dates = sorted(set(hours.date))
    for day_number, service_date in enumerate(dates):
        for station_id in range(1, 11):
            for hour in range(7, 13):
                timestamp = pd.Timestamp(
                    datetime(
                        service_date.year,
                        service_date.month,
                        service_date.day,
                        hour,
                        tzinfo=timezone,
                    )
                )
                passenger_rows.append(
                    {
                        "station_id": station_id,
                        "timestamp": timestamp.isoformat(),
                        "passenger_count": float(80 + day_number + station_id + hour),
                    }
                )
    passengers = pd.DataFrame(passenger_rows)
    missing_mask = (passengers["station_id"] == 1) & passengers["timestamp"].str.startswith(
        "2025-03-28T07:00"
    )
    passengers.loc[missing_mask, "passenger_count"] = np.nan

    weather = pd.DataFrame(
        {
            "timestamp": [item.isoformat() for item in hours],
            "temp_C": 8 + np.sin(np.arange(len(hours)) / 24),
            "precip_mm": np.where(np.arange(len(hours)) % 31 == 0, 0.7, 0.0),
        }
    )
    weather.loc[10, "temp_C"] = np.nan
    weather.loc[11, "precip_mm"] = np.nan
    sensor_coordinates = [
        (48.10, 11.50),
        (48.12, 11.52),
        (48.14, 11.54),
        (48.16, 11.56),
        (48.18, 11.58),
    ]
    traffic_rows: list[dict[str, object]] = []
    for sensor_number, (latitude, longitude) in enumerate(sensor_coordinates):
        for hour_number, timestamp in enumerate(hours):
            traffic_rows.append(
                {
                    "timestamp": timestamp.isoformat(),
                    "traffic_index": float(20 + sensor_number + hour_number % 24),
                    "lat": latitude,
                    "lon": longitude,
                }
            )
    traffic = pd.DataFrame(traffic_rows)
    return validate_datasets(stations, passengers, traffic, weather)


def test_schema_aliases_dst_and_spatial_mapping(synthetic_bundle: DatasetBundle) -> None:
    bundle = synthetic_bundle
    assert bundle.audit.station_count == 10
    assert bundle.audit.sensor_count == 5
    dst_day = bundle.passengers.loc[bundle.passengers["local_date"] == date(2025, 3, 30)]
    assert set(dst_day["local_hour"]) == set(range(7, 13))
    assert not dst_day.duplicated(["station_id", "timestamp_utc"]).any()
    mapping = nearest_sensor_mapping(bundle.stations, bundle.traffic)
    assert len(mapping) == 10
    assert mapping["sensor_distance_km"].ge(0).all()


def test_offset_is_mandatory() -> None:
    raw = pd.DataFrame(
        {"station_id": [1], "timestamp": ["2025-03-01 07:00:00"], "passenger_count": [3]}
    )
    with pytest.raises(DataContractError, match="explicit offsets"):
        normalize_passengers(raw)


def test_global_split_ignores_physical_row_order(synthetic_bundle: DatasetBundle) -> None:
    original = global_date_split(synthetic_bundle.passengers, holdout_days=7)
    shuffled = synthetic_bundle.passengers.sample(frac=1, random_state=20260808)
    reshuffled = global_date_split(shuffled, holdout_days=7)
    assert original.test_dates == reshuffled.test_dates
    assert set(original.test["local_date"]) == set(reshuffled.test["local_date"])
    assert len(original.test) == 420


def test_post_origin_passenger_and_traffic_mutation_has_no_effect(
    synthetic_bundle: DatasetBundle,
) -> None:
    bundle = synthetic_bundle
    origin = pd.Timestamp("2025-04-03T12:00:00", tz="Europe/Berlin")
    targets = bundle.passengers.loc[
        bundle.passengers["local_date"].between(date(2025, 4, 4), date(2025, 4, 10))
    ]
    builder = CutoffFeatureBuilder()
    first = builder.build(
        targets,
        origin=origin,
        stations=bundle.stations,
        passenger_history=bundle.passengers,
        weather=bundle.weather,
        traffic=bundle.traffic,
    )
    mutated_passengers = bundle.passengers.copy()
    mutated_passengers.loc[mutated_passengers["timestamp_utc"] > origin, "target"] = 999_999
    mutated_traffic = bundle.traffic.copy()
    mutated_traffic.loc[mutated_traffic["timestamp_utc"] > origin, "traffic"] = 999_999
    second = builder.build(
        targets,
        origin=origin,
        stations=bundle.stations,
        passenger_history=mutated_passengers,
        weather=bundle.weather,
        traffic=mutated_traffic,
        station_sensor_mapping=first.station_sensor_mapping,
    )
    assert len(first.frame) == 420
    assert set(first.feature_columns) <= set(first.lineage)
    assert_frame_equal(
        first.frame[list(first.feature_columns)],
        second.frame[list(second.feature_columns)],
        check_dtype=True,
    )


def test_repeated_local_traffic_hour_is_collapsed_before_lag_join(
    synthetic_bundle: DatasetBundle,
) -> None:
    bundle = synthetic_bundle
    origin = pd.Timestamp("2025-04-03T12:00:00", tz="Europe/Berlin")
    targets = bundle.passengers.loc[
        bundle.passengers["local_date"].between(date(2025, 4, 4), date(2025, 4, 10))
    ]
    repeated = bundle.traffic.iloc[[0]].copy()
    repeated["timestamp_utc"] = repeated["timestamp_utc"] + pd.Timedelta(minutes=30)
    traffic = pd.concat([bundle.traffic, repeated], ignore_index=True)
    built = CutoffFeatureBuilder().build(
        targets,
        origin=origin,
        stations=bundle.stations,
        passenger_history=bundle.passengers,
        weather=bundle.weather,
        traffic=traffic,
    )
    assert len(built.frame) == 420
    assert not built.frame.duplicated(["station_id", "timestamp_utc"]).any()


def test_persisted_numeric_mapping_ids_reenter_as_contract_strings(
    synthetic_bundle: DatasetBundle, tmp_path: Path
) -> None:
    bundle = synthetic_bundle
    origin = pd.Timestamp("2025-04-03T12:00:00", tz="Europe/Berlin")
    targets = bundle.passengers.loc[
        bundle.passengers["local_date"].between(date(2025, 4, 4), date(2025, 4, 10))
    ]
    mapping = nearest_sensor_mapping(bundle.stations, bundle.traffic)
    mapping_path = tmp_path / "station_sensor_mapping.csv"
    mapping.to_csv(mapping_path, index=False)
    persisted = pd.read_csv(mapping_path)
    assert persisted["station_id"].dtype.kind in "iu"

    built = CutoffFeatureBuilder().build(
        targets,
        origin=origin,
        stations=bundle.stations,
        passenger_history=bundle.passengers,
        weather=bundle.weather,
        traffic=bundle.traffic,
        station_sensor_mapping=persisted,
    )

    assert len(built.frame) == 420
    assert built.station_sensor_mapping["station_id"].map(type).eq(str).all()
    assert built.station_sensor_mapping["sensor_id"].map(type).eq(str).all()


def test_baseline_is_complete_and_uses_local_calendar_lag(
    synthetic_bundle: DatasetBundle,
) -> None:
    bundle = synthetic_bundle
    origin = pd.Timestamp("2025-04-03T12:00:00", tz="Europe/Berlin")
    targets = bundle.passengers.loc[
        bundle.passengers["local_date"].between(date(2025, 4, 4), date(2025, 4, 10))
    ]
    predictions = seasonal_naive(targets, bundle.passengers, origin=origin)
    assert len(predictions.values) == 420
    assert np.isfinite(predictions.values).all()
    assert (predictions.values >= 0).all()
    assert predictions.used_historical_median.any()
