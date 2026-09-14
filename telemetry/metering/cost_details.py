"""telemetry/metering — usage_details / cost_details cost model (issue #341).

WHY this exists: the metering lane already resolves *one* cost figure per call
(issue #33), but a FinOps single-pane has to answer "those tokens cost what,
on which price tier?" — and a bare scalar cannot. This module adds the
breakdown vocabulary the report/serving layer consumes:

* ``usage_details`` — the unit-keyed usage map (``input`` / ``output`` /
  ``total``), the shape Langfuse calls ``usageDetails``: usage is a map of
  measured quantities, never one blended number that hides the split.
* ``CostDetail`` — the *components* of one call's cost (input USD, output USD,
  total) plus the **pricing tier** that priced it: ``standard``,
  ``longContext`` (the call's input/prompt token count exceeded the model's
  long-context threshold — the issue #33 rate-card vocabulary, including the
  Gemini/Vertex convention that steps *both* rates up) or ``local`` (a
  card-declared $0 local model: a *priced* zero, not an unknown one).
* ``CostBreakdown`` — those components aggregated over many records, split per
  pricing tier, carrying the count of *unpriced* calls and the models behind
  them.

The cardinal rule is inherited unchanged from the rate-card estimator:
**unknown -> None, never 0**. A provider/model with no rate-card entry yields
``priced=False`` with every USD figure ``None`` and a machine reason, the
breakdown then reports ``costComplete=False``, and no surface may present an
incomplete cost as a total. An empty record set yields an *empty* usage map
(no data), never a zero map that would read as "no spend".

Provenance: the ``usageDetails`` / ``costDetails`` shapes follow Langfuse's
model-usage-and-cost model (a unit-keyed usage map beside a per-component cost
map), from the issue-#341 research pass; the tier vocabulary is this repo's
own rate-card vocabulary (``ratecards.RateCardEntry``), never a second one.

Pure data + arithmetic: no I/O, no network. The rate cards are loaded by the
caller through ``ratecards.RateCardStore``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from telemetry.metering.model import UsageRecord
from telemetry.metering.ratecards import RateCardEntry, RateCardStore

# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #
#: Usage units (Langfuse ``usageDetails`` keys; ``total`` is their sum).
UNIT_INPUT = "input"
UNIT_OUTPUT = "output"
UNIT_TOTAL = "total"

#: Pricing tiers — a call is priced on exactly one of these.
TIER_STANDARD = "standard"          # the card's standard rate
TIER_LONG_CONTEXT = "longContext"   # the long-context tier (input over threshold)
TIER_LOCAL = "local"                # a card-declared $0 local model
PRICING_TIERS = (TIER_STANDARD, TIER_LONG_CONTEXT, TIER_LOCAL)


def _tokens(count: Any) -> int:
    """Coerce a token count to a non-negative int (NaN/negative read as 0).

    Mirrors the rate-card estimator's clamp: a nonsensical count is honest
    *zero usage*, never a negative or NaN figure propagating into a total.
    """
    try:
        value = float(count)
    except (TypeError, ValueError):
        return 0
    if value != value or value in (float("inf"), float("-inf")) or value < 0:
        return 0
    return int(value)


def usage_details(input_tokens: Any, output_tokens: Any) -> Dict[str, int]:
    """The unit-keyed usage map for one call (or one pre-summed bucket)."""
    prompt = _tokens(input_tokens)
    completion = _tokens(output_tokens)
    return {UNIT_INPUT: prompt, UNIT_OUTPUT: completion, UNIT_TOTAL: prompt + completion}


def usage_details_for(records: Iterable[UsageRecord]) -> Dict[str, int]:
    """The summed unit map over ``records``.

    ``{}`` when no record is supplied — "no data" is an empty map, never a
    zero map that a caller could mistake for a measured no-spend.
    """
    rows: List[UsageRecord] = list(records)
    if not rows:
        return {}
    return usage_details(
        sum(_tokens(record.input_tokens) for record in rows),
        sum(_tokens(record.output_tokens) for record in rows),
    )


def tier_for(entry: RateCardEntry, input_tokens: Any) -> str:
    """The pricing tier a card applies for a call with this input token count.

    A card-declared local model is ``local`` (its explicit $0 is a price, not
    an unknown); otherwise the long-context tier applies when the card has one
    and the call's input exceeded its threshold; otherwise ``standard``.
    """
    if entry.local:
        return TIER_LOCAL
    if (
        entry.long_context is not None
        and entry.long_context_threshold_tokens is not None
        and _tokens(input_tokens) > entry.long_context_threshold_tokens
    ):
        return TIER_LONG_CONTEXT
    return TIER_STANDARD


# --------------------------------------------------------------------------- #
# One call
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CostDetail:
    """The cost components + pricing tier of one priced (or unpriced) call.

    ``priced`` is the honesty flag: ``True`` means every USD figure below is a
    real figure (a card-declared $0 local model included); ``False`` means the
    provider/model has no rate card, every USD figure is ``None``, and
    ``reason`` says so. A caller must never read an unpriced call as $0.
    """

    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    priced: bool
    tier: Optional[str] = None
    input_usd: Optional[float] = None
    output_usd: Optional[float] = None
    total_usd: Optional[float] = None
    input_usd_per_million: Optional[float] = None
    output_usd_per_million: Optional[float] = None
    long_context_applied: bool = False
    reason: str = ""

    @property
    def usage(self) -> Dict[str, int]:
        """The call's unit-keyed usage map."""
        return usage_details(self.input_tokens, self.output_tokens)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to the camelCase reporting shape.

        ``costDetails`` is ``None`` for an unpriced call: a components object
        full of nulls would still invite a reader to sum it to 0.
        """
        return {
            "provider": self.provider,
            "model": self.model,
            "priced": self.priced,
            "tier": self.tier,
            "usageDetails": self.usage,
            "costDetails": (
                {
                    "inputUsd": round(self.input_usd or 0.0, 10),
                    "outputUsd": round(self.output_usd or 0.0, 10),
                    "totalUsd": round(self.total_usd or 0.0, 10),
                }
                if self.priced
                else None
            ),
            "rates": {
                "inputUsdPerMillion": self.input_usd_per_million,
                "outputUsdPerMillion": self.output_usd_per_million,
            },
            "longContextApplied": self.long_context_applied,
            "reason": self.reason,
        }


class CostModel:
    """Prices calls through the metering rate cards, exposing pricing tiers."""

    def __init__(self, cards: RateCardStore) -> None:
        self.cards = cards

    def cost_details(
        self,
        provider: Optional[str],
        model: Optional[str],
        input_tokens: Any,
        output_tokens: Any,
    ) -> CostDetail:
        """Resolve one call's cost components and the tier that priced them."""
        vendor = provider or ""
        model_id = model or ""
        prompt = _tokens(input_tokens)
        completion = _tokens(output_tokens)
        entry = self.cards.lookup(vendor, model_id)
        if entry is None:
            return CostDetail(
                provider=vendor,
                model=model_id,
                input_tokens=prompt,
                output_tokens=completion,
                priced=False,
                reason=(
                    f"no rate card for {vendor!r}/{model_id!r}: cost is unknown, "
                    "never assumed zero"
                ),
            )
        rate = entry.rate_for(prompt)
        input_usd = (prompt / 1_000_000.0) * rate.input_usd_per_million
        output_usd = (completion / 1_000_000.0) * rate.output_usd_per_million
        return CostDetail(
            provider=vendor,
            model=model_id,
            input_tokens=prompt,
            output_tokens=completion,
            priced=True,
            tier=tier_for(entry, prompt),
            input_usd=input_usd,
            output_usd=output_usd,
            total_usd=input_usd + output_usd,
            input_usd_per_million=rate.input_usd_per_million,
            output_usd_per_million=rate.output_usd_per_million,
            long_context_applied=rate is entry.long_context,
        )

    def cost_details_for(self, record: UsageRecord) -> CostDetail:
        """Resolve one stored ``UsageRecord``'s cost components."""
        return self.cost_details(
            record.provider,
            record.model,
            record.input_tokens,
            record.output_tokens,
        )

    @classmethod
    def from_rate_cards(cls, cards: RateCardStore) -> "CostModel":
        """Build a model over an already-loaded rate-card store."""
        return cls(cards)


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TierTotals:
    """The calls, usage and cost components a single pricing tier produced."""

    tier: str
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    input_usd: float = 0.0
    output_usd: float = 0.0
    total_usd: float = 0.0

    @property
    def usage(self) -> Dict[str, int]:
        return usage_details(self.input_tokens, self.output_tokens)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tier": self.tier,
            "calls": self.calls,
            "usageDetails": self.usage,
            "costDetails": {
                "inputUsd": round(self.input_usd, 10),
                "outputUsd": round(self.output_usd, 10),
                "totalUsd": round(self.total_usd, 10),
            },
        }


@dataclass(frozen=True)
class CostBreakdown:
    """Cost components for a group of records, split by pricing tier.

    ``recorded_cost_usd`` is what the durable feed itself recorded for those
    same records (the attributed spend the budgets enforce on); ``total_usd``
    is the rate-card re-pricing of the same tokens. ``reconciles`` is True only
    when every call was priced by a card *and* the two agree — so a report can
    state the difference rather than hide it. ``cost_complete`` is False when
    any call could not be priced: its tokens are still counted, its cost is
    unknown, and ``unpriced_models`` names the models responsible.
    """

    calls: int = 0
    priced_calls: int = 0
    unpriced_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    input_usd: float = 0.0
    output_usd: float = 0.0
    total_usd: float = 0.0
    recorded_cost_usd: float = 0.0
    tiers: Tuple[TierTotals, ...] = ()
    unpriced_models: Tuple[str, ...] = ()

    @property
    def cost_complete(self) -> bool:
        """True only when every counted call was priced by a rate card."""
        return self.unpriced_calls == 0

    @property
    def reconciles(self) -> bool:
        """True when the card re-pricing matches the feed's recorded cost."""
        if not self.cost_complete or self.calls == 0:
            return False
        return abs(self.total_usd - self.recorded_cost_usd) <= 1e-6

    @property
    def usage(self) -> Dict[str, int]:
        """The group's unit-keyed usage map (``{}`` when it has no calls)."""
        if self.calls == 0:
            return {}
        return usage_details(self.input_tokens, self.output_tokens)

    def tier(self, tier_id: str) -> Optional[TierTotals]:
        """The subtotal for one pricing tier, or ``None`` when it priced nothing."""
        for row in self.tiers:
            if row.tier == tier_id:
                return row
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to the camelCase reporting shape."""
        return {
            "calls": self.calls,
            "pricedCalls": self.priced_calls,
            "unpricedCalls": self.unpriced_calls,
            "usageDetails": self.usage,
            "costDetails": (
                {
                    "inputUsd": round(self.input_usd, 10),
                    "outputUsd": round(self.output_usd, 10),
                    "totalUsd": round(self.total_usd, 10),
                }
                if self.priced_calls
                else None
            ),
            "recordedCostUsd": round(self.recorded_cost_usd, 10),
            "costComplete": self.cost_complete,
            "reconciles": self.reconciles,
            "tiers": [row.to_dict() for row in self.tiers],
            "unpricedModels": list(self.unpriced_models),
        }


def breakdown_for(records: Iterable[UsageRecord], model: CostModel) -> CostBreakdown:
    """Price every record through ``model`` and aggregate the components.

    Each record is priced on its **own** input token count (the long-context
    tier is a per-call decision — pricing a summed token count would apply the
    wrong tier to every call in the group), then the components are summed.
    """
    rows: List[UsageRecord] = list(records)
    tiers: Dict[str, TierTotals] = {}
    input_tokens = 0
    output_tokens = 0
    input_usd = 0.0
    output_usd = 0.0
    recorded = 0.0
    priced = 0
    unpriced = 0
    unpriced_models: List[str] = []

    for record in rows:
        detail = model.cost_details_for(record)
        input_tokens += detail.input_tokens
        output_tokens += detail.output_tokens
        if record.cost_usd is not None:
            recorded += float(record.cost_usd)
        if not detail.priced or detail.tier is None:
            unpriced += 1
            label = f"{detail.provider}/{detail.model}"
            if label not in unpriced_models:
                unpriced_models.append(label)
            continue
        priced += 1
        row = tiers.get(detail.tier) or TierTotals(tier=detail.tier)
        tiers[detail.tier] = TierTotals(
            tier=detail.tier,
            calls=row.calls + 1,
            input_tokens=row.input_tokens + detail.input_tokens,
            output_tokens=row.output_tokens + detail.output_tokens,
            input_usd=row.input_usd + (detail.input_usd or 0.0),
            output_usd=row.output_usd + (detail.output_usd or 0.0),
            total_usd=row.total_usd + (detail.total_usd or 0.0),
        )
        input_usd += detail.input_usd or 0.0
        output_usd += detail.output_usd or 0.0

    return CostBreakdown(
        calls=len(rows),
        priced_calls=priced,
        unpriced_calls=unpriced,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_usd=input_usd,
        output_usd=output_usd,
        total_usd=input_usd + output_usd,
        recorded_cost_usd=recorded,
        tiers=tuple(tiers[tier] for tier in PRICING_TIERS if tier in tiers),
        unpriced_models=tuple(unpriced_models),
    )
