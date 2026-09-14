"""Cache-efficiency audit regression tests (issue #673 / PF-8).

A synthetic usage record with known hits/misses must produce the exact
expected table — the table is asserted (not just the exit code) so any drift
in the rendering or the aggregation is caught. The honest-join path is pinned
too: with no billing-query ids in the input the script reports
``no billing-query ids present`` and still prints the aggregate row.
"""

from __future__ import annotations

import json

import cache_audit
from cache_audit import (
    NO_BILLING_QUERY,
    audit,
    load_usage,
    parse_record,
    render,
)


def _write(path, records) -> None:
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )


def test_parse_record_canonical_snake_case() -> None:
    row = parse_record(
        {
            "prompt_cache_hit_tokens": 2000,
            "prompt_cache_miss_tokens": 200,
            "billing_query_id": "bq-1234",
            "model": "deepseek-chat",
        }
    )
    assert row.hit_tokens == 2000
    assert row.miss_tokens == 200
    assert row.billing_query_id == "bq-1234"
    assert row.prompt_tokens == 2200
    assert row.hit_ratio == 2000 / 2200


def test_parse_record_camelcase_alias_and_missing() -> None:
    row = parse_record(
        {"promptCacheHitTokens": 10, "promptCacheMissTokens": 40}
    )
    assert row.hit_tokens == 10
    assert row.miss_tokens == 40
    assert row.billing_query_id is None  # no billing-query id carried


def test_table_pinned_with_billing_query(tmp_path) -> None:
    usage = tmp_path / "usage.jsonl"
    _write(
        usage,
        [
            {
                "provider": "deepseek",
                "model": "deepseek-chat",
                "prompt_cache_hit_tokens": 2000,
                "prompt_cache_miss_tokens": 200,
                "billing_query_id": "bq-1234",
            },
            {
                "provider": "deepseek",
                "model": "deepseek-chat",
                "prompt_cache_hit_tokens": 500,
                "prompt_cache_miss_tokens": 300,
                "billing_query_id": "bq-5678",
            },
            {
                "provider": "deepseek",
                "model": "deepseek-reasoner",
                "prompt_cache_hit_tokens": 1200,
                "prompt_cache_miss_tokens": 300,
                "billing_query_id": "bq-1234",
            },
        ],
    )
    rows = load_usage(usage)
    assert len(rows) == 3
    result = audit(rows)
    assert result.has_billing_query is True
    assert result.total.calls == 3
    assert result.total.hit_tokens == 3700
    assert result.total.miss_tokens == 800

    expected = (
        "join: billing-query ids present\n"
        "\n"
        "billing_query_id            calls  hit_tokens  miss_tokens  cache_hit_ratio\n"
        "bq-1234                       2        3200          500           0.8649\n"
        "bq-5678                       1         500          300           0.6250\n"
        "TOTAL                         3        3700          800           0.8222\n"
        "\n"
        "aggregate cache-hit ratio: 0.8222"
    )
    assert render(result) == expected


def test_honest_join_reports_no_billing_query(tmp_path) -> None:
    usage = tmp_path / "usage.jsonl"
    _write(
        usage,
        [
            {
                "provider": "deepseek",
                "model": "deepseek-chat",
                "prompt_cache_hit_tokens": 2000,
                "prompt_cache_miss_tokens": 200,
            },
            {
                "provider": "deepseek",
                "model": "deepseek-chat",
                "prompt_cache_hit_tokens": 500,
                "prompt_cache_miss_tokens": 300,
            },
        ],
    )
    result = audit(load_usage(usage))
    assert result.has_billing_query is False
    assert result.per_query == {}
    out = render(result)
    # The join is reported honestly, never fabricated.
    assert f"join: {NO_BILLING_QUERY}" in out
    # The aggregate row is still present so the baseline stays regressable.
    assert "TOTAL" in out
    assert "aggregate cache-hit ratio: 0.8333" in out


def test_empty_input_still_renders_total(tmp_path) -> None:
    usage = tmp_path / "usage.jsonl"
    usage.write_text("", encoding="utf-8")
    result = audit(load_usage(usage))
    assert result.total.calls == 0
    assert result.total.hit_ratio == 0.0
    out = render(result)
    assert f"join: {NO_BILLING_QUERY}" in out
    assert "aggregate cache-hit ratio: 0.0000" in out
