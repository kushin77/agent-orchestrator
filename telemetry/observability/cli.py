"""telemetry/observability — offline operator CLI (issue #32, phase 5).

Deterministic, fully offline observability tooling: seed a demo store through
the intake hook, inspect traces, evaluate per-tenant SLOs (from the
``slo_templates/``), run breach detection (outcome-not-liveness), produce
usage/chargeback reports and render the static dashboard.

Exit-code contract (honest gate): 0 = success / all SLOs OK / no alerts;
1 = an SLO is BREACHED/AT_RISK/NO_DATA or an alert fired; 2 = usage error.
A tenant that misses its SLO window (NO_DATA) exits non-zero — silence is
never read as health.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

from telemetry.observability.breach import BreachDetector
from telemetry.observability.dashboard import Dashboard
from telemetry.observability.intake import TelemetryRecorder
from telemetry.observability.model import (
    KIND_GUARD,
    KIND_MODEL_CALL,
    KIND_STEP,
    OUTCOME_DENIED,
    OUTCOME_FAILED,
    OUTCOME_NO_HEALTHY_ROUTE,
    OUTCOME_RATE_LIMITED,
    OUTCOME_SUCCESS,
    SERVICE_ENGINE,
    SERVICE_GATEWAY,
    SERVICE_GUARDRAILS,
    Trace,
    epoch_of,
)
from telemetry.observability.slos import (
    VERDICT_OK,
    SloEvaluator,
    SloResult,
    definitions_from_templates,
    load_slo_templates,
)
from telemetry.observability.store import JsonlSpanSink, TraceStore
from telemetry.observability.usage import UsageReporter

DEFAULT_STORE = "telemetry-store.jsonl"
DEFAULT_SLO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "slo_templates")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telemetry.observability.cli",
        description="Offline observability CLI (issue #32): traces, per-tenant "
                    "SLOs, breach detection, usage and dashboards.",
    )
    # ``--store`` must be a shared parent option attached to EVERY subcommand
    # (a top-level option would only parse before the subcommand name).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--store",
        default=DEFAULT_STORE,
        help=f"path to the telemetry JSONL store (default: {DEFAULT_STORE})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_seed = sub.add_parser("seed", parents=[common],
                            help="generate a deterministic demo store")
    p_seed.add_argument("--tenants", default="acme,nimbus",
                        help="comma-separated tenant ids (default: acme,nimbus)")
    p_seed.add_argument("--mode", choices=("healthy", "breach"),
                        default="healthy",
                        help="healthy or breach demo data (default: healthy)")
    p_seed.set_defaults(func=_cmd_seed)

    p_trace = sub.add_parser("trace", parents=[common],
                             help="show an end-to-end trace")
    p_trace.add_argument("--trace-id", default=None, help="filter by trace id")
    p_trace.add_argument("--tenant", default=None, help="filter by tenant")
    p_trace.set_defaults(func=_cmd_trace)

    p_slo = sub.add_parser("slo-eval", parents=[common],
                           help="evaluate per-tenant SLOs")
    p_slo.add_argument("--slo-dir", default=DEFAULT_SLO_DIR,
                       help="directory of SLO template YAMLs")
    p_slo.add_argument("--tenant", default=None,
                       help="evaluate only this tenant")
    p_slo.set_defaults(func=_cmd_slo_eval)

    p_breach = sub.add_parser("breach", parents=[common],
                              help="run breach detection")
    p_breach.add_argument("--slo-dir", default=DEFAULT_SLO_DIR,
                          help="directory of SLO template YAMLs")
    p_breach.add_argument("--tenant", default=None,
                          help="evaluate only this tenant")
    p_breach.set_defaults(func=_cmd_breach)

    p_usage = sub.add_parser("usage", parents=[common],
                             help="per-tenant usage / chargeback")
    p_usage.add_argument("--dimensions", default="tenant",
                         help="comma-separated dimensions "
                              "(tenant,provider,model,agent)")
    p_usage.set_defaults(func=_cmd_usage)

    p_dash = sub.add_parser("dashboard", parents=[common],
                            help="render static dashboard")
    p_dash.add_argument("--slo-dir", default=DEFAULT_SLO_DIR,
                        help="directory of SLO template YAMLs")
    p_dash.add_argument("--html", default="observability-dashboard.html",
                        help="output html path")
    p_dash.add_argument("--json", default="observability-dashboard.json",
                        help="output json data path")
    p_dash.set_defaults(func=_cmd_dashboard)

    p_report = sub.add_parser("report", parents=[common],
                              help="terminal health report")
    p_report.add_argument("--slo-dir", default=DEFAULT_SLO_DIR,
                          help="directory of SLO template YAMLs")
    p_report.set_defaults(func=_cmd_report)
    return parser


# --------------------------------------------------------------------------- #
# Demo seed (deterministic-ish, exercises the intake hook end-to-end)
# --------------------------------------------------------------------------- #
def _demo_timestamp(start_epoch: float, step_s: float, index: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(start_epoch + step_s * index, UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _seed_demo(
    store_path: str,
    tenants: list[str],
    mode: str,
) -> int:
    if os.path.exists(store_path):
        os.remove(store_path)
    # A monotonic clock that ticks 1s per call keeps latency measurement
    # deterministic when a span does not carry an explicit latency_ms.
    state = {"t": 0.0}

    def _clock() -> float:
        state["t"] += 1.0
        return state["t"]

    start_epoch = epoch_of("2026-09-01T00:00:00Z")
    ts_counter = {"n": 0}

    def _ts() -> str:
        ts_counter["n"] += 1
        return _demo_timestamp(start_epoch, 60, ts_counter["n"])

    with JsonlSpanSink(store_path) as sink:
        rec = TelemetryRecorder(sink, clock=_clock, timestamp=_ts)
        for tenant_idx, tenant_id in enumerate(tenants):
            agent = f"agent-{tenant_idx + 1}"
            request_count = 4
            for req in range(request_count):
                with rec.trace(
                    tenant_id=tenant_id,
                    request_id=f"req-{tenant_id}-{req + 1}",
                    service=SERVICE_ENGINE,
                    name="task.run",
                    agent_id=agent,
                    task_type="summarize",
                ):
                    # guardrail decision
                    with rec.span(
                        SERVICE_GUARDRAILS,
                        KIND_GUARD,
                        "policy.gate",
                        outcome=OUTCOME_DENIED if (req == 2 and tenant_idx == 0) else OUTCOME_SUCCESS,
                    ):
                        pass
                    # one or two gateway model calls
                    for hop in range(2):
                        outcome = OUTCOME_SUCCESS
                        if mode == "breach" and req == 0 and tenant_idx == 1:
                            outcome = OUTCOME_FAILED
                        if (
                            mode == "breach"
                            and req == 3
                            and tenant_idx == 0
                        ):
                            outcome = (
                                OUTCOME_NO_HEALTHY_ROUTE
                                if hop
                                else OUTCOME_RATE_LIMITED
                            )
                        rec.emit(
                            SERVICE_GATEWAY,
                            KIND_MODEL_CALL,
                            "model.call",
                            outcome=outcome,
                            provider=("anthropic" if hop == 0 else "deepseek"),
                            model=(
                                "claude-3-5-sonnet"
                                if hop == 0
                                else "deepseek-chat"
                            ),
                            tier="MED",
                            input_tokens=800 + 40 * hop,
                            output_tokens=200 + 20 * hop,
                            latency_ms=120.0 + 30.0 * hop,
                            estimated_cost_usd=0.002 + 0.0005 * hop,
                        )
                    # an engine step outcome
                    step_outcome = (
                        OUTCOME_FAILED
                        if (
                            mode == "breach"
                            and req == 1
                            and tenant_idx == 1
                        )
                        else OUTCOME_SUCCESS
                    )
                    rec.emit(
                        SERVICE_ENGINE,
                        KIND_STEP,
                        "agent.step",
                        outcome=step_outcome,
                        input_tokens=100,
                        output_tokens=50,
                        latency_ms=60.0,
                    )
    print(f"seeded demo store: {store_path} (tenants={tenants}, mode={mode})")
    return 0


# --------------------------------------------------------------------------- #
# Command helpers
# --------------------------------------------------------------------------- #
def _load_store(path: str) -> TraceStore:
    try:
        return TraceStore(path=path)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def _tenants(store: TraceStore, requested: Optional[str]) -> list[str]:
    if requested:
        return [t.strip() for t in requested.split(",") if t.strip()]
    return store.tenants()


def _evaluate(store: TraceStore, slo_dir: str, tenants: list[str]) -> list[SloResult]:
    if not tenants:
        print("error: no tenants in store", file=sys.stderr)
        raise SystemExit(2)
    templates = load_slo_templates(slo_dir)
    definitions = definitions_from_templates(templates, tenants)
    evaluator = SloEvaluator(store)
    return evaluator.evaluate_all(definitions)


def _print_results(results: list[SloResult]) -> None:
    print(f"{'tenant':<16} {'slo':<34} {'kind':<12} {'verdict':<9} {'attempts':<9} {'burn':<8}")
    for r in results:
        burn = (
            f"{r.budget_consumed_ratio:.3f}"
            if r.budget_consumed_ratio is not None
            else "-"
        )
        print(f"{r.tenant_id:<16} {r.name:<34} {r.kind:<12} "
              f"{r.verdict:<9} {r.attempts:<9} {burn:<8}")


def _cmd_seed(args: argparse.Namespace) -> int:
    tenants = [t.strip() for t in args.tenants.split(",") if t.strip()]
    return _seed_demo(args.store, tenants, args.mode)


def _cmd_trace(args: argparse.Namespace) -> int:
    store = _load_store(args.store)
    trace_ids = store.trace_ids()
    if args.trace_id:
        trace_ids = [t for t in trace_ids if t == args.trace_id]
    if args.tenant:
        trace_ids = [
            t for t in trace_ids
            if store.spans_for_trace(t)
            and store.spans_for_trace(t)[0].tenant_id == args.tenant
        ]
    if not trace_ids:
        print("error: no matching traces", file=sys.stderr)
        return 1
    for trace_id in trace_ids:
        trace = _rebuild(store, trace_id)
        print("─" * 72)
        print(f"trace {trace.trace_id} tenant={trace.tenant_id} "
              f"request={trace.request_id} duration_ms={trace.duration_ms:.1f} "
              f"tokens={trace.total_tokens}")
        depth_map = _span_depths(trace)
        for span in trace.spans:
            indent = "  " * depth_map.get(span.span_id, 0)
            print(
                f"{indent}{span.name} [{span.service}/{span.kind}] "
                f"outcome={span.outcome} provider={span.provider} "
                f"model={span.model} tokens={span.tokens} "
                f"latency_ms={span.latency_ms:.1f}"
            )
    return 0


def _span_depths(trace: Trace) -> dict[str, int]:
    """Span-id -> depth (0 for roots), computed via the parent chain."""
    by_id = {s.span_id: s for s in trace.spans}
    depth: dict[str, int] = {}
    for span in trace.spans:
        d = 0
        cursor: Optional[str] = span.parent_span_id
        while cursor and cursor in by_id:
            d += 1
            cursor = by_id[cursor].parent_span_id
        depth[span.span_id] = d
    return depth


def _rebuild(store: TraceStore, trace_id: str) -> Trace:
    for trace in store.traces():
        if trace.trace_id == trace_id:
            return trace
    raise KeyError(f"trace not found: {trace_id}")


def _cmd_slo_eval(args: argparse.Namespace) -> int:
    store = _load_store(args.store)
    tenants = _tenants(store, args.tenant)
    results = _evaluate(store, args.slo_dir, tenants)
    _print_results(results)
    bad = [r for r in results if r.verdict != VERDICT_OK]
    if bad:
        print(f"\nslo-eval: {len(bad)} non-OK SLO result(s) — gate RED "
              f"(any BREACHED/AT_RISK/NO_DATA fails)", file=sys.stderr)
        return 1
    print("\nslo-eval: all SLOs OK")
    return 0


def _cmd_breach(args: argparse.Namespace) -> int:
    store = _load_store(args.store)
    results = _evaluate(store, args.slo_dir, _tenants(store, args.tenant))
    detector = BreachDetector()
    alerts = detector.detect(results)
    if not alerts:
        print("breach: no alerts — every SLO within budget with data")
        return 0
    for alert in alerts:
        print(
            f"[{alert.severity}] tenant={alert.tenant_id} slo={alert.slo} "
            f"reason={alert.reason} window=[{alert.window_start}..{alert.window_end}]"
        )
    print(f"\nbreach: {len(alerts)} alert(s) fired — outcome-not-liveness "
          f"gate RED", file=sys.stderr)
    return 1


def _cmd_usage(args: argparse.Namespace) -> int:
    store = _load_store(args.store)
    dimensions = tuple(
        d.strip() for d in args.dimensions.split(",") if d.strip()
    )
    reporter = UsageReporter(store, dimensions=dimensions)
    rows = reporter.report()
    print(json.dumps(
        {"dimensions": list(dimensions), "rows": [r.to_dict() for r in rows]},
        indent=2,
        sort_keys=True,
    ))
    return 0


def _cmd_dashboard(args: argparse.Namespace) -> int:
    store = _load_store(args.store)
    tenants = _tenants(store, None)
    results = _evaluate(store, args.slo_dir, tenants)
    detector = BreachDetector()
    alerts = detector.detect(results)
    reporter = UsageReporter(store)
    usage_rows = reporter.report()
    dashboard = Dashboard(store, slo_results=results,
                          usage_rows=usage_rows, alerts=alerts)
    html_path = dashboard.render_html(args.html)
    json_path = dashboard.render_json(args.json)
    print(dashboard.render_terminal_report())
    print(f"\ndashboard html: {html_path}")
    print(f"dashboard json: {json_path}")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    store = _load_store(args.store)
    tenants = _tenants(store, None)
    results = _evaluate(store, args.slo_dir, tenants)
    detector = BreachDetector()
    alerts = detector.detect(results)
    reporter = UsageReporter(store)
    dashboard = Dashboard(store, slo_results=results,
                          usage_rows=reporter.report(), alerts=alerts)
    print(dashboard.render_terminal_report())
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
