"""telemetry/metering/feed.py — live usage feed tests (issue #886).

Covers: a fresh ledger fed through intake produces schema-valid per-tenant
rows; unmetered calls count tokens but never fabricate a cost; a second
tenant's records never bleed into the first tenant's row (no cross-tenant
leak); the feed re-reads the ledger live (a record appended after the feed
object is constructed still shows up); and the negative control -- a
malformed row is refused BY NAME (FeedValidationError), never silently
coerced or served.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from telemetry.metering.feed import FeedValidationError, LiveUsageFeed, validate_row
from telemetry.metering.intake import MeteringIntake
from telemetry.metering.store import JsonlUsageStore, MemoryUsageStore

from conftest import T_SEP_08, T_SEP_08_LATE, metering_record, model_call_event


def _seed(store, records):
    intake = MeteringIntake(store=store)
    for record in records:
        intake.ingest(record)


def test_feed_serves_schema_valid_rows_for_a_billed_tenant():
    store = MemoryUsageStore()
    _seed(
        store,
        [
            model_call_event(
                tenant="acme",
                provider="anthropic",
                model="claude-sonnet-5",
                input_tokens=1000,
                output_tokens=500,
            )
        ],
    )
    feed = LiveUsageFeed(store, as_of=T_SEP_08)
    rows = feed.rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["tenantId"] == "acme"
    assert row["calls"] == 1
    assert row["billableCalls"] == 1
    assert row["unmeteredCalls"] == 0
    assert row["inputTokens"] == 1000
    assert row["outputTokens"] == 500
    assert row["costUsd"] > 0
    assert row["asOf"] == T_SEP_08
    # Every served row must itself pass the same validator (no false green).
    validate_row(row)


def test_unmetered_call_counts_tokens_but_never_fabricates_cost():
    store = MemoryUsageStore()
    _seed(
        store,
        [
            model_call_event(
                tenant="acme",
                provider="unknown-provider",
                model="unknown-model",
                input_tokens=200,
                output_tokens=100,
            )
        ],
    )
    feed = LiveUsageFeed(store, as_of=T_SEP_08)
    rows = feed.rows()
    row = rows[0]
    assert row["calls"] == 1
    assert row["unmeteredCalls"] == 1
    assert row["billableCalls"] == 0
    assert row["inputTokens"] == 200
    assert row["outputTokens"] == 100
    assert row["costUsd"] == 0.0


def test_tenants_do_not_leak_into_each_others_rows():
    store = MemoryUsageStore()
    _seed(
        store,
        [
            model_call_event(tenant="acme", input_tokens=100, output_tokens=10),
            model_call_event(tenant="globex", input_tokens=50, output_tokens=5),
        ],
    )
    feed = LiveUsageFeed(store, as_of=T_SEP_08)
    rows = {row["tenantId"]: row for row in feed.rows()}
    assert set(rows) == {"acme", "globex"}
    assert rows["acme"]["inputTokens"] == 100
    assert rows["globex"]["inputTokens"] == 50


def test_a_non_billable_outcome_is_excluded_from_every_row():
    store = MemoryUsageStore()
    _seed(
        store,
        [metering_record(tenant="acme", outcome="blocked", input_tokens=999)],
    )
    feed = LiveUsageFeed(store, as_of=T_SEP_08)
    assert feed.rows() == []


def test_feed_is_live_a_record_appended_after_construction_still_shows(tmp_path: Path):
    ledger = tmp_path / "metering.jsonl"
    store = JsonlUsageStore(ledger)
    feed = LiveUsageFeed(store, as_of=T_SEP_08)
    assert feed.rows() == []

    _seed(store, [model_call_event(tenant="acme", input_tokens=10, output_tokens=5)])
    rows = feed.rows()
    assert len(rows) == 1
    assert rows[0]["tenantId"] == "acme"

    # A second feed object opened fresh against the same ledger file (the
    # from_path constructor, the portal's actual entry point) sees it too --
    # the feed is genuinely reading the ledger, not a private in-process cache.
    reopened = LiveUsageFeed.from_path(ledger, as_of=T_SEP_08_LATE)
    reopened_rows = reopened.rows()
    assert len(reopened_rows) == 1
    assert reopened_rows[0]["asOf"] == T_SEP_08_LATE


# --------------------------------------------------------------------------- #
# Negative control: a malformed row is refused BY NAME, never served.
# --------------------------------------------------------------------------- #
def test_validate_row_refuses_a_missing_required_field():
    bad_row = {
        "schemaVersion": 1,
        "tenantId": "acme",
        "calls": 1,
        "billableCalls": 1,
        "unmeteredCalls": 0,
        "inputTokens": 10,
        "outputTokens": 5,
        # costUsd is missing
        "asOf": T_SEP_08,
    }
    with pytest.raises(FeedValidationError, match="missing required field"):
        validate_row(bad_row)


def test_validate_row_refuses_a_non_numeric_cost():
    bad_row = {
        "schemaVersion": 1,
        "tenantId": "acme",
        "calls": 1,
        "billableCalls": 1,
        "unmeteredCalls": 0,
        "inputTokens": 10,
        "outputTokens": 5,
        "costUsd": "not-a-number",
        "asOf": T_SEP_08,
    }
    with pytest.raises(FeedValidationError, match="does not match declared type"):
        validate_row(bad_row)


def test_validate_row_refuses_a_negative_cost():
    bad_row = {
        "schemaVersion": 1,
        "tenantId": "acme",
        "calls": 1,
        "billableCalls": 1,
        "unmeteredCalls": 0,
        "inputTokens": 10,
        "outputTokens": 5,
        "costUsd": -1.0,
        "asOf": T_SEP_08,
    }
    with pytest.raises(FeedValidationError, match="below its declared minimum"):
        validate_row(bad_row)


def test_validate_row_refuses_a_bool_masquerading_as_a_numeric_cost():
    # bool is an int subclass in Python; the validator must not let a stray
    # True/False slip through a naive isinstance(x, (int, float)) check.
    bad_row = {
        "schemaVersion": 1,
        "tenantId": "acme",
        "calls": 1,
        "billableCalls": 1,
        "unmeteredCalls": 0,
        "inputTokens": 10,
        "outputTokens": 5,
        "costUsd": True,
        "asOf": T_SEP_08,
    }
    with pytest.raises(FeedValidationError, match="does not match declared type"):
        validate_row(bad_row)
