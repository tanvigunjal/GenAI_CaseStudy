"""Official metric calculations at row grain."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from passenger_forecast.contracts import StationMetric


@dataclass(frozen=True)
class MetricReport:
    overall_rmse: float
    overall_bias: float
    scoreable_rows: int
    forecast_rows: int
    coverage: float
    per_station: tuple[StationMetric, ...]


def root_mean_squared_error(observed: NDArray[Any], predicted: NDArray[Any]) -> float:
    actual = np.asarray(observed, dtype=float)
    forecast = np.asarray(predicted, dtype=float)
    if actual.shape != forecast.shape:
        raise ValueError("observed and predicted shapes differ")
    mask = np.isfinite(actual)
    if not mask.any():
        raise ValueError("RMSE needs at least one observed label")
    if not np.isfinite(forecast).all():
        raise ValueError("predictions must all be finite")
    error = actual[mask] - forecast[mask]
    return float(np.sqrt(np.mean(np.square(error))))


def evaluate_predictions(
    station_ids: pd.Series[Any] | NDArray[Any],
    observed: pd.Series[Any] | NDArray[Any],
    predicted: pd.Series[Any] | NDArray[Any],
) -> MetricReport:
    """Clip to zero, score known labels directly, and retain station sample sizes."""

    stations = np.asarray(station_ids, dtype=str)
    actual = np.asarray(observed, dtype=float)
    raw_forecast = np.asarray(predicted, dtype=float)
    if stations.shape != actual.shape or actual.shape != raw_forecast.shape:
        raise ValueError("station, observed, and predicted arrays must have the same shape")
    if not np.isfinite(raw_forecast).all():
        raise ValueError("predictions must all be finite")
    forecast = np.clip(raw_forecast, 0, None)
    known = np.isfinite(actual)
    if not known.any():
        raise ValueError("evaluation needs at least one observed label")
    error = forecast[known] - actual[known]
    station_results: list[StationMetric] = []
    for station in sorted(set(stations)):
        station_mask = known & (stations == station)
        count = int(station_mask.sum())
        if count == 0:
            continue
        station_error = forecast[station_mask] - actual[station_mask]
        station_results.append(
            StationMetric(
                station_id=station,
                rmse=float(np.sqrt(np.mean(np.square(station_error)))),
                bias=float(np.mean(station_error)),
                sample_count=count,
            )
        )
    return MetricReport(
        overall_rmse=float(np.sqrt(np.mean(np.square(error)))),
        overall_bias=float(np.mean(error)),
        scoreable_rows=int(known.sum()),
        forecast_rows=len(actual),
        coverage=float(known.mean()),
        per_station=tuple(station_results),
    )
