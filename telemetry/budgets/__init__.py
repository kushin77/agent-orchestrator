"""telemetry/budgets — per-tenant budgets, quotas, global kill switch + SLO export (issue #34).

---knowledge---
module_id: telemetry.budgets.__init__
system: telemetry
app: budgets
solution_class: pattern
patterns: [package-contract, consumed-never-redefined]
derives_from: null
owner_sme: platform-sme
tier: L0
interfaces: []
invariants: "the package docstring declares the lane contract; the vocabulary it names is consumed from the merged sibling lanes"
gotchas: ""
related: ["#34", "#1510"]
do_not_duplicate: null
---knowledge---


Operational safety rails for the multi-tenant AI SaaS (EPIC-00, phase 5):
per-tenant soft/hard quotas enforced before dispatch (calls, tokens,
concurrency, storage), a per-tenant + per-vendor/model budget enforcer with a
warn -> block ladder over the durable metering feed (issue #33), a
platform-wide global kill switch that halts all non-critical billable model
calls with audit, and a machine-readable SLO/budget state exporter for tenant
dashboards and alerting (consuming the SLO vocabulary of issue #32).

Importable from the repo root as ``telemetry.budgets`` (PEP-420 namespace;
``telemetry/`` carries no ``__init__.py``). Fully offline — Python 3 stdlib +
PyYAML only, no network, no server, no npm/node.

Public surface
--------------

- ``model`` — decision / resource / window vocabulary + value objects.
- ``ledger`` — the spend ledger protocol + ``MeteringReporterLedger`` adapter
  that consumes the metering feed (issue #33) for durable current usage.
- ``budget`` — ``BudgetEnforcer``: per-tenant (and per-vendor/model) budget
  policy with a warn -> block ladder and observe/enforce rollout modes.
- ``quota`` — ``QuotaEnforcer``: per-tenant soft/hard quotas over calls,
  tokens, concurrency and storage with plan-keyed overrides (entitlements
  link, phase 6).
- ``killswitch`` — ``KillSwitchController``: the platform-wide global pause
  (flag-gated OFF by default) that refuses all non-critical model calls.
- ``audit`` — ``BudgetAuditStore``: durable append-only audit of BLOCK/WARN
  budget decisions + kill-switch transitions.
- ``preflight`` — ``CallPreflight``: the composed pre-dispatch check
  (kill switch -> quota -> budget) that the gateway/engine lanes wire in.
- ``exporter`` — ``BudgetStateExporter``: machine-readable budget/quota/SLO
  state export for dashboards and alerting.
- ``alerts`` — ``SpendAlertEvaluator``: the warning/alert thresholds behind
  the spend alerts (soft vs hard cap, honest NO_DATA when the feed has never
  metered the tenant) consumed by the FinOps single-pane (issue #341).
- ``chargeback`` — ``ChargebackReporter``: per-tenant chargeback lines for
  tenant billing from the metering feed.
- ``cli`` — ``python3 -m telemetry.budgets.cli <cmd>``.
"""

from telemetry.budgets import (  # noqa: F401
    alerts,
    audit,
    budget,
    chargeback,
    exporter,
    killswitch,
    ledger,
    model,
    preflight,
    quota,
)

# The re-export above is DELIBERATE (issue #1203): `telemetry.budgets.<module>`
# is the documented public surface of this package, so it is declared here
# rather than left as an import the linter reads as dead code.
__all__ = [
    "alerts",
    "audit",
    "budget",
    "chargeback",
    "exporter",
    "killswitch",
    "ledger",
    "model",
    "preflight",
    "quota",
]
