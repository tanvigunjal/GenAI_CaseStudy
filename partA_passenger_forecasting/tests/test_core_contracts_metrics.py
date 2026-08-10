from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from pydantic import ValidationError

from passenger_forecast.contracts import ForecastRecord, ForecastRequest, ForecastRole
from passenger_forecast.metrics import evaluate_predictions, root_mean_squared_error


def test_immutable_request_and_record_round_half_up() -> None:
    timezone = ZoneInfo("Europe/Berlin")
    origin = datetime(2026, 1, 26, 12, tzinfo=timezone)
    request = ForecastRequest(
        origin=origin,
        station_ids=tuple(str(index) for index in range(1, 11)),
        model_version="candidate-1",
    )
    assert request.expected_records == 420
    with pytest.raises(ValidationError):
        request.horizon_days = 4  # type: ignore[misc]
    record = ForecastRecord.from_prediction(
        batch_id="batch",
        origin=origin,
        target_timestamp=origin + timedelta(hours=19),
        station_id="1",
        prediction=2.5,
        role=ForecastRole.CHAMPION,
        model_name="model",
        source_hash="a" * 64,
        model_hash="b" * 64,
        config_hash="c" * 64,
    )
    assert record.rounded_count == 3


def test_metrics_match_direct_numpy_and_do_not_average_station_rmse() -> None:
    observed = np.array([1.0, 3.0, 10.0, np.nan])
    predicted = np.array([-1.0, 5.0, 16.0, 8.0])
    stations = np.array(["a", "a", "b", "b"])
    report = evaluate_predictions(stations, observed, predicted)
    clipped = np.clip(predicted, 0, None)
    direct = np.sqrt(np.mean(np.square(observed[:3] - clipped[:3])))
    assert report.overall_rmse == pytest.approx(direct)
    assert report.scoreable_rows == 3
    assert report.forecast_rows == 4
    assert report.coverage == pytest.approx(0.75)
    assert root_mean_squared_error(observed, clipped) == pytest.approx(direct)
    assert report.overall_rmse != pytest.approx(
        np.mean([station.rmse for station in report.per_station])
    )
