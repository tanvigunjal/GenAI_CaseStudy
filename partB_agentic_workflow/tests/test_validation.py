from __future__ import annotations

import hashlib
from copy import deepcopy
from decimal import Decimal

import pytest
from pydantic import ValidationError

from order_pipeline.domain import FieldEvidence, OrderLineProposal, OrderProposal, SourceKind, SourceRef
from order_pipeline.erp_client import ERPNotFound
from order_pipeline.validation import OrderPolicy


class FakeERP:
    def __init__(self) -> None:
        self.create_calls = 0
        self.products: dict[str, dict[str, object]] = {
            "STL-BEAM-200": {
                "sku": "STL-BEAM-200",
                "unit_price": "184.50",
                "currency": "EUR",
                "stock_qty": 420,
                "catalog_version": "catalog-v1",
            },
            "REB-12MM": {
                "sku": "REB-12MM",
                "unit_price": "11.60",
                "currency": "EUR",
                "stock_qty": 7_000,
                "catalog_version": "catalog-v1",
            },
            "BIG": {
                "sku": "BIG",
                "unit_price": "1000",
                "currency": "EUR",
                "stock_qty": 100,
                "catalog_version": "catalog-v1",
            },
            "USD-SKU": {
                "sku": "USD-SKU",
                "unit_price": "10",
                "currency": "USD",
                "stock_qty": 100,
                "catalog_version": "catalog-v1",
            },
            "V2-SKU": {
                "sku": "V2-SKU",
                "unit_price": "10",
                "currency": "EUR",
                "stock_qty": 100,
                "catalog_version": "catalog-v2",
            },
            "BAD-PRICE": {
                "sku": "BAD-PRICE",
                "unit_price": "NaN",
                "currency": "EUR",
                "stock_qty": 100,
                "catalog_version": "catalog-v1",
            },
        }

    def lookup_product(self, sku: str) -> dict[str, object]:
        if sku not in self.products:
            raise ERPNotFound(sku)
        return deepcopy(self.products[sku])


EVIDENCE = FieldEvidence(
    field="lines.0",
    source=SourceRef(kind=SourceKind.BODY),
    excerpt="10 x STL-BEAM-200",
)


def line(
    sku: str | None = "STL-BEAM-200",
    quantity: Decimal | None = Decimal("10"),
    quote: Decimal | None = Decimal("184.50"),
    *,
    evidence: bool = True,
) -> OrderLineProposal:
    return OrderLineProposal(
        description="Synthetic line",
        candidate_sku=sku,
        quantity=quantity,
        quoted_unit_price=quote,
        evidence=(EVIDENCE,) if evidence else (),
    )


def proposal(**updates: object) -> OrderProposal:
    values: dict[str, object] = {
        "detected_language": "en",
        "lines": (line(),),
        "provenance": (EVIDENCE,),
        "model_confidence": Decimal("0.10"),
    }
    values.update(updates)
    return OrderProposal.model_validate(values)


SCENARIOS = [
    ("golden-en-body", proposal(), None),
    ("golden-de-body", proposal(detected_language="de"), None),
    ("golden-quote-within-two-percent", proposal(lines=(line(quote=Decimal("186.00")),)), None),
    (
        "golden-duplicate-sku-aggregated",
        proposal(lines=(line(quantity=Decimal("2")), line(quantity=Decimal("3")))),
        None,
    ),
    ("golden-low-confidence-does-not-authorize-or-deny", proposal(model_confidence=Decimal("0")), None),
    ("adversarial-missing-sku", proposal(lines=(line(sku=None),)), "MISSING_SKU"),
    ("adversarial-missing-quantity", proposal(lines=(line(quantity=None),)), "MISSING_QUANTITY"),
    ("adversarial-zero-quantity", proposal(lines=(line(quantity=Decimal("0")),)), "NONPOSITIVE_QUANTITY"),
    ("adversarial-negative-quantity", proposal(lines=(line(quantity=Decimal("-1")),)), "NONPOSITIVE_QUANTITY"),
    ("adversarial-fractional-quantity", proposal(lines=(line(quantity=Decimal("1.5")),)), "FRACTIONAL_QUANTITY"),
    ("adversarial-unknown-sku", proposal(lines=(line(sku="MISSING"),)), "UNKNOWN_SKU"),
    ("adversarial-price-over-tolerance", proposal(lines=(line(quote=Decimal("100")),)), "QUOTED_PRICE_MISMATCH"),
    (
        "adversarial-conflicting-quoted-prices",
        proposal(lines=(line(quantity=Decimal("1")), line(quantity=Decimal("1"), quote=Decimal("180")))),
        "SOURCE_PRICE_CONFLICT",
    ),
    ("adversarial-stock-overflow", proposal(lines=(line(quantity=Decimal("421")),)), "INSUFFICIENT_AGGREGATE_STOCK"),
    (
        "adversarial-duplicate-aggregate-stock-overflow",
        proposal(lines=(line(quantity=Decimal("220")), line(quantity=Decimal("220")))),
        "INSUFFICIENT_AGGREGATE_STOCK",
    ),
    ("adversarial-french", proposal(detected_language="fr"), "UNSUPPORTED_LANGUAGE"),
    ("adversarial-italian", proposal(detected_language="it"), "UNSUPPORTED_LANGUAGE"),
    ("adversarial-ambiguity", proposal(ambiguities=("quantity uncertain",)), "UNRESOLVED_AMBIGUITY"),
    ("adversarial-parser-warning", proposal(warnings=("partial XLSX parse",)), "EXTRACTION_WARNING"),
    ("adversarial-empty-order", proposal(lines=()), "NO_ORDER_LINES"),
    ("adversarial-missing-provenance", proposal(lines=(line(evidence=False),)), "MISSING_LINE_EVIDENCE"),
    ("adversarial-high-value", proposal(lines=(line("BIG", Decimal("51"), Decimal("1000")),)), "HIGH_VALUE_REVIEW"),
    (
        "adversarial-currency-mismatch",
        proposal(lines=(line("USD-SKU", Decimal("1"), Decimal("10")),)),
        "CURRENCY_MISMATCH",
    ),
    (
        "adversarial-invalid-catalog-price",
        proposal(lines=(line("BAD-PRICE", Decimal("1"), None),)),
        "INVALID_CATALOG_PRICE",
    ),
    (
        "adversarial-catalog-version-conflict",
        proposal(
            lines=(line("STL-BEAM-200", Decimal("1"), Decimal("184.50")), line("V2-SKU", Decimal("1"), Decimal("10")))
        ),
        "CATALOG_VERSION_CONFLICT",
    ),
    ("adversarial-source-conflict", proposal(warnings=("body/attachment conflict",)), "EXTRACTION_WARNING"),
    ("adversarial-multiple-orders", proposal(ambiguities=("suspected multiple orders",)), "UNRESOLVED_AMBIGUITY"),
]


@pytest.mark.parametrize("case_id,order_proposal,expected_code", SCENARIOS, ids=[case[0] for case in SCENARIOS])
def test_golden_and_adversarial_policy_matrix(case_id: str, order_proposal: OrderProposal, expected_code: str | None):
    del case_id
    erp = FakeERP()
    decision = OrderPolicy(erp).validate(
        order_proposal,
        customer_id="CUST-1001",
        input_digest=hashlib.sha256(b"input").hexdigest(),
        idempotency_key=hashlib.sha256(b"key").hexdigest(),
    )
    codes = {issue.code for issue in decision.issues}
    if expected_code is None:
        assert decision.approved
        assert decision.command is not None
    else:
        assert not decision.approved
        assert expected_code in codes
    assert erp.create_calls == 0


@pytest.mark.parametrize("invalid", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_nonfinite_numeric_proposals_fail_at_schema_boundary(invalid: Decimal):
    with pytest.raises(ValidationError):
        line(quantity=invalid)
