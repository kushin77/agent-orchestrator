"""The budget hook: an ERP operation at a tenant's budget is stopped, by name.

Acceptance criterion 2 of issue #654 is a **deterministic hard stop**. The stop
is built out of the platform's own enforcement ladder
(``telemetry.budgets``, issue #34) rather than a second threshold check, because
the platform already owns the vocabulary the rest of the fleet branches on:

* :class:`~telemetry.budgets.budget.BudgetEnforcer` is handed a spend ledger and
  per-tenant policies and returns an
  :class:`~telemetry.budgets.model.EnforcerDecision`;
* the spend ledger is the platform's ``MeteringReporterLedger`` over *this
  lane's* usage store, so the budget an ERP operation is checked against is the
  spend this lane has metered — the figure and the check cannot drift.

Two things this module adds, and nothing else:

1. **fail closed on an undeclared tenant.** The platform enforcer treats a
   tenant with no policy as *unlimited* (correct for a model gateway, where most
   calls are not budgeted). ERP operations are billable by definition, so a
   tenant with no declared budget here is refused by name
   (``budget-unknown-tenant``) rather than metered for free. The policy is the
   *declaration that the tenant may spend*, not an optional extra.
2. **the refusal, named.** A blocking decision becomes
   ``Refused("budget-exhausted", ...)`` carrying the enforcer's own reason and
   code, so the stop is one code an operator can alert on, and the negative
   control can prove the stop happens without asserting on prose.

The guard runs **before** any sink is written (see :mod:`.meter`), so a stopped
operation leaves nothing behind: no ledger record, no usage record, no cost.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple, Union

from telemetry.budgets.budget import BudgetEnforcer, BudgetLimit, TenantBudgetPolicy
from telemetry.budgets.ledger import MeteringReporterLedger, SpendLedger
from telemetry.budgets.model import (
    CAPS,
    MODE_ENFORCE,
    MODES,
    WINDOWS,
    EnforcerDecision,
)

from . import schema as schemas
from .model import Refused

__all__ = [
    "DEFAULT_BUDGETS",
    "BUDGET_SCHEMA",
    "ErpBudgetGuard",
    "load_policies",
    "spend_ledger",
]

PACKAGE = Path(__file__).resolve().parent
BUDGET_SCHEMA = PACKAGE / "schema" / "budget-policy.schema.json"
DEFAULT_BUDGETS = PACKAGE / "catalog" / "budgets.json"


def spend_ledger(reporter: Any) -> SpendLedger:
    """The spend ledger an ERP budget is enforced against.

    The platform's own adapter over a metering reporter — the same object the
    gateway lanes use, so an ERP overrun and a model overrun are compared
    against one kind of figure.
    """
    return MeteringReporterLedger(reporter)


def _read(source: Union[str, Path, Mapping[str, Any]]) -> Tuple[Mapping[str, Any], str]:
    if isinstance(source, Mapping):
        return source, "<memory>"
    path = Path(source)
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, Mapping):
        raise Refused(
            "budget-policy-invalid", f"{path}: a budget policy must be a JSON object"
        )
    return payload, str(path)


def load_policies(
    source: Union[str, Path, Mapping[str, Any]] = DEFAULT_BUDGETS,
) -> Dict[str, TenantBudgetPolicy]:
    """Load per-tenant ERP budget policies from the declaration (fail closed).

    Every entry becomes a platform ``TenantBudgetPolicy`` carrying one monthly
    cost limit. A declaration the platform type cannot accept (a non-positive
    limit, an unknown window or cap, an unknown mode) is refused by name rather
    than dropped: a policy that silently disappears is a tenant that silently
    becomes unbudgeted.
    """
    raw, where = _read(source)

    violations = schemas.validate(
        dict(raw), schemas.load_and_refuse(BUDGET_SCHEMA), where=where
    )
    if violations:
        raise Refused("budget-policy-invalid", "; ".join(violations), where=where)

    policies: Dict[str, TenantBudgetPolicy] = {}
    for index, entry in enumerate(raw.get("policies") or []):
        entry_where = f"{where}:policies[{index}]"
        tenant = str(entry["tenantId"])
        if tenant in policies:
            raise Refused(
                "budget-policy-invalid", f"{tenant} is declared twice", where=entry_where
            )
        mode = str(entry.get("mode") or MODE_ENFORCE)
        window = str(entry.get("window") or "month")
        cap = str(entry.get("cap") or "hard")
        if mode not in MODES:
            raise Refused(
                "budget-policy-invalid",
                f"{tenant}: unknown mode {mode!r} (one of {sorted(MODES)})",
                where=entry_where,
            )
        if window not in WINDOWS:
            raise Refused(
                "budget-policy-invalid",
                f"{tenant}: unknown window {window!r} (one of {sorted(WINDOWS)})",
                where=entry_where,
            )
        if cap not in CAPS:
            raise Refused(
                "budget-policy-invalid",
                f"{tenant}: unknown cap {cap!r} (one of {sorted(CAPS)})",
                where=entry_where,
            )
        try:
            limit = BudgetLimit(
                window=window,
                limit=float(entry["limitUsd"]),
                warn_at_pct=float(entry.get("warnAtPct", 0.8)),
                cap=cap,
            )
        except (TypeError, ValueError) as exc:
            raise Refused(
                "budget-policy-invalid",
                f"{tenant}: the declared limit cannot be enforced: {exc}",
                where=entry_where,
            ) from exc
        policies[tenant] = TenantBudgetPolicy(
            tenant_id=tenant, mode=mode, cost_limit=limit
        )
    return policies


@dataclass
class ErpBudgetGuard:
    """The pre-operation budget check for ERP document operations."""

    ledger: SpendLedger
    policies: Mapping[str, TenantBudgetPolicy]

    def __post_init__(self) -> None:
        self.enforcer = BudgetEnforcer(self.ledger, dict(self.policies))

    def declared(self, tenant: str) -> bool:
        """Whether a budget policy is declared for this tenant."""
        return tenant in self.policies

    def check(
        self,
        tenant: str,
        *,
        requested_cost_usd: float = 0.0,
        month: Optional[str] = None,
    ) -> EnforcerDecision:
        """The raw enforcer decision (a read: available to a caller that wants it)."""
        return self.enforcer.check(
            tenant, requested_cost_usd=requested_cost_usd, month=month
        )

    def guard(
        self,
        tenant: str,
        *,
        requested_cost_usd: float = 0.0,
        month: Optional[str] = None,
        where: Optional[str] = None,
    ) -> EnforcerDecision:
        """Refuse by name unless this operation may be metered for this tenant.

        ``budget-unknown-tenant`` when the tenant has no declared budget
        (billable, so it is never free); ``budget-exhausted`` when the platform
        enforcer returns a blocking decision. The decision is returned on
        success so a caller can surface a warn without a second check.
        """
        if not self.declared(tenant):
            raise Refused(
                "budget-unknown-tenant",
                f"{tenant} has no declared budget policy, so its ERP operations "
                f"cannot be metered",
                where=where,
            )
        decision = self.check(
            tenant, requested_cost_usd=requested_cost_usd, month=month
        )
        if not decision.allowed:
            raise Refused(
                "budget-exhausted",
                f"{tenant} is stopped at {decision.decision} "
                f"({decision.code}): {decision.reason}",
                where=where,
            )
        return decision
