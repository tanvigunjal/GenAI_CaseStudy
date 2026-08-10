"""Typed, immutable application configuration."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SearchBudget(BaseModel):
    """Hard bounds for remote model selection."""

    model_config = ConfigDict(frozen=True)

    seed: int = 20260808
    cpu_threads: int = Field(default=6, ge=1, le=64)
    max_rss_gib: float = Field(default=8.0, gt=0)
    max_tuning_seconds: int = Field(default=7200, ge=1)
    lightgbm_trials: int = Field(default=20, ge=0)
    catboost_trials: int = Field(default=12, ge=0)
    tweedie_alphas: tuple[float, ...] = (0.0001, 0.001, 0.01, 0.1, 1.0, 10.0)


class ForecastSettings(BaseSettings):
    """Settings shared by every command; environment prefix is ``FORECAST_``."""

    model_config = SettingsConfigDict(
        env_prefix="FORECAST_",
        env_file=".env",
        extra="ignore",
        frozen=True,
    )

    raw_data_dir: Path = Path("data/raw")
    output_dir: Path = Path("artifacts")
    timezone: str = "Europe/Berlin"
    execution_target: str = "local"
    log_level: str = "INFO"
    horizon_days: int = Field(default=7, ge=1)
    service_hours: tuple[int, ...] = (7, 8, 9, 10, 11, 12)
    schema_version: str = "1.0.0"
    max_rss_gib: float = Field(default=8.0, gt=0)
    max_tuning_seconds: int = Field(default=7200, ge=1)
    cpu_threads: int = Field(default=6, ge=1, le=64)
    seed: int = 20260808

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @field_validator("service_hours")
    @classmethod
    def valid_service_hours(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if (
            not value
            or len(set(value)) != len(value)
            or any(hour < 0 or hour > 23 for hour in value)
        ):
            raise ValueError("service_hours must contain unique hours in [0, 23]")
        return tuple(sorted(value))

    @property
    def search_budget(self) -> SearchBudget:
        return SearchBudget(
            seed=self.seed,
            cpu_threads=self.cpu_threads,
            max_rss_gib=self.max_rss_gib,
            max_tuning_seconds=self.max_tuning_seconds,
        )

    def stable_hash(self) -> str:
        return stable_hash(self.model_dump(mode="json"))


def stable_hash(value: Any) -> str:
    """Hash JSON-compatible configuration with deterministic ordering."""

    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(payload).hexdigest()
