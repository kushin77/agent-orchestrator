from __future__ import annotations

import pytest

from integrations.erp.webhooks import schema
from integrations.erp.webhooks.model import Refused
from .conftest import make_payload


def test_parse_golden_event():
    event = schema.parse(make_payload())
    assert event.event_id == "evt-001"
    assert event.hook == "erp.crm.opportunity-win"
    assert event.currency == "USD"
    assert event.idempotency_key == "acme:evt-001"


def test_parse_rejects_non_mapping():
    with pytest.raises(Refused) as exc:
        schema.parse(["not", "a", "mapping"])
    assert exc.value.code == "invalid-body"


def test_parse_rejects_missing_field():
    payload = make_payload()
    del payload["amount"]
    with pytest.raises(Refused) as exc:
        schema.parse(payload)
    assert exc.value.code == "schema-violation"
    assert "amount" in exc.value.detail


def test_parse_rejects_unknown_field():
    payload = make_payload(extra="nope")
    with pytest.raises(Refused) as exc:
        schema.parse(payload)
    assert exc.value.code == "schema-violation"


def test_parse_rejects_unknown_hook():
    payload = make_payload(hook="salesforce.opportunity.won")
    with pytest.raises(Refused) as exc:
        schema.parse(payload)
    assert exc.value.code == "unknown-hook"
    assert "hook" in exc.value.detail


def test_parse_rejects_non_positive_amount():
    payload = make_payload(amount=0)
    with pytest.raises(Refused):
        schema.parse(payload)


def test_parse_rejects_bad_currency():
    payload = make_payload(currency="US")
    with pytest.raises(Refused):
        schema.parse(payload)
