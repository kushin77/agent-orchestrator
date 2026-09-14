"""telemetry/chat — chat-turn value objects + attribution record (issue #506).

The **turn** is the unit of chat FinOps: one user/agent exchange through the
chat surface, which the gateway served with exactly one call record.  This
module owns the two value objects the rest of the package works with:

- ``ChatTurn`` — what the *chat surface* knows about the turn: its identity
  (turn, conversation, tenant, agent), the prompt it assembled (the stable
  static prefix + the user delta), how much of that prefix the provider
  reported serving from cache, and — when the turn was spawned from a ticket
  — the ticket id (the ADR-0014 join node).
- ``TurnAttribution`` — what the *numbers* are: one record per turn, derived
  from the turn joined with the gateway's call record, priced by the
  ``telemetry/metering`` rate cards, and anchored to the one metering record
  and one ledger event the turn produced.

Vocabulary is CONSUMED and never redefined: the decision ladder
(``allow``/``warn``/``fallback``/``block``/``refuse``), the enforcer decision
object and the metering non-billable outcome strings come from
``telemetry.budgets.model`` (which consumes them from ``gateway/finops``);
the cost-source vocabulary and timestamp normalization come from
``telemetry.metering.model``; the prompt-cache discipline comes from
``engine.memory.prompt_cache``.

Two hard rules this module encodes:

1. **No number is re-derived.**  The tier, model, provider, tokens and
   latency are *promoted* from the gateway call record — the routing stamp of
   the lane that produced them — never guessed here.
2. **A client tier is never authoritative.**  If the surface hands a
   ``client_tier`` it is carried for the audit trail only; the turn's tier is
   the one the FinOps chooser stamped on the record (see ``tiering``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from telemetry.budgets.model import DECISION_ALLOW  # consumed vocabulary
from telemetry.metering.model import parse_ts

from .cache_accounting import CacheAccounting, PrefixAccounting, account_prefix

SCHEMA_VERSION = 1

#: Record kind stamped on the serialized attribution.
RECORD_KIND = "chat_turn_attribution"

#: Ledger action for a served turn / a refused turn (audit shape).
LEDGER_ACTION_TURN = "chat.turn"
LEDGER_ACTION_REFUSED = "chat.turn.refused"

#: Ledger resource stamp for everything this lane writes.
LEDGER_RESOURCE = "telemetry/chat"

#: The gateway's whole-response cache hit (no provider call, no usage).
OUTCOME_CACHE_HIT = "cache_hit"


class TurnError(ValueError):
    """A chat turn is malformed or cannot be attributed."""


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TurnError(f"{field_name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class ChatTurn:
    """One chat turn: the chat surface's own view of an exchange.

    ``cached_tokens`` is the prefix reuse the provider *reported* for this
    turn (``None``/absent = not observed, accounted cold).  ``client_tier``
    is a client *claim*, never authoritative.  ``critical`` marks spend the
    kill switch may let through a global pause (the consumed
    ``telemetry.budgets`` kill-switch flag).
    """

    turn_id: str
    conversation_id: str
    tenant_id: str
    agent_id: str
    ts: str = ""
    ticket_id: Optional[str] = None
    client_tier: Optional[str] = None
    static_prefix: str = ""
    user_delta: str = ""
    cached_tokens: Optional[int] = None
    critical: bool = False
    prompt_module: Optional[str] = None

    def __post_init__(self) -> None:
        _require_text(self.turn_id, "turn_id")
        _require_text(self.conversation_id, "conversation_id")
        _require_text(self.tenant_id, "tenant_id")
        _require_text(self.agent_id, "agent_id")
        if self.ticket_id is not None:
            _require_text(self.ticket_id, "ticket_id")
        if self.client_tier is not None:
            _require_text(self.client_tier, "client_tier")
        if self.cached_tokens is not None and self.cached_tokens < 0:
            raise TurnError(f"cached_tokens must be >= 0, got {self.cached_tokens}")

    @property
    def normalized_ts(self) -> str:
        """The turn timestamp in the repo-wide RFC 3339 ``Z`` shape."""
        return parse_ts(self.ts or None)

    @property
    def prefix(self) -> PrefixAccounting:
        """The prompt's cacheable-prefix shape (pure function of the text)."""
        return account_prefix(self.static_prefix, self.user_delta)

    def cache_accounting(self, output_tokens: int) -> CacheAccounting:
        """This turn's cache footprint with the record's output tokens."""
        return CacheAccounting(
            prefix=self.prefix,
            cached_tokens=int(self.cached_tokens or 0),
            output_tokens=output_tokens,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turnId": self.turn_id,
            "conversationId": self.conversation_id,
            "tenantId": self.tenant_id,
            "agentId": self.agent_id,
            "ts": self.normalized_ts,
            "ticketId": self.ticket_id,
            "clientTier": self.client_tier,
            "critical": self.critical,
            "promptModule": self.prompt_module,
        }


@dataclass(frozen=True)
class TurnAttribution:
    """One attribution record per chat turn — the lane's unit of output.

    Every field is either promoted from the gateway call record (identity,
    routing stamp, tokens, latency) or resolved from a consumed authority
    (``cost_usd`` from the metering rate cards, ``tier`` from the chooser's
    stamp, ``cache`` from the prompt-cache discipline).  ``usage_record_id``
    and ``ledger_seq`` name the one metering record and one ledger event this
    turn produced, so the numbers are always traceable back to their row.

    ``input_tokens`` is the **billable** input — the uncached remainder the
    provider charged for; ``cache.prompt_tokens`` is the full prompt the turn
    sent, and ``cold_equivalent_cost_usd`` what that same turn would have cost
    with no prefix reuse at all.
    """

    turn_id: str
    conversation_id: str
    tenant_id: str
    agent_id: str
    ts: str
    outcome: str
    tier: Optional[str]
    provider: Optional[str]
    model: Optional[str]
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cache: CacheAccounting
    cost_usd: Optional[float]
    cost_source: Optional[str]
    cold_equivalent_cost_usd: Optional[float]
    billable: bool
    metered: bool
    unmetered_reason: Optional[str]
    budget_action: str = DECISION_ALLOW
    ticket_id: Optional[str] = None
    client_tier: Optional[str] = None
    tier_claim_honoured: bool = False
    usage_record_id: Optional[str] = None
    ledger_seq: Optional[int] = None
    ledger_hash: Optional[str] = None
    cache_response_hit: bool = False
    schema_version: int = SCHEMA_VERSION

    @property
    def tokens(self) -> int:
        """Total tokens the turn consumed (input + output)."""
        return self.input_tokens + self.output_tokens

    @property
    def cache_hit_share(self) -> float:
        return self.cache.hit_share

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "kind": RECORD_KIND,
            "turnId": self.turn_id,
            "conversationId": self.conversation_id,
            "tenantId": self.tenant_id,
            "agentId": self.agent_id,
            "ticketId": self.ticket_id,
            "ts": self.ts,
            "outcome": self.outcome,
            "tier": self.tier,
            "clientTier": self.client_tier,
            "tierClaimHonoured": self.tier_claim_honoured,
            "provider": self.provider,
            "model": self.model,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "tokens": self.tokens,
            "latencyMs": self.latency_ms,
            "costUsd": None if self.cost_usd is None else round(self.cost_usd, 10),
            "costSource": self.cost_source,
            "coldEquivalentCostUsd": (
                None
                if self.cold_equivalent_cost_usd is None
                else round(self.cold_equivalent_cost_usd, 10)
            ),
            "billable": self.billable,
            "metered": self.metered,
            "unmeteredReason": self.unmetered_reason,
            "budgetAction": self.budget_action,
            "cache": self.cache.to_dict(),
            "cacheResponseHit": self.cache_response_hit,
            "usageRecordId": self.usage_record_id,
            "ledgerSeq": self.ledger_seq,
            "ledgerHash": self.ledger_hash,
        }
