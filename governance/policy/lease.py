#!/usr/bin/env python3
"""One declared policy for every fleet lease and TTL (issue #322).

---knowledge---
module_id: governance.policy.lease
system: governance
app: policy
solution_class: enterprise
patterns: [no-false-green, honesty-tri-state, declared-authority, lane-isolation, bounded-work]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [Lease, values, Invariant, Mutation, mutation_by_name, violations, scan_findings, scan, cmd_check, cmd_scan, (+5 more)]
invariants: ""
gotchas: ""
related: ["#322", "#740", "#1545"]
do_not_duplicate: null
---knowledge---

The fleet honours at least seven independent timings, and their *relationships*
are load-bearing: a session that is judged orphaned between two rung heartbeats
loses a live lane, and a claim reaped before its session's TTL destroys work in
flight. Before this module those numbers were declared in four unrelated
modules — ``fleet/channel.STALE_HEARTBEAT_SECONDS``, the reconcile TTL, the
dispatch reap threshold and the claim TTL — so the relationships could only be
derived by reading all four and could drift apart silently.

This module is the single source of truth. It declares, once:

* every **lease** (how long a claim is held) and **TTL** (when a beat or a
  snapshot is considered stale) the fleet honours, with its owning module and
  the reason it has its value;
* the **ordering invariants** between them, as machine-checkable declarations
  rather than prose — each invariant is a relation between two named values
  that :func:`check` evaluates and names when it is broken;
* the **consumer contract**: which module must read which value from here, so
  a module that redeclares a policy value as its own constant is a gate
  finding, not a silent divergence;
* a **self-control**: a mutation for every invariant, each of which must be
  *refused* with the invariant named. A policy whose invariants cannot break is
  a formality (GR-12 / AO-GR-19).

Consumers import this module and read the value — they never restate it::

    from governance.policy import lease
    STALE_HEARTBEAT_SECONDS = lease.RUNG_HEARTBEAT_SECONDS

Exit-code contract (the repo's tri-state): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.

Usage::

    python3 governance/policy/lease.py check                 # validate the policy
    python3 governance/policy/lease.py check --mutate <name>  # prove an invariant bites
    python3 governance/policy/lease.py scan                  # consumers + hard-codes
    python3 governance/policy/lease.py self-control          # every mutation refused
    python3 governance/policy/lease.py dump                  # human-readable table
    python3 governance/policy/lease.py json                  # machine-readable policy
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2

# ── the declared values ─────────────────────────────────────────────────────
# Each is stated in its natural unit and nowhere else. Changing one here changes
# it for every consumer; changing one in a consumer is a gate failure.

#: A rung's beat older than this means the loop died rather than that it is
#: busy. The loop beats every poll cycle (30s default) and before each
#: directive, so 120s is four missed beats.
RUNG_HEARTBEAT_SECONDS = 120

#: How often a running session refreshes its per-lane beat.
SESSION_HEARTBEAT_SECONDS = 60

#: A session whose beat has not advanced for this long is an orphan. Must
#: exceed the rung heartbeat, or a live lane is judged dead between beats.
SESSION_TTL_MINUTES = 15

#: How long a claim is held before it may be taken over. Must exceed the
#: session TTL, or a claim is reaped while its lane is still legitimately
#: running.
CLAIM_TTL_HOURS = 24

#: A claim whose holder has been gone this long may be reaped by the brain.
#: Must exceed the session TTL: a lane that is still beating is not reaped.
CLAIM_REAP_MINUTES = 45

#: A board snapshot older than this is refused as stale, so a claim is never
#: judged against board state the board has already moved past.
SNAPSHOT_STALENESS_MINUTES = 15

#: A directive the sister never consumes is abandoned after this long. Must
#: cover the claim TTL it authorises, or an authorisation expires under a live
#: lease.
DIRECTIVE_LIFETIME_HOURS = 48
DIRECTIVE_LIFETIME_SECONDS = DIRECTIVE_LIFETIME_HOURS * 3600

#: An unmatched real-tree artifact (worktree/branch) younger than this is a
#: LIVE lane still being set up — a subagent's worktree, or the detached
#: scratch worktree running the gate itself — not an orphan. It is reported
#: (INFO, counted as ``young``) but must not fail the gate: a snapshot audit
#: with no grace window goes red the instant any agent creates a worktree,
#: i.e. always, and the gate can never be green while the fleet works (#740
#: follow-up). Only an artifact OLDER than this grace window fails unless it
#: is explicitly baselined.
REAL_TREE_GRACE_HOURS = 24

#: How long a claim on a LIVE RESOURCE — a thing that is not a file — is held
#: (issue #1545), declared PER RESOURCE TYPE. An issue claim could use one
#: number because a lane's shape does not change with what it edits; a
#: resource's does, and the resource types differ in how long a legitimate
#: holder mutates them. A `terraform apply` against shared state can outlive
#: ten minutes, and a lease that expires mid-apply is a SECOND writer on that
#: state — the exact failure a resource lease exists to prevent. So this is a
#: mapping read by `governance/dispatch/resource_lease.py`, never one global
#: number: shortening it for a one-shot phase must not shorten the state lease.
RESOURCE_CLAIM_TTL_SECONDS: dict[str, int] = {
    "tf-state": 4 * 3600,
    "cloudflare-phase": 3600,
}

#: The TTL for a resource type with NO declared entry above. It is bounded
#: rather than "forever": an unknown resource type still releases, and it is
#: declared here so the number a consumer reads is this one and not its own.
RESOURCE_CLAIM_TTL_DEFAULT_SECONDS = 3600


@dataclass(frozen=True)
class Lease:
    """One declared lease/TTL: its value, its unit, its owner and its reason."""

    key: str
    value: float
    unit: str
    owner: str
    why: str

    @property
    def seconds(self) -> float:
        return self.value * _UNIT_SECONDS[self.unit]


_UNIT_SECONDS = {"seconds": 1.0, "minutes": 60.0, "hours": 3600.0}

#: The declared policy, in seconds-vocabulary order (shortest lease first).
LEASES: tuple[Lease, ...] = (
    Lease(
        key="rung_heartbeat",
        value=RUNG_HEARTBEAT_SECONDS,
        unit="seconds",
        owner="fleet/channel.py",
        why="a rung whose beat is older than this is dead, not busy (four missed polls)",
    ),
    Lease(
        key="session_heartbeat",
        value=SESSION_HEARTBEAT_SECONDS,
        unit="seconds",
        owner="governance/reconcile/heartbeat.py",
        why="how often a live session refreshes its per-lane beat",
    ),
    Lease(
        key="session_ttl",
        value=SESSION_TTL_MINUTES,
        unit="minutes",
        owner="governance/reconcile/heartbeat.py",
        why="a lane whose beat is older than this is orphaned",
    ),
    Lease(
        key="snapshot_staleness",
        value=SNAPSHOT_STALENESS_MINUTES,
        unit="minutes",
        owner="governance/dispatch/snapshot.py",
        why="a claim is never judged against board state older than this",
    ),
    Lease(
        key="claim_reap",
        value=CLAIM_REAP_MINUTES,
        unit="minutes",
        owner="governance/dispatch/claims.py",
        why="a claim whose holder has been gone this long may be reaped",
    ),
    Lease(
        key="claim_ttl",
        value=CLAIM_TTL_HOURS,
        unit="hours",
        owner="governance/dispatch/claims.py",
        why="how long a claim is held before it may be taken over",
    ),
    Lease(
        key="directive_lifetime",
        value=DIRECTIVE_LIFETIME_HOURS,
        unit="hours",
        owner="fleet/channel.py",
        why="a directive the sister never consumes is abandoned after this long",
    ),
    Lease(
        key="resource_claim_default",
        value=RESOURCE_CLAIM_TTL_DEFAULT_SECONDS,
        unit="seconds",
        owner="governance/dispatch/resource_lease.py",
        why="how long a claim on a resource type with no declared TTL is held",
    ),
    Lease(
        key="resource_claim_cloudflare_phase",
        value=RESOURCE_CLAIM_TTL_SECONDS["cloudflare-phase"],
        unit="seconds",
        owner="governance/dispatch/resource_lease.py",
        why="how long a live-resource claim on one Cloudflare phase is held",
    ),
    Lease(
        key="resource_claim_tf_state",
        value=RESOURCE_CLAIM_TTL_SECONDS["tf-state"],
        unit="seconds",
        owner="governance/dispatch/resource_lease.py",
        why="how long a live-resource claim on terraform state is held (an apply outlives a phase)",
    ),
)


def values() -> dict[str, float]:
    """The policy as ``{key: seconds}`` — the vocabulary invariants evaluate in."""
    return {lease.key: lease.seconds for lease in LEASES}


@dataclass(frozen=True)
class Invariant:
    """An ordering constraint between two policy values, machine-checkable."""

    name: str
    left: str
    relation: str
    right: str
    why: str

    def holds(self, policy: Mapping[str, float]) -> bool:
        left = policy.get(self.left)
        right = policy.get(self.right)
        if left is None or right is None:
            return False
        if self.relation == ">":
            return left > right
        if self.relation == ">=":
            return left >= right
        if self.relation == "<":
            return left < right
        raise ValueError(f"{self.name}: unknown relation '{self.relation}'")

    def describe(self) -> str:
        return f"{self.left} {self.relation} {self.right}"


#: The ordering constraints. These are the relationships the session that tuned
#: the numbers had to derive by reading three modules; here they are declared.
INVARIANTS: tuple[Invariant, ...] = (
    Invariant(
        name="session-ttl-exceeds-rung-heartbeat",
        left="session_ttl",
        relation=">",
        right="rung_heartbeat",
        why="a live lane must not be judged dead between two rung beats",
    ),
    Invariant(
        name="session-beat-fits-session-ttl",
        left="session_heartbeat",
        relation="<",
        right="session_ttl",
        why="a beat interval longer than the TTL would orphan a running session",
    ),
    Invariant(
        name="claim-ttl-exceeds-session-ttl",
        left="claim_ttl",
        relation=">",
        right="session_ttl",
        why="a claim must not be reaped while its lane is still legitimately running",
    ),
    Invariant(
        name="claim-reap-exceeds-session-ttl",
        left="claim_reap",
        relation=">",
        right="session_ttl",
        why="the reap threshold must not release a claim whose lane is still beating",
    ),
    Invariant(
        name="directive-lifetime-covers-claim-ttl",
        left="directive_lifetime",
        relation=">",
        right="claim_ttl",
        why="the authorisation must outlive the lease it authorises",
    ),
    Invariant(
        name="resource-claim-outlives-the-session-ttl",
        left="resource_claim_default",
        relation=">",
        right="session_ttl",
        why="a resource lease must not look stale while the lane holding it is still beating",
    ),
    Invariant(
        name="resource-claim-tf-state-outlives-a-phase",
        left="resource_claim_tf_state",
        relation=">",
        right="resource_claim_cloudflare_phase",
        why="a terraform apply on shared state outlives a one-shot phase; a state lease shorter than a phase lease is the mid-apply lease loss (#1545)",
    ),
)


@dataclass(frozen=True)
class Mutation:
    """A deliberately broken policy; :func:`check` must refuse it."""

    name: str
    override: dict[str, float]
    invariant: str


#: Every invariant has a mutation that breaks exactly it. ``--mutate`` applies
#: the override in memory only; ``self-control`` requires each to be refused.
MUTATIONS: tuple[Mutation, ...] = (
    Mutation(
        name="session-ttl-below-rung-heartbeat",
        override={"session_ttl": 60.0},
        invariant="session-ttl-exceeds-rung-heartbeat",
    ),
    Mutation(
        name="claim-ttl-below-session-ttl",
        override={"claim_ttl": 600.0},
        invariant="claim-ttl-exceeds-session-ttl",
    ),
    Mutation(
        name="claim-reap-below-session-ttl",
        override={"claim_reap": 120.0},
        invariant="claim-reap-exceeds-session-ttl",
    ),
    Mutation(
        name="beat-interval-above-session-ttl",
        override={"session_heartbeat": 1800.0},
        invariant="session-beat-fits-session-ttl",
    ),
    Mutation(
        name="directive-lifetime-below-claim-ttl",
        override={"directive_lifetime": 3600.0},
        invariant="directive-lifetime-covers-claim-ttl",
    ),
    Mutation(
        name="resource-claim-default-below-session-ttl",
        override={"resource_claim_default": 60.0},
        invariant="resource-claim-outlives-the-session-ttl",
    ),
    Mutation(
        name="resource-claim-tf-state-below-a-phase",
        override={"resource_claim_tf_state": 600.0},
        invariant="resource-claim-tf-state-outlives-a-phase",
    ),
)


def mutation_by_name(name: str) -> Mutation | None:
    for mutation in MUTATIONS:
        if mutation.name == name:
            return mutation
    return None


def violations(policy: Mapping[str, float] | None = None) -> list[str]:
    """Every invariant the policy breaks, each naming the invariant and the values."""
    current = dict(values() if policy is None else policy)
    broken: list[str] = []
    for invariant in INVARIANTS:
        if not invariant.holds(current):
            broken.append(
                f"invariant '{invariant.name}' is broken: {invariant.describe()} "
                f"({invariant.left}={current.get(invariant.left):g}s, "
                f"{invariant.right}={current.get(invariant.right):g}s) — {invariant.why}"
            )
    return broken


# ── the consumer contract ───────────────────────────────────────────────────
# (path, local constant the module declares, policy key that constant must read)
# ``local`` is None when the module reads the policy indirectly (through a
# sibling that already read it) or names the value differently.
CONSUMERS: tuple[tuple[str, str | None, str], ...] = (
    ("fleet/channel.py", "STALE_HEARTBEAT_SECONDS", "RUNG_HEARTBEAT_SECONDS"),
    ("fleet/channel.py", "DIRECTIVE_LIFETIME_SECONDS", "DIRECTIVE_LIFETIME_SECONDS"),
    ("governance/reconcile/heartbeat.py", "DEFAULT_TTL_MINUTES", "SESSION_TTL_MINUTES"),
    ("governance/reconcile/heartbeat.py", "DEFAULT_BEAT_SECONDS", "SESSION_HEARTBEAT_SECONDS"),
    ("governance/dispatch/claims.py", "DEFAULT_TTL_HOURS", "CLAIM_TTL_HOURS"),
    ("governance/dispatch/claims.py", "DEFAULT_REAP_MINUTES", "CLAIM_REAP_MINUTES"),
    ("governance/dispatch/model.py", None, "CLAIM_TTL_HOURS"),
    ("governance/dispatch/snapshot.py", "DEFAULT_STALENESS_MINUTES", "SNAPSHOT_STALENESS_MINUTES"),
    ("governance/reconcile/real_tree_baseline.py", "DEFAULT_GRACE_HOURS", "REAL_TREE_GRACE_HOURS"),
    ("governance/dispatch/resource_lease.py", "RESOURCE_CLAIM_TTL_SECONDS", "RESOURCE_CLAIM_TTL_SECONDS"),
    ("governance/dispatch/resource_lease.py", "RESOURCE_CLAIM_TTL_DEFAULT_SECONDS", "RESOURCE_CLAIM_TTL_DEFAULT_SECONDS"),
)

#: Modules that must read the policy *through* another module's constant.
INDIRECT_READS: tuple[tuple[str, str, str], ...] = (
    (
        "governance/dispatch/cli.py",
        "claims.DEFAULT_REAP_MINUTES",
        "the reap CLI default must come from the policy, not a literal",
    ),
)

#: Values that could be inlined without ever naming the policy constant, so the
#: name-based scan above cannot see them. Each regex must NOT match.
LITERAL_GUARDS: tuple[tuple[str, str, str], ...] = (
    (
        "governance/dispatch/cli.py",
        r'--older-than-minutes"[^\n]*default=\d',
        "the reap threshold default is hard-coded instead of read from the policy",
    ),
    (
        "governance/dispatch/model.py",
        r"ttl_hours:\s*int\s*=\s*\d",
        "the claim TTL default is hard-coded instead of read from the policy",
    ),
    (
        "governance/dispatch/model.py",
        r'get\(\s*"ttl_hours"\s*,\s*\d\s*\)',
        "the claim TTL fallback is hard-coded instead of read from the policy",
    ),
)

#: Policy keys whose own name must never be redeclared with a numeric literal.
OWNED_NAMES: tuple[str, ...] = (
    "RUNG_HEARTBEAT_SECONDS",
    "SESSION_HEARTBEAT_SECONDS",
    "SESSION_TTL_MINUTES",
    "CLAIM_TTL_HOURS",
    "CLAIM_REAP_MINUTES",
    "SNAPSHOT_STALENESS_MINUTES",
    "DIRECTIVE_LIFETIME_SECONDS",
    "REAL_TREE_GRACE_HOURS",
)

POLICY_MODULE = "governance/policy/lease.py"
SCAN_DIRS = ("fleet", "governance")


def _tracked_python(root: Path) -> list[str]:
    """Every tracked ``*.py`` under ``SCAN_DIRS`` outside ``vendor/``."""
    import subprocess

    try:
        listing = subprocess.run(
            ["git", "ls-files", "--", *SCAN_DIRS],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:  # pragma: no cover - env failure
        print(f"lease-policy: CANNOT-ASSESS — git ls-files failed: {exc}", file=sys.stderr)
        return []
    return [
        line
        for line in listing.splitlines()
        if line.endswith(".py") and not line.startswith("vendor/") and "/tests/" not in line
    ]


def scan_findings(text: str, name: str) -> list[str]:
    """Findings for a source text that redeclares ``name`` as a numeric literal.

    Pure predicate, so the self-control can feed it a synthetic hard-code and
    require a finding (the gate must be able to fail).
    """
    pattern = re.compile(rf"^\s*{re.escape(name)}\s*=\s*[0-9]", re.M)
    return [f"{name} is hard-coded as a literal (read it from {POLICY_MODULE})"] * len(
        pattern.findall(text)
    )


def scan(root: Path | str) -> list[str]:
    """Every consumer that restates a policy value, or fails to read it."""
    base = Path(root)
    findings: list[str] = []
    for path in _tracked_python(base):
        if path == POLICY_MODULE:
            continue
        text = (base / path).read_text(encoding="utf-8")
        for name in OWNED_NAMES:
            for finding in scan_findings(text, name):
                findings.append(f"{path}: {finding}")
    for path, local, policy_name in CONSUMERS:
        target = base / path
        if not target.is_file():
            findings.append(f"{path}: missing (it must read {policy_name} from the policy)")
            continue
        text = target.read_text(encoding="utf-8")
        if f"lease.{policy_name}" not in text:
            findings.append(f"{path}: does not read {policy_name} from {POLICY_MODULE}")
        if local is not None:
            for finding in scan_findings(text, local):
                findings.append(f"{path}: {local} — {finding}")
    for path, needle, why in INDIRECT_READS:
        target = base / path
        if not target.is_file() or needle not in target.read_text(encoding="utf-8"):
            findings.append(f"{path}: {why} (expected {needle})")
    for path, pattern, why in LITERAL_GUARDS:
        target = base / path
        if not target.is_file():
            findings.append(f"{path}: missing — {why}")
            continue
        text = target.read_text(encoding="utf-8")
        if re.search(pattern, text):
            findings.append(f"{path}: {why}")
    return findings


# ── commands ────────────────────────────────────────────────────────────────
def cmd_check(args: argparse.Namespace) -> int:
    policy = values()
    applied = ""
    if args.mutate:
        mutation = mutation_by_name(args.mutate)
        if mutation is None:
            print(f"lease-policy: CANNOT-ASSESS — unknown mutation '{args.mutate}'", file=sys.stderr)
            return EXIT_CANNOT_ASSESS
        policy.update(mutation.override)
        applied = f" (mutation '{mutation.name}' applied)"
    print(f"lease policy{applied}:")
    for lease in LEASES:
        print(f"  {lease.key:22s} {lease.value:6g} {lease.unit:8s} owner={lease.owner}")
    print("ordering invariants:")
    broken = violations(policy)
    for invariant in INVARIANTS:
        state = "OK   " if invariant.holds(policy) else "BROKEN"
        print(f"  {state} {invariant.name}: {invariant.describe()}")
    if broken:
        for line in broken:
            print(f"lease-policy: FAIL — {line}", file=sys.stderr)
        return EXIT_NOT_OK
    return EXIT_OK


def cmd_scan(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if not (root / POLICY_MODULE).is_file():
        print(f"lease-policy: CANNOT-ASSESS — {POLICY_MODULE} not found under {root}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    findings = scan(root)
    if findings:
        for line in findings:
            print(f"lease-policy: FAIL — {line}", file=sys.stderr)
        return EXIT_NOT_OK
    print(f"lease-policy: OK — {len(CONSUMERS)} consumer(s) read {POLICY_MODULE}; no hard-coded value")
    return EXIT_OK


def cmd_self_control(args: argparse.Namespace) -> int:
    """Every invariant must be refutable, and the hard-code scan must bite."""
    failures: list[str] = []
    live = violations()
    if live:
        # A broken live policy confounds the mutant proof: a mutation would be
        # refused for the wrong reason, so report the real defect instead.
        print(
            "lease-policy: FAIL — the live policy is already inconsistent, so a mutation "
            "cannot be attributed to the mutation:",
            file=sys.stderr,
        )
        for line in live:
            print(f"  {line}", file=sys.stderr)
        return EXIT_NOT_OK
    for mutation in MUTATIONS:
        policy = values()
        policy.update(mutation.override)
        broken = violations(policy)
        if not broken:
            failures.append(f"mutation '{mutation.name}' was ACCEPTED (the invariant is a formality)")
            continue
        if not any(f"'{mutation.invariant}'" in line for line in broken):
            failures.append(
                f"mutation '{mutation.name}' was refused without naming '{mutation.invariant}': {broken}"
            )
            continue
        print(f"  OK    refused '{mutation.name}' naming '{mutation.invariant}'")
    synthetic = "STALE_HEARTBEAT_SECONDS = 120\n"
    if not scan_findings(synthetic, "STALE_HEARTBEAT_SECONDS"):
        failures.append("the hard-code scan accepted a restated policy value (it is a formality)")
    else:
        print("  OK    refused a synthetic hard-code of RUNG_HEARTBEAT_SECONDS")
    for line in failures:
        print(f"lease-policy: FAIL — {line}", file=sys.stderr)
    return EXIT_NOT_OK if failures else EXIT_OK


def cmd_dump(args: argparse.Namespace) -> int:
    print(f"{'key':22s} {'value':>6s}  {'unit':8s} owner")
    for lease in LEASES:
        print(f"{lease.key:22s} {lease.value:6g}  {lease.unit:8s} {lease.owner}")
    print("invariants:")
    for invariant in INVARIANTS:
        print(f"  {invariant.name}: {invariant.describe()} — {invariant.why}")
    return EXIT_OK


def cmd_json(args: argparse.Namespace) -> int:
    payload = {
        "leases": [
            {
                "key": lease.key,
                "value": lease.value,
                "unit": lease.unit,
                "seconds": lease.seconds,
                "owner": lease.owner,
                "why": lease.why,
            }
            for lease in LEASES
        ],
        "invariants": [
            {
                "name": invariant.name,
                "left": invariant.left,
                "relation": invariant.relation,
                "right": invariant.right,
                "why": invariant.why,
            }
            for invariant in INVARIANTS
        ],
    }
    print(json.dumps(payload, indent=2))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="The fleet's one declared lease/TTL policy (issue #322).")
    parser.add_argument("--root", default=".", help="repository root to scan (default: cwd)")
    sub = parser.add_subparsers(dest="command")

    check = sub.add_parser("check", help="validate the declared values and their ordering invariants")
    check.add_argument("--mutate", default="", help="apply a named mutation before checking (proves it bites)")
    check.set_defaults(func=cmd_check)

    scan_cmd = sub.add_parser("scan", help="fail when a module restates or omits a policy value")
    scan_cmd.set_defaults(func=cmd_scan)

    self_control = sub.add_parser("self-control", help="prove every invariant and the hard-code scan bite")
    self_control.set_defaults(func=cmd_self_control)

    dump = sub.add_parser("dump", help="print the policy in human-readable form")
    dump.set_defaults(func=cmd_dump)

    json_cmd = sub.add_parser("json", help="print the policy as JSON")
    json_cmd.set_defaults(func=cmd_json)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not getattr(args, "command", None):
        args.mutate = getattr(args, "mutate", "")
        return cmd_check(args)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
