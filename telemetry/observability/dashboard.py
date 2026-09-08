"""telemetry/observability — offline dashboard generation (issue #32).

Builds a static, self-contained HTML dashboard plus a machine-readable JSON
data file and a terminal health report — all generated offline from the
telemetry store, the per-tenant SLO results, the breach alerts and the usage
rows.  No external dashboard server and no network/CDN dependencies (the
fleet-standard offline constraint).  The JSON data file is the canonical
artifact (the HTML is a thin render of the same data).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any, Iterable, Optional

from telemetry.observability.breach import Alert
from telemetry.observability.model import (
    SERVED_OUTCOMES,
    SpanRecord,
    is_attempt_kind,
)
from telemetry.observability.slos import SloResult
from telemetry.observability.store import TraceStore
from telemetry.observability.usage import UsageRow

VERDICT_COLOR = {
    "OK": "#1a7f37",
    "AT_RISK": "#9a6700",
    "BREACHED": "#cf222e",
    "NO_DATA": "#6e7781",
}


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _outcome_mix(spans: Iterable[SpanRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for span in spans:
        counts[span.outcome] = counts.get(span.outcome, 0) + 1
    return counts


class Dashboard:
    """Renders the observability dashboard from in-memory results."""

    def __init__(
        self,
        store: TraceStore,
        slo_results: Optional[list[SloResult]] = None,
        usage_rows: Optional[list[UsageRow]] = None,
        alerts: Optional[list[Alert]] = None,
    ) -> None:
        self.store = store
        self.slo_results = list(slo_results or [])
        self.usage_rows = list(usage_rows or [])
        self.alerts = list(alerts or [])
        self.generated_at = _now_iso()

    # -- data -------------------------------------------------------------
    def data(self) -> dict[str, Any]:
        """The canonical dashboard data (JSON-serializable)."""
        spans = self.store.all_spans()
        by_tenant: dict[str, list[SpanRecord]] = {}
        for span in spans:
            by_tenant.setdefault(span.tenant_id, []).append(span)

        tenant_rows: list[dict[str, Any]] = []
        for tenant_id in sorted(by_tenant):
            tenant_spans = by_tenant[tenant_id]
            # Same attempt definition as the SLO evaluator: model calls and
            # engine/agent-loop steps only (never trace roots or guardrails).
            attempts = [
                s for s in tenant_spans if is_attempt_kind(s.kind) and s.is_attempt
            ]
            served = sum(1 for s in attempts if s.outcome in SERVED_OUTCOMES)
            calls = sum(
                1 for s in tenant_spans if s.kind in ("model_call", "step")
            )
            tenant_rows.append(
                {
                    "tenantId": tenant_id,
                    "spans": len(tenant_spans),
                    "calls": calls,
                    "attempts": len(attempts),
                    "served": served,
                    "failed": len(attempts) - served,
                    "inputTokens": sum(s.input_tokens for s in tenant_spans),
                    "outputTokens": sum(s.output_tokens for s in tenant_spans),
                    "tokens": sum(s.tokens for s in tenant_spans),
                    "estimatedCostUsd": round(
                        sum(s.estimated_cost_usd for s in tenant_spans), 6
                    ),
                    "latencyP95Ms": self._latency_p95(attempts),
                    "alerts": sum(
                        1 for a in self.alerts if a.tenant_id == tenant_id
                    ),
                }
            )

        return {
            "schemaVersion": 1,
            "generatedAt": self.generated_at,
            "tenants": tenant_rows,
            "sloResults": [r.to_dict() for r in self.slo_results],
            "alerts": [a.to_dict() for a in self.alerts],
            "usage": [u.to_dict() for u in self.usage_rows],
            "outcomes": _outcome_mix(spans),
            "burnRate": self._burn_rate_view(),
        }

    def _latency_p95(self, attempts: list[SpanRecord]) -> Optional[float]:
        latencies = [s.latency_ms for s in attempts]
        if not latencies:
            return None
        ordered = sorted(latencies)
        return round(ordered[max(0, int(0.95 * len(ordered)) - 1)], 3)

    def _burn_rate_view(self) -> list[dict[str, Any]]:
        """Per-tenant/SLO error-budget burn, highest first (the burn-rate view).

        A burn above 1.0 means the window consumed more than its full error
        budget (breach); the view is sorted so the worst tenants lead.
        """
        rows: list[dict[str, Any]] = []
        for result in self.slo_results:
            rows.append(
                {
                    "tenantId": result.tenant_id,
                    "slo": result.name,
                    "kind": result.kind,
                    "verdict": result.verdict,
                    "budgetConsumedRatio": result.budget_consumed_ratio,
                    "missedWindow": result.missed_window,
                }
            )
        rows.sort(
            key=lambda r: (
                0 if r["verdict"] != "OK" else 1,
                -(
                    r["budgetConsumedRatio"]
                    if r["budgetConsumedRatio"] is not None
                    else -1.0
                ),
                r["tenantId"],
                r["slo"],
            )
        )
        return rows

    # -- files ------------------------------------------------------------
    def render_html(self, path: str) -> str:
        """Write a self-contained static HTML dashboard; returns the path."""
        data = self.data()
        path = os.path.abspath(path)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        html = self._html(data)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(html)
        return path

    def render_json(self, path: str) -> str:
        """Write the canonical dashboard JSON data file; returns the path."""
        data = self.data()
        path = os.path.abspath(path)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        return path

    def render_terminal_report(self) -> str:
        """A monospace terminal health report (outcome-not-liveness first)."""
        data = self.data()
        lines: list[str] = []
        lines.append("═" * 72)
        lines.append("agent-orchestrator observability health report")
        lines.append(f"generated: {self.generated_at}")
        lines.append(f"spans: {len(self.store)}  traces: "
                     f"{len(self.store.trace_ids())}  tenants: {len(data['tenants'])}")
        lines.append("═" * 72)

        # Outcome-not-liveness: outcome velocity, not heartbeat presence.
        lines.append("OUTCOMES (actual results):")
        if not data["outcomes"]:
            lines.append("  (no spans recorded — nothing to report)")
        for outcome, count in sorted(data["outcomes"].items()):
            lines.append(f"  {outcome:<18} {count}")

        lines.append("")
        lines.append("TENANTS:")
        if not data["tenants"]:
            lines.append("  (none)")
        for t in data["tenants"]:
            lines.append(
                f"  {t['tenantId']:<20} calls={t['calls']:<4} "
                f"served={t['served']:<4} failed={t['failed']:<4} "
                f"tokens={t['tokens']:<6} cost=${t['estimatedCostUsd']:<8.4f} "
                f"alerts={t['alerts']}"
            )

        lines.append("")
        lines.append("SLOs:")
        for r in self.slo_results:
            consumed = (
                f"{r.budget_consumed_ratio:.3f}"
                if r.budget_consumed_ratio is not None
                else "-"
            )
            lines.append(
                f"  {r.tenant_id:<16} {r.name:<34} {r.kind:<12} "
                f"{r.verdict:<9} burn={consumed}"
            )

        lines.append("")
        lines.append("BURN-RATE (worst first):")
        for b in data["burnRate"]:
            ratio = (
                f"{b['budgetConsumedRatio']:.3f}"
                if b["budgetConsumedRatio"] is not None
                else "-"
            )
            lines.append(
                f"  {b['tenantId']:<16} {b['slo']:<30} {b['verdict']:<9} "
                f"burn={ratio}"
            )

        lines.append("")
        if data["alerts"]:
            lines.append(f"ALERTS ({len(data['alerts'])}):")
            for alert in data["alerts"]:
                lines.append(
                    f"  [{alert['severity']:<8}] {alert['tenantId']:<16} "
                    f"{alert['slo']:<30} {alert['reason']}"
                )
        else:
            lines.append("ALERTS: none — every SLO within budget with data")

        lines.append("═" * 72)
        return "\n".join(lines)

    # -- html -------------------------------------------------------------
    def _html(self, data: dict[str, Any]) -> str:
        rows = "".join(
            f"<tr><td>{t['tenantId']}</td><td>{t['calls']}</td>"
            f"<td>{t['served']}</td><td>{t['failed']}</td>"
            f"<td>{t['tokens']}</td><td>${t['estimatedCostUsd']:.4f}</td>"
            f"<td>{t['alerts']}</td></tr>"
            for t in data["tenants"]
        )
        slo_rows = "".join(
            f"<tr><td>{r['tenantId']}</td><td>{r['slo']}</td>"
            f"<td>{r['kind']}</td>"
            f"<td style='color:{VERDICT_COLOR.get(r['verdict'], '#000')}'>{r['verdict']}</td>"
            f"<td>{r['attempts']}</td>"
            f"<td>{r['budgetConsumedRatio'] if r['budgetConsumedRatio'] is not None else '-'}</td></tr>"
            for r in data["sloResults"]
        )
        burn_rows = "".join(
            f"<tr><td>{b['tenantId']}</td><td>{b['slo']}</td>"
            f"<td>{b['verdict']}</td>"
            f"<td>{b['budgetConsumedRatio'] if b['budgetConsumedRatio'] is not None else '-'}</td></tr>"
            for b in data["burnRate"]
        )
        usage_rows = "".join(
            f"<tr><td>{u['tenantId']}</td><td>{u['provider'] or '-'}</td>"
            f"<td>{u['model'] or '-'}</td><td>{u['calls']}</td>"
            f"<td>{u['totalTokens']}</td><td>${u['estimatedCostUsd']:.4f}</td></tr>"
            for u in data["usage"]
        )
        alert_html: list[str] = []
        for a in data["alerts"]:
            color = "#cf222e" if a["severity"] == "critical" else "#9a6700"
            alert_html.append(
                f"<li style='color:{color}'>[{a['severity']}] "
                f"tenant <b>{a['tenantId']}</b> — {a['slo']}: {a['reason']}</li>"
            )
        alerts_block = (
            f"<ul>{''.join(alert_html)}</ul>"
            if alert_html
            else "<p>none — every SLO within budget with data</p>"
        )
        payload = json.dumps(data, sort_keys=True)
        return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>agent-orchestrator observability dashboard</title>
<style>
  body {{ font-family: system-ui, -apple-system, sans-serif; margin: 2rem; }}
  h1, h2 {{ color: #24292f; }}
  table {{ border-collapse: collapse; margin: 1rem 0; width: 100%; }}
  th, td {{ border: 1px solid #d0d7de; padding: .4rem .6rem; text-align: left; }}
  th {{ background: #f6f8fa; }}
  .ok {{ color: #1a7f37; }} .warn {{ color: #9a6700; }}
  .bad {{ color: #cf222e; }}
</style>
</head>
<body>
<h1>agent-orchestrator — observability dashboard</h1>
<p>generated {data['generatedAt']} · spans {len(self.store)} ·
   traces {len(self.store.trace_ids())} · schemaVersion {data['schemaVersion']}</p>
<h2>Alerts</h2>
{alerts_block}
<h2>Per-tenant summary</h2>
<table><thead><tr><th>tenant</th><th>calls</th><th>served</th><th>failed</th>
<th>tokens</th><th>cost</th><th>alerts</th></tr></thead>
<tbody>{rows}</tbody></table>
<h2>Per-tenant SLOs</h2>
<table><thead><tr><th>tenant</th><th>SLO</th><th>kind</th><th>verdict</th>
<th>attempts</th><th>budget consumed</th></tr></thead>
<tbody>{slo_rows}</tbody></table>
<h2>Burn rate (worst first)</h2>
<table><thead><tr><th>tenant</th><th>SLO</th><th>verdict</th>
<th>budget consumed</th></tr></thead>
<tbody>{burn_rows}</tbody></table>
<h2>Usage / chargeback</h2>
<table><thead><tr><th>tenant</th><th>provider</th><th>model</th><th>calls</th>
<th>tokens</th><th>cost</th></tr></thead>
<tbody>{usage_rows}</tbody></table>
<script type="application/json" id="dashboard-data">{payload}</script>
</body>
</html>
"""


def render_dashboard(
    store: TraceStore,
    *,
    slo_results: Optional[list[SloResult]] = None,
    usage_rows: Optional[list[UsageRow]] = None,
    alerts: Optional[list[Alert]] = None,
    html_path: Optional[str] = None,
    json_path: Optional[str] = None,
) -> str:
    """One-shot convenience: build the dashboard and return the terminal report."""
    dashboard = Dashboard(
        store, slo_results=slo_results, usage_rows=usage_rows, alerts=alerts
    )
    if html_path:
        dashboard.render_html(html_path)
    if json_path:
        dashboard.render_json(json_path)
    return dashboard.render_terminal_report()


__all__ = [
    "Dashboard",
    "render_dashboard",
    "VERDICT_COLOR",
]
