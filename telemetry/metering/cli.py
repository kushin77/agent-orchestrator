"""telemetry/metering — offline operator CLI (issue #33).

Evidence/demo surface for the usage metering + cost engine.  Fully offline
(stdlib + PyYAML): no network, no server.  Run from the repo root:

    python3 -m telemetry.metering.cli estimate --provider gemini \
        --model gemini-2.5-pro --input 250000 --output 5000
    python3 -m telemetry.metering.cli ingest --feed records.jsonl \
        --store /tmp/ao33-usage.jsonl
    python3 -m telemetry.metering.cli report --store /tmp/ao33-usage.jsonl
    python3 -m telemetry.metering.cli demo
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from telemetry.metering.budget import (
    DECISION_BLOCK,
    DailyTokenBudget,
    DEFAULT_BUDGET_CONFIG,
    load_budget_config,
)
from telemetry.metering.intake import MeteringIntake
from telemetry.metering.ratecards import DEFAULT_RATE_CARD_DIR, RateCardStore
from telemetry.metering.report import UsageReporter
from telemetry.metering.store import JsonlUsageStore, MemoryUsageStore

EXIT_OK = 0
EXIT_UNMETERED = 2


def _rate_dir(args: argparse.Namespace) -> Path:
    """The --rate-dir override, or the shipped default rate-card directory."""
    value = getattr(args, "rate_dir", None)
    return Path(value) if value else DEFAULT_RATE_CARD_DIR


def _load_sources(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{line_no}: invalid JSON: {exc}") from exc
            records.append(payload)
    return records


def cmd_estimate(args: argparse.Namespace) -> int:
    """Price one hypothetical call from the rate cards."""
    store = RateCardStore.load_dir(_rate_dir(args))
    estimate = store.estimate(
        args.provider, args.model, args.input_tokens, args.output_tokens
    )
    if estimate is None:
        print(
            f"UNMETERED: no rate card for {args.provider}/{args.model} "
            "(never priced as zero)"
        )
        return EXIT_UNMETERED
    print(json.dumps(estimate.to_dict(), indent=2))
    return EXIT_OK


def cmd_cards(args: argparse.Namespace) -> int:
    """Dump the loaded multi-provider rate cards."""
    store = RateCardStore.load_dir(_rate_dir(args))
    print(
        json.dumps(
            {"providers": store.providers(), "fingerprint": store.fingerprint()},
            indent=2,
        )
    )
    for provider in store.providers():
        card = store.card(provider)
        if card is None:
            continue
        print(f"\n[{provider}] ({card.source})")
        for model, entry in card.entries.items():
            standard = entry.standard
            line = (
                f"  {model}: in ${standard.input_usd_per_million}/MTok, "
                f"out ${standard.output_usd_per_million}/MTok"
            )
            if entry.long_context is not None:
                line += (
                    f" | long-context in ${entry.long_context.input_usd_per_million}"
                    f"/MTok, out ${entry.long_context.output_usd_per_million}/MTok "
                    f"> {entry.long_context_threshold_tokens} prompt tokens"
                )
            if entry.local:
                line += " [local: explicit $0]"
            print(line)
    return EXIT_OK


def _make_store(args: argparse.Namespace) -> JsonlUsageStore:
    if not args.store:
        raise SystemExit("--store <path> is required for this command")
    return JsonlUsageStore(Path(args.store))


def cmd_ingest(args: argparse.Namespace) -> int:
    """Ingest a JSONL feed of merged model-call records (idempotent)."""
    sources = _load_sources(Path(args.feed))
    store = _make_store(args)
    intake = MeteringIntake(store=store)
    summary = intake.ingest_many(sources)
    print(json.dumps(summary.to_dict(), sort_keys=True))
    print(f"store: {store.path} ({store.count()} records)")
    if summary.unmetered:
        print(
            f"WARNING: {summary.unmetered} unmetered call(s) flagged "
            "(never priced as zero)"
        )
    return EXIT_OK


def cmd_report(args: argparse.Namespace) -> int:
    """Cost-attribution report over a usage store."""
    store = _make_store(args)
    reporter = UsageReporter(store)
    if args.tenant:
        print(f"== tenant {args.tenant} ==")
        print(json.dumps(reporter.totals(tenant_id=args.tenant).to_dict(), indent=2))
        print("\n-- daily --")
        daily = reporter.tenant_daily(args.tenant)
        print(json.dumps({k: v.to_dict() for k, v in sorted(daily.items())}, indent=2))
        return EXIT_OK
    print("== totals ==")
    print(json.dumps(reporter.totals().to_dict(), indent=2))
    print("\n== per-tenant (monthly) ==")
    by_tenant = reporter.by_tenant()
    print(json.dumps({k: v.to_dict() for k, v in sorted(by_tenant.items())}, indent=2))
    print("\n== per-agent (tenant::agent, monthly) ==")
    by_agent = reporter.by_agent()
    print(json.dumps({k: v.to_dict() for k, v in by_agent.items()}, indent=2))
    print("\n== provider mix ==")
    print(json.dumps(reporter.provider_mix(), indent=2))
    print("\n== billing summary ==")
    print(json.dumps(reporter.billing_summary(), indent=2))
    return EXIT_OK


def cmd_budget(args: argparse.Namespace) -> int:
    """Check the daily token budget for a tenant (observe -> enforce)."""
    store = _make_store(args)
    config = Path(args.config) if getattr(args, "config", None) else DEFAULT_BUDGET_CONFIG
    policies = load_budget_config(config)
    budget = DailyTokenBudget(UsageReporter(store), policies=policies)
    verdict = budget.check(
        args.tenant, requested_tokens=args.requested_tokens, day=args.day
    )
    print(json.dumps(verdict.to_dict(), sort_keys=True))
    if verdict.decision == DECISION_BLOCK:
        return 1
    return EXIT_OK  # observe would_block is advisory only


def cmd_demo(args: argparse.Namespace) -> int:
    """Offline end-to-end evidence walk-through (intake -> store -> report)."""
    store = MemoryUsageStore()
    intake = MeteringIntake(store=store)

    feed = [
        # (1) ModelCallEvent: real Gemini call with usage -> rate-card priced.
        {
            "source": "model_call_event",
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "tenant_id": "acme",
            "agent_id": "coder-1",
            "logical_key": "MED",
            "status": "success",
            "usage": {"input_tokens": 1000, "output_tokens": 500, "tokens": 1500},
            "latency_ms": 320.0,
            "ts": "2026-09-08T10:00:00Z",
        },
        # (2) ModelCallEvent: unknown model -> unmetered (fail closed).
        {
            "source": "model_call_event",
            "provider": "futureco",
            "model": "future-model-x",
            "tenant_id": "acme",
            "agent_id": "coder-2",
            "logical_key": "LOW",
            "status": "success",
            "usage": {"input_tokens": 500, "output_tokens": 100, "tokens": 600},
            "ts": "2026-09-08T10:05:00Z",
        },
        # (3) CallRecord (gateway finops) -> attached estimate attributed.
        {
            "source": "call_record",
            "tenant_id": "globex",
            "agent_id": "arch-1",
            "task_class": "research",
            "tier": "L1",
            "model": "deepseek-reasoner",
            "provider": "deepseek",
            "estimated_cost_usd": 0.0042,
            "budget_action": "allow",
            "timestamp": "2026-09-08T11:00:00Z",
        },
        # (4) MeteringRecord cache hit -> explicit zero-cost.
        {
            "source": "metering_record",
            "tenant": "acme",
            "agent": "coder-1",
            "model_tier": "LOW",
            "request_id": "req-cache-1",
            "outcome": "cache_hit",
            "cached": True,
            "zero_cost": True,
            "input_tokens": 0,
            "output_tokens": 0,
            "at": "2026-09-08T12:00:00Z",
        },
    ]
    summary = intake.ingest_many(feed)
    print("== ingest summary ==")
    print(json.dumps(summary.to_dict(), indent=2))
    for record in store.read():
        flagged = " [UNMETERED]" if record.unmetered_reason else ""
        print(
            f"  {record.ts} {record.tenant_id}/{record.agent_id} "
            f"{record.provider}/{record.model} in={record.input_tokens} "
            f"out={record.output_tokens} cost={record.cost_usd} "
            f"source={record.cost_source}{flagged}"
        )

    reporter = UsageReporter(store)
    print("\n== totals ==")
    print(json.dumps(reporter.totals().to_dict(), indent=2))
    print("\n== provider mix ==")
    print(json.dumps(reporter.provider_mix(), indent=2))

    print("\n== budget (enforce tenant globex) ==")
    from telemetry.metering.budget import BudgetPolicy

    budget = DailyTokenBudget(
        reporter,
        policies={"globex": BudgetPolicy("globex", 1_000_000, "enforce")},
    )
    print(json.dumps(budget.check("globex", requested_tokens=5_000).to_dict(), indent=2))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telemetry.metering.cli",
        description="Usage metering + cost engine (issue #33) — offline CLI.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_est = sub.add_parser("estimate", help="price one call from the rate cards")
    p_est.add_argument("--provider", required=True)
    p_est.add_argument("--model", required=True)
    p_est.add_argument("--input", dest="input_tokens", type=int, default=0)
    p_est.add_argument("--output", dest="output_tokens", type=int, default=0)
    p_est.add_argument("--rate-dir", default=None)
    p_est.set_defaults(func=cmd_estimate)

    p_cards = sub.add_parser("cards", help="dump the loaded rate cards")
    p_cards.add_argument("--rate-dir", default=None)
    p_cards.set_defaults(func=cmd_cards)

    p_ingest = sub.add_parser("ingest", help="ingest a JSONL feed (idempotent)")
    p_ingest.add_argument("--feed", required=True)
    p_ingest.add_argument("--store", required=True)
    p_ingest.set_defaults(func=cmd_ingest)

    p_report = sub.add_parser("report", help="cost-attribution report")
    p_report.add_argument("--store", required=True)
    p_report.add_argument("--tenant", default=None)
    p_report.set_defaults(func=cmd_report)

    p_budget = sub.add_parser("budget", help="daily token budget check")
    p_budget.add_argument("--tenant", required=True)
    p_budget.add_argument("--requested-tokens", type=int, default=0)
    p_budget.add_argument("--day", default=None)
    p_budget.add_argument("--store", required=True)
    p_budget.add_argument("--config", default=None)
    p_budget.set_defaults(func=cmd_budget)

    p_demo = sub.add_parser("demo", help="offline end-to-end evidence walk-through")
    p_demo.set_defaults(func=cmd_demo)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
