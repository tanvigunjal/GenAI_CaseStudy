"""Canonical schemas, supplied-data quality gates, split, and spatial policy."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final, cast
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from passenger_forecast.sources import sha256_file


class DataContractError(ValueError):
    """Raised when data cannot safely enter feature generation."""


SUPPLIED_FILES: Final[dict[str, tuple[int, int, str]]] = {
    "station_metadata.csv": (
        10,
        7,
        "f619def0c760d9b9d01b1614252a15ea22dc3db987cfac1227c34db78ab9ff93",
    ),
    "timeseries_with_target.csv": (
        67_740,
        3,
        "60693930f3de3415124c9334188eae51e34a0545f39098c3508463b5421f13b0",
    ),
    "traffic_hourly.csv": (
        135_480,
        4,
        "723ea8ccb6e2e886ee028771b3a1853f8f0f77a8b6afe76ac598d2bdf2fb830a",
    ),
    "weather_hourly.csv": (
        27_096,
        3,
        "1e34227dbc2b65f375e20d0ca19ac50a4ca65d24f9267dded015266edfa64698",
    ),
}

_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "timestamp": ("timestamp", "datetime", "date_time", "time"),
    "station_id": ("station_id", "station", "id"),
    "target": ("target", "passengers", "passenger_count", "passenger_counts", "entries"),
    "latitude": ("latitude", "lat"),
    "longitude": ("longitude", "lon", "lng"),
    "station_name": ("station_name", "name"),
    "station_capacity": ("station_capacity", "capacity"),
    "interchange": ("interchange", "is_interchange", "transfer_station"),
    "connectivity": ("connectivity", "connectivity_score", "connections", "num_connections"),
    "temperature": ("temperature", "temperature_c", "temp", "temp_c"),
    "precipitation": (
        "precipitation",
        "precipitation_mm",
        "precip_mm",
        "rainfall",
        "precip",
    ),
    "traffic": ("traffic", "traffic_index", "traffic_volume", "vehicle_count", "count"),
}


@dataclass(frozen=True)
class DatasetAudit:
    station_rows: int
    passenger_rows: int
    traffic_rows: int
    weather_rows: int
    station_count: int
    sensor_count: int
    date_count: int
    null_targets: int
    null_test_targets: int
    capacity_exceedances: int
    weather_temperature_nulls: int
    weather_precipitation_nulls: int
    traffic_nulls: int
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class DatasetBundle:
    stations: pd.DataFrame
    passengers: pd.DataFrame
    traffic: pd.DataFrame
    weather: pd.DataFrame
    audit: DatasetAudit


@dataclass(frozen=True)
class DateSplit:
    train: pd.DataFrame
    test: pd.DataFrame
    train_dates: tuple[date, ...]
    test_dates: tuple[date, ...]


def _column(frame: pd.DataFrame, canonical: str, *, required: bool = True) -> str | None:
    normalized = {str(name).strip().lower(): str(name) for name in frame.columns}
    for alias in _ALIASES[canonical]:
        if alias in normalized:
            return normalized[alias]
    if required:
        raise DataContractError(
            f"missing {canonical!r}; accepted columns: {', '.join(_ALIASES[canonical])}"
        )
    return None


def _rename(frame: pd.DataFrame, fields: tuple[str, ...]) -> pd.DataFrame:
    mapping: dict[str, str] = {}
    for field in fields:
        found = _column(frame, field)
        assert found is not None
        mapping[found] = field
    return frame.rename(columns=mapping).copy()


def _timestamps(values: pd.Series[Any], timezone: str) -> pd.DataFrame:
    del timezone  # Offsets are mandatory; local conversion happens after parsing.
    if values.isna().any():
        raise DataContractError("timestamp contains null values")
    offset_pattern = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})$")
    bad = []
    for index, raw in values.items():
        if isinstance(raw, str):
            if not offset_pattern.search(raw.strip()):
                bad.append(index)
        elif getattr(raw, "tzinfo", None) is None or raw.utcoffset() is None:
            bad.append(index)
    if bad:
        raise DataContractError(f"timestamps must carry explicit offsets; invalid rows: {bad[:5]}")
    try:
        parsed = pd.to_datetime(values, utc=True, errors="raise")
    except (TypeError, ValueError) as exc:
        raise DataContractError(f"malformed timestamp: {exc}") from exc
    return pd.DataFrame({"timestamp_utc": parsed}, index=values.index)


def _add_calendar_keys(frame: pd.DataFrame, timezone: str) -> pd.DataFrame:
    result = frame.copy()
    local = result["timestamp_utc"].dt.tz_convert(ZoneInfo(timezone))
    result["local_date"] = local.dt.date
    result["local_hour"] = local.dt.hour.astype("int16")
    return result


def _require_unique(frame: pd.DataFrame, key: list[str], label: str) -> None:
    duplicate = frame.duplicated(key, keep=False)
    if duplicate.any():
        count = int(duplicate.sum())
        raise DataContractError(f"{label} has {count} rows with duplicate keys {key}")


def normalize_stations(frame: pd.DataFrame) -> pd.DataFrame:
    result = _rename(frame, ("station_id", "latitude", "longitude"))
    optional_defaults: Mapping[str, object] = {
        "station_name": "",
        "station_capacity": np.nan,
        "interchange": False,
        "connectivity": 0.0,
    }
    for name, default in optional_defaults.items():
        found = _column(frame, name, required=False)
        result[name] = frame[found] if found is not None else default
    result["station_id"] = result["station_id"].astype(str)
    result["latitude"] = pd.to_numeric(result["latitude"], errors="raise")
    result["longitude"] = pd.to_numeric(result["longitude"], errors="raise")
    result["station_capacity"] = pd.to_numeric(result["station_capacity"], errors="coerce")
    result["connectivity"] = pd.to_numeric(result["connectivity"], errors="coerce")
    result["interchange"] = result["interchange"].astype(bool)
    if result[["station_id", "latitude", "longitude"]].isna().any().any():
        raise DataContractError("station identity and coordinates cannot be null")
    invalid_latitude = not result["latitude"].between(-90, 90).all()
    invalid_longitude = not result["longitude"].between(-180, 180).all()
    if invalid_latitude or invalid_longitude:
        raise DataContractError("station coordinates are outside valid bounds")
    _require_unique(result, ["station_id"], "station metadata")
    return result[
        [
            "station_id",
            "station_name",
            "latitude",
            "longitude",
            "station_capacity",
            "interchange",
            "connectivity",
        ]
    ].reset_index(drop=True)


def normalize_passengers(frame: pd.DataFrame, timezone: str = "Europe/Berlin") -> pd.DataFrame:
    result = _rename(frame, ("timestamp", "station_id", "target"))
    result = result.join(_timestamps(result.pop("timestamp"), timezone))
    result["station_id"] = result["station_id"].astype(str)
    result["target"] = pd.to_numeric(result["target"], errors="coerce")
    known = result["target"].dropna()
    if (known < 0).any():
        raise DataContractError("known passenger targets cannot be negative")
    if not np.allclose(known.to_numpy(), np.round(known.to_numpy()), atol=1e-9):
        raise DataContractError("known passenger targets must be integer-like")
    result = _add_calendar_keys(result, timezone)
    _require_unique(result, ["station_id", "timestamp_utc"], "passenger history")
    return result[["timestamp_utc", "local_date", "local_hour", "station_id", "target"]]


def _sensor_ids(frame: pd.DataFrame) -> pd.Series[Any]:
    def make_id(row: pd.Series[Any]) -> str:
        coordinates = f"{float(row['latitude']):.6f},{float(row['longitude']):.6f}"
        return "sensor_" + hashlib.sha256(coordinates.encode()).hexdigest()[:12]

    return frame[["latitude", "longitude"]].apply(make_id, axis=1)


def normalize_traffic(frame: pd.DataFrame, timezone: str = "Europe/Berlin") -> pd.DataFrame:
    result = _rename(frame, ("timestamp", "latitude", "longitude", "traffic"))
    result = result.join(_timestamps(result.pop("timestamp"), timezone))
    for column in ("latitude", "longitude", "traffic"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    if result[["latitude", "longitude"]].isna().any().any():
        raise DataContractError("traffic sensor coordinates cannot be null")
    if (result["traffic"].dropna() < 0).any():
        raise DataContractError("traffic values cannot be negative")
    result["sensor_id"] = _sensor_ids(result)
    result = _add_calendar_keys(result, timezone)
    _require_unique(result, ["sensor_id", "timestamp_utc"], "traffic history")
    return result[
        [
            "timestamp_utc",
            "local_date",
            "local_hour",
            "sensor_id",
            "latitude",
            "longitude",
            "traffic",
        ]
    ]


def normalize_weather(frame: pd.DataFrame, timezone: str = "Europe/Berlin") -> pd.DataFrame:
    result = _rename(frame, ("timestamp", "temperature", "precipitation"))
    result = result.join(_timestamps(result.pop("timestamp"), timezone))
    result["temperature"] = pd.to_numeric(result["temperature"], errors="coerce")
    result["precipitation"] = pd.to_numeric(result["precipitation"], errors="coerce")
    if (result["precipitation"].dropna() < 0).any():
        raise DataContractError("precipitation cannot be negative")
    result = _add_calendar_keys(result, timezone)
    _require_unique(result, ["timestamp_utc"], "weather")
    return result[["timestamp_utc", "local_date", "local_hour", "temperature", "precipitation"]]


def _assert_complete_hours(frame: pd.DataFrame, keys: list[str], label: str) -> None:
    groups: list[tuple[object, pd.Series[Any]]]
    if keys:
        groups = list(frame.groupby(keys, observed=True)["timestamp_utc"])
    else:
        groups = [("all", frame["timestamp_utc"])]
    for group_key, timestamps in groups:
        unique = pd.DatetimeIndex(timestamps.unique()).sort_values()
        expected = pd.date_range(unique.min(), unique.max(), freq="h", tz="UTC")
        if len(unique) != len(expected):
            raise DataContractError(f"{label} {group_key!r} has missing source hours")


def verify_supplied_files(data_dir: Path) -> dict[str, str]:
    """Verify byte digests and physical shapes before parsing supplied data."""

    found: dict[str, str] = {}
    for filename, (rows, columns, expected_digest) in SUPPLIED_FILES.items():
        path = data_dir / filename
        if not path.is_file():
            raise DataContractError(f"missing supplied file: {path}")
        digest = sha256_file(path)
        if digest != expected_digest:
            raise DataContractError(f"checksum mismatch for {filename}: {digest}")
        shape = pd.read_csv(path).shape
        if shape != (rows, columns):
            raise DataContractError(f"shape mismatch for {filename}: {shape} != {(rows, columns)}")
        found[filename] = digest
    return found


def global_date_split(
    passengers: pd.DataFrame,
    *,
    holdout_days: int = 7,
    require_rows_per_date: int | None = None,
) -> DateSplit:
    """Split on the final unique local dates, independent of CSV row ordering."""

    dates = tuple(sorted(passengers["local_date"].unique()))
    if len(dates) <= holdout_days:
        raise DataContractError("not enough unique dates for the requested holdout")
    test_dates = dates[-holdout_days:]
    train_dates = dates[:-holdout_days]
    test_mask = passengers["local_date"].isin(test_dates)
    train = (
        passengers.loc[~test_mask]
        .sort_values(["timestamp_utc", "station_id"])
        .reset_index(drop=True)
    )
    test = (
        passengers.loc[test_mask]
        .sort_values(["timestamp_utc", "station_id"])
        .reset_index(drop=True)
    )
    if require_rows_per_date is not None:
        counts = passengers.groupby("local_date", observed=True).size()
        bad = counts[counts != require_rows_per_date]
        if len(bad):
            raise DataContractError(f"unexpected passenger rows per date: {bad.to_dict()}")
    return DateSplit(train=train, test=test, train_dates=train_dates, test_dates=test_dates)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    value = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius_km * math.asin(math.sqrt(value))


def nearest_sensor_mapping(stations: pd.DataFrame, traffic: pd.DataFrame) -> pd.DataFrame:
    """Build one stable nearest-sensor row per station with distance diagnostics."""

    sensor_locations = traffic[["sensor_id", "latitude", "longitude"]].drop_duplicates()
    if sensor_locations["sensor_id"].duplicated().any():
        raise DataContractError("a sensor ID maps to multiple coordinate pairs")
    if sensor_locations.empty:
        raise DataContractError("traffic contains no sensors")
    records: list[dict[str, object]] = []
    for station in stations.itertuples(index=False):
        station_latitude = float(cast(Any, station.latitude))
        station_longitude = float(cast(Any, station.longitude))
        distance_values = [
            haversine_km(
                station_latitude,
                station_longitude,
                float(cast(Any, sensor.latitude)),
                float(cast(Any, sensor.longitude)),
            )
            for sensor in sensor_locations.itertuples(index=False)
        ]
        distances = pd.Series(
            distance_values,
            index=sensor_locations.index,
            dtype=float,
        )
        chosen_index = distances.idxmin()
        chosen = sensor_locations.loc[chosen_index]
        records.append(
            {
                "station_id": str(station.station_id),
                "sensor_id": str(chosen["sensor_id"]),
                "sensor_distance_km": float(distances.loc[chosen_index]),
            }
        )
    result = pd.DataFrame(records).sort_values("station_id").reset_index(drop=True)
    _require_unique(result, ["station_id"], "station-to-sensor mapping")
    return result


def validate_datasets(
    stations: pd.DataFrame,
    passengers: pd.DataFrame,
    traffic: pd.DataFrame,
    weather: pd.DataFrame,
    *,
    supplied: bool = False,
    timezone: str = "Europe/Berlin",
) -> DatasetBundle:
    """Normalize four frames and enforce target-grain and time-series invariants."""

    station_frame = normalize_stations(stations)
    passenger_frame = normalize_passengers(passengers, timezone)
    traffic_frame = normalize_traffic(traffic, timezone)
    weather_frame = normalize_weather(weather, timezone)

    orphaned = set(passenger_frame["station_id"]) - set(station_frame["station_id"])
    if orphaned:
        raise DataContractError(f"passenger rows reference unknown stations: {sorted(orphaned)}")
    service_hours = set(passenger_frame["local_hour"].unique())
    if service_hours != {7, 8, 9, 10, 11, 12}:
        raise DataContractError(
            f"passenger service hours are {sorted(service_hours)}, expected 07:00-12:00"
        )
    _assert_complete_hours(weather_frame, [], "weather")
    _assert_complete_hours(traffic_frame, ["sensor_id"], "traffic sensor")

    sensor_count = traffic_frame["sensor_id"].nunique()
    split = global_date_split(
        passenger_frame,
        holdout_days=7,
        require_rows_per_date=60 if supplied else None,
    )
    joined = passenger_frame[["station_id", "timestamp_utc"]].merge(
        weather_frame[["timestamp_utc"]],
        on="timestamp_utc",
        how="left",
        validate="many_to_one",
        indicator=True,
    )
    if (joined["_merge"] != "both").any():
        raise DataContractError("weather is missing passenger target hours")

    metadata_capacity = station_frame[["station_id", "station_capacity"]]
    capacity_check = passenger_frame.merge(
        metadata_capacity, on="station_id", validate="many_to_one"
    )
    exceedances = int((capacity_check["target"] > capacity_check["station_capacity"]).sum())
    warnings: list[str] = []
    if exceedances:
        warnings.append(
            f"{exceedances} known targets exceed descriptive station_capacity; rows retained"
        )
    if passenger_frame["target"].isna().any():
        warnings.append(f"{int(passenger_frame['target'].isna().sum())} target labels are null")

    if supplied:
        expected = {
            "station rows": (len(station_frame), 10),
            "passenger rows": (len(passenger_frame), 67_740),
            "traffic rows": (len(traffic_frame), 135_480),
            "weather rows": (len(weather_frame), 27_096),
            "stations": (station_frame["station_id"].nunique(), 10),
            "sensors": (sensor_count, 5),
            "dates": (passenger_frame["local_date"].nunique(), 1_129),
            "train rows": (len(split.train), 67_320),
            "test rows": (len(split.test), 420),
            "null targets": (int(passenger_frame["target"].isna().sum()), 465),
            "null test targets": (int(split.test["target"].isna().sum()), 3),
            "capacity exceedances": (exceedances, 394),
            "weather temperature nulls": (
                int(weather_frame["temperature"].isna().sum()),
                143,
            ),
            "weather precipitation nulls": (
                int(weather_frame["precipitation"].isna().sum()),
                158,
            ),
            "traffic nulls": (int(traffic_frame["traffic"].isna().sum()), 0),
        }
        failures = {name: actual for name, (actual, wanted) in expected.items() if actual != wanted}
        if failures:
            raise DataContractError(f"supplied dataset facts do not match: {failures}")

    audit = DatasetAudit(
        station_rows=len(station_frame),
        passenger_rows=len(passenger_frame),
        traffic_rows=len(traffic_frame),
        weather_rows=len(weather_frame),
        station_count=station_frame["station_id"].nunique(),
        sensor_count=sensor_count,
        date_count=passenger_frame["local_date"].nunique(),
        null_targets=int(passenger_frame["target"].isna().sum()),
        null_test_targets=int(split.test["target"].isna().sum()),
        capacity_exceedances=exceedances,
        weather_temperature_nulls=int(weather_frame["temperature"].isna().sum()),
        weather_precipitation_nulls=int(weather_frame["precipitation"].isna().sum()),
        traffic_nulls=int(traffic_frame["traffic"].isna().sum()),
        warnings=tuple(warnings),
    )
    return DatasetBundle(station_frame, passenger_frame, traffic_frame, weather_frame, audit)
