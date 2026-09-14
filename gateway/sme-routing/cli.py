#!/usr/bin/env python3
"""Offline CLI for the SME-squad routing surface (issue #149).

Runs directly -- no network, no server, no clock:

    python3 gateway/sme-routing/cli.py validate
    python3 gateway/sme-routing/cli.py vocabulary
    python3 gateway/sme-routing/cli.py sme --text "rotate the terraform state bucket"
    python3 gateway/sme-routing/cli.py route --type doc_update --text "README wording"
    python3 gateway/sme-routing/cli.py dispatch --text "refactor the loader" --tokens 900
    python3 gateway/sme-routing/cli.py dispatch --text "README wording" --tokens 9000 --no-escalate
    python3 gateway/sme-routing/cli.py demo
    python3 gateway/sme-routing/cli.py --policies /tmp/other-policies validate

EXIT-CODE CONTRACT (tri-state, GR-12 / no-false-green):

  0 OK            -- the policy validates, the task routed, or the dispatch was
                     accepted (including the terminal `human_advisor` outcome:
                     the escalation engine reaching its declared human hand-off
                     is correct behaviour, not a defect -- `status=` carries the
                     real answer for a machine reader).
  1 NOT-OK        -- a policy invariant is violated, a dispatch is `refused`
                     under `--no-escalate` (a cap was breached and no
                     escalation was authorised), or a demo expectation broke.
  2 CANNOT-ASSESS -- the policy is missing/unparseable/schema-invalid, or the
                     task cannot be assessed at all (unknown key, out-of-range
                     complexity). Never reported as a pass.

The directory is hyphenated so it cannot be an importable package; like
``control-plane/instructions`` this entry point arranges ``sys.path`` itself so
it runs from any cwd.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from router import (  # noqa: E402  (path arranged above)
    DISPATCHED,
    HUMAN_ADVISOR,
    REFUSED,
    Outcome,
    Router,
    TaskInvalid,
    load_router,
)
from smeroute_config import (  # noqa: E402  (path arranged above)
    PolicyError,
    PolicyInvariantViolated,
    PolicyMalformed,
    PolicyUnavailable,
)

OK = 0
NOT_OK = 1
CANNOT_ASSESS = 2


def _emit(payload: Mapping[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(
        "decision: route={route} tier={tier} path_mode={path_mode} "
        "chain={chain}".format(
            route=payload["route"],
            tier=payload["tier"],
            path_mode=payload["path_mode"],
            chain=",".join(payload["chain"]),
        )
    )
    print(
        "sme: domain={domain} sme={sme} squad={squad} module={module}".format(
            domain=payload["domain"],
            sme=payload["sme"],
            squad=payload["squad"],
            module=payload["module"] or "-",
        )
    )
    caps = payload["caps"]
    print(
        "caps: max_tokens={max_tokens} timeout_seconds={timeout_seconds} "
        "fallback={fallback} models={models}".format(
            max_tokens=caps["max_tokens"],
            timeout_seconds=caps["timeout_seconds"],
            fallback=caps["fallback"] if caps["fallback"] else "null (human/advisor)",
            models=",".join(caps["models"]),
        )
    )
    print(f"fail_safe: {'yes' if payload['fail_safe'] else 'no'}")
    print(f"reason: {payload['reason']}")


def _emit_outcome(outcome: Outcome, as_json: bool) -> None:
    if as_json:
        print(json.dumps(outcome.as_dict(), indent=2, sort_keys=True))
        return
    print(f"status={outcome.status} tier={outcome.tier} escalated={outcome.escalated}")
    for attempt in outcome.attempts:
        accepted = "yes" if attempt.accepted else "no"
        print(f"  attempt: tier={attempt.tier} accepted={accepted} reason={attempt.reason}")
    print(f"reason: {outcome.reason}")
    _emit(outcome.decision.as_dict(), False)


def _task_from_args(args: argparse.Namespace) -> Dict[str, Any]:
    task: Dict[str, Any] = {"text": args.text or ""}
    if args.task_id:
        task["id"] = args.task_id
    if args.type:
        task["type"] = args.type
    if args.complexity is not None:
        task["complexity"] = args.complexity
    if args.risk:
        task["risk"] = args.risk
    if args.tokens is not None:
        task["tokens"] = args.tokens
    if args.timeout is not None:
        task["timeout_seconds"] = args.timeout
    return task


def cmd_validate(router: Router, args: argparse.Namespace) -> int:
    bundle = router.bundle
    print(f"policies: {bundle.policies_dir}")
    print(
        "  capability-registry: {v} -- {agents} agent role(s), {fleets} worker "
        "fleet(s), {domains} SME domain(s), {squads} squad(s)".format(
            v=bundle.capability_registry.version,
            agents=len(bundle.capability_registry.agents),
            fleets=len(bundle.capability_registry.worker_fleet),
            domains=len(bundle.capability_registry.sme_domains),
            squads=len(bundle.capability_registry.squads),
        )
    )
    print(
        "  route-policy:       {v} -- routes {routes}, fail-safe unknown_task_type="
        "{fail_safe}".format(
            v=bundle.route_policy.version,
            routes=sorted(bundle.route_policy.routes),
            fail_safe=bundle.route_policy.normalised_defaults.get("unknown-task-type"),
        )
    )
    print(
        "  tier-policy:        {v} -- tiers {tiers} in escalation order, terminal="
        "{terminal}".format(
            v=bundle.tier_policy.version,
            tiers=list(bundle.tier_policy.tier_order),
            terminal=bundle.tier_policy.terminal_tier,
        )
    )
    print("validate: OK -- schema valid and every declared invariant holds")
    return OK


def cmd_vocabulary(router: Router, args: argparse.Namespace) -> int:
    bundle = router.bundle
    for name in bundle.tier_policy.tier_order:
        tier = bundle.tier_policy.tier(name)
        print(
            f"tier {name}: models={','.join(tier.models)} "
            f"max_tokens={tier.max_tokens} timeout_seconds={tier.timeout_seconds} "
            f"fallback={tier.fallback if tier.fallback else 'null'}"
        )
    for name in sorted(bundle.route_policy.routes):
        route = bundle.route_policy.routes[name]
        print(
            f"route {name}: path_mode={route.path_mode} tier={route.model_tier} "
            f"chain={','.join(route.agents)} workers={','.join(route.worker_types)}"
        )
    for name, domain in bundle.capability_registry.sme_domains.items():
        print(
            f"domain {name}: sme={domain.sme} module={domain.module or '-'} "
            f"labels={len(domain.labels)}"
        )
    for name, squad in bundle.capability_registry.squads.items():
        print(f"squad {name}: lens={squad.lens} keywords={len(squad.keywords)}")
    print(f"squad_default: {bundle.capability_registry.squad_default}")
    print(f"sme_default: {bundle.capability_registry.sme_default}")
    return OK


def cmd_sme(router: Router, args: argparse.Namespace) -> int:
    classified = router.classify(args.text or "")
    print(
        "sme: domain={domain} sme={sme} squad={squad} module={module}".format(
            domain=classified["domain"],
            sme=classified["sme"],
            squad=classified["squad"],
            module=classified["module"] or "-",
        )
    )
    return OK


def cmd_route(router: Router, args: argparse.Namespace) -> int:
    decision = router.route(_task_from_args(args))
    _emit(decision.as_dict(), args.json)
    return OK


def cmd_dispatch(router: Router, args: argparse.Namespace) -> int:
    outcome = router.dispatch(
        _task_from_args(args), escalate=not args.no_escalate
    )
    _emit_outcome(outcome, args.json)
    if outcome.status == REFUSED:
        return NOT_OK
    return OK


# Each demo: (label, task, escalate, expected) where expected holds the fields
# that must match the outcome/decision exactly. `demo` is the executable form of
# the issue's definition of done -- it fails (rc 1) if any path stops being
# demonstrated, so it cannot rot into a printed claim.
DEMO_CASES: Tuple[Tuple[str, Dict[str, Any], bool, Dict[str, Any]], ...] = (
    (
        "fast-path dispatch",
        {"id": "demo-fast", "type": "doc_update", "text": "tighten the README wording",
         "tokens": 40},
        True,
        {"status": DISPATCHED, "route": "fast", "tier": "flash",
         "chain": ("executor",)},
    ),
    (
        "deep-path dispatch",
        {"id": "demo-deep", "text": "refactor the loader across multi-file modules",
         "tokens": 900},
        True,
        {"status": DISPATCHED, "route": "deep", "tier": "pro",
         "chain": ("planner", "executor", "verifier")},
    ),
    (
        "strict-path dispatch",
        {"id": "demo-strict", "text": "rotate the production secret credential"},
        True,
        {"status": DISPATCHED, "route": "strict", "tier": "auditor",
         "chain": ("planner", "executor", "verifier", "critic")},
    ),
    (
        "unknown task type -> deep fail-safe",
        {"id": "demo-unknown", "type": "teleport", "text": "do the undeclared thing",
         "tokens": 10},
        True,
        {"status": DISPATCHED, "route": "deep", "tier": "pro", "fail_safe": True},
    ),
    (
        "token-cap breach refused (no escalation authorised)",
        {"id": "demo-cap", "type": "doc_update", "text": "tighten the README wording",
         "tokens": 9000},
        False,
        {"status": REFUSED, "tier": "flash"},
    ),
    (
        "token-cap breach escalated to a higher tier",
        {"id": "demo-escalate", "type": "doc_update",
         "text": "tighten the README wording", "tokens": 9000},
        True,
        {"status": DISPATCHED, "tier": "pro", "escalated": True},
    ),
    (
        "escalation terminal: human/advisor hand-off",
        {"id": "demo-terminal", "type": "doc_update",
         "text": "tighten the README wording", "tokens": 40000},
        True,
        {"status": HUMAN_ADVISOR, "tier": "auditor", "attempt_tiers": 3},
    ),
)


def cmd_demo(router: Router, args: argparse.Namespace) -> int:
    as_json = bool(getattr(args, "json", False))
    failures: List[str] = []
    payload: List[Dict[str, Any]] = []
    for label, task, escalate, expected in DEMO_CASES:
        try:
            outcome = router.dispatch(task, escalate=escalate)
        except (PolicyError, TaskInvalid) as exc:
            failures.append(f"{label}: {exc}")
            continue
        actual: Dict[str, Any] = {
            "status": outcome.status,
            "route": outcome.decision.route,
            "tier": outcome.tier,
            "chain": outcome.decision.chain,
            "fail_safe": outcome.decision.fail_safe,
            "escalated": outcome.escalated,
            "attempt_tiers": len(outcome.attempts),
        }
        mismatches = {
            key: (expected[key], actual.get(key))
            for key in expected
            if actual.get(key) != expected[key]
        }
        payload.append(
            {
                "label": label,
                "expected": {k: str(v) for k, v in expected.items()},
                "actual": {k: str(v) for k, v in actual.items()},
                "ok": not mismatches,
            }
        )
        if mismatches:
            detail = ", ".join(
                f"{key}: expected {want!r}, got {got!r}"
                for key, (want, got) in mismatches.items()
            )
            failures.append(f"{label}: {detail}")
        elif not as_json:
            print(f"  OK    {label}: {actual['status']} route={actual['route']} "
                  f"tier={actual['tier']} chain={','.join(actual['chain'])}")

    if as_json:
        print(json.dumps({"cases": payload, "ok": not failures}, indent=2,
                         sort_keys=True))
    if failures:
        for failure in failures:
            print(f"  FAIL  {failure}", file=sys.stderr)
        print(f"demo: NOT-OK -- {len(failures)} expectation(s) broke", file=sys.stderr)
        return NOT_OK
    if not as_json:
        print(f"demo: OK -- {len(DEMO_CASES)} dispatch path(s) demonstrated")
    return OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sme-routing",
        description="SME-squad routing + capability/route/tier FinOps surface.",
    )
    parser.add_argument("--policies", metavar="DIR",
                        help="policy directory (default: gateway/sme-routing/policies)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="load, schema-validate and invariant-check")
    sub.add_parser("vocabulary", help="print the declared tiers/routes/domains")
    demo = sub.add_parser(
        "demo", help="demonstrate every dispatch path (fails if one rots)"
    )
    demo.add_argument("--json", action="store_true", help="emit JSON")

    for name, help_text in (
        ("route", "route a task to a chain + tier"),
        ("dispatch", "route a task and enforce the tier's caps"),
    ):
        child = sub.add_parser(name, help=help_text)
        child.add_argument("--id", dest="task_id", help="task id (echoed back)")
        child.add_argument("--type", help="declared task type")
        child.add_argument("--text", help="task text (keyword classification input)")
        child.add_argument("--complexity", type=int, help="complexity score 0..100")
        child.add_argument("--risk", choices=("low", "high"), help="declared risk")
        child.add_argument("--tokens", type=int, help="requested output token budget")
        child.add_argument("--timeout", type=float, help="requested timeout (seconds)")
        child.add_argument("--json", action="store_true", help="emit JSON")
        if name == "dispatch":
            child.add_argument(
                "--no-escalate",
                action="store_true",
                help="refuse on a cap breach instead of climbing the ladder",
            )

    sme = sub.add_parser("sme", help="classify text to domain/sme/squad/module")
    sme.add_argument("--text", help="text to classify")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        router = load_router(args.policies)
    except PolicyInvariantViolated as exc:
        print(f"sme-routing: NOT-OK -- {exc}", file=sys.stderr)
        return NOT_OK
    except (PolicyUnavailable, PolicyMalformed) as exc:
        print(f"sme-routing: CANNOT-ASSESS -- {exc}", file=sys.stderr)
        return CANNOT_ASSESS
    except PolicyError as exc:  # pragma: no cover - defensive base catch
        print(f"sme-routing: CANNOT-ASSESS -- {exc}", file=sys.stderr)
        return CANNOT_ASSESS

    try:
        if args.command == "validate":
            return cmd_validate(router, args)
        if args.command == "vocabulary":
            return cmd_vocabulary(router, args)
        if args.command == "demo":
            return cmd_demo(router, args)
        if args.command == "route":
            return cmd_route(router, args)
        if args.command == "dispatch":
            return cmd_dispatch(router, args)
        if args.command == "sme":
            return cmd_sme(router, args)
    except TaskInvalid as exc:
        print(f"sme-routing: CANNOT-ASSESS -- {exc}", file=sys.stderr)
        return CANNOT_ASSESS
    except PolicyInvariantViolated as exc:
        print(f"sme-routing: NOT-OK -- {exc}", file=sys.stderr)
        return NOT_OK
    except (PolicyUnavailable, PolicyMalformed) as exc:
        print(f"sme-routing: CANNOT-ASSESS -- {exc}", file=sys.stderr)
        return CANNOT_ASSESS

    parser.error(f"unknown command {args.command!r}")  # pragma: no cover
    return CANNOT_ASSESS  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
