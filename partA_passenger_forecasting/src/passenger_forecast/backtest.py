"""Locked rolling folds, bounded search, selection, ablations, and fit gates."""

from __future__ import annotations

import json
import pickle
import resource
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as clock_time
from types import MappingProxyType
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from passenger_forecast.config import SearchBudget
from passenger_forecast.contracts import AblationResult, CandidateResult, TabPFNDisposition
from passenger_forecast.data import DatasetBundle
from passenger_forecast.features import CutoffFeatureBuilder
from passenger_forecast.metrics import MetricReport, evaluate_predictions
from passenger_forecast.models import ModelAdapter, create_model

LOCKED_VALIDATION_WINDOWS = (
    (date(2026, 1, 6), date(2026, 1, 12)),
    (date(2026, 1, 13), date(2026, 1, 19)),
    (date(2026, 1, 20), date(2026, 1, 26)),
)


class ResourceBudgetExceeded(RuntimeError):
    pass


class SelectionNotFrozen(RuntimeError):
    pass


@dataclass(frozen=True)
class RollingFold:
    fold_id: str
    train_dates: tuple[date, ...]
    validation_dates: tuple[date, ...]
    origin: datetime


@dataclass(frozen=True)
class CandidateSpec:
    candidate_id: str
    family: str
    parameters: Mapping[str, Any]

    @classmethod
    def create(cls, candidate_id: str, family: str, parameters: Mapping[str, Any]) -> CandidateSpec:
        return cls(candidate_id, family, MappingProxyType(dict(parameters)))


@dataclass(frozen=True)
class PreparedFold:
    """Candidate-invariant matrices built once with the fold's own cutoff."""

    fold_id: str
    train_features: pd.DataFrame
    train_target: NDArray[Any]
    validation_features: pd.DataFrame
    validation_target: NDArray[Any]
    validation_stations: NDArray[Any]


@dataclass(frozen=True)
class ResourceUsage:
    elapsed_seconds: float
    peak_rss_gib: float
    cpu_threads: int
    completed_trials: int
    failed_trials: int
    deadline_reached: bool


@dataclass(frozen=True)
class SearchOutcome:
    candidates: tuple[CandidateResult, ...]
    selection: FrozenSelection
    resource_usage: ResourceUsage


@dataclass(frozen=True)
class FrozenSelection:
    selected_spec: CandidateSpec
    selected_result: CandidateResult
    candidate_results: tuple[CandidateResult, ...]
    locked: bool = True


@dataclass(frozen=True)
class OfficialEvaluation:
    selected_candidate_id: str
    champion: MetricReport
    seasonal_naive: MetricReport
    historical_median: MetricReport


def rolling_folds(
    passengers: pd.DataFrame,
    *,
    timezone: str = "Europe/Berlin",
    windows: Sequence[tuple[date, date]] = LOCKED_VALIDATION_WINDOWS,
) -> tuple[RollingFold, ...]:
    available = tuple(sorted(passengers["local_date"].unique()))
    available_set = set(available)
    folds: list[RollingFold] = []
    for position, (start, end) in enumerate(windows, start=1):
        validation_dates = tuple(
            start + timedelta(days=offset) for offset in range((end - start).days + 1)
        )
        missing = set(validation_dates) - available_set
        if missing:
            raise ValueError(f"fold {position} validation dates are absent: {sorted(missing)}")
        train_dates = tuple(item for item in available if item < start)
        if not train_dates:
            raise ValueError(f"fold {position} has no earlier training dates")
        origin_date = start - timedelta(days=1)
        origin = datetime.combine(origin_date, clock_time(12), tzinfo=ZoneInfo(timezone))
        folds.append(
            RollingFold(
                fold_id=f"fold_{position}",
                train_dates=train_dates,
                validation_dates=validation_dates,
                origin=origin,
            )
        )
    return tuple(folds)


def current_peak_rss_gib() -> float:
    raw = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    byte_count = raw if sys.platform == "darwin" else raw * 1024
    return byte_count / (1024**3)


class ResourceAccountant:
    def __init__(self, budget: SearchBudget) -> None:
        self.budget = budget
        self.started = time.monotonic()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def check(self) -> None:
        if self.elapsed > self.budget.max_tuning_seconds:
            raise ResourceBudgetExceeded("aggregate tuning deadline reached")
        rss = current_peak_rss_gib()
        if rss > self.budget.max_rss_gib:
            raise ResourceBudgetExceeded(
                f"process peak RSS {rss:.3f} GiB exceeds {self.budget.max_rss_gib:.3f} GiB"
            )


def _feature_frames(
    bundle: DatasetBundle,
    fold: RollingFold,
    builder: CutoffFeatureBuilder,
) -> tuple[
    pd.DataFrame,
    NDArray[Any],
    pd.DataFrame,
    NDArray[Any],
    NDArray[Any],
]:
    train_rows = bundle.passengers.loc[bundle.passengers["local_date"].isin(fold.train_dates)]
    validation_rows = bundle.passengers.loc[
        bundle.passengers["local_date"].isin(fold.validation_dates)
    ]
    train_features = builder.build(
        train_rows,
        origin=fold.origin,
        stations=bundle.stations,
        passenger_history=bundle.passengers,
        weather=bundle.weather,
        traffic=bundle.traffic,
    )
    validation_features = builder.build(
        validation_rows,
        origin=fold.origin,
        stations=bundle.stations,
        passenger_history=bundle.passengers,
        weather=bundle.weather,
        traffic=bundle.traffic,
        station_sensor_mapping=train_features.station_sensor_mapping,
    )
    if train_features.feature_columns != validation_features.feature_columns:
        raise ValueError("train and validation feature schemas differ")
    columns = list(train_features.feature_columns)
    return (
        train_features.frame[columns],
        train_rows["target"].to_numpy(dtype=float),
        validation_features.frame[columns],
        validation_rows["target"].to_numpy(dtype=float),
        validation_rows["station_id"].to_numpy(dtype=str),
    )


def prepare_fold_matrices(
    bundle: DatasetBundle,
    *,
    builder: CutoffFeatureBuilder | None = None,
    accountant: ResourceAccountant | None = None,
) -> tuple[PreparedFold, ...]:
    """Materialize the three point-in-time fold datasets once for all search trials."""

    feature_builder = builder or CutoffFeatureBuilder()
    prepared: list[PreparedFold] = []
    for fold in rolling_folds(bundle.passengers, timezone=feature_builder.timezone):
        if accountant is not None:
            accountant.check()
        x_train, y_train, x_validation, y_validation, stations = _feature_frames(
            bundle, fold, feature_builder
        )
        prepared.append(
            PreparedFold(
                fold_id=fold.fold_id,
                train_features=x_train,
                train_target=y_train,
                validation_features=x_validation,
                validation_target=y_validation,
                validation_stations=stations,
            )
        )
    return tuple(prepared)


def run_candidate_cv(
    spec: CandidateSpec,
    bundle: DatasetBundle,
    *,
    builder: CutoffFeatureBuilder | None = None,
    budget: SearchBudget | None = None,
    accountant: ResourceAccountant | None = None,
    drop_columns: Sequence[str] = (),
    model_factory: Callable[..., ModelAdapter] = create_model,
    prepared_folds: Sequence[PreparedFold] | None = None,
) -> CandidateResult:
    """Evaluate one fixed spec; a new model/preprocessor is fitted inside every fold."""

    settings = budget or SearchBudget()
    tracker = accountant or ResourceAccountant(settings)
    feature_builder = builder or CutoffFeatureBuilder()
    fold_scores: list[float] = []
    station_parts: list[NDArray[Any]] = []
    target_parts: list[NDArray[Any]] = []
    prediction_parts: list[NDArray[Any]] = []
    inference_ms: list[float] = []
    artifact_sizes: list[int] = []
    try:
        folds = (
            tuple(prepared_folds)
            if prepared_folds is not None
            else prepare_fold_matrices(bundle, builder=feature_builder, accountant=tracker)
        )
        for fold in folds:
            tracker.check()
            x_train = fold.train_features
            y_train = fold.train_target
            x_validation = fold.validation_features
            y_validation = fold.validation_target
            stations = fold.validation_stations
            usable = [column for column in x_train.columns if column not in set(drop_columns)]
            if not usable:
                raise ValueError("ablation removed every feature")
            categorical = tuple(column for column in ("station_id",) if column in usable)
            model = model_factory(
                spec.family,
                dict(spec.parameters),
                categorical_columns=categorical,
                seed=settings.seed,
                threads=settings.cpu_threads,
            )
            model.fit(
                x_train[usable],
                y_train,
                validation_features=x_validation[usable],
                validation_target=y_validation,
            )
            started = time.perf_counter()
            predictions = np.asarray(model.predict(x_validation[usable]), dtype=float)
            elapsed_ms = (time.perf_counter() - started) * 1000
            report = evaluate_predictions(stations, y_validation, predictions)
            fold_scores.append(report.overall_rmse)
            station_parts.append(stations)
            target_parts.append(y_validation)
            prediction_parts.append(predictions)
            inference_ms.append(elapsed_ms)
            artifact_sizes.append(len(pickle.dumps(model, protocol=pickle.HIGHEST_PROTOCOL)))
        combined = evaluate_predictions(
            np.concatenate(station_parts),
            np.concatenate(target_parts),
            np.concatenate(prediction_parts),
        )
        tracker.check()
        return CandidateResult(
            candidate_id=spec.candidate_id,
            model_family=spec.family,
            parameters_json=json.dumps(
                dict(spec.parameters), sort_keys=True, separators=(",", ":")
            ),
            mean_rmse=float(np.mean(fold_scores)),
            worst_station_rmse=max(metric.rmse for metric in combined.per_station),
            inference_latency_ms=float(np.mean(inference_ms)),
            artifact_size_bytes=max(artifact_sizes),
            fold_rmse=tuple(fold_scores),
            completed=True,
        )
    except Exception as exc:  # A trial failure is evidence, not a study-level failure.
        return CandidateResult(
            candidate_id=spec.candidate_id,
            model_family=spec.family,
            parameters_json=json.dumps(
                dict(spec.parameters), sort_keys=True, separators=(",", ":")
            ),
            fold_rmse=tuple(fold_scores),
            completed=False,
            failure_reason=f"{type(exc).__name__}: {exc}",
        )


def select_champion(
    specs: Sequence[CandidateSpec], results: Sequence[CandidateResult]
) -> FrozenSelection:
    """Apply the locked 1%-band, worst-station, latency, then size rule."""

    completed = [item for item in results if item.completed]
    if not completed:
        raise ValueError("model selection has no completed candidates")
    for item in completed:
        if (
            item.mean_rmse is None
            or item.worst_station_rmse is None
            or item.inference_latency_ms is None
            or item.artifact_size_bytes is None
        ):
            raise ValueError("completed candidate is missing selection metrics")
    best_mean = min(float(item.mean_rmse) for item in completed if item.mean_rmse is not None)
    within_one_percent = [
        item
        for item in completed
        if float(item.mean_rmse) <= best_mean * 1.01  # type: ignore[arg-type]
    ]
    selected = min(
        within_one_percent,
        key=lambda item: (
            float(item.worst_station_rmse),  # type: ignore[arg-type]
            float(item.inference_latency_ms),  # type: ignore[arg-type]
            int(item.artifact_size_bytes),  # type: ignore[arg-type]
            float(item.mean_rmse),  # type: ignore[arg-type]
            item.candidate_id,
        ),
    )
    by_id = {spec.candidate_id: spec for spec in specs}
    if selected.candidate_id not in by_id:
        raise ValueError("selected result has no matching candidate spec")
    return FrozenSelection(by_id[selected.candidate_id], selected, tuple(results))


def _lightgbm_parameters(trial: Any) -> dict[str, Any]:
    return {
        "n_estimators": 2000,
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.12, log=True),
        "num_leaves": trial.suggest_int("num_leaves", 15, 127),
        "max_depth": trial.suggest_int("max_depth", 4, 12),
        "min_child_samples": trial.suggest_int("min_child_samples", 10, 120),
        "subsample": trial.suggest_float("subsample", 0.7, 1.0),
        "colsample_bytree": trial.suggest_float("colsample_bytree", 0.65, 1.0),
        "reg_lambda": trial.suggest_float("reg_lambda", 1e-4, 10.0, log=True),
    }


def _catboost_parameters(trial: Any) -> dict[str, Any]:
    return {
        "iterations": 1500,
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.12, log=True),
        "depth": trial.suggest_int("depth", 5, 10),
        "l2_leaf_reg": trial.suggest_float("l2_leaf_reg", 1e-3, 20.0, log=True),
        "random_strength": trial.suggest_float("random_strength", 0.0, 2.0),
    }


def run_bounded_search(
    bundle: DatasetBundle,
    *,
    budget: SearchBudget | None = None,
    builder: CutoffFeatureBuilder | None = None,
) -> SearchOutcome:
    """Run sequential seeded Optuna trials and fixed Tweedie alphas within hard bounds."""

    import optuna

    settings = budget or SearchBudget()
    tracker = ResourceAccountant(settings)
    prepared_folds = prepare_fold_matrices(bundle, builder=builder, accountant=tracker)
    specs: list[CandidateSpec] = []
    results: list[CandidateResult] = []
    deadline_reached = False
    for family, count, suggest in (
        ("lightgbm", settings.lightgbm_trials, _lightgbm_parameters),
        ("catboost", settings.catboost_trials, _catboost_parameters),
    ):
        study = optuna.create_study(
            direction="minimize", sampler=optuna.samplers.TPESampler(seed=settings.seed)
        )
        for index in range(count):
            try:
                tracker.check()
            except ResourceBudgetExceeded:
                deadline_reached = True
                break
            trial = study.ask()
            spec = CandidateSpec.create(f"{family}_{index:02d}", family, suggest(trial))
            result = run_candidate_cv(
                spec,
                bundle,
                builder=builder,
                budget=settings,
                accountant=tracker,
                prepared_folds=prepared_folds,
            )
            specs.append(spec)
            results.append(result)
            if result.completed:
                assert result.mean_rmse is not None
                study.tell(trial, result.mean_rmse)
            else:
                study.tell(trial, state=optuna.trial.TrialState.FAIL)
        if deadline_reached:
            break
    if not deadline_reached:
        for index, alpha in enumerate(settings.tweedie_alphas):
            try:
                tracker.check()
            except ResourceBudgetExceeded:
                deadline_reached = True
                break
            spec = CandidateSpec.create(f"tweedie_{index:02d}", "tweedie", {"alpha": alpha})
            specs.append(spec)
            results.append(
                run_candidate_cv(
                    spec,
                    bundle,
                    builder=builder,
                    budget=settings,
                    accountant=tracker,
                    prepared_folds=prepared_folds,
                )
            )
    selection = select_champion(specs, results)
    failed = sum(not item.completed for item in results)
    usage = ResourceUsage(
        elapsed_seconds=tracker.elapsed,
        peak_rss_gib=current_peak_rss_gib(),
        cpu_threads=settings.cpu_threads,
        completed_trials=len(results) - failed,
        failed_trials=failed,
        deadline_reached=deadline_reached,
    )
    return SearchOutcome(tuple(results), selection, usage)


def tabpfn_feasibility_record() -> TabPFNDisposition:
    """Return the locked v2 disposition without importing, installing, or running TabPFN."""

    return TabPFNDisposition()


def require_remote_full_data_fit(execution_target: str) -> None:
    if execution_target != "hetzner_prod":
        raise PermissionError(
            "full-data fitting and official holdout evaluation are allowed only on hetzner_prod"
        )


def run_feature_ablations(
    selection: FrozenSelection,
    bundle: DatasetBundle,
    *,
    execution_target: str,
    budget: SearchBudget | None = None,
) -> tuple[AblationResult, ...]:
    """Re-run fixed champion parameters after selection; never retune an ablation."""

    require_remote_full_data_fit(execution_target)
    if not selection.locked:
        raise SelectionNotFrozen("feature ablations require a frozen champion")
    families = {
        "calendar": (
            "local_hour",
            "weekday",
            "is_weekend",
            "month",
            "day_of_year",
            "weekday_sin",
            "weekday_cos",
            "day_of_year_sin",
            "day_of_year_cos",
            "hour_sin",
            "hour_cos",
            "trend_index",
        ),
        "station": (
            "station_id",
            "latitude",
            "longitude",
            "station_capacity",
            "interchange",
            "connectivity",
            "sensor_distance_km",
        ),
        "passenger_history": ("passenger_",),
        "weather": (
            "temperature",
            "precipitation",
            "rain_indicator",
        ),
        "traffic": ("traffic_",),
    }
    baseline_rmse = selection.selected_result.mean_rmse
    if baseline_rmse is None:
        raise ValueError("selected candidate has no CV RMSE")
    probe_fold = rolling_folds(bundle.passengers)[0]
    x_train, _, _, _, _ = _feature_frames(bundle, probe_fold, CutoffFeatureBuilder())
    results: list[AblationResult] = []
    for family, patterns in families.items():
        drop = tuple(
            column
            for column in x_train.columns
            if any(column == pattern or column.startswith(pattern) for pattern in patterns)
        )
        candidate = run_candidate_cv(
            selection.selected_spec,
            bundle,
            budget=budget,
            drop_columns=drop,
        )
        if not candidate.completed or candidate.mean_rmse is None:
            continue
        results.append(
            AblationResult(
                feature_family=family,
                overall_rmse=candidate.mean_rmse,
                delta_rmse=candidate.mean_rmse - baseline_rmse,
            )
        )
    return tuple(results)
