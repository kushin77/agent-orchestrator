#!/usr/bin/env python3
"""DeepSeek prompt-cache efficiency audit (issue #673 / PF-8).

Reads recorded DeepSeek usage (JSON Lines, one record per model call) and
audits the provider's cache-efficiency headers — ``prompt_cache_hit_tokens``
vs ``prompt_cache_miss_tokens`` — joined, where a record carries one, by the
ERPNext billing-query identifier. Prints the per-query hit/miss table and the
aggregate cache-hit ratio.

Honest join (GR-12): the ERPNext billing-query dimension (epic #645) is still
in flight, so usage records do not yet carry a billing-query id. This script
still runs and reports ``no billing-query ids present`` rather than
fabricating a join key. The per-query table is empty in that case; the
aggregate row is always printed so the baseline (PF-2) number stays
regressable even before #645 lands.

Input contract (one JSON object per line):

    prompt_cache_hit_tokens   int   tokens served from the prompt cache
    prompt_cache_miss_tokens  int   tokens billed uncached (the cache miss)
    billing_query_id          str   optional ERPNext billing-query identifier
    model / provider          str   optional provenance stamps

CamelCase aliases (``promptCacheHitTokens`` / ``promptCacheMissTokens`` /
``billingQueryId``) are accepted so the same reader can consume the repo's
serialized record shapes. A record that omits a cache header contributes
``0`` for that side (honest zero, never a fabricated hit).

Offline and reproducible: no network, no live API; deterministic output for a
given input file, so the regression test pins the exact table.

Standalone module (stdlib only); no cross-package imports.

---knowledge---
module_id: gateway.finops.cache_audit
system: gateway
app: finops
solution_class: enterprise
patterns: [offline-audit, honest-join, deterministic-render]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [UsageRow, QueryAggregate, AuditResult, parse_record, load_usage, audit, render]
invariants: "the across-all aggregate row is printed even when every per-query join is absent, so the baseline number stays regressable"
gotchas: "the ERPNext billing-query dimension is still in flight, so an absent join key is reported as no billing-query ids present rather than fabricated"
related: ["#673", "#645"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Canonical + accepted aliases for each input field (canonical first).
_HIT_KEYS = ("prompt_cache_hit_tokens", "promptCacheHitTokens")
_MISS_KEYS = ("prompt_cache_miss_tokens", "promptCacheMissTokens")
_BILLING_KEYS = ("billing_query_id", "billingQueryId")

#: Join-status sentinel text when no record carries a billing-query id.
NO_BILLING_QUERY = "no billing-query ids present"


@dataclass(frozen=True)
class UsageRow:
    """One DeepSeek call's cache-efficiency observation.

    ``hit_tokens`` / ``miss_tokens`` are the provider's cache-efficiency
    headers; ``billing_query_id`` is the ERPNext billing-query identifier when
    the record carries one (``None`` otherwise). ``model`` is provenance only
    and does not affect the table.
    """

    hit_tokens: int
    miss_tokens: int
    billing_query_id: Optional[str] = None
    model: Optional[str] = None

    @property
    def prompt_tokens(self) -> int:
        """Total prompt tokens the provider saw (hit + miss)."""
        return self.hit_tokens + self.miss_tokens

    @property
    def hit_ratio(self) -> float:
        """Fraction of this call's prompt tokens served from cache (0.0-1.0)."""
        total = self.hit_tokens + self.miss_tokens
        if total <= 0:
            return 0.0
        return self.hit_tokens / total


@dataclass
class QueryAggregate:
    """Accumulated hit/miss across all calls sharing one billing-query id."""

    billing_query_id: str
    calls: int = 0
    hit_tokens: int = 0
    miss_tokens: int = 0

    def add(self, row: UsageRow) -> None:
        self.calls += 1
        self.hit_tokens += row.hit_tokens
        self.miss_tokens += row.miss_tokens

    @property
    def hit_ratio(self) -> float:
        total = self.hit_tokens + self.miss_tokens
        if total <= 0:
            return 0.0
        return self.hit_tokens / total


@dataclass
class AuditResult:
    """The full audit: per-query aggregates plus the across-all aggregate."""

    rows: List[UsageRow] = field(default_factory=list)
    per_query: Dict[str, QueryAggregate] = field(default_factory=dict)
    total: QueryAggregate = field(
        default_factory=lambda: QueryAggregate("TOTAL")
    )

    @property
    def has_billing_query(self) -> bool:
        """True when at least one record carried a billing-query id."""
        return any(r.billing_query_id is not None for r in self.rows)


def _get_int(record: Dict[str, Any], keys: tuple) -> int:
    for key in keys:
        value = record.get(key)
        if value is not None:
            return int(value)
    return 0


def _get_str(record: Dict[str, Any], keys: tuple) -> Optional[str]:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def parse_record(record: Dict[str, Any]) -> UsageRow:
    """Build a ``UsageRow`` from one JSONL usage record (alias-tolerant)."""
    return UsageRow(
        hit_tokens=_get_int(record, _HIT_KEYS),
        miss_tokens=_get_int(record, _MISS_KEYS),
        billing_query_id=_get_str(record, _BILLING_KEYS),
        model=_get_str(record, ("model",)),
    )


def load_usage(path: Path) -> List[UsageRow]:
    """Read a JSON Lines usage file into ``UsageRow`` objects (offline)."""
    rows: List[UsageRow] = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:  # pragma: no cover - defensive
                raise ValueError(
                    f"unparseable usage record at {path}:{lineno}: {exc}"
                ) from exc
            rows.append(parse_record(record))
    return rows


def audit(rows: List[UsageRow]) -> AuditResult:
    """Aggregate usage rows per billing-query id and overall."""
    result = AuditResult(rows=list(rows))
    for row in rows:
        qid = row.billing_query_id
        if qid is not None:
            result.per_query.setdefault(qid, QueryAggregate(qid)).add(row)
        result.total.add(row)
    return result


def _fmt_row(label: str, agg: QueryAggregate) -> str:
    return (
        f"{label:<24} {agg.calls:>6} {agg.hit_tokens:>11} "
        f"{agg.miss_tokens:>12} {agg.hit_ratio:>16.4f}"
    )


_HEADER = (
    "billing_query_id            calls  hit_tokens  miss_tokens  cache_hit_ratio"
)


def render(result: AuditResult) -> str:
    """Render the deterministic audit table as text.

    The join status line is printed first so a reader sees immediately whether
    the per-query axis exists (``no billing-query ids present`` when it does
    not). The aggregate ``TOTAL`` row is always present.
    """
    lines: List[str] = []
    lines.append(
        "join: billing-query ids present"
        if result.has_billing_query
        else f"join: {NO_BILLING_QUERY}"
    )
    lines.append("")
    lines.append(_HEADER)
    if result.has_billing_query:
        for qid in sorted(result.per_query):
            lines.append(_fmt_row(qid, result.per_query[qid]))
    lines.append(_fmt_row("TOTAL", result.total))
    lines.append("")
    lines.append(f"aggregate cache-hit ratio: {result.total.hit_ratio:.4f}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gateway/finops/cache_audit.py",
        description="Audit DeepSeek prompt-cache efficiency (hit vs miss "
        "tokens) joined by ERPNext billing-query id.",
    )
    parser.add_argument(
        "usage",
        nargs="?",
        default=None,
        help="JSON Lines usage file (one DeepSeek call per line). If omitted, "
        "reads stdin.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.usage:
        rows = load_usage(Path(args.usage))
    else:
        rows = [parse_record(json.loads(line)) for line in sys.stdin if line.strip()]
    print(render(audit(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
