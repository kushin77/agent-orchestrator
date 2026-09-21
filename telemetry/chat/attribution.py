"""telemetry/chat — per-turn cost/latency attribution (issue #506).

---knowledge---
module_id: telemetry.chat.attribution
system: telemetry
app: chat
solution_class: enterprise
patterns: [join-node-no-recompute, promoted-fields, consumed-rate-cards]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [TurnAttributor, record_fields, TASK_TYPE_CHAT_TURN, CODE_IDENTITY_MISMATCH, CODE_DUPLICATE_TURN]
invariants: "nothing is re-derived: identity, the routing stamp, tokens and latency are promoted from the gateway call record and the price comes from the rate cards"
gotchas: ""
related: ["#506", "#1510"]
do_not_duplicate: null
---knowledge---


One attribution record per chat turn, derived from the turn joined with the
gateway's call record.  This is the join node of the chat FinOps surface: the
turn knows the conversation (and the ticket it came from, ADR-0014), the call
record knows the routing stamp and the usage, and the metering rate cards know
the price.  Nothing is re-derived here:

- the identity, routing stamp (``tier``/``provider``/``model``), tokens and
  latency are **promoted** from the gateway call record;
- the price is resolved by the ``telemetry/metering`` rate cards
  (``RateCardStore.estimate``) over the turn's *billable* input tokens — the
  uncached remainder the prompt-cache accounting reports — never by
  arithmetic on raw tokens in this lane;
- a turn that came from a ticket carries the ticket id (ADR-0014's join node)
  through to the attribution record and its ledger event, so a charge can be
  traced back to the ticket that ordered it;
- the tier is the chooser's routing stamp; a client tier claim is recorded and
  never honoured (see ``tiering``).

Every turn writes **exactly one** metering record and **exactly one** ledger
event.  The order is deliberate: metering first, audit second.  Metering is
the mechanism (a turn that reached a model is always counted, even if the
audit write later fails, which is loud); a turn that cannot be metered fails
before anything is written, so a half-attributed turn cannot exist.

The source record is consumed **structurally** — the camelCase shape that
``gateway/proxy`` ``GatewayCallRecord.to_dict()`` emits and that
``telemetry/metering`` and ``telemetry/observability`` already absorb.  This
lane never imports the gateway package (its modules are plain scripts that
require their own directory on ``sys.path``); ``scripts/check-chat-finops.sh``
feeds a real ``GatewayCallRecord`` through this parser to prove the shape
still matches.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from telemetry.metering.intake import MeteringIntake
from telemetry.metering.model import (
    COST_SOURCE_ATTACHED,
    COST_SOURCE_CACHE_HIT,
    COST_SOURCE_RATE_CARD,
)
from telemetry.metering.ratecards import RateCardStore
from telemetry.metering.store import MemoryUsageStore

from .cache_accounting import CacheAccounting
from .model import (
    OUTCOME_CACHE_HIT,
    LEDGER_ACTION_REFUSED,
    LEDGER_ACTION_TURN,
    LEDGER_RESOURCE,
    ChatTurn,
    TurnAttribution,
    TurnError,
)
from .tiering import resolve_turn_tier

#: Stable finding codes (the gate greps for these).
CODE_IDENTITY_MISMATCH = "CHAT-ATTRIBUTION-IDENTITY-MISMATCH"
CODE_DUPLICATE_TURN = "CHAT-DUPLICATE-TURN"

#: The chat surface's own derived task type for a turn.
TASK_TYPE_CHAT_TURN = "chat.turn"


def _pick(fields: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    """First present, non-empty candidate key (camelCase or snake_case)."""
    for name in names:
        if name in fields:
            value = fields[name]
            if value is not None and value != "":
                return value
    return default


def _as_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def record_fields(record: Any) -> Dict[str, Any]:
    """The gateway call record as a mapping, whatever shape it arrives in.

    Accepts the stored camelCase dict or any object exposing ``to_dict()``
    (a live ``gateway/proxy`` ``GatewayCallRecord``), so the chat surface can
    hand over the record it already has without re-serializing it.
    """
    if isinstance(record, Mapping):
        return dict(record)
    to_dict = getattr(record, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        if isinstance(payload, Mapping):
            return dict(payload)
    raise TurnError(
        "an attribution source must be a mapping or expose to_dict(); "
        f"got {type(record).__name__}"
    )


class TurnAttributor:
    """Derives one ``TurnAttribution`` per turn and writes its two rows.

    ``rate_store`` is the price authority, ``usage_store`` receives the one
    metering record per turn and ``ledger`` receives the one audit event.
    """

    def __init__(
        self,
        ledger: Any,
        *,
        rate_store: Optional[RateCardStore] = None,
        usage_store: Optional[Any] = None,
    ) -> None:
        self.rate_store = rate_store or RateCardStore.load_dir()
        self.ledger = ledger
        self.usage_store = usage_store if usage_store is not None else MemoryUsageStore()
        self.intake = MeteringIntake(self.rate_store, self.usage_store)

    # ------------------------------------------------------------------ #
    # pricing (the rate card is the authority)
    # ------------------------------------------------------------------ #
    def _input_basis(
        self, fields: Mapping[str, Any], cache: CacheAccounting
    ) -> tuple[int, int]:
        """Return ``(billable_input_tokens, cold_input_tokens)``.

        When the turn described its prompt, the billable basis is the prompt
        minus the cached prefix (the cache served it, so it is not re-billed)
        and the cold equivalent is the whole prompt.  When the turn carries no
        prompt text at all the record's own ``inputTokens`` is the basis — the
        record's figure, never an estimate invented here.
        """
        if cache.prompt_tokens > 0:
            return cache.billable_input_tokens, cache.prompt_tokens
        record_input = _as_int(_pick(fields, "inputTokens", "input_tokens"))
        return record_input, record_input

    def _price(
        self,
        fields: Mapping[str, Any],
        cache: CacheAccounting,
        *,
        response_cached: bool,
    ) -> tuple[Optional[float], Optional[str], Optional[float], Optional[str]]:
        """Return ``(cost_usd, cost_source, cold_equivalent_usd, unmetered_reason)``."""
        if response_cached:
            # The gateway served the whole response from its own cache: no
            # provider call happened, which is the metering lane's explicit
            # zero-cost path (never a figure this lane invents).
            return 0.0, COST_SOURCE_CACHE_HIT, 0.0, None

        provider = _pick(fields, "provider")
        model = _pick(fields, "model")
        attached = _as_float(_pick(fields, "estimatedCostUsd", "estimated_cost_usd"))
        billable_input, cold_input = self._input_basis(fields, cache)
        output_tokens = cache.output_tokens

        if provider and model:
            estimate = self.rate_store.estimate(
                str(provider), str(model), billable_input, output_tokens
            )
            cold = self.rate_store.estimate(
                str(provider), str(model), cold_input, output_tokens
            )
            if estimate is not None:
                return (
                    estimate.cost_usd,
                    COST_SOURCE_RATE_CARD,
                    cold.cost_usd if cold is not None else None,
                    None,
                )
        if attached is not None and attached > 0:
            # No usable rate card: the source record's own positive estimate is
            # the only trustworthy figure (attributed as-is, never re-priced).
            return attached, COST_SOURCE_ATTACHED, attached, None
        where = (
            f"{provider}/{model}"
            if (provider and model)
            else "unknown provider/model"
        )
        return (
            None,
            None,
            None,
            f"no rate card for {where} and no attached cost estimate",
        )

    # ------------------------------------------------------------------ #
    # rows
    # ------------------------------------------------------------------ #
    def _meter(self, source: Dict[str, Any]) -> Any:
        """Persist exactly one metering record for the turn (idempotently)."""
        outcome = self.intake.ingest(source)
        if outcome.duplicate:
            raise TurnError(
                f"{CODE_DUPLICATE_TURN}: turn {source.get('requestId')!r} already "
                "has a metering record — one turn is metered exactly once"
            )
        return outcome.record

    def _audit(
        self,
        turn: ChatTurn,
        *,
        action: str,
        cost_usd: Optional[float],
        payload: Dict[str, Any],
        model_used: Optional[str],
    ) -> Dict[str, Any]:
        """Append exactly one ledger event for the turn."""
        return self.ledger.append(
            turn.tenant_id,
            actor=f"agent:{turn.agent_id}",
            action=action,
            resource=LEDGER_RESOURCE,
            evidence=f"turn {turn.turn_id}",
            model_used=model_used,
            cost_usd=cost_usd,
            payload=payload,
            ts=turn.normalized_ts,
        )

    # ------------------------------------------------------------------ #
    # public surface
    # ------------------------------------------------------------------ #
    def attribute(
        self,
        turn: ChatTurn,
        record: Any,
        *,
        decision: str = "allow",
    ) -> TurnAttribution:
        """Attribute one served turn from its gateway call record."""
        fields = record_fields(record)
        self._assert_identity(turn, fields)

        resolution = resolve_turn_tier(
            _pick(fields, "tier"), client_tier=turn.client_tier
        )
        provider = _pick(fields, "provider")
        model = _pick(fields, "model")
        outcome = str(_pick(fields, "outcome", default="success"))
        response_cached = outcome == OUTCOME_CACHE_HIT
        output_tokens = _as_int(_pick(fields, "outputTokens", "output_tokens"))
        latency_ms = _as_float(_pick(fields, "latencyMs", "latency_ms")) or 0.0
        cache = turn.cache_accounting(output_tokens if not response_cached else 0)

        priced_cost, priced_source, cold_equivalent, priced_reason = self._price(
            fields, cache, response_cached=response_cached
        )
        billable_input = (
            0 if response_cached else self._input_basis(fields, cache)[0]
        )

        record_row = self._meter(
            {
                "requestId": turn.turn_id,
                "ts": turn.normalized_ts,
                "tenantId": turn.tenant_id,
                "agentId": turn.agent_id,
                "taskType": TASK_TYPE_CHAT_TURN,
                "taskClass": _pick(fields, "taskClass", "task_class"),
                "tier": resolution.tier,
                "provider": provider,
                "model": model,
                "outcome": outcome,
                "inputTokens": billable_input,
                "outputTokens": cache.output_tokens,
                "latencyMs": latency_ms,
                "estimatedCostUsd": priced_cost,
                "budgetAction": _pick(fields, "budgetAction", "budget_action", default=decision),
                "conversationId": turn.conversation_id,
                "cachedPromptTokens": cache.cached_tokens,
                "promptTokens": cache.prompt_tokens,
                "ticketId": turn.ticket_id,
                "cacheHitShare": cache.hit_share,
            }
        )
        # The metering row is the authority for the recorded figure: a
        # non-billable outcome prices to None there whatever was projected, so
        # the attribution echoes the row instead of keeping a second opinion.
        cost = record_row.cost_usd
        cost_source = record_row.cost_source or priced_source
        unmetered_reason = record_row.unmetered_reason or priced_reason

        ledger_row = self._audit(
            turn,
            action=LEDGER_ACTION_TURN,
            cost_usd=cost,
            model_used=(
                f"{provider}/{model}" if (provider and model) else None
            ),
            payload={
                "turnId": turn.turn_id,
                "conversationId": turn.conversation_id,
                "ticketId": turn.ticket_id,
                "tier": resolution.tier,
                "clientTier": turn.client_tier,
                "outcome": outcome,
                "costUsd": cost,
                "costSource": cost_source,
                "cacheHitShare": cache.hit_share,
                "cachedTokens": cache.cached_tokens,
                "billableInputTokens": billable_input,
                "promptModule": turn.prompt_module,
                "usageRecordId": record_row.record_id,
            },
        )

        return TurnAttribution(
            turn_id=turn.turn_id,
            conversation_id=turn.conversation_id,
            tenant_id=turn.tenant_id,
            agent_id=turn.agent_id,
            ts=turn.normalized_ts,
            outcome=outcome,
            tier=resolution.tier,
            provider=str(provider) if provider else None,
            model=str(model) if model else None,
            input_tokens=billable_input,
            output_tokens=cache.output_tokens,
            latency_ms=latency_ms,
            cache=cache,
            cost_usd=cost,
            cost_source=cost_source,
            cold_equivalent_cost_usd=cold_equivalent,
            billable=bool(record_row.billable),
            metered=bool(record_row.metered),
            unmetered_reason=unmetered_reason,
            budget_action=str(
                _pick(fields, "budgetAction", "budget_action", default=decision)
            ),
            ticket_id=turn.ticket_id,
            client_tier=turn.client_tier,
            tier_claim_honoured=resolution.claim_honoured,
            usage_record_id=record_row.record_id,
            ledger_seq=ledger_row.get("seq"),
            ledger_hash=ledger_row.get("hash"),
            cache_response_hit=response_cached,
        )

    def attribute_refusal(
        self,
        turn: ChatTurn,
        *,
        outcome: str,
        decision: str,
        code: str = "",
        reason: str = "",
        model: Optional[str] = None,
        tier: Optional[str] = None,
    ) -> TurnAttribution:
        """Attribute one refused turn: metered, audited, and never billed.

        No provider was called, so the cache observation is forced to zero (a
        cache share for a turn that never ran would be fabricated) and the
        cost is ``None`` — the honest "not billed" the metering lane records
        for a non-billable event, never a fabricated ``0.00``.
        """
        cache = CacheAccounting(prefix=turn.prefix, cached_tokens=0, output_tokens=0)
        record_row = self._meter(
            {
                "requestId": turn.turn_id,
                "ts": turn.normalized_ts,
                "tenantId": turn.tenant_id,
                "agentId": turn.agent_id,
                "taskType": TASK_TYPE_CHAT_TURN,
                "tier": tier,
                "provider": None,
                "model": model,
                "outcome": outcome,
                "inputTokens": 0,
                "outputTokens": 0,
                "latencyMs": 0.0,
                "estimatedCostUsd": None,
                "budgetAction": decision,
                "conversationId": turn.conversation_id,
                "ticketId": turn.ticket_id,
                "refusalCode": code,
                "refusalReason": reason,
            }
        )
        ledger_row = self._audit(
            turn,
            action=LEDGER_ACTION_REFUSED,
            cost_usd=None,
            model_used=model,
            payload={
                "turnId": turn.turn_id,
                "conversationId": turn.conversation_id,
                "ticketId": turn.ticket_id,
                "tier": tier,
                "decision": decision,
                "outcome": outcome,
                "code": code,
                "reason": reason,
                "usageRecordId": record_row.record_id,
            },
        )
        return TurnAttribution(
            turn_id=turn.turn_id,
            conversation_id=turn.conversation_id,
            tenant_id=turn.tenant_id,
            agent_id=turn.agent_id,
            ts=turn.normalized_ts,
            outcome=outcome,
            tier=tier,
            provider=None,
            model=model,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0.0,
            cache=cache,
            cost_usd=None,
            cost_source=None,
            cold_equivalent_cost_usd=None,
            billable=bool(record_row.billable),
            metered=bool(record_row.metered),
            unmetered_reason=None,
            budget_action=decision,
            ticket_id=turn.ticket_id,
            client_tier=turn.client_tier,
            tier_claim_honoured=False,
            usage_record_id=record_row.record_id,
            ledger_seq=ledger_row.get("seq"),
            ledger_hash=ledger_row.get("hash"),
        )

    # ------------------------------------------------------------------ #
    def _assert_identity(self, turn: ChatTurn, fields: Mapping[str, Any]) -> None:
        """Refuse a record whose tenant/agent is not the turn's.

        A record attributed to another tenant would leak one tenant's cost
        into another's numbers; the join must be exact.
        """
        record_tenant = _pick(fields, "tenantId", "tenant_id")
        record_agent = _pick(fields, "agentId", "agent_id")
        if record_tenant and str(record_tenant) != turn.tenant_id:
            raise TurnError(
                f"{CODE_IDENTITY_MISMATCH}: record tenant {record_tenant!r} is not "
                f"the turn's tenant {turn.tenant_id!r}"
            )
        if record_agent and str(record_agent) != turn.agent_id:
            raise TurnError(
                f"{CODE_IDENTITY_MISMATCH}: record agent {record_agent!r} is not "
                f"the turn's agent {turn.agent_id!r}"
            )
