#!/usr/bin/env python3
"""FinOps model chooser — offline CLI (issue #17).

Exercise the cheapest-capable model chooser end to end without any live model
provider. Mirrors the sibling-lane CLI style (self-contained, stdlib + PyYAML,
deterministic output for evidence).

Usage (from the repo root):

    python3 gateway/finops/cli.py table
    python3 gateway/finops/cli.py budgets
    python3 gateway/finops/cli.py choose --class research --complexity 25 --tenant tenant-acme
    python3 gateway/finops/cli.py choose --class security-review --complexity 10 --tenant tenant-gamma
    python3 gateway/finops/cli.py demo

``choose`` prints the routed choice as JSON; ``demo`` runs a small scripted
batch (with per-tenant budgets, health, and a JSONL metering sink) to show the
guardrail, escalation, budget and health behavior.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import budget as budget_mod
import chooser as chooser_mod
import loader as loader_mod
import metering as metering_mod
from budget import BudgetEnforcer
from chooser import Choice, ModelChooser
from loader import TierTable
from metering import JsonlMeteringSink


def _load_table() -> TierTable:
    return loader_mod.load_tier_table()


def _cmd_table(args: argparse.Namespace) -> int:
    table = _load_table()
    out: List[Dict[str, Any]] = []
    for tier in table.ladder:
        out.append(
            {
                "tier": tier.key,
                "label": tier.label,
                "modelTierHint": tier.model_tier_hint,
                "registryTier": tier.registry_tier,
                "models": [
                    {"id": m.id, "provider": m.provider, "costPerMTok": m.cost_per_mtok}
                    for m in tier.models
                ],
            }
        )
    print(json.dumps({"ladder": out}, indent=2, sort_keys=True))
    return 0


def _cmd_budgets(args: argparse.Namespace) -> int:
    path = budget_mod.BUDGETS_PATH
    enforcer = budget_mod.load_budgets(path)
    out: Dict[str, Any] = {"defaultPolicy": enforcer.default_policy.value}
    out["budgets"] = {
        tid: {
            "monthlyBudgetUsd": tb.monthly_budget_usd,
            "policy": tb.policy.value,
            "warnAtPct": tb.warn_at_pct,
            "hardCapPct": tb.hard_cap_pct,
        }
        for tid, tb in sorted(enforcer.budgets.items())
    }
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


def _parse_health(raw: Optional[str]) -> Dict[str, bool]:
    health: Dict[str, bool] = {}
    if not raw:
        return health
    for part in raw.split(","):
        mid, _, flag = part.partition("=")
        mid = mid.strip()
        if not mid:
            continue
        health[mid] = flag.strip().lower() in ("1", "true", "healthy", "yes")
    return health


def _cmd_choose(args: argparse.Namespace) -> int:
    table = _load_table()
    enforcer: Optional[BudgetEnforcer] = None
    if args.budget_config:
        enforcer = budget_mod.load_budgets(Path(args.budget_config))
    sink = JsonlMeteringSink(Path(args.meter)) if args.meter else metering_mod.NoopMeteringSink()
    health = _parse_health(args.health)
    chooser = ModelChooser(
        table=table,
        budget_enforcer=enforcer,
        sink=sink,
        health=health or None,
    )
    try:
        choice: Choice = chooser.choose(
            task_class=args.task_class,
            tenant_id=args.tenant,
            agent_id=args.agent,
            complexity=args.complexity,
            prompt=args.prompt,
        )
    except (loader_mod.ValidationError, chooser_mod.ChooserError) as exc:
        print(json.dumps({"error": str(exc)}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(choice.to_dict(), indent=2, sort_keys=True))
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    table = _load_table()
    enforcer = budget_mod.load_budgets()
    meter_path = Path(args.meter)
    sink = JsonlMeteringSink(meter_path)
    health = _parse_health(args.health)
    chooser = ModelChooser(
        table=table,
        budget_enforcer=enforcer,
        sink=sink,
        health=health or None,
    )
    script: List[Dict[str, Any]] = [
        {"task_class": "code-author", "tenant_id": "tenant-acme", "complexity": 15},
        {"task_class": "research", "tenant_id": "tenant-acme", "complexity": 55},
        {"task_class": "security-review", "tenant_id": "tenant-gamma", "complexity": 5},
        {"task_class": "security-review", "tenant_id": "tenant-gamma", "complexity": 95},
        {"task_class": "research", "tenant_id": "tenant-gamma", "complexity": 85},
        {"task_class": "finops-meter", "tenant_id": "tenant-beta", "complexity": 10},
    ]
    results: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []
    for call in script:
        try:
            choice = chooser.choose(
                task_class=call["task_class"],
                tenant_id=call["tenant_id"],
                complexity=call["complexity"],
            )
            enforcer.commit(call["tenant_id"], choice.estimated_cost_usd)
            results.append(choice.to_dict())
        except budget_mod.BudgetBlocked as exc:
            blocked.append({"tenant_id": call["tenant_id"], "error": str(exc)})
    # Simulate realized spend pushing tenant-beta (policy=stop, $50) past its
    # 80% warn threshold, then show the next call being budget-blocked.
    enforcer.commit("tenant-beta", 41.0)  # 82% of the $50 budget
    try:
        chooser.choose(
            task_class="research",
            tenant_id="tenant-beta",
            complexity=80,
        )
    except budget_mod.BudgetBlocked as exc:
        blocked.append({"tenant_id": "tenant-beta", "error": str(exc)})
    print(json.dumps({"choices": results, "blocked": blocked}, indent=2, sort_keys=True))
    print(f"metering records written to {meter_path} "
          f"({len(sink.read_records())} line(s))", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gateway/finops/cli.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("table", help="print the L0/L1/L2 model-tier table")

    sub.add_parser("budgets", help="print the seeded per-tenant budgets")

    p_choose = sub.add_parser("choose", help="route one task to a model")
    p_choose.add_argument("--task-class", required=True)
    p_choose.add_argument("--tenant", default="system")
    p_choose.add_argument("--agent", default="anonymous")
    p_choose.add_argument("--complexity", type=float, default=None)
    p_choose.add_argument("--prompt", default=None)
    p_choose.add_argument("--budget-config", default=None)
    p_choose.add_argument("--health", default=None)
    p_choose.add_argument("--meter", default=None)

    p_demo = sub.add_parser("demo", help="run a scripted end-to-end demo")
    p_demo.add_argument("--health", default=None)
    p_demo.add_argument(
        "--meter",
        default=None,
        help="JSONL metering path (default: a temp file)",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "table":
        return _cmd_table(args)
    if args.command == "budgets":
        return _cmd_budgets(args)
    if args.command == "choose":
        return _cmd_choose(args)
    if args.command == "demo":
        if args.meter is None:
            fd, name = tempfile.mkstemp(prefix="ao17-demo-meter-", suffix=".jsonl")
            import os

            os.close(fd)
            args.meter = name
        return _cmd_demo(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
