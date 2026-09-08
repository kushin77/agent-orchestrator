"""Cost attribution tests: every routed call emits a CallRecord (issue #17 AC 4).

The chooser emits one metering record per non-blocked call through the
injected MeteringSink (the Phase-5 metering-store hook). Verifies the record
carries tenant, agent, task class, tier, model, provider, estimated cost and
budget action, and that blocked calls emit nothing.
"""

from __future__ import annotations

import json

import pytest

from budget import (
    BudgetBlocked,
    BudgetEnforcer,
    BudgetLedger,
    BudgetPolicy,
    TenantBudget,
)
from chooser import ModelChooser
from metering import CallRecord, JsonlMeteringSink, ListMeteringSink


@pytest.fixture()
def recorder(table):
    sink = ListMeteringSink()
    chooser = ModelChooser(table=table, sink=sink)
    return chooser, sink


def test_routed_call_emits_call_record(recorder) -> None:
    chooser, sink = recorder
    choice = chooser.choose(
        task_class="research",
        tenant_id="tenant-acme",
        agent_id="agent-7",
        complexity=20.0,
    )
    assert len(sink.records) == 1
    rec = sink.records[0]
    assert isinstance(rec, CallRecord)
    assert rec.tenant_id == "tenant-acme"
    assert rec.agent_id == "agent-7"
    assert rec.task_class == "research"
    assert rec.tier == choice.tier
    assert rec.model == choice.model.id
    assert rec.provider == choice.model.provider
    assert rec.estimated_cost_usd == choice.estimated_cost_usd
    assert rec.estimated_cost_usd > 0
    assert rec.budget_action == "allow"


def test_escalation_emits_record_for_final_tier(recorder) -> None:
    chooser, sink = recorder
    chooser.choose(task_class="research", tenant_id="t1", complexity=90.0)  # -> L2
    assert len(sink.records) == 1
    assert sink.records[0].tier == "L2"


def test_jsonl_sink_writes_parseable_records(table, tmp_path) -> None:
    meter_path = tmp_path / "meter.jsonl"
    sink = JsonlMeteringSink(meter_path)
    chooser = ModelChooser(table=table, sink=sink)
    chooser.choose(task_class="code-author", tenant_id="tenant-acme", complexity=5.0)
    chooser.choose(task_class="research", tenant_id="tenant-acme", complexity=55.0)

    lines = meter_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    records = [json.loads(line) for line in lines]
    assert records[0]["tier"] == "L0"
    assert records[1]["tier"] == "L1"
    assert records[0]["tenant_id"] == "tenant-acme"
    assert records[0]["model"]  # concrete model id recorded
    # read_records() round-trips the same content
    assert len(sink.read_records()) == 2


def test_blocked_call_emits_no_record(table) -> None:
    sink = ListMeteringSink()
    ledger = BudgetLedger()
    ledger.add_spend("tenant-tight", 100.0)  # hard cap reached on a $100 budget
    enforcer = BudgetEnforcer(ledger=ledger)
    enforcer.add_budget(TenantBudget("tenant-tight", 100.0, BudgetPolicy.WARN))
    chooser = ModelChooser(table=table, budget_enforcer=enforcer, sink=sink)
    with pytest.raises(BudgetBlocked):
        chooser.choose(task_class="code-author", tenant_id="tenant-tight", complexity=5.0)
    assert sink.records == []  # nothing was called, nothing is attributed


def test_call_record_to_dict_round_trip() -> None:
    rec = CallRecord(
        tenant_id="t",
        agent_id="a",
        task_class="research",
        tier="L1",
        model="m",
        provider="p",
        estimated_cost_usd=0.001,
        complexity=50.0,
        reasons=["difficulty"],
    )
    payload = rec.to_dict()
    assert payload["tenant_id"] == "t"
    assert payload["tier"] == "L1"
    assert payload["model"] == "m"
    assert payload["provider"] == "p"
    assert payload["estimated_cost_usd"] == 0.001
    assert payload["complexity"] == 50.0
    assert payload["reasons"] == ["difficulty"]
    assert "timestamp" in payload
