"""The rate card: what it prices, what it refuses, and what it must cover."""

from __future__ import annotations

import pytest

from integrations.erp.finops import rates
from integrations.erp.finops.model import Refused


def test_the_shipped_card_prices_every_declared_operation() -> None:
    card = rates.load()
    assert card.currency == "USD"
    assert card.kinds(), "the shipped card prices nothing"
    for rate in card.rates:
        if rate.priced:
            assert rate.price_usd is not None and rate.price_usd > 0
        else:
            assert rate.price_usd is None


def test_the_shipped_card_covers_the_live_core_model(model) -> None:
    card = rates.load()
    assert card.coverage(model.document_kinds(), model.lifecycle_kinds()) == []
    for kind in model.document_kinds():
        assert (kind, "create") in {(r.kind, r.operation) for r in card.rates}
    for kind in model.lifecycle_kinds():
        assert (kind, "transition") in {(r.kind, r.operation) for r in card.rates}


def test_a_master_kind_is_not_priced_for_a_transition_it_cannot_take(model) -> None:
    card = rates.load()
    masters = set(model.document_kinds()) - set(model.lifecycle_kinds())
    for rate in card.rates:
        if rate.operation == "transition":
            assert rate.kind not in masters


def test_an_absent_rate_is_refused_rather_than_defaulted() -> None:
    card = rates.load()
    with pytest.raises(Refused) as caught:
        card.price_for("party", "transition")
    assert caught.value.code == "rate-missing"


def test_a_card_with_no_price_for_a_declared_operation_is_refused() -> None:
    with pytest.raises(Refused) as caught:
        rates.load(
            {
                "schemaVersion": "ao.erp.finops/v1",
                "currency": "USD",
                "supportedCurrencies": ["USD"],
                "rates": [{"kind": "sales-order", "operation": "create", "price": 0}],
            }
        )
    assert caught.value.code == "rate-invalid"


def test_an_unpriced_declaration_is_not_a_missing_one() -> None:
    card = rates.load(
        {
            "schemaVersion": "ao.erp.finops/v1",
            "currency": "USD",
            "supportedCurrencies": ["USD"],
            "rates": [{"kind": "sales-order", "operation": "create", "priced": False}],
        }
    )
    assert card.price_for("sales-order", "create") is None
    assert card.rate_for("sales-order", "create").priced is False


def test_a_currency_outside_the_declared_set_is_refused() -> None:
    with pytest.raises(Refused) as caught:
        rates.load(
            {
                "schemaVersion": "ao.erp.finops/v1",
                "currency": "USD",
                "supportedCurrencies": ["USD"],
                "rates": [
                    {
                        "kind": "sales-order",
                        "operation": "create",
                        "price": 1,
                        "currency": "EUR",
                    }
                ],
            }
        )
    assert caught.value.code == "unknown-currency"


def test_a_duplicate_entry_is_refused() -> None:
    entry = {"kind": "sales-order", "operation": "create", "price": 1}
    with pytest.raises(Refused) as caught:
        rates.load(
            {
                "schemaVersion": "ao.erp.finops/v1",
                "currency": "USD",
                "supportedCurrencies": ["USD"],
                "rates": [entry, dict(entry)],
            }
        )
    assert caught.value.code == "rate-card-invalid"


def test_coverage_reports_a_gap_rather_than_raising(model) -> None:
    card = rates.load(
        {
            "schemaVersion": "ao.erp.finops/v1",
            "currency": "USD",
            "supportedCurrencies": ["USD"],
            "rates": [{"kind": "sales-order", "operation": "create", "price": 1}],
        }
    )
    findings = card.coverage(model.document_kinds(), model.lifecycle_kinds())
    assert findings
    assert {finding.code for finding in findings} == {"rate-missing"}
    assert any("item" in finding.detail for finding in findings)
