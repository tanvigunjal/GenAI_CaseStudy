"""Validated runtime configuration for replay/live and shadow/auto modes."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelMode(StrEnum):
    REPLAY = "replay"
    LIVE = "live"


class WriteMode(StrEnum):
    SHADOW = "shadow"
    AUTO = "auto"


class RuntimeEnvironment(StrEnum):
    LOCAL_SANDBOX = "local_sandbox"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Application settings; replay mode is intentionally keyless and offline."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        validate_default=True,
    )

    model_mode: ModelMode = Field(default=ModelMode.REPLAY, alias="MODEL_MODE")
    write_mode: WriteMode = Field(default=WriteMode.SHADOW, alias="WRITE_MODE")
    runtime_environment: RuntimeEnvironment = Field(
        default=RuntimeEnvironment.LOCAL_SANDBOX,
        alias="RUNTIME_ENVIRONMENT",
    )
    kill_switch: bool = Field(default=False, alias="KILL_SWITCH")

    llm_provider: str = Field(default="gemini", alias="LLM_PROVIDER")
    google_api_key: str | None = Field(default=None, alias="GOOGLE_API_KEY", repr=False)
    gemini_extraction_model: str | None = Field(default="gemini-2.5-flash", alias="GEMINI_EXTRACTION_MODEL")
    gemini_screening_model: str | None = Field(default="gemini-2.5-flash-lite", alias="GEMINI_SCREENING_MODEL")
    replay_transcript_version: str = Field(default="replay-v1", alias="REPLAY_TRANSCRIPT_VERSION")

    code_version: str = Field(default="task-b", alias="CODE_VERSION")
    prompt_version: str = Field(default="prompt-v1", alias="PROMPT_VERSION")
    policy_version: str = Field(default="policy-v1", alias="POLICY_VERSION")

    price_tolerance_pct: float = Field(default=0.02, ge=0, le=1)
    min_confidence_threshold: float = Field(default=0.75, ge=0, le=1)
    max_order_value_circuit_breaker: float = Field(default=50_000.0, gt=0)

    max_tool_iterations: int = Field(default=6, ge=1, le=6)
    max_tool_calls: int = Field(default=8, ge=1, le=8)
    max_tool_result_characters: int = Field(default=12_000, ge=1)
    max_search_results: int = Field(default=5, ge=1, le=5)
    min_search_query_length: int = Field(default=3, ge=1)
    max_parse_retries: int = Field(default=0, ge=0, le=0)

    llm_max_retries: int = Field(default=4, ge=1)
    llm_retry_min_wait_seconds: float = Field(default=2.0, ge=0)
    llm_retry_max_wait_seconds: float = Field(default=30.0, ge=0)

    max_attachments: int = Field(default=10, ge=0)
    max_attachment_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    max_total_attachment_bytes: int = Field(default=25 * 1024 * 1024, gt=0)
    max_pdf_pages: int = Field(default=50, gt=0)
    max_pptx_slides: int = Field(default=100, gt=0)
    max_xlsx_sheets: int = Field(default=10, gt=0)
    max_xlsx_rows_per_sheet: int = Field(default=5_000, gt=0)
    max_xlsx_cells: int = Field(default=50_000, gt=0)
    max_extracted_characters: int = Field(default=100_000, gt=0)
    parser_timeout_seconds: float = Field(default=5.0, gt=0)

    workspace_dir: Path = Field(default=Path("workspace"), alias="WORKSPACE_DIR")
    state_db_path: Path | None = Field(default=None, alias="STATE_DB_PATH")

    @model_validator(mode="after")
    def validate_mode_contract(self) -> Settings:
        if self.model_mode is ModelMode.LIVE:
            if self.llm_provider.strip().lower() != "gemini":
                raise ValueError("Live mode currently supports only LLM_PROVIDER=gemini.")
            if not self.google_api_key or not self.google_api_key.strip():
                raise ValueError("GOOGLE_API_KEY is required when MODEL_MODE=live.")
            if not self.gemini_extraction_model or not self.gemini_extraction_model.strip():
                raise ValueError("GEMINI_EXTRACTION_MODEL must be explicit when MODEL_MODE=live.")
            if not self.gemini_screening_model or not self.gemini_screening_model.strip():
                raise ValueError("GEMINI_SCREENING_MODEL must be explicit when MODEL_MODE=live.")
        if self.write_mode is WriteMode.AUTO and self.runtime_environment is not RuntimeEnvironment.LOCAL_SANDBOX:
            raise ValueError("WRITE_MODE=auto is limited to RUNTIME_ENVIRONMENT=local_sandbox.")
        if self.llm_retry_min_wait_seconds > self.llm_retry_max_wait_seconds:
            raise ValueError("llm_retry_min_wait_seconds must not exceed llm_retry_max_wait_seconds.")
        return self

    @property
    def database_path(self) -> Path:
        return self.state_db_path or self.workspace_dir / "workflow.sqlite3"

    @property
    def writes_enabled(self) -> bool:
        return (
            self.write_mode is WriteMode.AUTO
            and self.runtime_environment is RuntimeEnvironment.LOCAL_SANDBOX
            and not self.kill_switch
        )

    def ensure_workspace(self) -> Path:
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        return self.workspace_dir

    def version_manifest(self) -> dict[str, Any]:
        model_versions = (
            {
                "provider": "gemini",
                "extraction": self.gemini_extraction_model,
                "screening": self.gemini_screening_model,
            }
            if self.model_mode is ModelMode.LIVE
            else {"provider": "replay", "transcript": self.replay_transcript_version}
        )
        return {
            "code_version": self.code_version,
            "prompt_version": self.prompt_version,
            "policy_version": self.policy_version,
            "model_mode": self.model_mode.value,
            "write_mode": self.write_mode.value,
            "model_versions": model_versions,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def version_manifest(settings: Settings | None = None) -> dict[str, Any]:
    return (settings or get_settings()).version_manifest()
