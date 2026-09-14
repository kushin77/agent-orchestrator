"""telemetry/metering — usage_details / cost_details cost model tests (issue #341).

Covers the unit-keyed usage map, the per-component cost at the standard tier,
the long-context tier (per-call threshold, both rates stepped up), the local $0
tier (a *priced* zero) and the core negative: a provider/model with no rate
card is **unknown**, never priced as zero.
"""

from __future__ import annotations

import pytest

from telemetry.metering.cost_details import (
    TIER_LOCAL,
    TIER_LONG_CONTEXT,
    TIER_STANDARD,
    CostModel,
    breakdown_for,
    tier_for,
    usage_details,
    usage_details_for,
)
from telemetry.metering.model import UsageRecord
from telemetry.metering.ratecards import (
    ModelRate,
    RateCard,
    RateCardEntry,
    RateCardStore,
)

PROVIDER = "acme-ai"
STANDARD = "acme-standard"
LONG = "acme-long"
LOCAL = "acme-local"
UNKNOWN = "acme-secret"


def _entry(
    model: str,
    standard: tuple[float, float],
    long_context: tuple[float, float] | None = None,
    threshold: int | None = None,
    local: bool = False,
) -> RateCardEntry:
    return RateCardEntry(
        model=model,
        standard=ModelRate(*standard),
        long_context=ModelRate(*long_context) if long_context else None,
        long_context_threshold_tokens=threshold,
        local=local,
    )


def _cards(*entries: RateCardEntry) -> RateCardStore:
    card = RateCard(
        provider=PROVIDER,
        entries={entry.model: entry for entry in entries},
        source="test.yaml",
    )
    return RateCardStore([card])


def _model() -> CostModel:
    return CostModel(
        _cards(
            _entry(STANDARD, (3.0, 15.0)),
            # long-context steps BOTH rates up past 200k input tokens
            _entry(LONG, (3.0, 15.0), (6.0, 30.0), threshold=200_000),
            _entry(LOCAL, (0.0, 0.0), local=True),
        )
    )


def _record(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float | None = 0.0,
    *,
    agent: str = "coder-1",
    provider: str = PROVIDER,
    metered: bool = True,
    n: int = 1,
) -> UsageRecord:
    return UsageRecord(
        tenant_id="acme",
        agent_id=agent,
        provider=provider,
        model=model,
        route=None,
        outcome="ok",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        billable=True,
        metered=metered,
        ts="2026-09-08T10:00:00Z",
        source_type="call_record",
        source_key=f"acme-{model}-{agent}-{n}",
        cost_usd=cost_usd if metered else None,
        cost_source="rate_card" if metered else None,
    )


# --------------------------------------------------------------------------- #
# usage_details
# --------------------------------------------------------------------------- #
def test_usage_details_splits_input_output_and_total():
    assert usage_details(1200, 300) == {"input": 1200, "output": 300, "total": 1500}


def test_usage_details_clamps_nonsense_counts():
    assert usage_details(-5, "not-a-number") == {"input": 0, "output": 0, "total": 0}


def test_usage_details_for_empty_records_is_no_data_not_zero():
    """No records -> an EMPTY map; a zero map would read as a measured no-spend."""
    assert usage_details_for([]) == {}


def test_usage_details_for_sums_every_record():
    rows = [_record(STANDARD, 100, 10), _record(STANDARD, 50, 5, n=2)]
    assert usage_details_for(rows) == {"input": 150, "output": 15, "total": 165}


# --------------------------------------------------------------------------- #
# cost_details
# --------------------------------------------------------------------------- #
def test_cost_details_standard_tier_components():
    detail = _model().cost_details(PROVIDER, STANDARD, 1_000_000, 1_000_000)
    assert detail.priced
    assert detail.tier == TIER_STANDARD
    assert detail.input_usd == pytest.approx(3.0)
    assert detail.output_usd == pytest.approx(15.0)
    assert detail.total_usd == pytest.approx(18.0)
    assert detail.long_context_applied is False


def test_cost_details_long_context_tier_steps_both_rates_up():
    model = _model()
    under = model.cost_details(PROVIDER, LONG, 200_000, 0)
    over = model.cost_details(PROVIDER, LONG, 200_001, 0)
    # the threshold is strict: exactly at it is still the standard tier
    assert under.tier == TIER_STANDARD
    assert under.input_usd == pytest.approx(200_000 / 1_000_000 * 3.0)
    assert over.tier == TIER_LONG_CONTEXT
    assert over.long_context_applied is True
    assert over.input_usd == pytest.approx(200_001 / 1_000_000 * 6.0)


def test_cost_details_local_model_is_a_priced_zero():
    detail = _model().cost_details(PROVIDER, LOCAL, 10_000, 2_000)
    assert detail.priced is True
    assert detail.tier == TIER_LOCAL
    assert detail.total_usd == 0.0


def test_unknown_model_is_unknown_never_zero():
    detail = _model().cost_details(PROVIDER, UNKNOWN, 10_000, 2_000)
    assert detail.priced is False
    assert detail.tier is None
    assert detail.total_usd is None
    assert "unknown" in detail.reason
    payload = detail.to_dict()
    assert payload["costDetails"] is None  # no components to accidentally sum
    assert payload["usageDetails"]["total"] == 12_000  # usage is still measured


def test_unknown_provider_is_unknown_never_zero():
    detail = _model().cost_details("nobody", STANDARD, 1, 1)
    assert detail.priced is False
    assert detail.total_usd is None


# --------------------------------------------------------------------------- #
# breakdown
# --------------------------------------------------------------------------- #
def test_breakdown_splits_components_by_pricing_tier():
    rows = [
        _record(STANDARD, 1_000_000, 1_000_000, cost_usd=18.0),
        _record(LONG, 200_001, 0, cost_usd=(200_001 / 1_000_000 * 6.0)),
    ]
    result = breakdown_for(rows, _model())
    assert result.calls == 2
    assert result.cost_complete is True
    assert [row.tier for row in result.tiers] == [TIER_STANDARD, TIER_LONG_CONTEXT]
    assert result.tier(TIER_STANDARD).total_usd == pytest.approx(18.0)
    assert result.tier(TIER_LONG_CONTEXT).total_usd == pytest.approx(
        200_001 / 1_000_000 * 6.0
    )
    assert result.total_usd == pytest.approx(18.0 + 200_001 / 1_000_000 * 6.0)


def test_breakdown_prices_each_call_not_the_group_sum():
    """A group whose SUM exceeds the threshold keeps the standard tier.

    Pricing the summed token count would step every call up to the
    long-context rate — the breakdown must decide per call.
    """
    rows = [
        _record(LONG, 150_000, 0, cost_usd=0.45, n=1),
        _record(LONG, 150_000, 0, cost_usd=0.45, n=2),
    ]
    result = breakdown_for(rows, _model())
    assert result.tier(TIER_LONG_CONTEXT) is None
    assert result.tier(TIER_STANDARD).calls == 2
    assert result.total_usd == pytest.approx(300_000 / 1_000_000 * 3.0)


def test_breakdown_flags_unpriced_calls_instead_of_zeroing_them():
    rows = [
        _record(STANDARD, 1_000_000, 0, cost_usd=3.0),
        _record(UNKNOWN, 500_000, 0, cost_usd=None, metered=False),
    ]
    result = breakdown_for(rows, _model())
    assert result.cost_complete is False
    assert result.unpriced_calls == 1
    assert result.priced_calls == 1
    assert f"{PROVIDER}/{UNKNOWN}" in result.unpriced_models
    assert result.reconciles is False
    # the unpriced call's tokens are still counted as usage
    assert result.usage["input"] == 1_500_000
    assert result.to_dict()["costComplete"] is False


def test_breakdown_reconciles_the_recorded_cost_when_complete():
    rows = [_record(STANDARD, 1_000_000, 1_000_000, cost_usd=18.0)]
    result = breakdown_for(rows, _model())
    assert result.cost_complete is True
    assert result.recorded_cost_usd == pytest.approx(18.0)
    assert result.reconciles is True


def test_breakdown_of_no_records_reports_no_data():
    result = breakdown_for([], _model())
    assert result.calls == 0
    payload = result.to_dict()
    assert payload["usageDetails"] == {}
    assert payload["costDetails"] is None
    assert payload["costComplete"] is True  # nothing was left unpriced


def test_tier_for_reads_the_card_vocabulary():
    local = _entry(LOCAL, (0.0, 0.0), local=True)
    tiered = _entry(LONG, (1.0, 2.0), (2.0, 4.0), threshold=100)
    plain = _entry(STANDARD, (1.0, 2.0))
    assert tier_for(local, 10**9) == TIER_LOCAL
    assert tier_for(tiered, 100) == TIER_STANDARD
    assert tier_for(tiered, 101) == TIER_LONG_CONTEXT
    assert tier_for(plain, 10**9) == TIER_STANDARD
