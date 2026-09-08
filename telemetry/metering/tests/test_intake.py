"""Intake tests (issue #33): normalization, cost resolution, fail-closed.

The fail-closed doctrine is negative-tested here: an unmetered call is
flagged with ``metered=False`` and ``cost_usd=None`` and is NEVER priced as
zero; the only metered zero-cost path is an explicit cache-hit record.
"""

from __future__ import annotations

import pytest

from telemetry.metering.intake import MeteringIntake
from telemetry.metering.ratecards import RateCardStore

from conftest import (
    call_record,
    gateway_record,
    metering_record,
    model_call_event,
)


@pytest.fixture()
def intake() -> MeteringIntake:
    return MeteringIntake(rate_store=RateCardStore.load_dir())


# --------------------------------------------------------------------------- #
# Source-vocabulary detection + normalization
# --------------------------------------------------------------------------- #
def test_detects_each_source_vocabulary(intake):
    assert intake.normalize(model_call_event()).source_type == "model_call_event"
    assert intake.normalize(call_record()).source_type == "call_record"
    assert intake.normalize(metering_record()).source_type == "metering_record"
    assert intake.normalize(gateway_record()).source_type == "gateway_record"


def test_unknown_vocabulary_rejected(intake):
    with pytest.raises(ValueError):
        intake.normalize({"tenantId": "acme", "foo": 1})


def test_missing_tenant_rejected(intake):
    rec = model_call_event(tenant="acme")
    rec["tenant_id"] = ""
    with pytest.raises(ValueError):
        intake.normalize(rec)


# --------------------------------------------------------------------------- #
# Live calls priced from the rate card
# --------------------------------------------------------------------------- #
def test_model_call_event_priced_from_rate_card(intake):
    record = intake.normalize(
        model_call_event(provider="gemini", model="gemini-2.5-flash",
                         input_tokens=1000, output_tokens=500)
    )
    assert record.billable is True
    assert record.metered is True
    assert record.cost_source == "rate_card"
    assert record.cost_usd == pytest.approx(0.00155)
    assert record.input_tokens == 1000
    assert record.output_tokens == 500


def test_gateway_record_priced_from_rate_card(intake):
    record = intake.normalize(
        gateway_record(provider="anthropic", model="claude-sonnet-4-5",
                       input_tokens=800, output_tokens=200)
    )
    assert record.metered is True
    assert record.cost_source == "rate_card"
    # 800/1e6*3.00 + 200/1e6*15.00 = 0.0024 + 0.003
    assert record.cost_usd == pytest.approx(0.0054)


# --------------------------------------------------------------------------- #
# FAIL-CLOSED NEGATIVES: unknown provider/model never silently zero
# --------------------------------------------------------------------------- #
def test_unmetered_call_is_flagged_and_cost_is_none(intake):
    record = intake.normalize(
        model_call_event(provider="futureco", model="future-model-x",
                         input_tokens=500, output_tokens=100)
    )
    assert record.metered is False
    assert record.cost_usd is None
    assert record.unmetered_reason is not None
    # The tokens are real and counted, but the call is NOT a zero-cost call.
    assert record.total_tokens == 600


def test_unmetered_never_assigned_zero(intake):
    """No code path turns an unmetered call into cost 0.0."""
    record = intake.normalize(
        model_call_event(provider="futureco", model="future-model-x",
                         input_tokens=1, output_tokens=1)
    )
    assert record.cost_usd is None
    assert not (record.cost_usd == 0.0)
    assert record.cost_source is None


def test_unmetered_known_provider_unknown_model(intake):
    record = intake.normalize(
        model_call_event(provider="deepseek", model="deepseek-v0-unknown",
                         input_tokens=10, output_tokens=10)
    )
    assert record.metered is False
    assert record.unmetered_reason is not None


def test_zero_tokens_on_unknown_model_still_unmetered(intake):
    """Even a 0-token call on an unknown model is unmetered, not $0."""
    record = intake.normalize(
        model_call_event(provider="futureco", model="future-model-x",
                         input_tokens=0, output_tokens=0)
    )
    assert record.metered is False
    assert record.cost_usd is None


# --------------------------------------------------------------------------- #
# Explicit zero-cost: the ONLY allowed paths
# --------------------------------------------------------------------------- #
def test_cache_hit_metering_record_is_explicit_zero(intake):
    record = intake.normalize(
        metering_record(outcome="cache_hit", cached=True, zero_cost=True)
    )
    assert record.cache_hit is True
    assert record.billable is True
    assert record.metered is True
    assert record.cost_usd == 0.0
    assert record.cost_source == "cache_hit"


def test_gateway_cache_hit_outcome_is_explicit_zero(intake):
    record = intake.normalize(gateway_record(outcome="cache_hit"))
    assert record.cache_hit is True
    assert record.metered is True
    assert record.cost_usd == 0.0
    assert record.cost_source == "cache_hit"


def test_local_ollama_model_is_priced_zero_from_card(intake):
    """A local (self-hosted) model is a priced $0, not an unknown one."""
    record = intake.normalize(
        model_call_event(provider="ollama", model="llama3.2",
                         input_tokens=5000, output_tokens=1000)
    )
    assert record.metered is True
    assert record.cost_source == "rate_card"
    assert record.cost_usd == 0.0


# --------------------------------------------------------------------------- #
# Non-billable events (guard/policy decisions never bill)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "builder",
    [
        lambda: metering_record(outcome="budget_exceeded"),
        lambda: metering_record(outcome="rate_limited"),
        lambda: metering_record(outcome="queued"),
        lambda: metering_record(outcome="degraded"),
        lambda: gateway_record(outcome="blocked"),
        lambda: gateway_record(outcome="denied"),
    ],
)
def test_non_call_outcomes_are_non_billable(intake, builder):
    record = intake.normalize(builder())
    assert record.billable is False
    assert record.metered is True
    assert record.cost_usd is None
    assert record.unmetered_reason is None


def test_finops_stop_action_is_non_billable(intake):
    record = intake.normalize(call_record(action="stop", estimate=0.0))
    assert record.billable is False
    assert record.cost_usd is None


def test_model_call_failure_without_usage_is_non_billable(intake):
    event = model_call_event(status="circuit_open", include_usage=False)
    record = intake.normalize(event)
    assert record.billable is False
    assert record.cost_usd is None


# --------------------------------------------------------------------------- #
# Attached estimates (gateway finops CallRecord / observability)
# --------------------------------------------------------------------------- #
def test_call_record_positive_estimate_attributed(intake):
    record = intake.normalize(
        call_record(provider="deepseek", model="deepseek-reasoner",
                    estimate=0.0042)
    )
    assert record.billable is True
    assert record.metered is True
    assert record.cost_source == "attached"
    assert record.cost_usd == pytest.approx(0.0042)


def test_call_record_zero_estimate_fails_closed(intake):
    """A CallRecord with a $0 estimate is a source defect — never $0 silent."""
    record = intake.normalize(
        call_record(provider="deepseek", model="deepseek-reasoner", estimate=0.0)
    )
    assert record.metered is False
    assert record.cost_usd is None
    assert record.unmetered_reason is not None


def test_call_record_known_local_model_zero_is_ok(intake):
    """A routed local model priced $0 by FinOps is an honest $0."""
    record = intake.normalize(
        call_record(provider="ollama", model="llama3.2", estimate=0.0)
    )
    assert record.metered is True
    assert record.cost_usd == 0.0


def test_gateway_record_unknown_model_positive_attached_estimate(intake):
    record = intake.normalize(
        gateway_record(provider="futureco", model="future-model-x",
                       estimated_cost_usd=0.0033, outcome="success")
    )
    assert record.metered is True
    assert record.cost_source == "attached"
    assert record.cost_usd == pytest.approx(0.0033)


def test_gateway_record_known_model_tokens_beat_attached_estimate(intake):
    """When the model is on the card and tokens exist, the card is authoritative."""
    record = intake.normalize(
        gateway_record(provider="anthropic", model="claude-sonnet-4-5",
                       input_tokens=800, output_tokens=200,
                       estimated_cost_usd=0.99)
    )
    assert record.metered is True
    assert record.cost_source == "rate_card"
    assert record.cost_usd == pytest.approx(0.0054)


# --------------------------------------------------------------------------- #
# Limits MeteringRecord: token usage counted, cost unmetered (no model)
# --------------------------------------------------------------------------- #
def test_metering_provider_outcome_counts_tokens_but_cannot_price(intake):
    """A limits MeteringRecord has no provider/model, so its cost is unmetered.

    Its tokens are still counted (usage rollups) but never silently priced.
    """
    record = intake.normalize(
        metering_record(outcome="provider", input_tokens=200, output_tokens=50)
    )
    assert record.billable is True
    assert record.metered is False
    assert record.cost_usd is None
    assert record.total_tokens == 250


# --------------------------------------------------------------------------- #
# Idempotent ingest (dedup by source key) + ingest_many tallies
# --------------------------------------------------------------------------- #
def test_ingest_dedups_replay_by_source_key():
    store, intake = _fresh()
    outcome1 = intake.ingest(model_call_event())
    outcome2 = intake.ingest(model_call_event())  # identical replay
    assert outcome1.duplicate is False
    assert outcome2.duplicate is True
    assert store.count() == 1


def test_ingest_many_tallies(memory_store):
    store, intake = memory_store
    summary = intake.ingest_many(
        [
            model_call_event(),                      # metered, priced
            model_call_event(provider="futureco", model="future-model-x"),  # unmetered
            metering_record(outcome="cache_hit", cached=True, zero_cost=True),  # cache
            gateway_record(outcome="blocked"),       # non-billable
            model_call_event(),                      # replay duplicate of #1
        ]
    )
    assert summary.total == 5
    assert summary.ingested == 4
    assert summary.duplicates == 1
    assert summary.unmetered == 1
    assert summary.cache_hits == 1
    assert summary.non_billable == 1
    assert store.count() == 4


def _fresh():
    from telemetry.metering.store import MemoryUsageStore

    store = MemoryUsageStore()
    return store, MeteringIntake(store=store)
