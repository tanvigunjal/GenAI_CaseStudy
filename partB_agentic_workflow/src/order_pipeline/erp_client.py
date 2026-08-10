"""Scoped REST client for the local ERP sandbox."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

import httpx

from .erp_sandbox import SERVICE_TOKEN, canonical_payload_digest
from .schemas import ValidatedOrderCommand


class ERPError(RuntimeError):
    pass


class ERPNotFound(ERPError):
    pass


class ERPConflict(ERPError):
    pass


class ERPTransientError(ERPError):
    pass


class ERPUnknownWriteOutcome(ERPError):
    pass


@runtime_checkable
class ERPClient(Protocol):
    def resolve_customer(self, email: str) -> dict[str, object]: ...
    def lookup_product(self, sku: str) -> dict[str, object]: ...
    def search_products(self, query: str) -> list[dict[str, object]]: ...
    def purchase_history(self) -> list[dict[str, object]]: ...
    def create_validated_order(self, command: ValidatedOrderCommand) -> dict[str, object]: ...
    def lookup_by_idempotency_key(self, idempotency_key: str) -> dict[str, object] | None: ...


class HTTPClient(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response: ...


class RestERPClient:
    """An HTTP-only client with bounded read retries and reconciled writes.

    No update or delete methods exist. Customer history is permanently scoped to
    the authenticated customer selected when constructing the client.
    """

    def __init__(
        self,
        http_client: HTTPClient,
        *,
        customer_context: str | None = None,
        service_token: str = SERVICE_TOKEN,
        timeout_seconds: float = 2.0,
        max_read_attempts: int = 3,
    ) -> None:
        self._http = http_client
        self._customer_context = customer_context
        self._headers = {"X-Service-Token": service_token}
        self._timeout = timeout_seconds
        self._max_read_attempts = max(1, max_read_attempts)

    def for_customer(self, customer_id: str) -> RestERPClient:
        return RestERPClient(
            self._http,
            customer_context=customer_id,
            service_token=self._headers["X-Service-Token"],
            timeout_seconds=self._timeout,
            max_read_attempts=self._max_read_attempts,
        )

    def _read(
        self, path: str, *, params: Mapping[str, object] | None = None, headers: Mapping[str, str] | None = None
    ) -> httpx.Response:
        combined_headers = {**self._headers, **(headers or {})}
        last_error: Exception | None = None
        for attempt in range(self._max_read_attempts):
            try:
                response = self._http.request(
                    "GET", path, params=params, headers=combined_headers, timeout=self._timeout
                )
                if response.status_code not in {408, 425, 429, 500, 502, 503, 504}:
                    return response
                last_error = ERPTransientError(f"Transient ERP response: {response.status_code}")
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
            if attempt + 1 < self._max_read_attempts:
                time.sleep(min(0.02 * (2**attempt), 0.1))
        raise ERPTransientError("ERP read failed after bounded retries") from last_error

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, object]:
        try:
            value = response.json()
        except ValueError as exc:
            raise ERPError("ERP returned malformed JSON") from exc
        if not isinstance(value, dict):
            raise ERPError("ERP returned an unexpected response schema")
        return value

    def resolve_customer(self, email: str) -> dict[str, object]:
        response = self._read("/customers/by-email", params={"email": email})
        if response.status_code == 404:
            raise ERPNotFound("Customer not found")
        if response.status_code != 200:
            raise ERPError(f"Customer lookup failed: {response.status_code}")
        return self._json_object(response)

    def lookup_product(self, sku: str) -> dict[str, object]:
        response = self._read(f"/products/{sku}")
        if response.status_code == 404:
            raise ERPNotFound("Product not found")
        if response.status_code != 200:
            raise ERPError(f"Product lookup failed: {response.status_code}")
        return self._json_object(response)

    def search_products(self, query: str) -> list[dict[str, object]]:
        query = query.strip()
        if len(query) < 3:
            raise ValueError("Product search query must contain at least three characters")
        response = self._read("/products/search", params={"q": query, "limit": 5})
        if response.status_code != 200:
            raise ERPError(f"Product search failed: {response.status_code}")
        value = response.json()
        if not isinstance(value, list) or len(value) > 5 or not all(isinstance(item, dict) for item in value):
            raise ERPError("ERP returned an unexpected product-search schema")
        allowlist = {"sku", "name", "unit_price", "currency", "stock_qty"}
        return [{key: item[key] for key in allowlist if key in item} for item in value]

    def purchase_history(self) -> list[dict[str, object]]:
        if not self._customer_context:
            raise ERPError("Authenticated customer context is required for purchase history")
        response = self._read(
            "/customers/me/history",
            params={"limit": 5},
            headers={"X-Customer-Context": self._customer_context},
        )
        if response.status_code != 200:
            raise ERPError(f"Purchase-history lookup failed: {response.status_code}")
        value = response.json()
        if not isinstance(value, list) or len(value) > 5 or not all(isinstance(item, dict) for item in value):
            raise ERPError("ERP returned an unexpected history schema")
        allowlist = {"order_id", "total", "currency", "created_at"}
        return [{key: item[key] for key in allowlist if key in item} for item in value]

    def lookup_by_idempotency_key(self, idempotency_key: str) -> dict[str, object] | None:
        response = self._read(f"/orders/by-idempotency-key/{idempotency_key}")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise ERPError(f"Order reconciliation failed: {response.status_code}")
        return self._json_object(response)

    @staticmethod
    def _command_payload(command: ValidatedOrderCommand) -> tuple[str, dict[str, object]]:
        if not isinstance(command, ValidatedOrderCommand):
            raise TypeError("ERP writes require a ValidatedOrderCommand")
        raw = command.model_dump(mode="json")
        idempotency_key = str(raw.pop("idempotency_key"))
        lines = [
            {
                "sku": line["sku"],
                "quantity": line["quantity"],
                "unit_price": line["authoritative_unit_price"],
            }
            for line in raw["lines"]
        ]
        payload = {
            "customer_id": raw["customer_id"],
            "lines": lines,
            "currency": raw["currency"],
            "total": raw["total"],
            "catalog_version": raw["catalog_version"],
            "input_digest": raw["input_digest"],
            "policy_version": raw["policy_version"],
        }
        return idempotency_key, payload

    def create_validated_order(self, command: ValidatedOrderCommand) -> dict[str, object]:
        idempotency_key, payload = self._command_payload(command)
        payload_digest = canonical_payload_digest(payload)
        headers = {
            **self._headers,
            "Idempotency-Key": idempotency_key,
            "X-Payload-Digest": payload_digest,
        }
        try:
            response = self._http.request("POST", "/orders", json=payload, headers=headers, timeout=self._timeout)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            return self._reconcile_unknown(idempotency_key, exc)
        if response.status_code in {200, 201}:
            return self._json_object(response)
        if response.status_code == 409:
            raise ERPConflict("ERP atomically rejected the validated command")
        if response.status_code in {408, 425, 429, 500, 502, 503, 504}:
            return self._reconcile_unknown(idempotency_key, ERPTransientError("Unknown POST outcome"))
        raise ERPError(f"ERP write failed: {response.status_code}")

    def _reconcile_unknown(self, idempotency_key: str, cause: Exception) -> dict[str, object]:
        try:
            existing = self.lookup_by_idempotency_key(idempotency_key)
        except ERPError as reconciliation_error:
            raise ERPUnknownWriteOutcome("ERP write outcome could not be reconciled") from reconciliation_error
        if existing is not None:
            return existing
        raise ERPUnknownWriteOutcome("ERP did not confirm creation for the idempotency key") from cause
