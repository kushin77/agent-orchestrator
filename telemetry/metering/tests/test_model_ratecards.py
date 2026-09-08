"""Model + rate-card tests (issue #33): buckets, serialization, unknown->None."""

from __future__ import annotations

import json

import pytest

from telemetry.metering.model import (
    SCHEMA_VERSION,
    UsageRecord,
    day_bucket,
    month_bucket,
    now_utc_iso,
    parse_ts,
)
from telemetry.metering.ratecards import (
    RateCardError,
    RateCardStore,
    load_card_file,
)


def test_now_utc_iso_shape():
    assert now_utc_iso().endswith("Z")
    assert len(now_utc_iso()) == 20


def test_parse_ts_normalizes_epoch_and_offsets():
    assert parse_ts(1_700_000_000.0) == "2023-11-14T22:13:20Z"
    assert parse_ts("2026-09-08T10:00:00Z") == "2026-09-08T10:00:00Z"
    assert parse_ts("2026-09-08T12:00:00+02:00") == "2026-09-08T10:00:00Z"
    assert parse_ts(None).endswith("Z")
    with pytest.raises(ValueError):
        parse_ts("not-a-timestamp")


def test_day_and_month_buckets():
    ts = "2026-09-08T18:30:00Z"
    assert day_bucket(ts) == "2026-09-08"
    assert month_bucket(ts) == "2026-09"


def test_usage_record_round_trip():
    record = UsageRecord(
        tenant_id="acme",
        agent_id="coder-1",
        provider="gemini",
        model="gemini-2.5-flash",
        route="MED",
        outcome="success",
        input_tokens=1000,
        output_tokens=500,
        billable=True,
        metered=True,
        ts="2026-09-08T10:00:00Z",
        source_type="model_call_event",
        source_key="k1",
        cost_usd=0.00155,
        cost_source="rate_card",
    )
    payload = record.to_dict()
    assert payload["schemaVersion"] == SCHEMA_VERSION
    assert payload["kind"] == "usage"
    assert payload["totalTokens"] == 1500
    rebuilt = UsageRecord.from_dict(payload)
    assert rebuilt == record


def test_usage_record_unmetered_serializes_null_cost():
    record = UsageRecord(
        tenant_id="acme",
        agent_id=None,
        provider="futureco",
        model="future-model-x",
        route=None,
        outcome="success",
        input_tokens=100,
        output_tokens=50,
        billable=True,
        metered=False,
        ts="2026-09-08T10:00:00Z",
        source_type="model_call_event",
        source_key="k2",
        unmetered_reason="no rate card",
    )
    payload = record.to_dict()
    assert payload["costUsd"] is None
    assert payload["metered"] is False
    assert payload["unmeteredReason"] == "no rate card"


def test_load_all_shipped_cards():
    store = RateCardStore.load_dir()
    assert store.providers() == ["anthropic", "deepseek", "gemini", "ollama", "openai"]
    assert store.lookup("gemini", "gemini-2.5-flash") is not None
    assert store.fingerprint()


def test_estimate_known_model_standard_tier():
    store = RateCardStore.load_dir()
    estimate = store.estimate("gemini", "gemini-2.5-flash", 1000, 500)
    assert estimate is not None
    # 1000/1e6 * 0.30 + 500/1e6 * 2.50 = 0.0003 + 0.00125
    assert estimate.cost_usd == pytest.approx(0.00155)
    assert estimate.long_context_applied is False


def test_estimate_long_context_tier_steps_up():
    store = RateCardStore.load_dir()
    standard = store.estimate("gemini", "gemini-2.5-pro", 100_000, 5_000)
    long_ctx = store.estimate("gemini", "gemini-2.5-pro", 250_000, 5_000)
    assert standard is not None and long_ctx is not None
    assert standard.long_context_applied is False
    assert long_ctx.long_context_applied is True
    # Long-context prices both input and output rates up together.
    assert long_ctx.cost_usd > standard.cost_usd
    # 250000/1e6*2.50 + 5000/1e6*15.00 = 0.625 + 0.075
    assert long_ctx.cost_usd == pytest.approx(0.70)


def test_estimate_unknown_model_returns_none_never_zero():
    store = RateCardStore.load_dir()
    assert store.estimate("gemini", "gemini-2.5-flash-ultra", 1000, 500) is None
    assert store.estimate("futureco", "future-model-x", 1000, 500) is None
    assert store.estimate("deepseek", "unknown-model", 0, 0) is None


def test_local_ollama_is_explicit_zero_not_unknown():
    store = RateCardStore.load_dir()
    entry = store.lookup("ollama", "llama3.2")
    assert entry is not None
    assert entry.local is True
    estimate = store.estimate("ollama", "llama3.2", 10_000, 2_000)
    assert estimate is not None
    assert estimate.cost_usd == 0.0


def test_card_schema_rejects_negative_prices(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "schemaVersion: 1\n"
        "provider: bad\n"
        "models:\n"
        "  m:\n"
        "    standard:\n"
        "      inputUsdPerMillion: -1.0\n"
        "      outputUsdPerMillion: 1.0\n",
        encoding="utf-8",
    )
    with pytest.raises(RateCardError):
        load_card_file(bad)


def test_card_schema_rejects_bad_long_context(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "schemaVersion: 1\n"
        "provider: bad\n"
        "models:\n"
        "  m:\n"
        "    standard:\n"
        "      inputUsdPerMillion: 1.0\n"
        "      outputUsdPerMillion: 2.0\n"
        "    longContext:\n"
        "      inputUsdPerMillion: 3.0\n"
        "      outputUsdPerMillion: 4.0\n",
        encoding="utf-8",
    )
    with pytest.raises(RateCardError):
        load_card_file(bad)


def test_card_schema_rejects_non_numeric_prices(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "schemaVersion: 1\n"
        "provider: bad\n"
        "models:\n"
        "  m:\n"
        "    standard:\n"
        "      inputUsdPerMillion: expensive\n"
        "      outputUsdPerMillion: 1.0\n",
        encoding="utf-8",
    )
    with pytest.raises(RateCardError):
        load_card_file(bad)


def test_shipped_cards_serialize_as_valid_json():
    store = RateCardStore.load_dir()
    for provider in store.providers():
        card = store.card(provider)
        assert card is not None
        # every model has a standard rate and non-negative prices
        for entry in card.entries.values():
            assert entry.standard.input_usd_per_million >= 0
            assert entry.standard.output_usd_per_million >= 0
        json.dumps(card.to_dict())  # must not raise
