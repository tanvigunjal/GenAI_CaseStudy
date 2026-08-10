"""Immutable contracts shared by ingestion, modeling, and publication."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return value


class FrozenContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ForecastRole(StrEnum):
    CHAMPION = "champion"
    FALLBACK = "fallback"


class Severity(StrEnum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"


class SourceSnapshot(FrozenContract):
    source_name: str = Field(min_length=1)
    extracted_at: datetime
    watermark: datetime
    row_count: int = Field(ge=0)
    schema_version: str = Field(min_length=1)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    _validate_extracted_at = field_validator("extracted_at")(_aware)
    _validate_watermark = field_validator("watermark")(_aware)


class ForecastRequest(FrozenContract):
    origin: datetime
    timezone: str = "Europe/Berlin"
    horizon_days: int = Field(default=7, ge=1)
    service_hours: tuple[int, ...] = (7, 8, 9, 10, 11, 12)
    station_ids: tuple[str, ...]
    model_version: str = Field(min_length=1)

    _validate_origin = field_validator("origin")(_aware)

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @field_validator("service_hours")
    @classmethod
    def valid_hours(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if (
            not value
            or len(set(value)) != len(value)
            or any(hour not in range(24) for hour in value)
        ):
            raise ValueError("service_hours must be non-empty unique clock hours")
        return tuple(sorted(value))

    @field_validator("station_ids")
    @classmethod
    def valid_stations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or any(not item.strip() for item in value) or len(set(value)) != len(value):
            raise ValueError("station_ids must be non-empty and unique")
        return tuple(sorted(value))

    @property
    def expected_records(self) -> int:
        return self.horizon_days * len(self.service_hours) * len(self.station_ids)


class ForecastRecord(FrozenContract):
    batch_id: str = Field(min_length=1)
    origin: datetime
    target_timestamp: datetime
    station_id: str = Field(min_length=1)
    lead_hours: float = Field(gt=0)
    forecast: float = Field(ge=0, allow_inf_nan=False)
    rounded_count: int = Field(ge=0)
    role: ForecastRole
    model_name: str = Field(min_length=1)
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    _validate_origin = field_validator("origin")(_aware)
    _validate_target = field_validator("target_timestamp")(_aware)

    @model_validator(mode="after")
    def target_follows_origin(self) -> ForecastRecord:
        actual_lead = (
            self.target_timestamp.astimezone(UTC) - self.origin.astimezone(UTC)
        ).total_seconds()
        if actual_lead <= 0:
            raise ValueError("target_timestamp must follow origin")
        if abs(actual_lead / 3600 - self.lead_hours) > 1e-9:
            raise ValueError("lead_hours does not match origin and target_timestamp")
        expected_rounded = int(Decimal(str(self.forecast)).quantize(Decimal("1"), ROUND_HALF_UP))
        if self.rounded_count != expected_rounded:
            raise ValueError("rounded_count must use deterministic round-half-up")
        return self

    @classmethod
    def from_prediction(
        cls,
        *,
        batch_id: str,
        origin: datetime,
        target_timestamp: datetime,
        station_id: str,
        prediction: float,
        role: ForecastRole,
        model_name: str,
        source_hash: str,
        model_hash: str,
        config_hash: str,
    ) -> ForecastRecord:
        clipped = max(0.0, float(prediction))
        rounded = int(Decimal(str(clipped)).quantize(Decimal("1"), ROUND_HALF_UP))
        lead = (target_timestamp.astimezone(UTC) - origin.astimezone(UTC)).total_seconds() / 3600
        return cls(
            batch_id=batch_id,
            origin=origin,
            target_timestamp=target_timestamp,
            station_id=station_id,
            lead_hours=lead,
            forecast=clipped,
            rounded_count=rounded,
            role=role,
            model_name=model_name,
            source_hash=source_hash,
            model_hash=model_hash,
            config_hash=config_hash,
        )


class StationMetric(FrozenContract):
    station_id: str
    rmse: float = Field(ge=0, allow_inf_nan=False)
    bias: float = Field(allow_inf_nan=False)
    sample_count: int = Field(ge=0)


class CandidateResult(FrozenContract):
    candidate_id: str
    model_family: str
    parameters_json: str
    mean_rmse: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    worst_station_rmse: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    inference_latency_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    artifact_size_bytes: int | None = Field(default=None, ge=0)
    fold_rmse: tuple[float, ...]
    completed: bool = True
    failure_reason: str | None = None

    @model_validator(mode="after")
    def result_matches_status(self) -> CandidateResult:
        metrics = (
            self.mean_rmse,
            self.worst_station_rmse,
            self.inference_latency_ms,
            self.artifact_size_bytes,
        )
        if self.completed and any(value is None for value in metrics):
            raise ValueError("a completed candidate requires all selection metrics")
        if not self.completed and not self.failure_reason:
            raise ValueError("a failed candidate requires failure_reason")
        return self


class AblationResult(FrozenContract):
    feature_family: str
    overall_rmse: float = Field(ge=0, allow_inf_nan=False)
    delta_rmse: float = Field(allow_inf_nan=False)


class TabPFNDisposition(FrozenContract):
    status: str = "skipped_resource_and_fit_gate"
    observed_labeled_rows: int = 66858
    direct_v2_primary_row_limit: int = 10000
    reasons: tuple[str, ...] = (
        "direct v2 scale gate exceeded",
        "configured host has no GPU",
        "TabPFN-TS would discard required static and past-only covariates",
    )
    reconsideration_requirements: tuple[str, ...] = (
        "approved GPU or explicit cloud-egress approval",
        "pinned v2 weights with telemetry disabled",
        "4096-step context and identical rolling-origin protocol",
    )


class HostProfile(FrozenContract):
    hostname: str
    cpu_count: int = Field(ge=1)
    memory_gib: float = Field(gt=0)
    gpu: str | None = None


class EvaluationManifest(FrozenContract):
    schema_version: str
    created_at: datetime
    source_revision: str
    data_snapshots: tuple[SourceSnapshot, ...]
    train_start_date: str
    train_end_date: str
    test_start_date: str
    test_end_date: str
    candidate_results: tuple[CandidateResult, ...]
    selected_candidate_id: str
    official_overall_rmse: float = Field(ge=0, allow_inf_nan=False)
    station_metrics: tuple[StationMetric, ...]
    scoreable_rows: int = Field(ge=0)
    forecast_rows: int = Field(ge=0)
    ablations: tuple[AblationResult, ...]
    tabpfn: TabPFNDisposition
    host_profile: HostProfile
    artifact_digests: tuple[tuple[str, str], ...]
    limitations: tuple[str, ...] = ()

    _validate_created_at = field_validator("created_at")(_aware)

    @model_validator(mode="after")
    def selected_candidate_exists(self) -> EvaluationManifest:
        completed_ids = {item.candidate_id for item in self.candidate_results if item.completed}
        if self.selected_candidate_id not in completed_ids:
            raise ValueError("selected candidate must be a completed CV result")
        if self.scoreable_rows > self.forecast_rows:
            raise ValueError("scoreable_rows cannot exceed forecast_rows")
        return self


class MonitoringResult(FrozenContract):
    run_id: str
    evaluated_at: datetime
    severity: Severity
    freshness_findings: tuple[str, ...] = ()
    completeness_findings: tuple[str, ...] = ()
    drift_findings: tuple[str, ...] = ()
    performance_findings: tuple[str, ...] = ()
    action: str
    fallback_activated: bool = False

    _validate_evaluated_at = field_validator("evaluated_at")(_aware)


def utc_now() -> datetime:
    return datetime.now(UTC)


def contract_json(value: FrozenContract) -> dict[str, Any]:
    """Return JSON-compatible output for manifests and sinks."""

    return value.model_dump(mode="json")
