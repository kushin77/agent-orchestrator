#!/usr/bin/env python3
"""DeepSeek token-flow baseline — per-call-class prompt-cache hit/miss/cost table.

Offline, stdlib + PyYAML only (no live API, no network). Run from the repo root:

    python3 gateway/finops/cache_baseline.py --help
    python3 gateway/finops/cache_baseline.py                  # bundled synthetic sample
    python3 gateway/finops/cache_baseline.py --store /tmp/usage.jsonl
    python3 gateway/finops/cache_baseline.py --format json

Purpose (issue #667): a baseline for DeepSeek's automatic prefix caching.
DeepSeek bills prompt tokens in two buckets — cache-hit tokens (the shared
prefix served from its prefix cache, at a discounted input rate) and
cache-miss tokens (new input, at the standard input rate) — plus output
tokens. This script aggregates usage per call class and prints, per class:
requests, prompt tokens, hit tokens, miss tokens, cache-hit ratio, and cost.

Honest-data contract (verified against this checkout, not assumed):

  * The repo's recorded usage shape (gateway/providers ``Usage``,
    telemetry/metering ``UsageRecord``, gateway/finops ``CallRecord``) tracks
    only ``input_tokens`` and ``output_tokens``. No
    ``prompt_cache_hit_tokens`` / ``prompt_cache_miss_tokens`` (or any
    cache-token) field exists anywhere in-tree, so a real-data join can price
    prompt + output from the rate cards but CANNOT split prompt into hit vs
    miss tokens.
  * Therefore: the default run uses a bundled synthetic sample (labeled
    SYNTHETIC) so the hit/miss/ratio/cache-cost columns have illustrative
    numbers; a ``--store`` run over real recorded usage prints MEASURED
    prompt/output tokens and standard cost, and CANNOT-ASSESS for hit tokens,
    miss tokens, cache-hit ratio, and cache-adjusted cost.
  * Standard input/output rates come from the repo's rate cards
    (telemetry/metering/rate_cards/deepseek.yaml). The cache-hit input
    discount is a point-in-time DeepSeek list price declared as a baseline
    constant below — it is NOT in the repo's rate card and must be re-verified
    against DeepSeek current pricing before any billing use.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

_HERE = os.path.dirname(os.path.abspath(__file__))  # gateway/finops
_GATEWAY_ROOT = os.path.dirname(_HERE)  # gateway/
_REPO_ROOT = os.path.dirname(_GATEWAY_ROOT)  # repo root
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from telemetry.metering.ratecards import (  # noqa: E402
    DEFAULT_RATE_CARD_DIR,
    RateCardStore,
)

#: Provider this baseline prices (issue #667 scope: DeepSeek token flow).
PROVIDER = "deepseek"

#: Point-in-time DeepSeek prompt-cache *input* list prices (USD / 1M tokens),
#: keyed by model id. NOT in the repo's rate cards (deepseek.yaml declares only
#: standard input/output) — a baseline assumption to re-verify against
#: https://api-docs.deepseek.com/quick_start/pricing before any billing use.
CACHE_HIT_INPUT_USD_PER_MILLION: Dict[str, float] = {
    "deepseek-chat": 0.07,
    "deepseek-reasoner": 0.14,
}

#: Candidate field names that would carry a prompt-cache hit/miss token split in
#: a recorded usage payload. None of these exist in-tree today (verified).
CACHE_HIT_FIELDS: Sequence[str] = (
    "prompt_cache_hit_tokens",
    "cache_hit_tokens",
    "cache_read_input_tokens",
)
CACHE_MISS_FIELDS: Sequence[str] = (
    "prompt_cache_miss_tokens",
    "cache_miss_tokens",
    "cache_creation_input_tokens",
)

#: Bundled synthetic sample: (call_class, model, requests, prompt_tokens,
#: cache_hit_tokens, output_tokens). Illustrative token flows per call class —
#: mechanical/classification classes reuse a long stable prefix (high hit),
#: research/architecture reuse little (low hit). ``miss_tokens = prompt - hit``.
#: Used only because no real recorded DeepSeek cache-token flow exists in-tree.
SYNTHETIC_SAMPLE: Sequence[tuple] = (
    ("classify-route", "deepseek-chat", 1200, 180_000, 126_000, 48_000),
    ("memory-ops", "deepseek-chat", 800, 64_000, 51_200, 16_000),
    ("docs-authoring", "deepseek-chat", 600, 240_000, 120_000, 180_000),
    ("code-author", "deepseek-chat", 400, 320_000, 128_000, 160_000),
    ("test-run", "deepseek-chat", 300, 90_000, 45_000, 60_000),
    ("code-review", "deepseek-chat", 200, 160_000, 48_000, 80_000),
    ("research", "deepseek-reasoner", 100, 50_000, 10_000, 30_000),
    ("architecture-decision", "deepseek-reasoner", 40, 24_000, 2_400, 16_000),
)

CANNOT_ASSESS = "CANNOT-ASSESS"
SOURCE_SYNTHETIC = "SYNTHETIC"
SOURCE_MEASURED = "MEASURED"


@dataclass
class ClassRow:
    """Aggregated per-call-class totals with an honesty flag for the cache split."""

    call_class: str
    source: str
    model: str
    requests: int
    prompt_tokens: int
    output_tokens: int
    hit_tokens: Optional[int]  # None => CANNOT-ASSESS
    miss_tokens: Optional[int]  # None => CANNOT-ASSESS
    cache_assessable: bool

    @property
    def cache_hit_ratio(self) -> Optional[float]:
        """hit/prompt ratio, or None when the split is CANNOT-ASSESS."""
        if self.hit_tokens is None or self.prompt_tokens == 0:
            return None
        return self.hit_tokens / self.prompt_tokens

    def cost(self, cards: RateCardStore) -> tuple[Optional[float], Optional[float]]:
        """(standard_cost, cache_adjusted_cost) in USD; None when unpriced.

        Standard cost is purely from the rate cards (input + output). Cache-
        adjusted cost prices hit tokens at the baseline cache-hit input rate and
        miss tokens at the card's standard input rate.
        """
        entry = cards.lookup(PROVIDER, self.model)
        if entry is None:
            return None, None
        std = entry.standard
        standard_cost = (
            self.prompt_tokens * std.input_usd_per_million
            + self.output_tokens * std.output_usd_per_million
        ) / 1_000_000.0
        hit_rate = CACHE_HIT_INPUT_USD_PER_MILLION.get(self.model)
        if self.hit_tokens is None or self.miss_tokens is None or hit_rate is None:
            return standard_cost, None
        cached_cost = (
            self.hit_tokens * hit_rate
            + self.miss_tokens * std.input_usd_per_million
            + self.output_tokens * std.output_usd_per_million
        ) / 1_000_000.0
        return standard_cost, cached_cost


# --------------------------------------------------------------------------- #
# data loading
# --------------------------------------------------------------------------- #


def _to_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _first_present(payload: Mapping[str, Any], keys: Sequence[str]) -> Optional[int]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    return None


def _extract_cache_tokens(
    payload: Mapping[str, Any],
) -> tuple[Optional[int], Optional[int]]:
    """(hit, miss) token counts from a raw usage payload, or (None, None)."""
    usage = payload.get("usage")
    usage_map = usage if isinstance(usage, dict) else {}
    hit = _first_present(payload, CACHE_HIT_FIELDS)
    if hit is None:
        hit = _first_present(usage_map, CACHE_HIT_FIELDS)
    miss = _first_present(payload, CACHE_MISS_FIELDS)
    if miss is None:
        miss = _first_present(usage_map, CACHE_MISS_FIELDS)
    return hit, miss


def _call_class_of(payload: Mapping[str, Any]) -> str:
    for key in ("task_class", "taskClass", "route", "logical_key", "tier", "model_tier"):
        value = payload.get(key)
        if value:
            return str(value)
    return "unknown"


def _synthetic_rows() -> List[ClassRow]:
    """Build the bundled synthetic sample as ClassRows."""
    rows: List[ClassRow] = []
    for call_class, model, requests, prompt, hit, output in SYNTHETIC_SAMPLE:
        rows.append(
            ClassRow(
                call_class=call_class,
                source=SOURCE_SYNTHETIC,
                model=model,
                requests=requests,
                prompt_tokens=prompt,
                output_tokens=output,
                hit_tokens=hit,
                miss_tokens=prompt - hit,
                cache_assessable=True,
            )
        )
    return rows


def _load_real_usage(path: Path) -> List[ClassRow]:
    """Read a recorded-usage JSONL store into DeepSeek call-class rows.

    Real rows carry measured prompt/output tokens; hit/miss are None
    (CANNOT-ASSESS) unless the payload itself carries a prompt-cache token
    split — which the repo's recorded format does not today.
    """
    rows: List[ClassRow] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        provider = payload.get("provider")
        model = payload.get("model")
        if provider != PROVIDER and not str(model or "").startswith("deepseek"):
            continue
        prompt = _to_int(payload.get("input_tokens", payload.get("inputTokens")))
        output = _to_int(payload.get("output_tokens", payload.get("outputTokens")))
        hit, miss = _extract_cache_tokens(payload)
        rows.append(
            ClassRow(
                call_class=_call_class_of(payload),
                source=SOURCE_MEASURED,
                model=str(model or ""),
                requests=1,
                prompt_tokens=prompt,
                output_tokens=output,
                hit_tokens=hit,
                miss_tokens=miss,
                cache_assessable=(hit is not None and miss is not None),
            )
        )
    return rows


def _aggregate(rows: Sequence[ClassRow]) -> List[ClassRow]:
    """Sum per-call-class rows, collapsing the cache split when not assessable."""
    acc: Dict[str, ClassRow] = {}
    for row in rows:
        cur = acc.get(row.call_class)
        if cur is None:
            acc[row.call_class] = ClassRow(
                call_class=row.call_class,
                source=row.source,
                model=row.model,
                requests=0,
                prompt_tokens=0,
                output_tokens=0,
                hit_tokens=0,
                miss_tokens=0,
                cache_assessable=row.cache_assessable,
            )
            cur = acc[row.call_class]
        cur.requests += row.requests
        cur.prompt_tokens += row.prompt_tokens
        cur.output_tokens += row.output_tokens
        if row.cache_assessable and cur.cache_assessable:
            cur.hit_tokens = (cur.hit_tokens or 0) + (row.hit_tokens or 0)
            cur.miss_tokens = (cur.miss_tokens or 0) + (row.miss_tokens or 0)
        else:
            cur.cache_assessable = False
            cur.hit_tokens = None
            cur.miss_tokens = None
    return sorted(acc.values(), key=lambda r: r.call_class)


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #


def _int_or_na(value: Optional[int]) -> str:
    return CANNOT_ASSESS if value is None else f"{value:,}"


def _pct(value: Optional[float]) -> str:
    return CANNOT_ASSESS if value is None else f"{value * 100.0:.1f}%"


def _usd(value: Optional[float]) -> str:
    return CANNOT_ASSESS if value is None else f"{value:.6f}"


def _render_table(rows: List[ClassRow], cards: RateCardStore) -> str:
    headers = [
        "call_class",
        "requests",
        "prompt_tokens",
        "hit_tokens",
        "miss_tokens",
        "cache_hit_ratio",
        "cost_std_usd",
        "cost_cached_usd",
    ]
    body: List[List[str]] = []
    for row in rows:
        std, cached = row.cost(cards)
        body.append(
            [
                row.call_class,
                f"{row.requests:,}",
                f"{row.prompt_tokens:,}",
                _int_or_na(row.hit_tokens),
                _int_or_na(row.miss_tokens),
                _pct(row.cache_hit_ratio),
                _usd(std),
                _usd(cached),
            ]
        )

    # totals row (sum what is summable; cache split only if every row assessable)
    total = ClassRow(
        call_class="TOTAL",
        source=rows[0].source if rows else SOURCE_SYNTHETIC,
        model="",
        requests=sum(r.requests for r in rows),
        prompt_tokens=sum(r.prompt_tokens for r in rows),
        output_tokens=sum(r.output_tokens for r in rows),
        hit_tokens=None,
        miss_tokens=None,
        cache_assessable=all(r.cache_assessable for r in rows),
    )
    if total.cache_assessable:
        total.hit_tokens = sum(r.hit_tokens or 0 for r in rows)
        total.miss_tokens = sum(r.miss_tokens or 0 for r in rows)

    # The total spans multiple models, so sum per-row costs rather than price
    # the total under one model. Each cost is None when the row is unpriced.
    stds = [r.cost(cards)[0] for r in rows]
    cacheds = [r.cost(cards)[1] for r in rows]
    t_std = sum(stds) if all(s is not None for s in stds) else None
    t_cached = sum(cacheds) if all(c is not None for c in cacheds) else None

    body.append(
        [
            total.call_class,
            f"{total.requests:,}",
            f"{total.prompt_tokens:,}",
            _int_or_na(total.hit_tokens),
            _int_or_na(total.miss_tokens),
            _pct(total.cache_hit_ratio),
            _usd(t_std),
            _usd(t_cached),
        ]
    )

    widths = [
        max(len(headers[i]), *(len(row[i]) for row in body))
        for i in range(len(headers))
    ]
    header = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    sep = "  ".join("-" * widths[i] for i in range(len(headers)))
    lines = [header, sep]
    for row in body:
        lines.append("  ".join(row[i].ljust(widths[i]) for i in range(len(row))))
    return "\n".join(lines)


def _render_json(
    rows: List[ClassRow], cards: RateCardStore, *, cache_fields_present: bool
) -> str:
    out_rows: List[Dict[str, Any]] = []
    for row in rows:
        std, cached = row.cost(cards)
        out_rows.append(
            {
                "callClass": row.call_class,
                "source": row.source,
                "model": row.model,
                "requests": row.requests,
                "promptTokens": row.prompt_tokens,
                "hitTokens": row.hit_tokens,
                "missTokens": row.miss_tokens,
                "cacheHitRatio": (
                    round(row.cache_hit_ratio, 8)
                    if row.cache_hit_ratio is not None
                    else None
                ),
                "costStandardUsd": round(std, 8) if std is not None else None,
                "costCachedUsd": round(cached, 8) if cached is not None else None,
            }
        )
    return json.dumps(
        {
            "provider": PROVIDER,
            "dataSource": rows[0].source if rows else "none",
            "cacheTokenFieldsPresent": cache_fields_present,
            "rows": out_rows,
        },
        indent=2,
        sort_keys=True,
    )


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "DeepSeek token-flow baseline: per-call-class prompt-cache "
            "hit/miss/cost table (offline)."
        )
    )
    parser.add_argument(
        "--store",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "JSONL recorded-usage store to consume (repo telemetry/metering "
            "format). Default: bundled synthetic sample."
        ),
    )
    parser.add_argument(
        "--rate-dir",
        type=Path,
        default=DEFAULT_RATE_CARD_DIR,
        metavar="DIR",
        help="rate-card directory override (default: telemetry/metering/rate_cards).",
    )
    parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="output format (default: table).",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    cards = RateCardStore.load_dir(args.rate_dir)

    if args.store is not None:
        raw_rows = _load_real_usage(args.store)
        rows = _aggregate(raw_rows)
        cache_fields_present = any(r.cache_assessable for r in rows)
        source_label = SOURCE_MEASURED
    else:
        rows = _aggregate(_synthetic_rows())
        cache_fields_present = True
        source_label = SOURCE_SYNTHETIC

    if not rows:
        print(f"No {PROVIDER} usage records found in {args.store} — nothing to aggregate.")
        return 0

    if args.format == "json":
        print(_render_json(rows, cards, cache_fields_present=cache_fields_present))
        return 0

    banner = [
        f"DeepSeek token-flow baseline (provider={PROVIDER})",
        f"Data source: {source_label}"
        + (
            " (bundled sample — no real recorded DeepSeek cache-token flow exists in-tree)"
            if source_label == SOURCE_SYNTHETIC
            else f" ({args.store})"
        ),
        (
            "Prompt-cache hit/miss split: ASSESSABLE"
            if cache_fields_present
            else "Prompt-cache hit/miss split: CANNOT-ASSESS (no cache-token fields in recorded usage)"
        ),
        "",
    ]
    print("\n".join(banner))
    print(_render_table(rows, cards))
    print()
    print(
        "cost_std_usd   = prompt*input + output*output, from the repo rate cards "
        "(no cache discount)."
    )
    print(
        "cost_cached_usd = hit*cache-hit-input + miss*input + output*output; the "
        "cache-hit input rate is a point-in-time DeepSeek baseline constant, NOT "
        "in the repo rate card."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
