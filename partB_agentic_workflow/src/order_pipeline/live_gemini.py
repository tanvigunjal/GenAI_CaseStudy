"""Optional, lazily imported Gemini constructors for live shadow evaluation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .config import ModelMode, Settings, WriteMode
from .extraction import GeminiExtractionModel, ModelAction
from .security import AdvisoryVerdict, GeminiSecurityClassifier
from .tool_executor import TOOL_SCHEMAS


@dataclass(frozen=True)
class LiveGeminiModels:
    security: GeminiSecurityClassifier
    extraction: GeminiExtractionModel


def build_live_gemini_models(settings: Settings) -> LiveGeminiModels:
    """Build both live adapters only when explicitly configured for shadow mode."""

    if settings.model_mode is not ModelMode.LIVE:
        raise ValueError("Gemini models can only be built when MODEL_MODE=live")
    if settings.write_mode is not WriteMode.SHADOW:
        raise ValueError("Live Gemini execution is restricted to shadow mode")
    if not settings.google_api_key or not settings.gemini_screening_model or not settings.gemini_extraction_model:
        raise ValueError("Live Gemini credentials and explicit model names are required")

    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
    except ImportError as exc:
        raise RuntimeError("Install the 'live' dependency group to use Gemini") from exc

    screening_chat = ChatGoogleGenerativeAI(
        model=settings.gemini_screening_model,
        google_api_key=settings.google_api_key,
        temperature=0,
        max_retries=settings.llm_max_retries,
    )
    extraction_chat = ChatGoogleGenerativeAI(
        model=settings.gemini_extraction_model,
        google_api_key=settings.google_api_key,
        temperature=0,
        max_retries=settings.llm_max_retries,
    )
    screening_structured = screening_chat.with_structured_output(AdvisoryVerdict, method="json_schema")
    extraction_structured = extraction_chat.with_structured_output(ModelAction, method="json_schema")

    def screen(prompt: str) -> str:
        result = screening_structured.invoke(prompt)
        if isinstance(result, AdvisoryVerdict):
            return result.model_dump_json()
        if isinstance(result, Mapping):
            return json.dumps(dict(result))
        raise RuntimeError("Gemini screening returned an unexpected schema")

    def extract(payload: dict[str, object]) -> Mapping[str, object]:
        prompt = {
            **payload,
            "allowed_tools": TOOL_SCHEMAS,
            "response_contract": (
                "Return either kind=tools with one or more allowed tool requests, or kind=final "
                "with one strict OrderProposal-shaped proposal. Never create, update, or delete data."
            ),
        }
        result: Any = extraction_structured.invoke(json.dumps(prompt, default=str))
        if isinstance(result, ModelAction):
            return result.model_dump(mode="json")
        if isinstance(result, Mapping):
            return dict(result)
        raise RuntimeError("Gemini extraction returned an unexpected schema")

    return LiveGeminiModels(
        security=GeminiSecurityClassifier(screen, model_name=settings.gemini_screening_model),
        extraction=GeminiExtractionModel(extract, model_name=settings.gemini_extraction_model),
    )
