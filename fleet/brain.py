#!/usr/bin/env python3
"""The brain loop — the middle rung of the hierarchy (M26, issue #160).

    operator  →  BRAIN  →  sister  →  subagents
    (orders)     (this)    (executes)  (build)

Issue #160's operating model named three rungs but only shipped two: the sister
loop and the transport. The brain was an interactive session a human had to
type into, so the operator's only working trigger was to write directives into
the sister's inbox — which *is* the brain's job, and which the channel now
refuses (`operator → sister` bypasses the brain). This module makes the middle
rung a real process:

* it watches `.fleet/brain/inbox` for the operator's orders and never idles;
* it turns each order into a brain-signed directive for the sister, deriving the
  FinOps block from the order (and refusing a tier/thinking outside the
  contract's allowlist);
* it answers the operator in `.fleet/brain/outbox` — an `ack` naming the
  directive it issued, or a `result` reporting a refusal with the exact reason;
* it publishes `.fleet/brain.heartbeat.json` so `status`/`health` can tell a
  live brain from a dead one and catch code drift;
* it refuses — never improvises — when the order is malformed, names no issue,
  or asks for a tier below the floor that work requires.

It reaches the sister only through `channel.py send`, so the contract's trust
rules (only a brain-signed directive reaches the sister) hold by construction.

Usage:
    python3 fleet/brain.py run [--once]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "fleet"))
sys.path.insert(0, str(ROOT / "governance" / "dispatch"))

import channel  # noqa: E402
import singleton  # noqa: E402

HEARTBEAT = ROOT / ".fleet" / "brain.heartbeat.json"
CHANNEL = str(ROOT / "fleet" / "channel.py")
PROFILE_PATH = ROOT / "fleet" / "profiles" / "brain.profile.json"


def load_profile(path: Path | None = None) -> dict:
    """The brain's elite profile: mission, KB, controls, FinOps floors, anti-patterns.

    A profile that is missing or malformed is a REFUSAL, not a default: the brain
    steering a fleet on a half-loaded doctrine is worse than a brain that will
    not start. The floor vocabulary below is derived from it, so the profile is
    the single source for what the brain enforces.
    """
    target = path or PROFILE_PATH
    try:
        profile = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"[brain] REFUSED — cannot load the brain profile {target}: {exc}")
    required = ("mission", "authority", "kb", "finops", "controls", "escalation", "templates")
    missing = [key for key in required if key not in profile]
    if missing:
        raise SystemExit(f"[brain] REFUSED — brain profile {target} is missing {', '.join(missing)}")
    return profile


PROFILE = load_profile()

# The FinOps floor the brain enforces when it dispatches. Security, secrets,
# auth and production-IaC work never drops below the high floor (fleet doctrine),
# so a lane whose name says so is escalated rather than accepted at flash/none.
# Vocabulary comes from the profile, not from this file, so the doctrine and the
# machine cannot drift apart.
HIGH_FLOOR_LANES = tuple(PROFILE["finops"]["high_floor_lanes"])
DEFAULT_TIER = PROFILE["finops"]["default_tier"]
DEFAULT_THINKING = PROFILE["finops"]["default_thinking"]
HIGH_TIER = PROFILE["finops"]["high_floor_tier"]
HIGH_THINKING = PROFILE["finops"]["high_floor_thinking"]
CONTROLS = tuple(PROFILE["controls"])
KB_SOURCES = tuple(PROFILE["kb"]["fleet_modules"])

# Order kinds that are not work: the operator may ask the brain to report or to
# ping instead of dispatching an issue (the vocabulary lives in the channel).
NON_WORK_KINDS = channel.NON_WORK_KINDS


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_heartbeat(state: str, *, started_at: str, commit: str) -> None:
    entry = {
        "pid": os.getpid(),
        "state": state,
        "started_at": started_at,
        "commit": commit,
        "ts": now_iso(),
    }
    HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
    tmp = HEARTBEAT.with_suffix(".tmp")
    tmp.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    tmp.replace(HEARTBEAT)


def order_issue(order: dict) -> int | None:
    """The issue an order names, or None when the order is not dispatchable work."""
    task = order.get("task") or {}
    issue = task.get("issue")
    if isinstance(issue, bool) or not isinstance(issue, int) or issue < 1:
        return None
    return issue


def needs_high_floor(order: dict) -> bool:
    """A lane that touches security/secrets/auth/IaC must not be dispatched at flash."""
    task = order.get("task") or {}
    lane = str(task.get("lane") or "").lower()
    title = str(task.get("title") or "").lower()
    return any(word in lane or word in title for word in HIGH_FLOOR_LANES)


def choose_model(order: dict) -> tuple[str, str]:
    """Derive the FinOps block: an operator may raise the tier, never lower the floor."""
    model = order.get("model") or {}
    tier = model.get("tier") or DEFAULT_TIER
    thinking = model.get("thinking") or DEFAULT_THINKING
    if needs_high_floor(order):
        if channel.MODEL_TIERS.index(tier) < channel.MODEL_TIERS.index(HIGH_TIER):
            tier = HIGH_TIER
        if channel.THINKING_LEVELS.index(thinking) < channel.THINKING_LEVELS.index(HIGH_THINKING):
            thinking = HIGH_THINKING
    return tier, thinking


def order_reference(order: dict) -> str:
    """The id the operator can correlate on: the order's id, else its own reference."""
    return str(order.get("id") or order.get("correlation_id") or "")


def build_directive(order: dict) -> dict:
    """Compose the brain-signed directive the sister will execute."""
    task = dict(order.get("task") or {})
    tier, thinking = choose_model(order)
    body = order.get("body") or ""
    # The profile travels with the order: the subagent gets the KB it must read
    # and the evidence it must return, so dispatch quality does not depend on the
    # operator remembering to say it.
    kb = "\n".join(f"  - {source}" for source in KB_SOURCES)
    directive = {
        "from": "brain",
        "to": "sister",
        "type": "directive",
        "correlation_id": order_reference(order),
        "task": task,
        "model": {"tier": tier, "thinking": thinking},
        "body": (
            f"Operator order {order_reference(order)}:\n{body}\n\n"
            f"Brain doctrine: {PROFILE['mission']}\n"
            f"Read first (fleet KB):\n{kb}\n"
            f"Return: {PROFILE['templates']['report']}"
        ),
    }
    if order.get("control") == "override" or task.get("override"):
        # The operator's override still travels the hierarchy: the operator orders
        # the brain, and the brain issues the control to the sister.
        directive["control"] = "override"
    return directive


def dispatch(order: dict) -> tuple[bool, str]:
    """Send the composed directive through the channel; return (ok, message)."""
    directive = build_directive(order)
    result = subprocess.run(
        ["python3", CHANNEL, "send", "--message", json.dumps(directive)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


WAVES = ROOT / ".fleet" / "waves"


def gh_issue_create(title: str, body: str) -> int:
    """File a micro-task child issue; returns its number, or raises on failure."""
    result = subprocess.run(
        ["gh", "issue", "create", "--repo", "kushin77/agent-orchestrator", "--title", title, "--body", body],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh issue create failed: {result.stderr.strip()[-400:]}")
    match = re.search(r"/issues/(\d+)", result.stdout)
    if not match:
        raise RuntimeError(f"gh issue create returned no issue number: {result.stdout.strip()}")
    return int(match.group(1))


def handle_decompose(order: dict) -> tuple[bool, str]:
    """Turn a decomposition spec into child issues and dispatch the ready wave.

    Micro-decomposition: one parent issue becomes N small, collision-free child
    issues (the pmo-sme discipline), filed with a `Parent: #N` marker so the chain
    gate recognises them, and the ready wave is dispatched immediately — the brain
    prepares the next waves in advance instead of waiting for the parent.
    """
    spec = (order.get("task") or {}).get("decompose")
    if not isinstance(spec, dict) or not spec.get("children"):
        return False, "decompose order carries no children — nothing to file"
    parent = int(spec["parent_issue"])
    WAVES.mkdir(parents=True, exist_ok=True)
    plan = {"parent": parent, "children": [], "dispatched": []}
    for index, child in enumerate(spec["children"]):
        title = str(child.get("title", "")).strip()
        lane = str(child.get("lane", "fleet"))
        verify = str(child.get("verify", ""))
        files = ", ".join(child.get("files", []))
        body = (
            f"Parent: #{parent}\n\n"
            f"Lane: {lane}\n\nFiles: {files}\n\nVerify: `{verify}`\n\n"
            f"Micro-task {index} of #{parent} (decomposed by the brain, pmo-sme discipline)."
        )
        number = gh_issue_create(title, body)
        plan["children"].append(
            {
                "index": index,
                "issue": number,
                "lane": lane,
                "verify": verify,
                "depends_on": [int(d) for d in child.get("depends_on", [])],
            }
        )
    (WAVES / f"{parent}.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    dispatched = dispatch_ready_children(parent, plan)
    return True, (
        f"decomposed #{parent} into {len(plan['children'])} micro-tasks; "
        f"filed {[c['issue'] for c in plan['children']]}; dispatched {dispatched}"
    )


def issue_is_closed(number: int) -> bool:
    result = subprocess.run(
        ["gh", "api", f"repos/kushin77/agent-orchestrator/issues/{number}", "--jq", ".state"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() == "closed"


def dispatch_ready_children(parent: int, plan: dict) -> list[int]:
    """Dispatch any child whose dependencies are all closed and not yet dispatched."""
    dispatched = []
    for child in plan["children"]:
        if child["issue"] in plan["dispatched"]:
            continue
        deps = [plan["children"][d]["issue"] for d in child["depends_on"] if d < len(plan["children"])]
        if deps and not all(issue_is_closed(d) for d in deps):
            continue
        order = {
            "type": "directive",
            "task": {
                "issue": child["issue"],
                "lane": child["lane"],
                "title": child["title"] if "title" in child else "",
            },
            "body": f"Micro-task of #{parent}. Verify: {child['verify']}",
        }
        ok, message = dispatch(order)
        if ok:
            plan["dispatched"].append(child["issue"])
            dispatched.append(child["issue"])
        else:
            print(f"[brain] dispatch of micro-task #{child['issue']} refused: {message}", file=sys.stderr, flush=True)
    (WAVES / f"{parent}.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    return dispatched


def advance_waves() -> list[int]:
    """Called on the idle path: dispatch any newly-ready children of every plan."""
    if not WAVES.exists():
        return []
    advanced = []
    for path in WAVES.glob("*.json"):
        try:
            plan = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        advanced.extend(dispatch_ready_children(int(plan["parent"]), plan))
    return advanced


def handle_order(order: dict) -> tuple[bool, str]:
    """One order in, one directive or one refusal out. Never a silent drop."""
    kind = str((order.get("task") or {}).get("kind") or order.get("kind") or "").lower()
    if (order.get("task") or {}).get("decompose"):
        return handle_decompose(order)
    if kind in NON_WORK_KINDS:
        level, reasons = _health()
        return True, f"order kind '{kind}' — no dispatch; fleet health {level}: {'; '.join(reasons)}"

    issue = order_issue(order)
    if issue is None:
        return False, (
            "order names no issue (task.issue missing or not a positive integer) — the brain "
            "dispatches work only for a real issue; nothing was sent to the sister"
        )

    ok, message = dispatch(order)
    if not ok:
        return False, f"dispatch refused for #{issue}: {message}"
    tier, thinking = choose_model(order)
    return True, f"dispatched #{issue} to the sister at {tier}/{thinking} — {message}"


def _health() -> tuple[int, list[str]]:
    """Read the fleet health signal without importing the CLI's exit semantics."""
    result = subprocess.run(
        ["python3", str(ROOT / "fleet" / "health.py"), "check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return result.returncode, ["health signal unreadable"]
    return result.returncode, [payload.get("status", "unknown")] + list(payload.get("reasons") or [])


def loop(args: argparse.Namespace) -> int:
    if not singleton.guard("brain", "bash fleet/run-fleet.sh (or: bash fleet/brain.sh)"):
        return 1
    started_at = now_iso()
    commit = channel.head_commit()
    idle_printed = False
    while True:
        write_heartbeat("idle", started_at=started_at, commit=commit)
        watch = subprocess.run(
            ["python3", CHANNEL, "brain-inbox", "--timeout-seconds", str(args.watch_timeout), "--interval", "1"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if watch.returncode != 0:
            advanced = advance_waves()
            if advanced:
                print(f"[brain] advanced waves: dispatched {advanced}", flush=True)
            if not idle_printed:
                print("[brain] idle — watching .fleet/brain/inbox for operator orders", flush=True)
                idle_printed = True
            if args.once:
                return 0
            continue
        idle_printed = False
        try:
            order = json.loads(watch.stdout)
        except json.JSONDecodeError:
            print("[brain] unparseable order — refusing", file=sys.stderr, flush=True)
            continue

        order_id = order.get("id", "")
        print(f"[brain] order {order_id}: {channel_issue(order)}", flush=True)
        write_heartbeat("dispatching", started_at=started_at, commit=commit)
        ok, report = handle_order(order)
        channel.brain_reply(order, "ack" if ok else "result", report)
        channel.consume_order(order_id)
        print(f"[brain] {order_id}: {report[:200]}", flush=True)
        if args.once:
            return 0 if ok else 1


def channel_issue(order: dict) -> str:
    issue = order_issue(order)
    return f"#{issue}" if issue else "no issue"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleet-brain", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run the brain loop (never idles)")
    run.add_argument("--watch-timeout", type=float, default=30.0)
    run.add_argument("--once", action="store_true")
    run.set_defaults(func=loop)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
