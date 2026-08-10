"""Single cutoff-aware feature builder used by training, backtests, and forecasting."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from passenger_forecast.config import stable_hash
from passenger_forecast.data import DataContractError, nearest_sensor_mapping

PASSENGER_LAG_DAYS = (7, 14, 21, 28, 364)
PASSENGER_WINDOWS_DAYS = (28, 56, 91)
TRAFFIC_LAG_DAYS = (7, 14, 28)


@dataclass(frozen=True)
class FeatureBuildResult:
    frame: pd.DataFrame
    feature_columns: tuple[str, ...]
    categorical_columns: tuple[str, ...]
    lineage: Mapping[str, str]
    station_sensor_mapping: pd.DataFrame
    schema_version: str
    schema_hash: str

    def persist_schema(self, path: Path) -> Path:
        payload = {
            "schema_version": self.schema_version,
            "schema_hash": self.schema_hash,
            "feature_columns": list(self.feature_columns),
            "categorical_columns": list(self.categorical_columns),
            "lineage": dict(self.lineage),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return path


def _as_utc(value: datetime | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise DataContractError("feature cutoff must include a UTC offset")
    return timestamp.tz_convert(UTC)


def _date_subtract(values: pd.Series[Any], days: int) -> pd.Series[Any]:
    return values.map(lambda value: value - timedelta(days=days))


def _history_groups(
    frame: pd.DataFrame,
    group_columns: Sequence[str],
    value_column: str,
) -> dict[tuple[object, ...], tuple[NDArray[np.int64], NDArray[np.float64]]]:
    groups: dict[tuple[object, ...], tuple[NDArray[np.int64], NDArray[np.float64]]] = {}
    grouping: str | list[str]
    grouping = group_columns[0] if len(group_columns) == 1 else list(group_columns)
    for raw_key, group in frame.groupby(grouping, observed=True, sort=False):
        key = raw_key if isinstance(raw_key, tuple) else (raw_key,)
        ordered = group.sort_values("local_date")
        dates = np.array([item.toordinal() for item in ordered["local_date"]], dtype=np.int64)
        values = ordered[value_column].to_numpy(dtype=float)
        groups[key] = (dates, values)
    return groups


def _window_stats(
    targets: pd.DataFrame,
    history: pd.DataFrame,
    *,
    group_columns: Sequence[str],
    value_column: str,
    windows: Sequence[int],
    prefix: str,
) -> dict[str, NDArray[np.float64]]:
    """Compute calendar-window statistics using only dates strictly before each target."""

    groups = _history_groups(history, group_columns, value_column)
    output = {
        f"{prefix}_mean_{days}d": np.full(len(targets), np.nan, dtype=float) for days in windows
    }
    output.update(
        {f"{prefix}_median_{days}d": np.full(len(targets), np.nan, dtype=float) for days in windows}
    )
    for position, row in enumerate(targets.itertuples(index=False)):
        key = tuple(getattr(row, column) for column in group_columns)
        grouped = groups.get(key)
        if grouped is None:
            continue
        dates, values = grouped
        target_ordinal = cast(date, row.local_date).toordinal()
        right = int(np.searchsorted(dates, target_ordinal, side="left"))
        for days in windows:
            left = int(np.searchsorted(dates, target_ordinal - days, side="left"))
            window = values[left:right]
            known = window[np.isfinite(window)]
            if len(known):
                output[f"{prefix}_mean_{days}d"][position] = float(np.mean(known))
                output[f"{prefix}_median_{days}d"][position] = float(np.median(known))
    return output


class CutoffFeatureBuilder:
    """Build point-in-time features with passenger/traffic bounded by one origin."""

    def __init__(self, timezone: str = "Europe/Berlin", schema_version: str = "1.0.0") -> None:
        self.timezone = timezone
        self.schema_version = schema_version

    def build(
        self,
        target_rows: pd.DataFrame,
        *,
        origin: datetime | pd.Timestamp,
        stations: pd.DataFrame,
        passenger_history: pd.DataFrame,
        weather: pd.DataFrame,
        traffic: pd.DataFrame,
        station_sensor_mapping: pd.DataFrame | None = None,
    ) -> FeatureBuildResult:
        cutoff = _as_utc(origin)
        targets = self._target_keys(target_rows)
        passenger_allowed = passenger_history.loc[
            passenger_history["timestamp_utc"] <= cutoff
        ].copy()
        traffic_allowed = traffic.loc[traffic["timestamp_utc"] <= cutoff].copy()
        mapping = (
            nearest_sensor_mapping(stations, traffic)
            if station_sensor_mapping is None
            else station_sensor_mapping.copy()
        )
        mapping_keys = {"station_id", "sensor_id"}
        missing_mapping_keys = mapping_keys - set(mapping.columns)
        if missing_mapping_keys:
            raise DataContractError(
                f"station-to-sensor mapping is missing columns: {sorted(missing_mapping_keys)}"
            )
        if mapping[list(mapping_keys)].isna().any().any():
            raise DataContractError("station-to-sensor mapping contains missing identifiers")
        for column in mapping_keys:
            mapping[column] = mapping[column].astype(str)
        if mapping["station_id"].duplicated().any():
            raise DataContractError("station-to-sensor mapping must be one row per station")

        result = self._calendar(targets)
        lineage: dict[str, str] = {
            "station_id": "target station key",
            "local_hour": "target timestamp converted to service timezone",
            "weekday": "service-calendar weekday",
            "is_weekend": "service-calendar Saturday/Sunday indicator",
            "month": "service-calendar month",
            "day_of_year": "service-calendar ordinal day",
            "weekday_sin": "cyclical service-calendar weekday encoding",
            "weekday_cos": "cyclical service-calendar weekday encoding",
            "day_of_year_sin": "cyclical service-calendar day-of-year encoding",
            "day_of_year_cos": "cyclical service-calendar day-of-year encoding",
            "hour_sin": "cyclical local-hour encoding",
            "hour_cos": "cyclical local-hour encoding",
            "trend_index": "service-calendar date ordinal",
        }
        result = self._station_features(result, stations, lineage)
        result = self._passenger_features(result, passenger_allowed, lineage)
        result = self._weather_features(result, weather, lineage)
        result = self._traffic_features(result, traffic_allowed, mapping, lineage)

        identity = {"timestamp_utc", "local_date", "target", "_row_order"}
        feature_columns = tuple(column for column in result.columns if column not in identity)
        categorical_columns = ("station_id",)
        schema_payload = {
            "version": self.schema_version,
            "features": feature_columns,
            "categorical": categorical_columns,
            "lineage": lineage,
        }
        schema_hash = stable_hash(schema_payload)
        result = result.sort_values("_row_order").drop(columns="_row_order").reset_index(drop=True)
        return FeatureBuildResult(
            frame=result,
            feature_columns=feature_columns,
            categorical_columns=categorical_columns,
            lineage=MappingProxyType(lineage),
            station_sensor_mapping=mapping.sort_values("station_id").reset_index(drop=True),
            schema_version=self.schema_version,
            schema_hash=schema_hash,
        )

    def _target_keys(self, target_rows: pd.DataFrame) -> pd.DataFrame:
        required = {"timestamp_utc", "station_id"}
        missing = required - set(target_rows.columns)
        if missing:
            raise DataContractError(f"target rows are missing columns: {sorted(missing)}")
        targets = target_rows.copy().reset_index(drop=True)
        targets["station_id"] = targets["station_id"].astype(str)
        targets["timestamp_utc"] = pd.to_datetime(
            targets["timestamp_utc"], utc=True, errors="raise"
        )
        if targets.duplicated(["station_id", "timestamp_utc"]).any():
            raise DataContractError("feature targets contain duplicate station/timestamp keys")
        local = targets["timestamp_utc"].dt.tz_convert(ZoneInfo(self.timezone))
        targets["local_date"] = local.dt.date
        targets["local_hour"] = local.dt.hour.astype("int16")
        targets["_row_order"] = np.arange(len(targets))
        return targets

    def _calendar(self, targets: pd.DataFrame) -> pd.DataFrame:
        result = targets.copy()
        local = result["timestamp_utc"].dt.tz_convert(ZoneInfo(self.timezone))
        weekday = local.dt.weekday
        day_of_year = local.dt.dayofyear
        result["weekday"] = weekday.astype("int8")
        result["is_weekend"] = weekday.isin([5, 6]).astype("int8")
        result["month"] = local.dt.month.astype("int8")
        result["day_of_year"] = day_of_year.astype("int16")
        result["weekday_sin"] = np.sin(2 * np.pi * weekday / 7)
        result["weekday_cos"] = np.cos(2 * np.pi * weekday / 7)
        result["day_of_year_sin"] = np.sin(2 * np.pi * day_of_year / 365.2425)
        result["day_of_year_cos"] = np.cos(2 * np.pi * day_of_year / 365.2425)
        result["hour_sin"] = np.sin(2 * np.pi * result["local_hour"] / 24)
        result["hour_cos"] = np.cos(2 * np.pi * result["local_hour"] / 24)
        result["trend_index"] = result["local_date"].map(date.toordinal).astype("int32")
        return result

    @staticmethod
    def _station_features(
        targets: pd.DataFrame, stations: pd.DataFrame, lineage: dict[str, str]
    ) -> pd.DataFrame:
        columns = [
            "station_id",
            "latitude",
            "longitude",
            "station_capacity",
            "interchange",
            "connectivity",
        ]
        result = targets.merge(
            stations[columns], on="station_id", how="left", validate="many_to_one"
        )
        if result["latitude"].isna().any() or result["longitude"].isna().any():
            unknown = sorted(result.loc[result["latitude"].isna(), "station_id"].unique())
            raise DataContractError(f"feature targets contain unsupported stations: {unknown}")
        for column in columns[1:]:
            lineage[column] = "station_metadata descriptive attribute (capacity is not a ceiling)"
        return result

    @staticmethod
    def _passenger_features(
        targets: pd.DataFrame,
        history: pd.DataFrame,
        lineage: dict[str, str],
    ) -> pd.DataFrame:
        result = targets.copy()
        keys = ["station_id", "local_hour", "local_date"]
        source = history[[*keys, "target"]].copy()
        for days in PASSENGER_LAG_DAYS:
            lag_key = f"_passenger_date_{days}d"
            value = f"passenger_lag_{days}d"
            result[lag_key] = _date_subtract(result["local_date"], days)
            lookup = source.rename(columns={"local_date": lag_key, "target": value})
            result = result.merge(
                lookup,
                on=["station_id", "local_hour", lag_key],
                how="left",
                validate="many_to_one",
            ).drop(columns=lag_key)
            result[f"{value}_missing"] = result[value].isna().astype("int8")
            lineage[value] = (
                f"same station/hour target at exact {days}-day local-date lag; <= cutoff"
            )
            lineage[f"{value}_missing"] = f"missingness indicator for {value}"
        aggregate = _window_stats(
            result,
            source,
            group_columns=("station_id", "local_hour"),
            value_column="target",
            windows=PASSENGER_WINDOWS_DAYS,
            prefix="passenger",
        )
        for name, values in aggregate.items():
            result[name] = values
            result[f"{name}_missing"] = np.isnan(values).astype("int8")
            lineage[name] = (
                "same station/hour historical target window, dates before target and <= cutoff"
            )
            lineage[f"{name}_missing"] = f"missingness indicator for {name}"
        return result

    @staticmethod
    def _weather_features(
        targets: pd.DataFrame,
        weather: pd.DataFrame,
        lineage: dict[str, str],
    ) -> pd.DataFrame:
        source = weather[["timestamp_utc", "temperature", "precipitation"]]
        result = targets.merge(source, on="timestamp_utc", how="left", validate="many_to_one")
        result["temperature_missing"] = result["temperature"].isna().astype("int8")
        result["precipitation_missing"] = result["precipitation"].isna().astype("int8")
        result["rain_indicator"] = (result["precipitation"].fillna(0) > 0).astype("int8")
        lineage["temperature"] = "weather forecast at target valid time (perfect-forecast proxy)"
        lineage["precipitation"] = "weather forecast at target valid time (perfect-forecast proxy)"
        lineage["temperature_missing"] = "missingness indicator for temperature"
        lineage["precipitation_missing"] = "missingness indicator for precipitation"
        lineage["rain_indicator"] = "target-valid precipitation > 0"
        return result

    @staticmethod
    def _traffic_features(
        targets: pd.DataFrame,
        traffic: pd.DataFrame,
        mapping: pd.DataFrame,
        lineage: dict[str, str],
    ) -> pd.DataFrame:
        result = targets.merge(mapping, on="station_id", how="left", validate="many_to_one")
        if result["sensor_id"].isna().any():
            raise DataContractError("station-to-sensor mapping is incomplete")
        city = (
            traffic.groupby(["local_date", "local_hour"], observed=True)["traffic"]
            .agg(["mean", "min", "max", "std"])
            .reset_index()
            .rename(
                columns={name: f"traffic_city_{name}" for name in ("mean", "min", "max", "std")}
            )
        )
        # Europe/Berlin repeats a local clock hour at the autumn DST transition.
        # Collapse that explicitly at the local-date/hour feature grain before
        # validated lag joins; service-hour targets remain one row per key.
        nearest = (
            traffic.groupby(
                ["sensor_id", "local_date", "local_hour"], observed=True, as_index=False
            )
            .agg(traffic=("traffic", "mean"))
            .sort_values(["sensor_id", "local_date", "local_hour"])
        )
        for days in TRAFFIC_LAG_DAYS:
            date_key = f"_traffic_date_{days}d"
            result[date_key] = _date_subtract(result["local_date"], days)
            nearest_lookup = nearest.rename(
                columns={"local_date": date_key, "traffic": f"traffic_nearest_lag_{days}d"}
            )
            result = result.merge(
                nearest_lookup,
                on=["sensor_id", "local_hour", date_key],
                how="left",
                validate="many_to_one",
            )
            city_lookup = city.rename(
                columns={
                    "local_date": date_key,
                    **{
                        f"traffic_city_{stat}": f"traffic_city_{stat}_lag_{days}d"
                        for stat in ("mean", "min", "max", "std")
                    },
                }
            )
            result = result.merge(
                city_lookup,
                on=["local_hour", date_key],
                how="left",
                validate="many_to_one",
            ).drop(columns=date_key)
            names = [f"traffic_nearest_lag_{days}d"] + [
                f"traffic_city_{stat}_lag_{days}d" for stat in ("mean", "min", "max", "std")
            ]
            for name in names:
                result[f"{name}_missing"] = result[name].isna().astype("int8")
                lineage[name] = f"traffic at exact {days}-day lag, source timestamp <= cutoff"
                lineage[f"{name}_missing"] = f"missingness indicator for {name}"

        nearest_aggregates = _window_stats(
            result,
            nearest,
            group_columns=("sensor_id", "local_hour"),
            value_column="traffic",
            windows=(28, 56, 91),
            prefix="traffic_nearest",
        )
        city_history = city.rename(columns={"traffic_city_mean": "traffic"})
        city_aggregates = _window_stats(
            result,
            city_history,
            group_columns=("local_hour",),
            value_column="traffic",
            windows=(28, 56, 91),
            prefix="traffic_city",
        )
        for name, values in {**nearest_aggregates, **city_aggregates}.items():
            result[name] = values
            result[f"{name}_missing"] = np.isnan(values).astype("int8")
            lineage[name] = "cutoff-safe historical traffic window before target"
            lineage[f"{name}_missing"] = f"missingness indicator for {name}"
        lineage["sensor_id"] = "stable coordinate-derived nearest traffic sensor"
        lineage["sensor_distance_km"] = "haversine station-to-nearest-sensor distance"
        return result.drop(columns="sensor_id")
