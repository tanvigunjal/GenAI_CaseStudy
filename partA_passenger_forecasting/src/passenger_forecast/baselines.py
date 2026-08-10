"""Fixed seasonal-naive and historical-median forecasting baselines."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from passenger_forecast.data import DataContractError


@dataclass(frozen=True)
class BaselinePrediction:
    values: NDArray[np.float64]
    used_historical_median: NDArray[np.bool_]


def _cutoff(origin: datetime | pd.Timestamp) -> pd.Timestamp:
    value = pd.Timestamp(origin)
    if value.tzinfo is None:
        raise DataContractError("baseline origin must include a UTC offset")
    return value.tz_convert(UTC)


def _targets(target_rows: pd.DataFrame, timezone: str = "Europe/Berlin") -> pd.DataFrame:
    frame = target_rows.copy().reset_index(drop=True)
    frame["timestamp_utc"] = pd.to_datetime(frame["timestamp_utc"], utc=True, errors="raise")
    frame["station_id"] = frame["station_id"].astype(str)
    local = frame["timestamp_utc"].dt.tz_convert(ZoneInfo(timezone))
    frame["local_date"] = local.dt.date
    frame["local_hour"] = local.dt.hour.astype("int16")
    if frame.duplicated(["station_id", "timestamp_utc"]).any():
        raise DataContractError("baseline targets contain duplicate station/timestamp keys")
    return frame


def historical_median(
    target_rows: pd.DataFrame,
    passenger_history: pd.DataFrame,
    *,
    origin: datetime | pd.Timestamp,
) -> NDArray[np.float64]:
    """Station by weekday by hour median with finite fallback levels."""

    targets = _targets(target_rows)
    history = passenger_history.loc[
        (passenger_history["timestamp_utc"] <= _cutoff(origin))
        & passenger_history["target"].notna()
    ].copy()
    if history.empty:
        return np.zeros(len(targets), dtype=float)
    history["weekday"] = history["local_date"].map(lambda item: item.weekday())
    targets["weekday"] = targets["local_date"].map(lambda item: item.weekday())
    detailed = (
        history.groupby(["station_id", "weekday", "local_hour"], observed=True)["target"]
        .median()
        .rename("_median")
        .reset_index()
    )
    station_hour = (
        history.groupby(["station_id", "local_hour"], observed=True)["target"]
        .median()
        .rename("_station_hour")
        .reset_index()
    )
    station = (
        history.groupby("station_id", observed=True)["target"]
        .median()
        .rename("_station")
        .reset_index()
    )
    result = targets.merge(
        detailed, on=["station_id", "weekday", "local_hour"], how="left", validate="many_to_one"
    )
    result = result.merge(
        station_hour, on=["station_id", "local_hour"], how="left", validate="many_to_one"
    )
    result = result.merge(station, on="station_id", how="left", validate="many_to_one")
    global_median = float(history["target"].median())
    values = result["_median"].fillna(result["_station_hour"]).fillna(result["_station"])
    return values.fillna(global_median).clip(lower=0).to_numpy(dtype=float)


def seasonal_naive(
    target_rows: pd.DataFrame,
    passenger_history: pd.DataFrame,
    *,
    origin: datetime | pd.Timestamp,
) -> BaselinePrediction:
    """Exact seven-day lag, falling back to the fixed historical hierarchy."""

    targets = _targets(target_rows)
    allowed = passenger_history.loc[passenger_history["timestamp_utc"] <= _cutoff(origin)].copy()
    targets["_lag_date"] = targets["local_date"].map(lambda item: item - timedelta(days=7))
    lookup = allowed[["station_id", "local_date", "local_hour", "target"]].rename(
        columns={"local_date": "_lag_date", "target": "_seasonal"}
    )
    joined = targets.merge(
        lookup,
        on=["station_id", "local_hour", "_lag_date"],
        how="left",
        validate="many_to_one",
        sort=False,
    )
    fallback = historical_median(target_rows, passenger_history, origin=origin)
    missing = joined["_seasonal"].isna().to_numpy()
    values = joined["_seasonal"].to_numpy(dtype=float)
    values[missing] = fallback[missing]
    values = np.clip(values, 0, None)
    if not np.isfinite(values).all():
        raise DataContractError("baseline could not produce finite predictions")
    return BaselinePrediction(values=values, used_historical_median=missing)
