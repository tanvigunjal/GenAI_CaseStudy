"""The extraction model's complete, bounded, read-only tool surface."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .erp_client import ERPClient, ERPError


class ToolDenied(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ExactProductArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ProductSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=3, max_length=100)


class PurchaseHistoryArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: str = Field(min_length=1, max_length=100)
    name: Literal["lookup_product", "search_products", "purchase_history"]
    arguments: dict[str, Any]


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: str
    name: str
    output: dict[str, object] | list[dict[str, object]]


TOOL_SCHEMAS: tuple[dict[str, object], ...] = (
    {
        "name": "lookup_product",
        "description": "Look up one exact SKU.",
        "parameters": ExactProductArguments.model_json_schema(),
    },
    {
        "name": "search_products",
        "description": "Search products; query must be at least three characters and results are capped at five.",
        "parameters": ProductSearchArguments.model_json_schema(),
    },
    {
        "name": "purchase_history",
        "description": "Return up to five orders for the already authenticated customer.",
        "parameters": PurchaseHistoryArguments.model_json_schema(),
    },
)


class ScopedToolExecutor:
    def __init__(
        self,
        erp: ERPClient,
        *,
        max_calls: int = 8,
        max_returned_characters: int = 12_000,
        event_sink: Callable[[str, Mapping[str, object]], None] | None = None,
    ) -> None:
        self._erp = erp
        self._max_calls = max_calls
        self._max_chars = max_returned_characters
        self._event_sink = event_sink or (lambda _event, _payload: None)
        self._calls = 0
        self._returned_chars = 0

    @property
    def calls(self) -> int:
        return self._calls

    @staticmethod
    def public_tool_names() -> tuple[str, ...]:
        return tuple(str(schema["name"]) for schema in TOOL_SCHEMAS)

    def _deny(self, request: ToolRequest, code: str, message: str) -> ToolDenied:
        self._event_sink("tool_denied", {"call_id": request.call_id, "tool": request.name, "code": code})
        return ToolDenied(code, message)

    def execute(self, request: ToolRequest | Mapping[str, object]) -> ToolResult:
        try:
            validated = request if isinstance(request, ToolRequest) else ToolRequest.model_validate(request)
        except ValidationError as exc:
            raise ToolDenied("MALFORMED_TOOL_REQUEST", "Tool request failed strict schema validation") from exc
        if self._calls >= self._max_calls:
            raise self._deny(validated, "TOOL_CALL_BUDGET", "Extraction tool-call budget exhausted")
        self._event_sink("tool_intent", {"call_id": validated.call_id, "tool": validated.name})
        try:
            if validated.name == "lookup_product":
                exact_arguments = ExactProductArguments.model_validate(validated.arguments)
                output: dict[str, object] | list[dict[str, object]] = self._erp.lookup_product(exact_arguments.sku)
            elif validated.name == "search_products":
                search_arguments = ProductSearchArguments.model_validate(validated.arguments)
                if not search_arguments.query.strip() or len(search_arguments.query.strip()) < 3:
                    raise self._deny(validated, "EMPTY_SEARCH", "Product search is too short")
                output = self._erp.search_products(search_arguments.query.strip())[:5]
            else:
                PurchaseHistoryArguments.model_validate(validated.arguments)
                output = self._erp.purchase_history()[:5]
        except ValidationError as exc:
            raise self._deny(validated, "MALFORMED_TOOL_ARGUMENTS", "Tool arguments failed strict validation") from exc
        except ERPError as exc:
            self._event_sink(
                "tool_failure",
                {"call_id": validated.call_id, "tool": validated.name, "code": "ERP_TOOL_FAILURE"},
            )
            raise ToolDenied("ERP_TOOL_FAILURE", "ERP read tool failed") from exc
        serialized = json.dumps(output, default=str, sort_keys=True, separators=(",", ":"))
        if self._returned_chars + len(serialized) > self._max_chars:
            raise self._deny(validated, "TOOL_OUTPUT_BUDGET", "Tool results exceed the extraction context budget")
        self._calls += 1
        self._returned_chars += len(serialized)
        self._event_sink(
            "tool_success",
            {"call_id": validated.call_id, "tool": validated.name, "result_characters": len(serialized)},
        )
        return ToolResult(call_id=validated.call_id, name=validated.name, output=output)
