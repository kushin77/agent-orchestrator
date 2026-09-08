"""telemetry/metering — usage metering + cost engine (issue #33, phase 5).

The billing-grade usage/cost layer of the telemetry pillar: per-call usage
records (tenant/agent/provider/model/route, tokens in+out, cache-hit,
cost-estimate), multi-provider YAML rate cards with context tiers, durable
idempotent aggregation, daily + monthly rollups, and an observe->enforce
daily token budget toggle.

Importable from the repo root as ``telemetry.metering`` (PEP-420 namespace;
``telemetry/`` carries no ``__init__.py``).  Fully offline — stdlib + PyYAML
only, no network, no external servers.

Public surface
--------------

- ``model`` — the canonical ``UsageRecord`` + time-bucket helpers.
- ``ratecards`` — ``RateCardStore`` over ``rate_cards/*.yaml``; the
  estimator returns ``None`` on an unknown provider/model (never 0).
- ``intake`` — ``MeteringIntake``: normalizes the merged gateway
  ``ModelCallEvent`` / ``CallRecord`` / ``MeteringRecord`` and the camelCase
  gateway/observability record shapes into ``UsageRecord``s and resolves
  cost (fail closed on unmetered).
- ``store`` — ``MemoryUsageStore`` / ``JsonlUsageStore``: durable append-only
  JSONL + idempotent (source-key dedup) ingest.
- ``report`` — ``UsageReporter``: daily/monthly rollups, per-tenant /
  per-agent / per-model cost attribution, provider mix, billing feed.
- ``budget`` — ``DailyTokenBudget``: observe->enforce daily token budgets.
- ``cli`` — ``python3 -m telemetry.metering.cli <cmd>``.
"""

from telemetry.metering import (  # noqa: F401
    budget,
    intake,
    model,
    ratecards,
    report,
    store,
)

__all__ = [
    "model",
    "ratecards",
    "intake",
    "store",
    "report",
    "budget",
]
