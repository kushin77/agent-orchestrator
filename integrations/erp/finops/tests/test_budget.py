"""The budget hook: what it declares, what it stops, and what it never stops.

The ladder itself belongs to ``telemetry/budgets`` (issue #34) and is tested
there. What this lane owes its own suite is that it *uses* that ladder: an
undeclared tenant is refused rather than metered for free, a hard cap stops, a
soft cap does not, and an unenforceable declaration is refused by name.
"""

from __future__ import annotations

import pytest

from integrations.erp.finops.budget import ErpBudgetGuard, load_policies, spend_ledger
from integrations.erp.finops.harness import build_workspace, policies_from, stamp
from integrations.erp.finops.model import Refused
from telemetry.budgets.model import CAP_SOFT, MODE_OBSERVE
from telemetry.metering.report import UsageReporter
from telemetry.metering.store import MemoryUsageStore


def _empty_ledger():
    """The production spend ledger over an empty metering feed."""
    return spend_ledger(UsageReporter(MemoryUsageStore()))


def test_the_shipped_declaration_loads_into_platform_policies() -> None:
    policies = load_policies()
    assert policies, "the shipped budget catalog declares no tenant"
    for tenant, policy in policies.items():
        assert policy.tenant_id == tenant
        assert policy.cost_limit is not None
        assert policy.cost_limit.limit > 0


def test_a_non_positive_limit_is_refused_by_name() -> None:
    with pytest.raises(Refused) as caught:
        load_policies(
            {
                "schemaVersion": "ao.erp.finops/v1",
                "policies": [{"tenantId": "acme", "limitUsd": 0}],
            }
        )
    assert caught.value.code == "budget-policy-invalid"


def test_an_unknown_mode_is_refused_by_name() -> None:
    with pytest.raises(Refused) as caught:
        load_policies(
            {
                "schemaVersion": "ao.erp.finops/v1",
                "policies": [{"tenantId": "acme", "limitUsd": 10, "mode": "shout"}],
            }
        )
    assert caught.value.code == "budget-policy-invalid"
    assert "mode" in caught.value.detail


def test_a_declaration_that_does_not_meet_its_schema_is_refused() -> None:
    with pytest.raises(Refused) as caught:
        load_policies(
            {"schemaVersion": "ao.erp.finops/v1", "policies": [{"tenantId": "acme"}]}
        )
    assert caught.value.code == "budget-policy-invalid"
    assert "limitUsd" in caught.value.detail


def test_an_undeclared_tenant_is_refused_rather_than_metered_for_free() -> None:
    guard = ErpBudgetGuard(_empty_ledger(), policies_from({"acme": 10.0}))
    assert guard.declared("acme") is True
    assert guard.declared("globex") is False
    with pytest.raises(Refused) as caught:
        guard.guard("globex")
    assert caught.value.code == "budget-unknown-tenant"


def test_a_hard_cap_stops_and_a_soft_cap_does_not() -> None:
    hard = build_workspace(policies=policies_from({"t": 0.05}))
    stops = 0
    for index in range(16):
        try:
            hard.meter.create(
                "sales-order", tenant="t", document_id=f"SO-{index}", actor="a", at=stamp(index)
            )
        except Refused as exc:
            assert exc.code == "budget-exhausted"
            stops += 1
            break
    assert stops == 1, "a hard cap must stop the tenant"

    soft = build_workspace(
        policies=policies_from({"t": 0.05}, cap=CAP_SOFT, mode=MODE_OBSERVE)
    )
    for index in range(16):
        soft.meter.create(
            "sales-order", tenant="t", document_id=f"SO-{index}", actor="a", at=stamp(index)
        )
    assert soft.audit.count("t") == 16, "a soft cap is a target, never a stop"


def test_the_guard_reports_the_decision_it_allowed() -> None:
    guard = ErpBudgetGuard(_empty_ledger(), policies_from({"acme": 100.0}))
    decision = guard.guard("acme", requested_cost_usd=1.0, month="2026-09")
    assert decision.allowed is True
    assert decision.tenant_id == "acme"
