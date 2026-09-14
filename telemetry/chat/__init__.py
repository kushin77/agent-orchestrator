"""telemetry/chat — chat-turn FinOps: attribution, budget caps, cache accounting (issue #506).

The per-turn cost layer of the chat surface (EPIC #500): every chat turn is
attributed **once** — tenant, agent, conversation, promoted provider/model/tier,
tokens, latency, an estimated cost resolved from the merged rate cards, and the
ticket id when the turn came from a ticket (ADR-0014's join node) — enforced
against the merged budget/quota/kill-switch rails *before* the model is called,
and accounted for prompt-cache reuse so a cache-hit turn is visibly cheaper
than the same turn cold.

Importable from the repo root as ``telemetry.chat`` (PEP-420 namespace;
``telemetry/`` carries no ``__init__.py``). Fully offline — stdlib + PyYAML,
no network, no server.

Public surface
--------------

- ``model`` — ``ChatTurn`` (what the chat surface knows) and
  ``TurnAttribution`` (the one record per turn).
- ``attribution`` — ``TurnAttributor``: joins a turn with its gateway call
  record, prices it from the metering rate cards, and writes exactly one
  metering record plus exactly one ledger event.
- ``budget_guard`` — ``TurnBudgetGuard`` over the merged ``telemetry.budgets``
  rails (kill switch -> quota -> budget) and ``GuardedTurnRunner``, which
  consults the guard before it will call a provider.  A refused turn is
  refused *and still metered*.
- ``tiering`` — the chooser owns the tier; a client tier claim is recorded and
  never honoured; escalation is observed-difficulty only.
- ``cache_accounting`` — the prompt-cache footprint and observed prefix reuse
  for a turn, on the consumed ``engine.memory.prompt_cache`` discipline.
- ``readmodel`` — ``ChatFinOpsReadModel``: the per-turn / per-conversation /
  per-tenant / per-ticket numbers the UX lane renders (this lane owns them).

Consumed, never redefined: the decision ladder and enforcer decisions
(``telemetry.budgets`` -> ``gateway/finops``), rate cards, cost-source
vocabulary and the ``UsageRecord`` (``telemetry/metering``), the tamper-evident
audit ledger (``telemetry/ledger``), and the prompt-cache discipline
(``engine/memory/prompt_cache``).
"""

from __future__ import annotations

from .attribution import TurnAttributor, record_fields
from .budget_guard import (
    GuardedTurnResult,
    GuardedTurnRunner,
    TurnBudgetGuard,
    TurnBudgetOutcome,
)
from .cache_accounting import (
    KIND_COLD,
    KIND_FULL,
    KIND_PARTIAL,
    CacheAccounting,
    CacheAccountingError,
    PrefixAccounting,
    account_prefix,
)
from .model import (
    SCHEMA_VERSION,
    LEDGER_ACTION_REFUSED,
    LEDGER_ACTION_TURN,
    ChatTurn,
    TurnAttribution,
    TurnError,
)
from .readmodel import ChatFinOpsReadModel, CostRollup, TurnCostView
from .tiering import (
    EscalationRefused,
    TierClaimError,
    TierResolution,
    TierUnresolvedError,
    escalate_on_observed_failure,
    resolve_turn_tier,
)

__all__ = [
    # value objects
    "ChatTurn",
    "TurnAttribution",
    "TurnError",
    "SCHEMA_VERSION",
    "LEDGER_ACTION_TURN",
    "LEDGER_ACTION_REFUSED",
    # attribution
    "TurnAttributor",
    "record_fields",
    # budget enforcement
    "TurnBudgetGuard",
    "TurnBudgetOutcome",
    "GuardedTurnRunner",
    "GuardedTurnResult",
    # tier discipline
    "TierResolution",
    "resolve_turn_tier",
    "escalate_on_observed_failure",
    "TierUnresolvedError",
    "TierClaimError",
    "EscalationRefused",
    # prompt cache accounting
    "CacheAccounting",
    "CacheAccountingError",
    "PrefixAccounting",
    "account_prefix",
    "KIND_COLD",
    "KIND_PARTIAL",
    "KIND_FULL",
    # read model
    "ChatFinOpsReadModel",
    "TurnCostView",
    "CostRollup",
]
