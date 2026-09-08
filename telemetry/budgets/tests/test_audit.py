"""telemetry/budgets — audit store tests (issue #34).

The audit feed records BLOCK/WARN decisions (and observe would-* variants)
and skips plain allows (no signal); the durable JSONL store round-trips.
"""

from __future__ import annotations

from pathlib import Path

from telemetry.budgets.audit import (
    JsonlAuditStore,
    MemoryAuditStore,
    record_decision,
)
from telemetry.budgets.model import (
    DECISION_ALLOW,
    DECISION_BLOCK,
    DECISION_WARN,
    OUTCOME_REFUSED,
    EnforcerDecision,
    KIND_BUDGET,
    KIND_KILL_SWITCH,
)


def _decision(decision: str, kind: str = KIND_BUDGET, tenant: str = "acme",
              code: str = "x") -> EnforcerDecision:
    return EnforcerDecision(
        tenant_id=tenant, kind=kind, decision=decision,
        reason=f"{code} reason", code=code,
    )


def test_plain_allow_is_not_audited():
    store = MemoryAuditStore()
    event = record_decision(store, _decision(DECISION_ALLOW))
    assert event is None
    assert store.count() == 0


def test_block_is_audited():
    store = MemoryAuditStore()
    decision = _decision(DECISION_BLOCK, code="budget.cost.exceeded")
    event = record_decision(store, decision)
    assert event is not None
    assert store.count() == 1
    recorded = store.read()[0]
    assert recorded.decision == DECISION_BLOCK
    assert recorded.code == "budget.cost.exceeded"
    assert recorded.tenant_id == "acme"


def test_warn_is_audited():
    store = MemoryAuditStore()
    record_decision(store, _decision(DECISION_WARN, code="quota.soft.requests.exceeded"))
    assert store.count() == 1


def test_jsonl_store_round_trip(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    store = JsonlAuditStore(path)
    store.record(record_decision(MemoryAuditStore(), _decision(DECISION_BLOCK)))
    # a second instance over the same file reads the same events (durable)
    store2 = JsonlAuditStore(path)
    assert store2.count() == 1
    assert store2.read()[0].decision == DECISION_BLOCK


def test_jsonl_store_append_is_durable(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    store = JsonlAuditStore(path)
    store.record(record_decision(MemoryAuditStore(), _decision(DECISION_WARN)))
    store.record(record_decision(MemoryAuditStore(), _decision(DECISION_BLOCK)))
    assert JsonlAuditStore(path).count() == 2


def test_kill_switch_refuse_decision_auditable():
    # refuse decisions are auditable (kill-switch blocks carry audit)
    decision = EnforcerDecision(
        tenant_id="acme", kind=KIND_KILL_SWITCH, decision="refuse",
        reason="global kill switch engaged", code="kill_switch.global_pause",
        outcome=OUTCOME_REFUSED,
    )
    store = MemoryAuditStore()
    event = record_decision(store, decision)
    assert event is not None
    assert store.read()[0].outcome == OUTCOME_REFUSED


def test_event_serialization_is_camelcase():
    decision = _decision(DECISION_BLOCK, code="budget.cost.exceeded")
    payload = record_decision(MemoryAuditStore(), decision).to_dict()
    assert "tenantId" in payload
    assert "eventType" in payload
    assert "decisionId" not in payload  # not exposed on the audit event
