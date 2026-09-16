from __future__ import annotations

from integrations.erp.webhooks.idempotency import IdempotencyStore
from integrations.erp.webhooks.model import BridgeResult


def test_seen_returns_none_for_unknown_key():
    store = IdempotencyStore()
    assert store.seen("nope") is None


def test_record_then_seen_roundtrips():
    store = IdempotencyStore()
    result = BridgeResult(status="posted", event_id="evt-1", voucher_id="crm-conversion:evt-1")
    store.record("acme:evt-1", result)
    assert store.seen("acme:evt-1") is result
    assert len(store) == 1


def test_record_is_first_write_wins():
    store = IdempotencyStore()
    first = BridgeResult(status="posted", event_id="evt-1", voucher_id="v1")
    second = BridgeResult(status="posted", event_id="evt-1", voucher_id="v2-should-not-win")
    store.record("k", first)
    store.record("k", second)
    assert store.seen("k") is first
