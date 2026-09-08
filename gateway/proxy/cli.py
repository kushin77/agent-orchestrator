#!/usr/bin/env python3
"""Gateway proxy offline CLI (issue #16) — evidence and demos.

Runs fully offline: the real merged sibling modules are composed by
``wiring.build_real_gateway`` and every provider call is served by the
scriptable transport rig (no sockets).

Commands:

    routes                         Show the routing policy table.
    route --task-type T [--agent A] [--tenant T] [--health k=v ...]
                                   Resolve one route decision.
    dispatch --task-type T --agent A [--tenant T]
                                   Dispatch one task (typed success).
    demo [--meter PATH]            End-to-end walkthrough incl. negatives and
                                   streaming; writes a JSONL call-record audit.

Run from the repo root (``gateway/`` is put on ``sys.path`` for you):

    python3 gateway/proxy/cli.py demo
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the proxy package (and its gateway siblings) importable when run as a
# script from the repo root: gateway/proxy -> gateway.
_GATEWAY_DIR = Path(__file__).resolve().parent.parent
if str(_GATEWAY_DIR) not in sys.path:
    sys.path.insert(0, str(_GATEWAY_DIR))

from proxy import model  # noqa: E402
from proxy.model import TaskRequest  # noqa: E402
from proxy.router import load_routing_config  # noqa: E402
from proxy.sinks import JsonlCallRecordSink  # noqa: E402
from proxy.wiring import build_real_gateway  # noqa: E402

# Canned schema-valid typed outputs per published task type (offline doubles).
CANNED_OUTPUTS: dict[str, str] = {
    "classify-route": json.dumps(
        {
            "route": "support",
            "priority": "high",
            "confidence": 0.92,
            "reasoning": "Customer reported an outage on the billing API.",
        }
    ),
    "code-review-verdict": json.dumps(
        {
            "verdict": "approve",
            "blockingFindings": [],
            "summary": "The diff is small, focused and correct.",
        }
    ),
    "summarize": json.dumps(
        {
            "summary": "The team agreed to ship the current milestone.",
            "keyPoints": ["Ship current milestone", "Follow-up on flaky test"],
            "actionItems": ["alice: merge the release PR"],
            "wordCount": 42,
        }
    ),
}

# Render variables per task type (the prompt module's {{...}} placeholders).
DEFAULT_INPUTS: dict[str, dict] = {
    "classify-route": {"input": "billing outage on the api"},
    "code-review-verdict": {"diff": "+raise_on_invalid()", "context": "small PR"},
    "summarize": {"thread": "long planning thread text"},
}


def _parse_health(items: list[str]) -> dict[str, bool]:
    """Parse ``k=v`` pairs into a provider-health deny map (k=false unhealthy)."""
    health: dict[str, bool] = {}
    for item in items:
        key, _, value = item.partition("=")
        health[key.strip()] = value.strip().lower() not in ("false", "0", "no")
    return health


def cmd_routes(_: argparse.Namespace) -> int:
    config = load_routing_config()
    print("gateway/proxy routing policy")
    print("----------------------------")
    for task_type in sorted(config.routes):
        route = config.route_for(task_type)
        print(f"  {task_type:20s} -> capability={route.capability:14s} "
              f"taskClass={route.task_class}")
    print("\nFinOps ladder tier -> registry tier:")
    for ladder, registry in config.tier_map.items():
        print(f"  {ladder} -> {registry}")
    print("\nProvider fallback chains (primary -> fallback -> local):")
    for tier, chain in config.provider_chains.items():
        print(f"  {tier:4s}: {', '.join(chain)}")
    return 0


def cmd_route(args: argparse.Namespace) -> int:
    wired = build_real_gateway(health=_parse_health(args.health or []))
    agent = wired.agent_resolver.resolve(args.tenant, args.agent)
    task = wired.task_resolver.resolve(
        args.task_type, dict(DEFAULT_INPUTS.get(args.task_type, {}))
    )
    decision = wired.gateway.router.route(
        task, agent, TaskRequest(tenant_id=args.tenant, task_type=args.task_type),
        chooser=wired.chooser_adapter, health=wired.gateway.health,
    )
    print(f"task        : {decision.task_type}")
    print(f"agent       : {args.agent} (profile {agent.profile_id})")
    print(f"capability  : {decision.capability}")
    print(f"task class  : {decision.task_class}")
    print(f"ladder tier : {decision.ladder_tier} -> registry tier {decision.registry_tier}")
    print(f"cost (USD)  : {decision.estimated_cost_usd:.6f}  budget={decision.budget_action}")
    print("candidates (healthy, ordered):")
    for candidate in decision.candidates:
        print(f"  - {candidate.role:8s} {candidate.provider} @ {candidate.registry_tier}")
    return 0


def cmd_dispatch(args: argparse.Namespace) -> int:
    wired = build_real_gateway(health=_parse_health(args.health or []))
    content = CANNED_OUTPUTS[args.task_type]
    wired.rig.script_success("deepseek", content)
    request = TaskRequest(tenant_id=args.tenant, task_type=args.task_type,
                          input=DEFAULT_INPUTS[args.task_type])
    result = wired.gateway.dispatch(args.agent, request)
    _print_result(result)
    return 0 if result.served() else 1


def _print_result(result: model.TaskResult) -> None:
    print(f"request     : {result.request_id}")
    print(f"task        : {result.task_type}  agent={result.agent_id} tenant={result.tenant_id}")
    print(f"outcome     : {result.outcome}")
    if result.served():
        print(f"typed output: {json.dumps(result.content, sort_keys=True)}")
    if result.provider:
        print(f"provider    : {result.provider}  model={result.model}  tier={result.tier}")
    if result.error:
        print(f"error       : {result.error}")
    if result.record is not None:
        rec = result.record.to_dict()
        print(f"call record : requestId={rec['requestId']} outcome={rec['outcome']} "
              f"provider={rec['provider']} tokens={rec['tokens']} "
              f"latencyMs={round(rec['latencyMs'], 3)}")


def cmd_demo(args: argparse.Namespace) -> int:
    print("=== gateway/proxy offline demo (issue #16) ===")
    cmd_routes(argparse.Namespace())

    # --- 1. typed success through the real modules -------------------------- #
    print("\n--- 1. dispatch: classify-route -> orchestrator (typed success) ---")
    wired = build_real_gateway(audit_sink=JsonlCallRecordSink(Path(args.meter)))
    wired.rig.script_success("deepseek", CANNED_OUTPUTS["classify-route"])
    success = wired.gateway.dispatch(
        "orchestrator",
        TaskRequest(tenant_id="acme", task_type="classify-route",
                    input=DEFAULT_INPUTS["classify-route"]),
    )
    _print_result(success)

    # --- 2. health-driven fallback (primary unhealthy -> openai) ------------ #
    print("\n--- 2. fallback: primary unhealthy -> next healthy (openai) ---")
    wired_fb = build_real_gateway(health={"deepseek": False},
                                  audit_sink=JsonlCallRecordSink(Path(args.meter)))
    wired_fb.rig.script_success("openai", CANNED_OUTPUTS["classify-route"])
    fallback = wired_fb.gateway.dispatch(
        "orchestrator",
        TaskRequest(tenant_id="acme", task_type="classify-route",
                    input=DEFAULT_INPUTS["classify-route"]),
    )
    _print_result(fallback)

    # --- 3. negative: all providers unhealthy -> explicit failure ----------- #
    print("\n--- 3. negative: all providers unhealthy -> no_healthy_route ---")
    wired_down = build_real_gateway(
        health={"deepseek": False, "openai": False, "ollama": False,
                "anthropic": False},
        audit_sink=JsonlCallRecordSink(Path(args.meter)),
    )
    down = wired_down.gateway.dispatch(
        "orchestrator",
        TaskRequest(tenant_id="acme", task_type="classify-route",
                    input=DEFAULT_INPUTS["classify-route"]),
    )
    _print_result(down)

    # --- 4. negative: budget exhausted -> blocked (never silent) ------------ #
    print("\n--- 4. negative: budget exhausted -> blocked (backpressure) ---")
    from limits.budget import BudgetController, BudgetMode, BudgetPolicy
    from limits.limiter import LimitsEngine

    enforce = LimitsEngine(
        budget=BudgetController(
            default_policy=BudgetPolicy(cap_tokens=5, mode=BudgetMode.ENFORCE)
        )
    )
    wired_budget = build_real_gateway(limits_engine=enforce,
                                      audit_sink=JsonlCallRecordSink(Path(args.meter)))
    wired_budget.rig.script_success("deepseek", CANNED_OUTPUTS["classify-route"])
    blocked = wired_budget.gateway.dispatch(
        "orchestrator",
        TaskRequest(tenant_id="acme", task_type="classify-route",
                    input=DEFAULT_INPUTS["classify-route"]),
    )
    _print_result(blocked)

    # --- 5. negative: invalid typed output -> cannot_assess ----------------- #
    print("\n--- 5. negative: schema-invalid output -> cannot_assess ---")
    wired_bad = build_real_gateway(audit_sink=JsonlCallRecordSink(Path(args.meter)))
    wired_bad.rig.set_default("this is not the schema-shaped JSON {{{")
    cannot = wired_bad.gateway.dispatch(
        "orchestrator",
        TaskRequest(tenant_id="acme", task_type="classify-route",
                    input=DEFAULT_INPUTS["classify-route"]),
    )
    _print_result(cannot)

    # --- 6. streaming semantics --------------------------------------------- #
    print("\n--- 6. streaming: incremental events then the result ---")
    events = [
        e for e in wired.gateway.dispatch_stream(
            "orchestrator",
            TaskRequest(tenant_id="acme", task_type="classify-route",
                        input=DEFAULT_INPUTS["classify-route"]),
        )
        if not hasattr(e, "outcome")
    ]
    print("stages: " + ", ".join(event.stage for event in events))
    print(f"\ncall-record audit written to: {args.meter}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gateway-proxy", description="gateway/proxy offline CLI (issue #16)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("routes", help="show the routing policy table")
    p_route = sub.add_parser("route", help="resolve one route decision")
    p_route.add_argument("--task-type", required=True)
    p_route.add_argument("--agent", default="orchestrator")
    p_route.add_argument("--tenant", default="acme")
    p_route.add_argument("--health", action="append", default=[])
    p_dispatch = sub.add_parser("dispatch", help="dispatch one task (typed)")
    p_dispatch.add_argument("--task-type", required=True,
                            choices=sorted(CANNED_OUTPUTS))
    p_dispatch.add_argument("--agent", default="orchestrator")
    p_dispatch.add_argument("--tenant", default="acme")
    p_dispatch.add_argument("--health", action="append", default=[])
    p_demo = sub.add_parser("demo", help="end-to-end offline walkthrough")
    p_demo.add_argument("--meter", default="/tmp/ao16-gateway-call-records.jsonl")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "routes":
        return cmd_routes(args)
    if args.command == "route":
        return cmd_route(args)
    if args.command == "dispatch":
        return cmd_dispatch(args)
    if args.command == "demo":
        return cmd_demo(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
