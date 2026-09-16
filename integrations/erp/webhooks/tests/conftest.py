"""Shared fixtures for the webhook-bridge suite (issue #671)."""

from __future__ import annotations

import json

import pytest

from integrations.erp.tx.ledger import PostingPolicy
from integrations.erp.webhooks import auth as auth_module
from integrations.erp.webhooks.bridge import ConversionBridge
from integrations.erp.webhooks.flags import FLAG_ID

SECRET = "test-webhook-secret"


@pytest.fixture()
def policy() -> PostingPolicy:
    return PostingPolicy(accounts={"receivable": "Accounts Receivable", "income": "Sales Income"})


@pytest.fixture()
def enabled_flags():
    return {FLAG_ID: True}


@pytest.fixture()
def bridge(policy, enabled_flags) -> ConversionBridge:
    return ConversionBridge(secret=SECRET, posting_policy=policy, flags=enabled_flags)


def make_payload(**overrides):
    payload = {
        "schemaVersion": 1,
        "eventId": "evt-001",
        "hook": "erp.crm.opportunity-win",
        "tenant": "acme",
        "occurredAt": "2026-09-16T00:00:00Z",
        "customerId": "cust-1",
        "customerName": "Acme Co",
        "amount": 1500.0,
        "currency": "usd",
        "sourceDocument": "opportunity/opp-1",
    }
    payload.update(overrides)
    return payload


def sign(payload: dict) -> tuple:
    raw = json.dumps(payload).encode("utf-8")
    return raw, auth_module.sign(SECRET, raw)


@pytest.fixture()
def golden_payload():
    return make_payload()
