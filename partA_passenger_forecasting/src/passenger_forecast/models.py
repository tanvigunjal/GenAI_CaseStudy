"""Fold-local adapters for the three locked trainable challengers."""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any, Protocol, Self, cast

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from sklearn.compose import ColumnTransformer  # type: ignore[import-untyped]
from sklearn.impute import SimpleImputer  # type: ignore[import-untyped]
from sklearn.linear_model import TweedieRegressor  # type: ignore[import-untyped]
from sklearn.pipeline import Pipeline  # type: ignore[import-untyped]
from sklearn.preprocessing import (  # type: ignore[import-untyped]
    OneHotEncoder,
    StandardScaler,
)


class ModelAdapter(Protocol):
    family: str

    def fit(
        self,
        features: pd.DataFrame,
        target: NDArray[Any],
        *,
        validation_features: pd.DataFrame | None = None,
        validation_target: NDArray[Any] | None = None,
    ) -> Self: ...

    def predict(self, features: pd.DataFrame) -> NDArray[np.float64]: ...


class _TreeFrameEncoder:
    """Encode categories fold-locally while retaining numeric NaNs for tree models."""

    def __init__(self, categorical_columns: tuple[str, ...]) -> None:
        self.categorical_columns = categorical_columns
        self.columns_: tuple[str, ...] | None = None
        self.categories_: dict[str, dict[str, int]] = {}

    def fit_transform(self, frame: pd.DataFrame) -> pd.DataFrame:
        self.columns_ = tuple(frame.columns)
        result = frame.copy()
        for column in self.categorical_columns:
            values = result[column].fillna("__missing__").astype(str)
            categories = {value: index for index, value in enumerate(sorted(values.unique()))}
            self.categories_[column] = categories
            result[column] = values.map(categories).astype(float)
        return result.astype(float)

    def transform(self, frame: pd.DataFrame) -> pd.DataFrame:
        if self.columns_ is None:
            raise RuntimeError("encoder is not fitted")
        if tuple(frame.columns) != self.columns_:
            raise ValueError("feature schema/order differs from fitted model")
        result = frame.copy()
        for column in self.categorical_columns:
            mapping = self.categories_[column]
            result[column] = (
                result[column]
                .fillna("__missing__")
                .astype(str)
                .map(mapping)
                .fillna(-1)
                .astype(float)
            )
        return result.astype(float)


class LightGBMAdapter:
    family = "lightgbm"

    def __init__(
        self,
        parameters: dict[str, Any] | None = None,
        *,
        categorical_columns: tuple[str, ...] = ("station_id",),
        seed: int = 20260808,
        threads: int = 6,
    ) -> None:
        self.parameters = dict(parameters or {})
        self.categorical_columns = categorical_columns
        self.seed = seed
        self.threads = threads
        self.encoder = _TreeFrameEncoder(categorical_columns)
        self.model: Any | None = None

    def fit(
        self,
        features: pd.DataFrame,
        target: NDArray[Any],
        *,
        validation_features: pd.DataFrame | None = None,
        validation_target: NDArray[Any] | None = None,
    ) -> Self:
        from lightgbm import LGBMRegressor, early_stopping

        known = np.isfinite(target)
        if not known.any():
            raise ValueError("training target has no observed labels")
        encoded = self.encoder.fit_transform(features.loc[known])
        defaults: dict[str, Any] = {
            "objective": "regression",
            "metric": "rmse",
            "n_estimators": 2000,
            "learning_rate": 0.03,
            "num_leaves": 31,
            "random_state": self.seed,
            "n_jobs": self.threads,
            "deterministic": True,
            "force_col_wise": True,
            "verbosity": -1,
        }
        defaults.update(self.parameters)
        self.model = LGBMRegressor(**defaults)
        fit_kwargs: dict[str, Any] = {}
        if validation_features is not None and validation_target is not None:
            validation_known = np.isfinite(validation_target)
            fit_kwargs = {
                "eval_set": [
                    (
                        self.encoder.transform(validation_features.loc[validation_known]),
                        validation_target[validation_known],
                    )
                ],
                "callbacks": [early_stopping(100, verbose=False)],
            }
        self.model.fit(encoded, target[known], **fit_kwargs)
        return self

    def predict(self, features: pd.DataFrame) -> NDArray[np.float64]:
        if self.model is None:
            raise RuntimeError("model is not fitted")
        return np.asarray(self.model.predict(self.encoder.transform(features)), dtype=float)


class CatBoostAdapter:
    family = "catboost"

    def __init__(
        self,
        parameters: dict[str, Any] | None = None,
        *,
        categorical_columns: tuple[str, ...] = ("station_id",),
        seed: int = 20260808,
        threads: int = 6,
    ) -> None:
        self.parameters = dict(parameters or {})
        self.categorical_columns = categorical_columns
        self.seed = seed
        self.threads = threads
        self.columns_: tuple[str, ...] | None = None
        self.model: Any | None = None

    def _prepare(self, features: pd.DataFrame) -> pd.DataFrame:
        if self.columns_ is not None and tuple(features.columns) != self.columns_:
            raise ValueError("feature schema/order differs from fitted model")
        result = features.copy()
        for column in self.categorical_columns:
            result[column] = result[column].fillna("__missing__").astype(str)
        for column in set(result.columns) - set(self.categorical_columns):
            result[column] = pd.to_numeric(result[column], errors="coerce")
        return result

    def fit(
        self,
        features: pd.DataFrame,
        target: NDArray[Any],
        *,
        validation_features: pd.DataFrame | None = None,
        validation_target: NDArray[Any] | None = None,
    ) -> Self:
        from catboost import CatBoostRegressor  # type: ignore[import-untyped]

        known = np.isfinite(target)
        if not known.any():
            raise ValueError("training target has no observed labels")
        self.columns_ = tuple(features.columns)
        defaults: dict[str, Any] = {
            "loss_function": "RMSE",
            "iterations": 1500,
            "learning_rate": 0.03,
            "depth": 8,
            "random_seed": self.seed,
            "thread_count": self.threads,
            "verbose": False,
            "allow_writing_files": False,
        }
        defaults.update(self.parameters)
        self.model = CatBoostRegressor(**defaults)
        fit_kwargs: dict[str, Any] = {}
        if validation_features is not None and validation_target is not None:
            validation_known = np.isfinite(validation_target)
            fit_kwargs = {
                "eval_set": (
                    self._prepare(validation_features.loc[validation_known]),
                    validation_target[validation_known],
                ),
                "early_stopping_rounds": 100,
                "use_best_model": True,
            }
        self.model.fit(
            self._prepare(features.loc[known]),
            target[known],
            cat_features=list(self.categorical_columns),
            **fit_kwargs,
        )
        return self

    def predict(self, features: pd.DataFrame) -> NDArray[np.float64]:
        if self.model is None:
            raise RuntimeError("model is not fitted")
        return np.asarray(self.model.predict(self._prepare(features)), dtype=float)


class TweedieAdapter:
    family = "tweedie"

    def __init__(
        self,
        parameters: dict[str, Any] | None = None,
        *,
        categorical_columns: tuple[str, ...] = ("station_id",),
        seed: int = 20260808,
        threads: int = 6,
    ) -> None:
        del seed, threads
        self.parameters = dict(parameters or {})
        self.categorical_columns = categorical_columns
        self.columns_: tuple[str, ...] | None = None
        self.pipeline: Pipeline | None = None

    def fit(
        self,
        features: pd.DataFrame,
        target: NDArray[Any],
        *,
        validation_features: pd.DataFrame | None = None,
        validation_target: NDArray[Any] | None = None,
    ) -> Self:
        del validation_features, validation_target
        known = np.isfinite(target)
        if not known.any():
            raise ValueError("training target has no observed labels")
        if (target[known] < 0).any():
            raise ValueError("Tweedie target cannot be negative")
        self.columns_ = tuple(features.columns)
        numeric_columns = [
            column for column in features.columns if column not in self.categorical_columns
        ]
        preprocess = ColumnTransformer(
            transformers=[
                (
                    "numeric",
                    Pipeline(
                        [
                            ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
                            ("scale", StandardScaler()),
                        ]
                    ),
                    numeric_columns,
                ),
                (
                    "categorical",
                    OneHotEncoder(handle_unknown="ignore", sparse_output=True),
                    list(self.categorical_columns),
                ),
            ]
        )
        defaults: dict[str, Any] = {"power": 1.0, "link": "log", "alpha": 0.1, "max_iter": 1000}
        defaults.update(self.parameters)
        self.pipeline = Pipeline(
            [("preprocess", preprocess), ("regressor", TweedieRegressor(**defaults))]
        )
        self.pipeline.fit(features.loc[known], target[known])
        return self

    def predict(self, features: pd.DataFrame) -> NDArray[np.float64]:
        if self.pipeline is None or self.columns_ is None:
            raise RuntimeError("model is not fitted")
        if tuple(features.columns) != self.columns_:
            raise ValueError("feature schema/order differs from fitted model")
        return np.asarray(self.pipeline.predict(features), dtype=float)


def create_model(
    family: str,
    parameters: dict[str, Any] | None = None,
    *,
    categorical_columns: tuple[str, ...] = ("station_id",),
    seed: int = 20260808,
    threads: int = 6,
) -> ModelAdapter:
    if family == "lightgbm":
        return LightGBMAdapter(
            parameters,
            categorical_columns=categorical_columns,
            seed=seed,
            threads=threads,
        )
    if family == "catboost":
        return CatBoostAdapter(
            parameters,
            categorical_columns=categorical_columns,
            seed=seed,
            threads=threads,
        )
    if family == "tweedie":
        return TweedieAdapter(
            parameters,
            categorical_columns=categorical_columns,
            seed=seed,
            threads=threads,
        )
    raise ValueError(f"unknown model family: {family}")


def save_model(model: ModelAdapter, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pickle.dumps(model, protocol=pickle.HIGHEST_PROTOCOL))
    return path


def load_model(path: Path) -> ModelAdapter:
    loaded: Any = pickle.loads(path.read_bytes())
    if not hasattr(loaded, "predict"):
        raise TypeError("artifact does not implement the model adapter contract")
    return cast(ModelAdapter, loaded)
