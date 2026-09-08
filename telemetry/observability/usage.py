"""telemetry/observability — per-tenant usage / chargeback reporter (#32).

Aggregates the telemetry store into per-tenant (and per provider/model)
usage rows — calls, tokens, estimated spend — the offline feed that the
billing/metering surface (issue #33) consumes.  Spend is whatever the
recording pillars attached (``estimatedCostUsd``); no cost is fabricated
here.  The output is a deterministic JSON/terminal report.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from telemetry.observability.model import (
    SERVED_OUTCOMES,
    SpanRecord,
    epoch_of,
)
from telemetry.observability.store import TraceStore

GROUP_TENANT = "tenant"
GROUP_PROVIDER = "provider"
GROUP_MODEL = "model"
GROUP_AGENT = "agent"


@dataclass(frozen=True)
class UsageRow:
    """Aggregated usage for one grouping key combination."""

    tenant_id: str
    calls: int
    served: int
    failed: int
    input_tokens: int
    output_tokens: int
    latency_ms_sum: float
    estimated_cost_usd: float
    provider: Optional[str] = None
    model: Optional[str] = None
    agent_id: Optional[str] = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenantId": self.tenant_id,
            "provider": self.provider,
            "model": self.model,
            "agentId": self.agent_id,
            "calls": self.calls,
            "served": self.served,
            "failed": self.failed,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "totalTokens": self.total_tokens,
            "latencyMsSum": round(self.latency_ms_sum, 3),
            "estimatedCostUsd": round(self.estimated_cost_usd, 6),
        }


def _usage_key(
    span: SpanRecord,
    dimensions: tuple[str, ...],
) -> tuple[Any, ...]:
    parts: list[Any] = []
    for dim in dimensions:
        if dim == GROUP_TENANT:
            parts.append(span.tenant_id)
        elif dim == GROUP_PROVIDER:
            parts.append(span.provider)
        elif dim == GROUP_MODEL:
            parts.append(span.model)
        elif dim == GROUP_AGENT:
            parts.append(span.agent_id)
        else:
            raise ValueError(f"unknown usage dimension: {dim!r}")
    return tuple(parts)


class UsageReporter:
    """Aggregates the store into usage rows (billing/chargeback feed)."""

    def __init__(
        self,
        store: TraceStore,
        *,
        dimensions: tuple[str, ...] = (GROUP_TENANT,),
    ) -> None:
        for dim in dimensions:
            if dim not in (GROUP_TENANT, GROUP_PROVIDER, GROUP_MODEL, GROUP_AGENT):
                raise ValueError(f"unknown usage dimension: {dim!r}")
        self.store = store
        self.dimensions = dimensions

    def report(
        self,
        *,
        since_iso: Optional[str] = None,
        until_iso: Optional[str] = None,
    ) -> list[UsageRow]:
        """Aggregate billable spans (model calls + attempts) over a window.

        Trace-root and pure-guardrail spans carry no model spend and are
        excluded from the chargeable count; they surface in the trace store,
        not on the bill.
        """
        since = epoch_of(since_iso) if since_iso else None
        until = epoch_of(until_iso) if until_iso else None
        buckets: dict[tuple[Any, ...], list[SpanRecord]] = {}
        for span in self.store.all_spans():
            if span.kind in ("trace", "guard"):
                continue
            ts = epoch_of(span.ts)
            if since is not None and ts < since:
                continue
            if until is not None and ts > until:
                continue
            buckets.setdefault(_usage_key(span, self.dimensions), []).append(span)

        rows: list[UsageRow] = []
        for key in sorted(buckets, key=lambda k: tuple(str(x or "") for x in k)):
            spans = buckets[key]
            calls = len(spans)
            served = sum(1 for s in spans if s.outcome in SERVED_OUTCOMES)
            failed = calls - served
            input_tokens = sum(s.input_tokens for s in spans)
            output_tokens = sum(s.output_tokens for s in spans)
            latency = sum(s.latency_ms for s in spans)
            cost = sum(s.estimated_cost_usd for s in spans)
            dims = dict(zip(self.dimensions, key))
            rows.append(
                UsageRow(
                    tenant_id=str(dims.get(GROUP_TENANT, "")),
                    provider=dims.get(GROUP_PROVIDER),
                    model=dims.get(GROUP_MODEL),
                    agent_id=dims.get(GROUP_AGENT),
                    calls=calls,
                    served=served,
                    failed=failed,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms_sum=latency,
                    estimated_cost_usd=cost,
                )
            )
        return rows

    def report_json(
        self,
        *,
        since_iso: Optional[str] = None,
        until_iso: Optional[str] = None,
    ) -> str:
        """JSON-serialized usage report (the billing feed)."""
        import json

        rows = self.report(since_iso=since_iso, until_iso=until_iso)
        return json.dumps(
            {
                "dimensions": list(self.dimensions),
                "rows": [r.to_dict() for r in rows],
            },
            indent=2,
            sort_keys=True,
        )


__all__ = [
    "GROUP_TENANT",
    "GROUP_PROVIDER",
    "GROUP_MODEL",
    "GROUP_AGENT",
    "UsageRow",
    "UsageReporter",
]
