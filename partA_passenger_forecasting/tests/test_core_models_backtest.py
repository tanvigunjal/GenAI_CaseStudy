from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Self

import numpy as np
import pandas as pd
import pytest

from passenger_forecast.backtest import (
    CandidateSpec,
    PreparedFold,
    require_remote_full_data_fit,
    rolling_folds,
    run_candidate_cv,
    select_champion,
    tabpfn_feasibility_record,
)
from passenger_forecast.config import SearchBudget
from passenger_forecast.contracts import CandidateResult
from passenger_forecast.data import DatasetAudit, DatasetBundle
from passenger_forecast.models import TweedieAdapter, load_model, save_model


def _empty_bundle() -> DatasetBundle:
    empty = pd.DataFrame()
    return DatasetBundle(
        stations=empty,
        passengers=empty,
        traffic=empty,
        weather=empty,
        audit=DatasetAudit(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, ()),
    )


def _result(
    candidate_id: str,
    mean_rmse: float,
    worst_station: float,
    latency: float,
    size: int,
) -> CandidateResult:
    return CandidateResult(
        candidate_id=candidate_id,
        model_family="tweedie",
        parameters_json="{}",
        mean_rmse=mean_rmse,
        worst_station_rmse=worst_station,
        inference_latency_ms=latency,
        artifact_size_bytes=size,
        fold_rmse=(mean_rmse,) * 3,
    )


def test_locked_folds_are_expanding_and_exact() -> None:
    dates = pd.date_range("2025-01-01", "2026-01-26", freq="D").date
    folds = rolling_folds(pd.DataFrame({"local_date": dates}))
    assert tuple((fold.validation_dates[0], fold.validation_dates[-1]) for fold in folds) == (
        (date(2026, 1, 6), date(2026, 1, 12)),
        (date(2026, 1, 13), date(2026, 1, 19)),
        (date(2026, 1, 20), date(2026, 1, 26)),
    )
    assert len(folds[0].train_dates) < len(folds[1].train_dates) < len(folds[2].train_dates)


def test_selection_uses_one_percent_band_before_tie_breakers() -> None:
    specs = [
        CandidateSpec.create("best_mean", "tweedie", {}),
        CandidateSpec.create("best_worst", "tweedie", {}),
        CandidateSpec.create("outside_band", "tweedie", {}),
    ]
    results = [
        _result("best_mean", 100.0, 30.0, 10.0, 10),
        _result("best_worst", 100.5, 20.0, 20.0, 20),
        _result("outside_band", 102.0, 1.0, 1.0, 1),
    ]
    assert select_champion(specs, results).selected_spec.candidate_id == "best_worst"


class _ExplodingModel:
    family = "broken"

    def fit(self, *_args: Any, **_kwargs: Any) -> Self:
        raise RuntimeError("synthetic fit failure")

    def predict(self, features: pd.DataFrame) -> np.ndarray:
        return np.zeros(len(features))


def test_failed_trial_becomes_valid_non_selectable_result() -> None:
    frame = pd.DataFrame({"station_id": ["1", "2"], "value": [1.0, 2.0]})
    prepared = PreparedFold(
        fold_id="synthetic",
        train_features=frame,
        train_target=np.array([1.0, 2.0]),
        validation_features=frame,
        validation_target=np.array([1.0, 2.0]),
        validation_stations=np.array(["1", "2"]),
    )
    spec = CandidateSpec.create("broken", "broken", {})

    def factory(*_args: Any, **_kwargs: Any) -> _ExplodingModel:
        return _ExplodingModel()

    result = run_candidate_cv(
        spec,
        _empty_bundle(),
        prepared_folds=(prepared,),
        model_factory=factory,
        budget=SearchBudget(max_rss_gib=100),
    )
    assert not result.completed
    assert result.mean_rmse is None
    assert "synthetic fit failure" in str(result.failure_reason)
    with pytest.raises(ValueError, match="no completed"):
        select_champion((spec,), (result,))


def test_tweedie_preprocessing_and_serialization_are_fold_fitted(tmp_path: Path) -> None:
    features = pd.DataFrame(
        {
            "station_id": ["1", "2", "1", "2", "1", "2"],
            "lag": [1.0, np.nan, 3.0, 4.0, 5.0, 6.0],
        }
    )
    target = np.array([2.0, 3.0, 4.0, 6.0, 8.0, 10.0])
    model = TweedieAdapter({"alpha": 0.1, "max_iter": 200}).fit(features, target)
    expected = model.predict(features)
    artifact = save_model(model, tmp_path / "model.pkl")
    actual = load_model(artifact).predict(features)
    assert np.isfinite(expected).all()
    np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-12)


def test_tabpfn_record_and_remote_fit_gate() -> None:
    assert tabpfn_feasibility_record().status == "skipped_resource_and_fit_gate"
    with pytest.raises(PermissionError, match="hetzner_prod"):
        require_remote_full_data_fit("local")
    require_remote_full_data_fit("hetzner_prod")
