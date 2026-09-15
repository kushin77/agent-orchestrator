"""Metering an operation: what it writes, in what order, and when it refuses.

These tests hold the line between the two acceptance criteria that meet in the
meter: criterion 1 (every create/transition emits a ledger record) and criterion
2 (a tenant at budget is stopped *before* anything is written).
"""

from __future__ import annotations

import pytest

from integrations.erp.finops.budget import ErpBudgetGuard, spend_ledger
from integrations.erp.finops.harness import (
    build_workspace,
    golden_path,
    matrix_plan,
    policies_from,
    run_matrix,
    stamp,
)
from integrations.erp.finops.model import OP_CREATE, OP_TRANSITION, Refused
from integrations.erp.finops.usage import ERP_PROVIDER, UNMETERED_RATE_UNPUBLISHED


def test_a_create_writes_one_audit_record_and_one_usage_record(workspace, tenant) -> None:
    workspace.meter.create(
        "sales-order",
        tenant=tenant,
        document_id="SO-1",
        actor="agent:erp-robot",
        at=stamp(0),
    )
    records = workspace.audit.records(tenant)
    assert len(records) == 1
    stored = records[0]
    assert stored["action"] == "erp.document.created"
    assert stored["resource"] == "erp/documents/sales-order/SO-1"
    assert stored["evidence"].startswith("erp:")
    assert stored["ts"] == stamp(0)

    usage = workspace.usage.records()
    assert len(usage) == 1
    assert usage[0].provider == ERP_PROVIDER
    assert usage[0].model == "sales-order"
    assert usage[0].route == OP_CREATE
    assert usage[0].tenant_id == tenant
    assert usage[0].cost_usd == workspace.rates.price_for("sales-order", OP_CREATE)
    assert usage[0].metered is True
    assert usage[0].total_tokens == 0


def test_a_transition_records_the_move_it_made(workspace, tenant) -> None:
    workflow = workspace.model.workflow_for("sales-order")
    move = workflow.transitions[0]
    workspace.meter.transition(
        "sales-order",
        tenant=tenant,
        document_id="SO-1",
        actor="agent:erp-robot",
        from_state=move.from_state,
        action=move.action,
        target=move.to,
        at=stamp(0),
    )
    stored = workspace.audit.records(tenant)[0]
    assert stored["action"] == "erp.document.transitioned"
    assert workspace.usage.records()[0].route == OP_TRANSITION


def test_the_whole_declared_surface_is_metered_one_record_per_operation(
    workspace, tenant, model
) -> None:
    events = run_matrix(workspace, tenant)
    plan = matrix_plan(model)
    assert len(events) == len(model.document_kinds()) + len(plan)
    assert len(workspace.audit.records(tenant)) == len(events)
    assert workspace.usage.count() == len(events)
    assert workspace.audit.verify(tenant).status == "OK"


def test_an_illegal_transition_is_refused_by_the_model_that_owns_it(workspace, tenant) -> None:
    with pytest.raises(Exception) as caught:
        workspace.meter.transition(
            "sales-order",
            tenant=tenant,
            document_id="SO-1",
            actor="agent:x",
            from_state="draft",
            action="teleport",
            at=stamp(0),
        )
    assert getattr(caught.value, "code", None) == "unknown_action"
    assert workspace.audit.count(tenant) == 0


def test_an_unknown_kind_is_refused_by_name(workspace, tenant) -> None:
    with pytest.raises(Refused) as caught:
        workspace.meter.create(
            "sprocket", tenant=tenant, document_id="S-1", actor="agent:x", at=stamp(0)
        )
    assert caught.value.code == "unknown-document-kind"


def test_a_replay_is_refused_and_writes_nothing_more(workspace, tenant) -> None:
    fields = dict(
        kind="sales-order", tenant=tenant, document_id="SO-1", actor="agent:x", at=stamp(0)
    )
    workspace.meter.create(**fields)
    with pytest.raises(Refused) as caught:
        workspace.meter.create(**fields)
    assert caught.value.code == "duplicate-event"
    assert workspace.audit.count(tenant) == 1
    assert workspace.usage.count() == 1


def test_an_out_of_order_event_is_refused_and_writes_nothing(workspace, tenant) -> None:
    workspace.meter.create(
        "sales-order", tenant=tenant, document_id="SO-1", actor="agent:x", at=stamp(10)
    )
    with pytest.raises(Refused) as caught:
        workspace.meter.create(
            "sales-order", tenant=tenant, document_id="SO-2", actor="agent:x", at=stamp(5)
        )
    assert caught.value.code == "clock-regression"
    assert workspace.audit.count(tenant) == 1


def test_a_document_from_another_tenant_is_refused(workspace, tenant) -> None:
    with pytest.raises(Refused) as caught:
        workspace.meter.create(
            "sales-order",
            tenant=tenant,
            document_id="SO-1",
            actor="agent:x",
            at=stamp(0),
            document={"doctype": "sales-order", "tenant": "globex", "state": "draft"},
        )
    assert caught.value.code == "tenant-mismatch"


def test_a_document_the_core_model_refuses_is_not_metered(workspace, tenant) -> None:
    with pytest.raises(Exception) as caught:
        workspace.meter.create(
            "sales-order",
            tenant=tenant,
            document_id="SO-1",
            actor="agent:x",
            at=stamp(0),
            document={"doctype": "sales-order", "tenant": tenant, "state": "draft"},
        )
    # The core model's refusal is an ErpError, raised before anything is written.
    assert caught.value.__class__.__name__ == "ErpError"
    assert workspace.audit.count(tenant) == 0


def test_the_budget_guard_stops_before_either_sink_is_written() -> None:
    workspace = build_workspace(policies=policies_from({"stopper": 0.05}))
    price = workspace.rates.price_for("sales-order", OP_CREATE)
    assert price is not None

    allowed = 0
    for index in range(16):
        try:
            workspace.meter.create(
                "sales-order",
                tenant="stopper",
                document_id=f"SO-{index:04d}",
                actor="agent:x",
                at=stamp(index),
            )
        except Refused as exc:
            assert exc.code == "budget-exhausted"
            break
        allowed += 1
    else:  # pragma: no cover - the loop must stop within the run
        pytest.fail("the budget guard never stopped the tenant")

    assert allowed * price < 0.05 <= (allowed + 1) * price
    assert workspace.audit.count("stopper") == allowed
    assert workspace.usage.count() == allowed


def test_a_tenant_with_no_declared_budget_is_refused_not_metered_for_free() -> None:
    workspace = build_workspace(policies=policies_from({"acme": 100.0}))
    with pytest.raises(Refused) as caught:
        workspace.meter.create(
            "sales-order", tenant="globex", document_id="SO-1", actor="agent:x", at=stamp(0)
        )
    assert caught.value.code == "budget-unknown-tenant"
    assert workspace.audit.count("globex") == 0


def test_a_declared_unpriced_operation_is_metered_but_never_priced() -> None:
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
    record = workspace.usage.records()[0]
    assert record.billable is True
    assert record.metered is False
    assert record.cost_usd is None
    assert record.unmetered_reason == UNMETERED_RATE_UNPUBLISHED


def test_a_golden_path_of_the_whole_lifecycle_is_deterministic() -> None:
    first = build_workspace()
    second = build_workspace()
    golden_path(first, "acme")
    golden_path(second, "acme")
    assert first.audit.tail("acme") == second.audit.tail("acme")
    assert first.audit.count("acme") == len(first.model.workflow_for("sales-order").transitions) + 1


def test_the_guard_exposes_the_decision_it_made() -> None:
    workspace = build_workspace(policies=policies_from({"acme": 100.0}))
    assert isinstance(workspace.budget, ErpBudgetGuard)
    decision = workspace.budget.check("acme", requested_cost_usd=1.0, month="2026-09")
    assert decision.allowed is True
    assert isinstance(spend_ledger(workspace.reporter), object)
