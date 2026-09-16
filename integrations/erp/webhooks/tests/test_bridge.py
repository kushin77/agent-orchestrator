"""Golden path + negative controls for the conversion-event bridge (#671).

Acceptance criteria exercised directly:
* golden path: conversion event -> GL entry posted, balanced.
* duplicate event is idempotent (no second post).
* malformed event is refused (quarantined), never half-posted.
"""

from __future__ import annotations

import json

import pytest

from integrations.erp.tx.ledger import PostingPolicy
from integrations.erp.webhooks import auth as auth_module
from integrations.erp.webhooks.bridge import ConversionBridge
from integrations.erp.webhooks.flags import FLAG_ID
from .conftest import SECRET, make_payload, sign


# --- golden path -------------------------------------------------------------


def test_golden_path_posts_balanced_entry(bridge, golden_payload):
    raw, header = sign(golden_payload)
    result = bridge.handle(raw, header, golden_payload)

    assert result.status == "posted"
    assert result.event_id == "evt-001"
    assert len(result.entries) == 2
    debit_total = sum(e.debit for e in result.entries)
    credit_total = sum(e.credit for e in result.entries)
    assert debit_total == credit_total == 1500.0
    findings = bridge.ledger.verify()
    assert findings == []
    assert len(bridge.quarantine_store) == 0


def test_golden_path_lead_conversion_hook_also_bridges(bridge):
    payload = make_payload(eventId="evt-lead", hook="erp.crm.lead-conversion")
    raw, header = sign(payload)
    result = bridge.handle(raw, header, payload)
    assert result.status == "posted"


# --- negative control: duplicate event is idempotent ------------------------


def test_duplicate_event_is_idempotent(bridge, golden_payload):
    raw, header = sign(golden_payload)
    first = bridge.handle(raw, header, golden_payload)
    second = bridge.handle(raw, header, golden_payload)

    assert first.status == "posted"
    assert second.status == "duplicate"
    assert second.voucher_id == first.voucher_id
    # the ledger was posted exactly once
    assert len(bridge.ledger.entries) == 2


# --- negative control: malformed event is refused, never half-posted --------


def test_malformed_event_is_quarantined_not_posted(bridge):
    payload = make_payload()
    del payload["amount"]
    raw, header = sign(payload)

    result = bridge.handle(raw, header, payload)

    assert result.status == "quarantined"
    assert result.reason.startswith("schema-violation")
    assert len(bridge.ledger.entries) == 0
    assert len(bridge.quarantine_store) == 1
    quarantined = bridge.quarantine_store.all()[0]
    assert quarantined.code == "schema-violation"


def test_unsigned_event_is_quarantined_before_schema_check(bridge):
    payload = make_payload()
    del payload["amount"]  # would also fail schema -- auth must fail first
    raw = json.dumps(payload).encode("utf-8")

    result = bridge.handle(raw, None, payload)

    assert result.status == "quarantined"
    assert result.reason.startswith("auth-failed")
    assert len(bridge.ledger.entries) == 0


def test_tampered_signature_is_quarantined(bridge, golden_payload):
    raw, _header = sign(golden_payload)
    bad_header = auth_module.sign("wrong-secret", raw)

    result = bridge.handle(raw, bad_header, golden_payload)

    assert result.status == "quarantined"
    assert result.reason.startswith("auth-failed")
    assert len(bridge.ledger.entries) == 0


def test_disabled_flag_quarantines_every_delivery(policy, golden_payload):
    disabled_bridge = ConversionBridge(secret=SECRET, posting_policy=policy, flags={FLAG_ID: False})
    raw, header = sign(golden_payload)

    result = disabled_bridge.handle(raw, header, golden_payload)

    assert result.status == "quarantined"
    assert len(disabled_bridge.ledger.entries) == 0


def test_missing_posting_policy_role_is_refused(golden_payload):
    incomplete_policy = PostingPolicy(accounts={"receivable": "Accounts Receivable"})
    bridge = ConversionBridge(secret=SECRET, posting_policy=incomplete_policy, flags={FLAG_ID: True})
    raw, header = sign(golden_payload)

    result = bridge.handle(raw, header, golden_payload)

    assert result.status == "quarantined"
    assert result.reason.startswith("missing-policy")
    assert len(bridge.ledger.entries) == 0


def test_default_flags_none_is_closed(policy, golden_payload):
    bridge = ConversionBridge(secret=SECRET, posting_policy=policy)  # flags=None
    raw, header = sign(golden_payload)
    result = bridge.handle(raw, header, golden_payload)
    assert result.status == "quarantined"
