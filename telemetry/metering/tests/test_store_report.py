"""Store + rollup/report tests (issue #33): durable idempotent aggregation.

The idempotency negative is here: replaying the same feed into the same
store (even across a process boundary / a reopened JSONL store) never
double-counts.  The unmetered negative is here too: a report's cost total
never includes an unmetered call as zero.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from telemetry.metering.intake import MeteringIntake
from telemetry.metering.report import UsageReporter
from telemetry.metering.store import JsonlUsageStore, MemoryUsageStore

from conftest import (
    T_OCT_01,
    T_SEP_07,
    T_SEP_08,
    call_record,
    metering_record,
    model_call_event,
)


def _seed(store, records):
    intake = MeteringIntake(store=store)
    for record in records:
        intake.ingest(record)
    return store


# --------------------------------------------------------------------------- #
# Store durability + idempotency negatives
# --------------------------------------------------------------------------- #
def test_memory_store_rejects_duplicate_append():
    store = MemoryUsageStore()
    intake = MeteringIntake(store=store)
    intake.ingest(model_call_event())
    intake.ingest(model_call_event())  # deduped at intake level
    assert store.count() == 1
    # a direct duplicate append is refused
    duplicate = intake.normalize(model_call_event())
    with pytest.raises(ValueError):
        store.append(duplicate)


def test_jsonl_store_replay_across_reopen_never_double_counts(tmp_path):
    path = Path(tmp_path) / "usage.jsonl"

    def feed_once():
        store = JsonlUsageStore(path)
        intake = MeteringIntake(store=store)
        summary = intake.ingest_many(
            [model_call_event(), call_record(), metering_record(outcome="provider")]
        )
        return store, summary

    store1, summary1 = feed_once()
    assert summary1.ingested == 3
    assert store1.count() == 3

    # Reopen (a fresh process / instance sharing the same store file).
    store2, summary2 = feed_once()
    assert summary2.ingested == 0
    assert summary2.duplicates == 3
    assert store2.count() == 3  # no double-counting on replay


def test_jsonl_store_persists_records_round_trip(tmp_path):
    path = Path(tmp_path) / "usage.jsonl"
    store = JsonlUsageStore(path)
    intake = MeteringIntake(store=store)
    intake.ingest(model_call_event())
    intake.ingest(call_record())

    reopened = JsonlUsageStore(path)
    records = reopened.read()
    assert len(records) == 2
    assert {r.source_type for r in records} == {"model_call_event", "call_record"}
    # raw lines carry the schemaVersion envelope
    text = path.read_text(encoding="utf-8")
    assert text.count("_schemaVersion") == 2


# --------------------------------------------------------------------------- #
# Rollups: daily + monthly, per tenant/agent/model/provider
# --------------------------------------------------------------------------- #
@pytest.fixture()
def seeded_store():
    store = MemoryUsageStore()
    intake = MeteringIntake(store=store)
    # acme, gemini-2.5-flash, day 07 and day 08 (same month)
    intake.ingest(model_call_event(tenant="acme", agent="coder-1",
                                   provider="gemini", model="gemini-2.5-flash",
                                   input_tokens=1000, output_tokens=500,
                                   ts=T_SEP_07))
    intake.ingest(model_call_event(tenant="acme", agent="coder-1",
                                   provider="gemini", model="gemini-2.5-flash",
                                   input_tokens=1000, output_tokens=500,
                                   ts=T_SEP_08))
    # acme, anthropic, day 08 (different model/provider)
    intake.ingest(model_call_event(tenant="acme", agent="arch-1",
                                   provider="anthropic", model="claude-haiku-4-5",
                                   input_tokens=2000, output_tokens=1000,
                                   ts=T_SEP_08))
    # globex, deepseek, day 08
    intake.ingest(call_record(tenant="globex", agent="arch-1",
                              provider="deepseek", model="deepseek-reasoner",
                              estimate=0.0042, ts=T_SEP_08))
    # globex cache hit day 08 (zero cost, counted as a call)
    intake.ingest(metering_record(tenant="globex", agent="coder-2",
                                  outcome="cache_hit", cached=True,
                                  zero_cost=True, ts=T_SEP_08))
    # acme, october (a different month bucket)
    intake.ingest(model_call_event(tenant="acme", agent="coder-1",
                                   provider="gemini", model="gemini-2.5-flash",
                                   input_tokens=100, output_tokens=50,
                                   ts=T_OCT_01))
    return store


def test_daily_rollup_separates_days(seeded_store):
    reporter = UsageReporter(seeded_store)
    daily = reporter.tenant_daily("acme")
    assert set(daily) == {"2026-09-07", "2026-09-08", "2026-10-01"}
    assert daily["2026-09-07"].calls == 1
    assert daily["2026-09-08"].calls == 2
    assert daily["2026-10-01"].calls == 1


def test_monthly_rollup_rolls_days_into_months(seeded_store):
    reporter = UsageReporter(seeded_store)
    monthly = reporter.tenant_monthly("acme")
    assert set(monthly) == {"2026-09", "2026-10"}
    assert monthly["2026-09"].calls == 3
    assert monthly["2026-10"].calls == 1


def test_rollup_window_bounds(seeded_store):
    reporter = UsageReporter(seeded_store)
    bounded = reporter.tenant_daily("acme", start="2026-09-08", end="2026-09-30")
    assert set(bounded) == {"2026-09-08"}


def test_per_agent_attribution(seeded_store):
    reporter = UsageReporter(seeded_store)
    by_agent = reporter.by_agent("acme")
    # two agents for acme: coder-1 (3 calls) and arch-1 (1 call)
    coder = by_agent["acme::coder-1"]
    arch = by_agent["acme::arch-1"]
    assert coder.calls == 3
    assert arch.calls == 1


def test_provider_mix_shares(seeded_store):
    reporter = UsageReporter(seeded_store)
    mix = reporter.provider_mix()
    # acme gemini 3 calls + globex deepseek 1 call + globex cache hit + ...
    assert "gemini" in mix
    assert "anthropic" in mix
    assert "deepseek" in mix
    assert mix["deepseek"]["calls"] == 1
    # shares sum to ~100 across providers with calls
    total_share = sum(v["callSharePct"] for v in mix.values())
    assert total_share == pytest.approx(100.0, abs=0.01)


def test_totals_include_cache_hits_as_zero_cost_calls(seeded_store):
    reporter = UsageReporter(seeded_store)
    totals = reporter.totals("globex")
    assert totals.calls == 2  # 1 real deepseek call + 1 cache hit
    assert totals.cache_hits == 1
    # deepseek estimate 0.0042 + cache 0
    assert totals.cost_usd == pytest.approx(0.0042)


# --------------------------------------------------------------------------- #
# UNMETERED NEGATIVE: cost totals never silently include unmetered as zero
# --------------------------------------------------------------------------- #
def test_unmetered_cost_never_folded_into_zero():
    store = MemoryUsageStore()
    intake = MeteringIntake(store=store)
    intake.ingest(model_call_event(provider="gemini", model="gemini-2.5-flash",
                                   input_tokens=1000, output_tokens=500,
                                   ts=T_SEP_08))                       # metered
    intake.ingest(model_call_event(provider="futureco", model="future-model-x",
                                   input_tokens=900, output_tokens=300,
                                   ts=T_SEP_08))                       # unmetered
    reporter = UsageReporter(store)
    totals = reporter.totals()
    assert totals.calls == 2            # both billable calls counted
    assert totals.unmetered_calls == 1  # the unknown model surfaced
    # cost includes ONLY the metered call — never the unmetered one as $0.
    assert totals.cost_usd == pytest.approx(0.00155)
    summary = reporter.billing_summary()
    assert summary["unmeteredCalls"] == 1
    assert summary["costUsd"] == pytest.approx(0.00155)


def test_unmetered_only_store_has_zero_but_explicit_unmetered_signal():
    store = MemoryUsageStore()
    intake = MeteringIntake(store=store)
    intake.ingest(model_call_event(provider="futureco", model="future-model-x",
                                   input_tokens=10, output_tokens=5))
    reporter = UsageReporter(store)
    totals = reporter.totals()
    assert totals.unmetered_calls == 1
    assert totals.calls == 1
    # the $0.00 here is an artifact of having no metered records at all, and
    # the unmetered signal is explicit — the record itself was never $0.
    assert totals.cost_usd == 0.0
    assert reporter.billing_summary()["unmeteredCalls"] == 1
    record = store.read()[0]
    assert record.metered is False
    assert record.cost_usd is None
