"""The roll-up: the figures it publishes, and the ones it refuses to invent."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from integrations.erp.finops.harness import build_workspace, golden_path, policies_from, stamp
from integrations.erp.finops.ledger import open_sink
from integrations.erp.finops.model import Refused
from integrations.erp.finops.rollup import ErpRollup
from telemetry.metering.report import UsageReporter


def test_the_rollup_is_the_rate_cards_own_sum(workspace, tenant) -> None:
    events = golden_path(workspace, tenant)
    rollup = ErpRollup(workspace.reporter)
    row = rollup.for_tenant(tenant, month="2026-09")
    assert row is not None
    expected = sum(
        workspace.rates.price_for(event.kind, event.operation) or 0.0 for event in events
    )
    assert row.operations == len(events)
    assert row.cost_usd == round(expected, 8)
    assert row.fully_metered is True
    assert dict(row.by_operation) == {
        "create": 1,
        "transition": len(events) - 1,
    }


def test_the_rollup_reports_the_columns_the_bill_needs(workspace, tenant) -> None:
    golden_path(workspace, tenant)
    payload = ErpRollup(workspace.reporter).usage()[0].to_dict()
    assert set(payload) == {
        "tenantId",
        "month",
        "operations",
        "meteredOperations",
        "unmeteredOperations",
        "costUsd",
        "byKind",
        "byOperation",
    }
    assert json.dumps(payload, sort_keys=True)


def test_a_tenant_with_no_usage_gets_no_row_and_is_never_billed(workspace) -> None:
    rollup = ErpRollup(workspace.reporter)
    assert rollup.for_tenant("nobody") is None
    assert rollup.usage() == ()
    assert rollup.totals()["tenants"] == 0
    with pytest.raises(Refused) as caught:
        rollup.bill("nobody")
    assert caught.value.code == "unmetered-usage"


def test_a_bill_over_an_unpriced_operation_is_refused() -> None:
    card = {
        "schemaVersion": "ao.erp.finops/v1",
        "currency": "USD",
        "supportedCurrencies": ["USD"],
        "rates": [{"kind": "sales-order", "operation": "create", "priced": False}],
    }
    workspace = build_workspace(rate_card=card, policies=policies_from({"acme": 100.0}))
    workspace.meter.create(
        "sales-order", tenant="acme", document_id="SO-1", actor="agent:x", at=stamp(0)
    )
    rollup = ErpRollup(workspace.reporter)
    row = rollup.for_tenant("acme")
    assert row is not None
    assert row.unmetered_operations == 1
    assert row.cost_usd == 0.0
    with pytest.raises(Refused) as caught:
        rollup.bill("acme")
    assert caught.value.code == "unmetered-usage"
    assert "no published price" in caught.value.detail


def test_only_erp_records_are_rolled_up(workspace, tenant) -> None:
    """A model-call record in the same feed is not ERP usage."""
    records = workspace.usage.store
    from telemetry.metering.model import UsageRecord

    records.append(
        UsageRecord(
            tenant_id=tenant,
            agent_id="agent:other",
            provider="openai",
            model="gpt-x",
            route="chat",
            outcome="ok",
            input_tokens=10,
            output_tokens=5,
            billable=True,
            metered=True,
            ts="2026-09-15T10:00:00Z",
            source_type="model_call_event",
            source_key="gateway:1",
            cost_usd=1.5,
        )
    )
    rollup = ErpRollup(UsageReporter(records))
    assert rollup.usage() == ()
    assert rollup.totals()["operations"] == 0


def test_a_cost_is_only_certified_over_an_intact_chain(tmp_path) -> None:
    sink = open_sink(str(tmp_path))
    workspace = build_workspace(policies=policies_from({"acme": 100.0}))
    workspace.meter.audit = sink
    workspace.meter.create(
        "sales-order", tenant="acme", document_id="SO-1", actor="agent:x", at=stamp(0)
    )
    rollup = ErpRollup(workspace.reporter)
    sequence, digest = rollup.certify(sink, "acme")
    assert sequence == 1 and len(digest) == 64

    path = Path(tmp_path) / "acme.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    index = next(
        position
        for position, line in enumerate(lines)
        if line.strip() and not line.strip().startswith("#")
    )
    record = json.loads(lines[index])
    record["action"] = "erp.document.tampered"
    lines[index] = json.dumps(record, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(Refused) as caught:
        rollup.certify(sink, "acme")
    assert caught.value.code == "ledger-unverified"
    assert "NOT-OK" in caught.value.detail
