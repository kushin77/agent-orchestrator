"""The lane's own harness: how a metered ERP workspace is built and walked.

Both the lane's tri-state check (:mod:`.cli`) and its negative control
(:mod:`.negative_control`) build their workspace through :func:`build_workspace`
and enumerate the ERP surface through :func:`matrix_plan`, so the control proves
the same object the check measures. Two harnesses would let the check pass while
the control provoked a configuration nothing ships.

Everything here is offline and deterministic: the ledger and the usage store are
in-memory, the timestamps are supplied by the caller (:func:`stamp`), and no
part of the harness reads the wall clock. That is what makes the lane's
determinism assertion meaningful — "two runs agree" is only evidence if neither
run was allowed to consult ``now()``.

---knowledge---
module_id: integrations.erp.finops.harness
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [stamp, policies_from, Workspace, build_workspace, matrix_plan, run_matrix, golden_path]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from telemetry.budgets.budget import BudgetLimit, TenantBudgetPolicy
from telemetry.budgets.model import CAP_HARD, MODE_ENFORCE
from telemetry.metering.report import UsageReporter
from telemetry.metering.store import MemoryUsageStore

from integrations.erp.core.validators import load_model

from . import ledger, rates
from .budget import ErpBudgetGuard, load_policies, spend_ledger
from .ledger import AuditSink
from .meter import ErpMeter
from .model import MeteredEvent
from .rates import RateCard
from .usage import UsageSink

__all__ = [
    "DEFAULT_TENANT",
    "Workspace",
    "build_workspace",
    "golden_path",
    "matrix_plan",
    "policies_from",
    "run_matrix",
    "stamp",
]

#: The tenant the default workspace meters under. It is declared in the shipped
#: budget catalog, because an undeclared tenant cannot be metered at all.
DEFAULT_TENANT = "acme"

#: The deterministic clock origin: 2026-09-15T10:00:00Z. Every event in a
#: harness-built workspace is stamped from here, so two runs of the same
#: sequence produce byte-identical chains.
_ORIGIN_SECONDS = 10 * 3600


def stamp(index: int, *, day: str = "2026-09-15") -> str:
    """A deterministic RFC 3339 UTC timestamp, ``index`` seconds after the origin."""
    seconds = _ORIGIN_SECONDS + int(index)
    return (
        f"{day}T{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}Z"
    )


def policies_from(
    limits: Mapping[str, float],
    *,
    mode: str = MODE_ENFORCE,
    window: str = "month",
    cap: str = CAP_HARD,
) -> Dict[str, TenantBudgetPolicy]:
    """Build platform budget policies from a ``{tenant: monthly USD limit}`` map.

    The platform type does the validation (a non-positive limit raises), which
    is why a bad limit in a test surfaces as the platform's own ``ValueError``
    rather than as a second, weaker rule here.
    """
    return {
        tenant: TenantBudgetPolicy(
            tenant_id=tenant,
            mode=mode,
            cost_limit=BudgetLimit(window=window, limit=float(limit), cap=cap),
        )
        for tenant, limit in limits.items()
    }


@dataclass(frozen=True)
class Workspace:
    """One metered ERP workspace: the core model plus this lane's two sinks."""

    model: Any
    rates: RateCard
    audit: AuditSink
    usage: UsageSink
    reporter: UsageReporter
    budget: ErpBudgetGuard
    meter: ErpMeter
    policies: Mapping[str, TenantBudgetPolicy]


def build_workspace(
    *,
    rate_card: Union[None, str, Path, Mapping[str, Any]] = None,
    policies: Optional[Mapping[str, TenantBudgetPolicy]] = None,
    with_budget: bool = True,
) -> Workspace:
    """Build an offline workspace over the live core model and shipped catalog.

    ``rate_card=None`` means the shipped catalog, so the default workspace is
    exactly what production would load; a test that needs a different price
    passes a mapping instead of editing the catalog.
    """
    model = load_model()
    card = rates.load() if rate_card is None else rates.load(rate_card)
    pol = dict(policies) if policies is not None else load_policies()

    audit = ledger.open_sink()
    usage = UsageSink(MemoryUsageStore())
    reporter = UsageReporter(usage.store)
    guard = ErpBudgetGuard(spend_ledger(reporter), pol) if with_budget else None
    meter = ErpMeter(
        model=model, rates=card, audit=audit, usage=usage, budget=guard
    )
    return Workspace(
        model=model,
        rates=card,
        audit=audit,
        usage=usage,
        reporter=reporter,
        budget=guard,  # type: ignore[arg-type]
        meter=meter,
        policies=pol,
    )


# --------------------------------------------------------------------------- #
# The surface, enumerated from the model rather than from a list kept here.
# --------------------------------------------------------------------------- #
def matrix_plan(
    model: Any,
) -> Tuple[Tuple[str, ...], ...]:
    """Every ``(kind, from_state, action, to)`` the core model declares.

    Read out of the workflow DATA (``Workflow.transitions``) rather than out of
    ``legal_targets``: the latter is a set of reachable states and would pair an
    action with a state it cannot reach. Enumerating the transitions themselves
    means the plan cannot over- or under-count the surface, so "one ledger
    record per operation" is measured against the model's own declaration.
    """
    plan: List[Tuple[str, ...]] = []
    for kind in sorted(model.lifecycle_kinds()):
        workflow = model.workflow_for(kind)
        for transition in workflow.transitions:
            plan.append((kind, transition.from_state, transition.action, transition.to))
    return tuple(plan)


def run_matrix(workspace: Workspace, tenant: str = "matrix") -> Tuple[MeteredEvent, ...]:
    """Meter a create for every kind and a transition for every declared move.

    The honest statement of acceptance criterion 1: *every* document kind the
    module can hold is created, and *every* transition every lifecycle declares
    is taken, each through the public API. The caller compares the resulting
    ledger to the plan.
    """
    events: List[MeteredEvent] = []
    for index, kind in enumerate(sorted(workspace.meter.kinds())):
        events.append(
            workspace.meter.create(
                kind,
                tenant=tenant,
                document_id=f"{kind.upper()}-{index:04d}",
                actor="agent:erp-robot",
                at=stamp(len(events)),
            )
        )
    for index, (kind, from_state, action, to) in enumerate(matrix_plan(workspace.model)):
        events.append(
            workspace.meter.transition(
                kind,
                tenant=tenant,
                document_id=f"{kind.upper()}-T{index:06d}",
                actor="agent:erp-robot",
                from_state=from_state,
                action=action,
                target=to,
                at=stamp(len(events)),
            )
        )
    return tuple(events)


def golden_path(
    workspace: Workspace,
    tenant: str = DEFAULT_TENANT,
    *,
    kind: str = "sales-order",
    document_id: str = "SO-0001",
    start: int = 0,
) -> Tuple[MeteredEvent, ...]:
    """One document's whole life: a create followed by every declared transition.

    Reads the transitions out of the workflow data, so the path grows with the
    model instead of drifting from it.
    """
    workflow = workspace.model.workflow_for(kind)
    events: List[MeteredEvent] = [
        workspace.meter.create(
            kind,
            tenant=tenant,
            document_id=document_id,
            actor="agent:erp-robot",
            at=stamp(start),
        )
    ]
    for offset, transition in enumerate(workflow.transitions, start=1):
        events.append(
            workspace.meter.transition(
                kind,
                tenant=tenant,
                document_id=document_id,
                actor="agent:erp-robot",
                from_state=transition.from_state,
                action=transition.action,
                target=transition.to,
                at=stamp(start + offset),
            )
        )
    return tuple(events)
