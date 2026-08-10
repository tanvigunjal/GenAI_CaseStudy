"""Deterministic authorization policy: the sole command-construction boundary."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict

from .domain import (
    CanonicalOrderLine,
    IssueSeverity,
    OrderProposal,
    ValidatedOrderCommand,
    ValidationIssue,
)
from .erp_client import ERPClient, ERPError, ERPNotFound


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    command: ValidatedOrderCommand | None = None
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def approved(self) -> bool:
        return self.command is not None and not self.issues


_SUPPORTED_LANGUAGES = {"en", "eng", "english", "de", "deu", "ger", "german", "deutsch"}


def _issue(code: str, explanation: str, field: str | None = None) -> ValidationIssue:
    return ValidationIssue(code=code, severity=IssueSeverity.ERROR, explanation=explanation, field=field)


class OrderPolicy:
    def __init__(
        self,
        erp: ERPClient,
        *,
        price_tolerance: Decimal = Decimal("0.02"),
        max_order_value: Decimal = Decimal("50000"),
        policy_version: str = "policy-v1",
    ) -> None:
        if price_tolerance < 0 or price_tolerance > 1:
            raise ValueError("price_tolerance must be between zero and one")
        if not max_order_value.is_finite() or max_order_value <= 0:
            raise ValueError("max_order_value must be finite and positive")
        self._erp = erp
        self._tolerance = price_tolerance
        self._max_value = max_order_value
        self._policy_version = policy_version

    def validate(
        self,
        proposal: OrderProposal,
        *,
        customer_id: str,
        input_digest: str,
        idempotency_key: str,
    ) -> PolicyDecision:
        issues: list[ValidationIssue] = []
        if proposal.detected_language.strip().lower() not in _SUPPORTED_LANGUAGES:
            issues.append(
                _issue("UNSUPPORTED_LANGUAGE", "Only English and German are auto-eligible.", "detected_language")
            )
        if not proposal.lines:
            issues.append(_issue("NO_ORDER_LINES", "At least one order line is required.", "lines"))
        if proposal.ambiguities:
            issues.append(_issue("UNRESOLVED_AMBIGUITY", "Extraction left unresolved ambiguities.", "ambiguities"))
        if proposal.warnings:
            issues.append(_issue("EXTRACTION_WARNING", "Extraction or parsing warnings require review.", "warnings"))

        quantities: defaultdict[str, int] = defaultdict(int)
        quoted_prices: defaultdict[str, set[Decimal]] = defaultdict(set)
        for index, line in enumerate(proposal.lines):
            field = f"lines.{index}"
            if not line.evidence:
                issues.append(
                    _issue("MISSING_LINE_EVIDENCE", "Every line requires inspectable source evidence.", field)
                )
            if not line.candidate_sku:
                issues.append(_issue("MISSING_SKU", "Every line requires a known SKU.", f"{field}.candidate_sku"))
                continue
            quantity = line.quantity
            if quantity is None:
                issues.append(_issue("MISSING_QUANTITY", "Every line requires a quantity.", f"{field}.quantity"))
                continue
            if not quantity.is_finite():
                issues.append(_issue("NONFINITE_QUANTITY", "Quantity must be finite.", f"{field}.quantity"))
                continue
            if quantity <= 0:
                issues.append(_issue("NONPOSITIVE_QUANTITY", "Quantity must be positive.", f"{field}.quantity"))
                continue
            if quantity != quantity.to_integral_value():
                issues.append(_issue("FRACTIONAL_QUANTITY", "Quantity must be a whole unit.", f"{field}.quantity"))
                continue
            quantities[line.candidate_sku] += int(quantity)
            if line.quoted_unit_price is not None:
                quoted = line.quoted_unit_price
                if not quoted.is_finite() or quoted <= 0:
                    issues.append(
                        _issue(
                            "INVALID_QUOTED_PRICE",
                            "Quoted prices must be finite and positive.",
                            f"{field}.quoted_unit_price",
                        )
                    )
                else:
                    quoted_prices[line.candidate_sku].add(quoted)

        canonical: list[CanonicalOrderLine] = []
        catalog_versions: set[str] = set()
        for sku in sorted(quantities):
            try:
                product = self._erp.lookup_product(sku)
            except ERPNotFound:
                issues.append(_issue("UNKNOWN_SKU", f"SKU {sku} does not exist in the catalog.", "lines"))
                continue
            except ERPError:
                issues.append(_issue("CATALOG_LOOKUP_FAILURE", f"Catalog lookup failed for {sku}.", "lines"))
                continue
            try:
                price = Decimal(str(product["unit_price"]))
                stock_value = product["stock_qty"]
                if not isinstance(stock_value, (int, str)) or isinstance(stock_value, bool):
                    raise TypeError("stock quantity must be an integer")
                stock = int(stock_value)
                currency = str(product["currency"])
                catalog_version = str(product["catalog_version"])
            except (KeyError, TypeError, ValueError, InvalidOperation):
                issues.append(_issue("INVALID_CATALOG_RECORD", f"Catalog data is incomplete for {sku}.", "lines"))
                continue
            if not price.is_finite() or price <= 0:
                issues.append(_issue("INVALID_CATALOG_PRICE", f"Catalog price is invalid for {sku}.", "lines"))
                continue
            if currency != "EUR":
                issues.append(_issue("CURRENCY_MISMATCH", f"Catalog currency for {sku} is not EUR.", "lines"))
                continue
            aggregate_quantity = quantities[sku]
            if aggregate_quantity > stock:
                issues.append(
                    _issue("INSUFFICIENT_AGGREGATE_STOCK", f"Aggregate quantity exceeds stock for {sku}.", "lines")
                )
            quotes = quoted_prices.get(sku, set())
            if len(quotes) > 1:
                issues.append(
                    _issue("SOURCE_PRICE_CONFLICT", f"Conflicting quoted prices were found for {sku}.", "lines")
                )
            for quoted in quotes:
                if abs(quoted - price) / price > self._tolerance:
                    issues.append(
                        _issue("QUOTED_PRICE_MISMATCH", f"Quoted price differs by more than 2% for {sku}.", "lines")
                    )
            catalog_versions.add(catalog_version)
            canonical.append(CanonicalOrderLine(sku=sku, quantity=aggregate_quantity, authoritative_unit_price=price))

        if len(catalog_versions) > 1:
            issues.append(
                _issue("CATALOG_VERSION_CONFLICT", "Products were read from inconsistent catalog versions.", "lines")
            )
        total = sum(
            (line.authoritative_unit_price * line.quantity for line in canonical),
            start=Decimal("0"),
        )
        if total > self._max_value:
            issues.append(_issue("HIGH_VALUE_REVIEW", "Order value exceeds the demonstration auto-write cap.", "total"))
        if issues:
            return PolicyDecision(issues=tuple(issues))
        if not canonical or len(catalog_versions) != 1:
            return PolicyDecision(issues=(_issue("VALIDATION_INCOMPLETE", "A complete catalog snapshot is required."),))
        return PolicyDecision(
            command=ValidatedOrderCommand(
                customer_id=customer_id,
                lines=tuple(canonical),
                currency="EUR",
                total=total,
                catalog_version=next(iter(catalog_versions)),
                input_digest=input_digest,
                policy_version=self._policy_version,
                idempotency_key=idempotency_key,
            )
        )
