#!/usr/bin/env python3
"""The director loop — the middle rung of the hierarchy (M26, issue #160).

    principal  →  DIRECTOR  →  dispatcher  →  executors
    (orders)     (this)    (executes)  (build)

Issue #160's operating model named three rungs but only shipped two: the dispatcher
loop and the transport. The director was an interactive session a human had to
type into, so the principal's only working trigger was to write directives into
the dispatcher's inbox — which *is* the director's job, and which the channel now
refuses (`operator → sister` bypasses the director). This module makes the middle
rung a real process:

* it watches `.fleet/brain/inbox` for the principal's orders and never idles;
* it turns each order into a brain-signed directive for the dispatcher, deriving the
  FinOps block from the order (and refusing a tier/thinking outside the
  contract's allowlist);
* it routes the order by the capability the work needs, through the routing
  policy that consumes the vendored orchestration contract (ADR-0012; #300,
  #301) — the director keeps no second copy of that vocabulary, so dispatch and the
  declared contract cannot drift apart;
* it answers the principal in `.fleet/brain/outbox` — an `ack` naming the
  directive it issued, or a `result` reporting a refusal with the exact reason;
* it publishes `.fleet/brain.heartbeat.json` so `status`/`health` can tell a
  live director from a dead one and catch code drift, and it keeps that beat fresh
  *while an order is being handled* — a decomposition files N child issues and
  refreshes the board, which outlives the watchdog's stale threshold (#274);
* it stops cleanly on SIGTERM/SIGINT (the watchdog's kill) instead of dying
  mid-order, and records a sent-marker *before* it sends, so a restart can never
  re-dispatch an order whose directive already went out (#274);
* it prints a low-noise CONTEXT STREAM to stdout — a startup banner, the order it
  received, what it did with it, the waves it advanced, and an idle heartbeat
  every ~30s. That stream is captured to `.fleet/brain.log` (the watchdog owns
  the spawn), which is what the `brain` window of the `fleet` tmux session shows;
* it refuses — never improvises — when the order is malformed, names no issue,
  names an issue the committed board says is already closed (or absent from it),
  or asks for a tier below the floor that work requires.

It reaches the dispatcher only through `channel.py send`, so the contract's trust
rules (only a brain-signed directive reaches the dispatcher) hold by construction.

Usage:
    python3 fleet/brain.py run [--once]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "fleet"))
sys.path.insert(0, str(ROOT / "governance" / "dispatch"))
# The master-health pre-check (RCA 2026-09-17 fix #5) reads the SAME attestation
# schema/reader landing uses (`governance/landing/evidence.py`) rather than
# inventing a second notion of "green" — a flat sibling import, same convention
# as `governance/dispatch`'s `model` above.
sys.path.insert(0, str(ROOT / "governance" / "landing"))

import channel  # noqa: E402
import decompose_policy  # noqa: E402
import evidence as landing_evidence  # noqa: E402
import markers  # noqa: E402
import routing  # noqa: E402
import runtime  # noqa: E402
import singleton  # noqa: E402

# The completion-triggered advance (#701) reads the board through the dispatch
# package: `order.advance_candidates` is the graph-advance recomputation, and
# `snapshot`/`claims` load the committed board and the live claim ledger. These
# are flat sibling imports, so they go on the path first (see `_conformance` for
# how the conformance seam's own flat `model` is kept separate).
import claims  # noqa: E402
import focus as focus_mod  # noqa: E402
import order  # noqa: E402
import snapshot as snapshot_mod  # noqa: E402

FLEET_DIR = runtime.FLEET_DIR
HEARTBEAT = FLEET_DIR / "brain.heartbeat.json"
# Where this process's stdout is captured. The watchdog owns the spawn and opens
# exactly this path (`fleet/watchdog.py`); it is named here only so the startup
# header can tell the principal where the stream they are reading came from.
LOG_PATH = FLEET_DIR / "brain.log"
CHANNEL = str(ROOT / "fleet" / "channel.py")
PROFILE_PATH = ROOT / "fleet" / "profiles" / "brain.profile.json"
# Where the director records that a directive has been *sent* for an order. The
# marker is written BEFORE the channel is invoked (#274): a director killed between
# the dispatch and `channel.consume_order` used to re-read the order on restart
# and send it a second time (measured: `['4242', '4242']`). On restart the marker
# suppresses the duplicate instead of repeating it.
#
# The marker set is `fleet/markers.py`'s (it owns the state machine: sending /
# sent / in-flight / completed / dead). This module keeps the name so the
# directory has one owner and one spelling, and passes it to the reconciler
# explicitly so a test that redirects it redirects the reconciliation with it.
DISPATCH_MARKERS = markers.DISPATCHED
# The prefix `dispatch()` returns when the marker says the order is already out.
# Callers report "already dispatched" and never send a second directive.
DUPLICATE_SUPPRESSED = "duplicate suppressed"
# The prefix `dispatch()` returns when the marker is TERMINAL — the order
# completed, or its directive was retired / its re-arm budget was exhausted
# (#796). Distinct from DUPLICATE_SUPPRESSED on purpose: "already sent" and
# "finished, parked" are different facts and the principal must see which one
# holds, with the issue named and the verb that lifts it.
PARKED_SUPPRESSED = "terminal marker"
# The committed board the director routes against, and the SAME artifact a claim is
# validated against (`governance/dispatch/snapshot.py`). Reading it here is what
# makes the director's answer agree with the claim layer's: an issue this file says
# is closed is an issue `claim` refuses, so a directive for it could never start
# (#693). It is refreshed explicitly by `python3 governance/dispatch/cli.py
# snapshot --from-github` — the only network-touching board read.
BOARD_PATH = ROOT / ".board" / "snapshot.json"

# Where the cheap, cached master-health verdict lives (RCA 2026-09-17 fix #5).
# It is the SAME attestation shape landing reads (`governance/landing/evidence.py`
# — `rc`/`commit`/`result`/`timestamp`), just written for `origin/master`'s own
# head instead of a lane's. WHO WRITES IT: `governance/landing/engine.py`'s
# `land()`, right after a successful squash-merge (engine.py, the
# `master-attestation` step right after the `merge` step) — every landed lane
# publishes master's own just-measured health at exactly the commit that
# lands, via `governance.landing.evidence.write_master_attestation` (the same
# schema, atomic tmp+rename write). This module never runs verify itself and
# never writes this file — it only reads the cached verdict, which is what
# keeps the pre-check cheap.
MASTER_ATTESTATION = FLEET_DIR / "master-attestation.json"
# The backstop only: an attestation whose COMMIT still matches origin/master's
# current head (the normal case between merges) never goes stale from wall
# clock alone — see `master_health_refusal` below. This cap exists only for
# the degenerate case of a head that has not moved in a very long time (a
# quiet repo, or a stopped landing driver), so a fact from a week ago is never
# silently trusted just because nothing has landed since. Generous on
# purpose: freshness is normally decided by the commit match, not the clock.
MASTER_ATTESTATION_WALLCLOCK_CAP_SECONDS = 86400.0


def current_master_head() -> str | None:
    """`origin/master`'s head SHA, read with NO fetch and NO verify.

    `git rev-parse` here only resolves whatever ref this checkout already has
    for `origin/master` (a plain local ref lookup — packed or loose, same as
    reading `.git/refs/remotes/origin/master`); it never reaches the network
    and never invokes `scripts/verify.sh` or `scripts/merge-gate.sh`, which is
    what keeps this pre-check as cheap as the reader it borrows from. `None`
    when the ref cannot be resolved at all (an unborn repo, no such remote) —
    that is a CANNOT-ASSESS input, same posture as an unreadable attestation.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--verify", "-q", "origin/master"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def master_health_refusal(order: dict) -> str | None:
    """Why no directive may be issued because master itself is not known-green.

    Dispatch opens a lane, and a lane that opens a PR against a red master can
    never land (RCA 2026-09-17: H2/H3) — landing already refuses at the END:
    this is the SAME check, cheaply, at the START, so the queue stops
    inflating with work that cannot land. It reads the cached attestation
    `MASTER_ATTESTATION` (never runs verify) through the SAME reader landing
    uses (`landing_evidence.read_attestation`), so "green" means one thing in
    this fleet.

    Freshness is HEAD-BOUND, not wall-clock: the attestation is fresh exactly
    when its `commit` names `origin/master`'s current head (`same_commit`,
    landing's own short-SHA-tolerant comparison) — an attestation for the
    current head never goes stale merely because time passed between merges,
    and one for an older head is CANNOT-ASSESS the instant a newer commit
    lands, however recently it was written. `MASTER_ATTESTATION_WALLCLOCK_CAP_SECONDS`
    (24h) is only the backstop for a head that has not moved in a long time.

    A lane explicitly fixing a red master is exempt — `task.allow_red_master`
    (a directive flag) or `task.master_red_fix` (an issue labelled as the fix
    itself) — otherwise nothing could ever repair master. Every other order is
    admitted only when the cached verdict is head-fresh AND green; absent,
    unreadable, head-stale, or red all refuse (CANNOT-ASSESS is never a pass,
    mirroring the honesty tri-state `governance/landing/evidence.py` already
    uses).
    """
    task = order.get("task") or {}
    if task.get("allow_red_master") or task.get("master_red_fix"):
        return None
    attestation = landing_evidence.read_attestation(MASTER_ATTESTATION)
    if not attestation.readable:
        return (
            f"master-health CANNOT-ASSESS — {MASTER_ATTESTATION} is {attestation.state} "
            f"({attestation.detail}); dispatch refuses rather than assume master is green"
        )
    head = current_master_head()
    if head is None:
        return (
            f"master-health CANNOT-ASSESS — origin/master's head could not be resolved "
            f"(no fetch, no verify was run); dispatch refuses rather than assume master is green"
        )
    if not landing_evidence.same_commit(attestation.commit, head):
        return (
            f"master-health CANNOT-ASSESS — {MASTER_ATTESTATION} names commit "
            f"{attestation.commit or 'none'}, but origin/master's head is now {head}; dispatch "
            "refuses a verdict for a commit master has since moved past"
        )
    age = time.time() - _attestation_epoch(attestation.timestamp)
    if age > MASTER_ATTESTATION_WALLCLOCK_CAP_SECONDS:
        return (
            f"master-health CANNOT-ASSESS — {MASTER_ATTESTATION} matches origin/master's head but "
            f"is {age:.0f}s old (> the {MASTER_ATTESTATION_WALLCLOCK_CAP_SECONDS:.0f}s backstop cap); "
            "dispatch refuses rather than trust a verdict this old even at the right commit"
        )
    if not attestation.green:
        return (
            f"master-health NOT-OK — {MASTER_ATTESTATION} reports "
            f"result={attestation.result or 'unknown'} exit_code={attestation.rc}; "
            "master is red, so a new lane's PR could never land (RCA 2026-09-17 fix #5) — "
            "pass task.allow_red_master (or label the issue a master-red fix) to dispatch anyway"
        )
    return None


def _attestation_epoch(timestamp: str) -> float:
    """The attestation's timestamp as epoch seconds, or -inf when unreadable.

    An unparsable/blank timestamp must never read as "just now" (that would
    silently defeat the TTL and let a stale attestation pass as fresh).
    """
    if not timestamp:
        return float("-inf")
    try:
        text = timestamp.replace("Z", "+00:00")
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return float("-inf")


def suppressed(message: str) -> bool:
    """True when `dispatch()` refused because the marker already covers the order.

    One predicate for both refusals, so a caller cannot handle "already sent" and
    silently mis-handle "parked" — which is exactly how a terminal marker used to
    read as a delivery failure.
    """
    return message.startswith((DUPLICATE_SUPPRESSED, PARKED_SUPPRESSED))


# How often the beat is refreshed while an order is being handled. The watchdog
# SIGTERMs a rung whose beat is older than `channel.STALE_HEARTBEAT_SECONDS`
# (120s), and a decompose order outlives that easily; the dispatcher beats every 15s
# for the same reason (`fleet/terminal.py`).
HEARTBEAT_INTERVAL_SECONDS = 15.0
# How often the idle path repeats its heartbeat line. The loop blocks up to
# `--watch-timeout` (30s) on each poll, so one line per idle tick is one line per
# ~30s: enough to prove the director is alive, not enough to bury an order in noise.
IDLE_HEARTBEAT_SECONDS = 30.0


def load_profile(path: Path | None = None) -> dict:
    """The director's elite profile: mission, KB, controls, FinOps floors, anti-patterns.

    A profile that is missing or malformed is a REFUSAL, not a default: the director
    steering a fleet on a half-loaded doctrine is worse than a director that will
    not start. The floor vocabulary below is derived from it, so the profile is
    the single source for what the director enforces.
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

# Capability routing is a CONSUMPTION of the vendored orchestration contract
# (ADR-0012; #300, #301), not a dialect grown here: the vocabulary lives in
# `fleet/profiles/routing.policy.json` and `fleet/routing.py` is its only reader.
# The director consults that module and keeps NO second copy of the mapping — the
# floors and defaults below are DERIVED from it, and the profile's own
# declaration is checked against the policy, so the doctrine and the machine
# cannot drift apart (the parity idiom the registry uses for its catalog and
# schema). A disagreement refuses the start rather than routing on a half-loaded
# contract, the same posture as `load_profile`.
try:
    ROUTING = routing.load()
except routing.RoutingRefusal as exc:
    raise SystemExit(f"[brain] REFUSED — cannot load the routing policy: {exc}")

# The FinOps floor the director enforces when it dispatches. Security, secrets,
# auth, identity and production-IaC work never drops below the high floor (fleet
# doctrine), so a lane whose name says so is escalated rather than accepted at
# flash/none. The vocabulary is the routing policy's, not this file's.
HIGH_FLOOR_LANES = ROUTING.high_floor_tokens
DEFAULT_TIER = PROFILE["finops"]["default_tier"]
DEFAULT_THINKING = PROFILE["finops"]["default_thinking"]
HIGH_TIER = PROFILE["finops"]["high_floor_tier"]
HIGH_THINKING = PROFILE["finops"]["high_floor_thinking"]
CONTROLS = tuple(PROFILE["controls"])

# One contract, two declarations: the profile states the doctrine, the routing
# policy carries it into the machine. Drift between them is a refusal.
_PROFILE_FLOORS = tuple(PROFILE["finops"]["high_floor_lanes"])
_PROFILE_FLOOR_BLOCK = (HIGH_TIER, HIGH_THINKING)
if HIGH_FLOOR_LANES != _PROFILE_FLOORS:
    raise SystemExit(
        f"[brain] REFUSED — the routing policy floors on {HIGH_FLOOR_LANES} but "
        f"{PROFILE_PATH} declares {_PROFILE_FLOORS}: one of the two is stale"
    )
if (DEFAULT_TIER, DEFAULT_THINKING) != ROUTING.default_block:
    raise SystemExit(
        f"[brain] REFUSED — the routing policy's default block {ROUTING.default_block} disagrees "
        f"with {PROFILE_PATH}'s ({DEFAULT_TIER}/{DEFAULT_THINKING})"
    )
if _PROFILE_FLOOR_BLOCK != ROUTING.high_floor_block:
    raise SystemExit(
        f"[brain] REFUSED — the routing policy's high floor {ROUTING.high_floor_block} disagrees "
        f"with {PROFILE_PATH}'s {_PROFILE_FLOOR_BLOCK}"
    )


def kb_sources(profile: dict) -> tuple[str, ...]:
    """The KB list a directive hands to the executor.

    ``own_repo`` paths are repo-relative (loadable from the worktree root);
    ``enterprise_instructions`` carry the principal's own instruction stack
    (home-relative, ``~``-prefixed) and are expanded here to real, openable
    paths; ``fleet_modules`` pointers are kept last as provenance (descriptive,
    not file paths).
    """
    kb = profile["kb"]
    sources = list(kb.get("own_repo") or [])
    for source in kb.get("enterprise_instructions") or []:
        sources.append(str(Path(source).expanduser()))
    sources.extend(kb.get("fleet_modules") or [])
    return tuple(sources)


KB_SOURCES = kb_sources(PROFILE)

# Order kinds that are not work: the principal may ask the director to report or to
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
        # What this build can READ on the envelope (issue #777). An emitter asks
        # the recipient's beat before choosing a dialect, so a rung that restarts
        # on this build switches the fleet's traffic to the current role names by
        # declaring it here — the deprecation window's terminus is this field.
        channel.ENVELOPE_SCHEMA_BEAT_KEY: channel.SCHEMA_VERSION_CURRENT,
        "ts": now_iso(),
    }
    HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
    tmp = HEARTBEAT.with_suffix(".tmp")
    tmp.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    tmp.replace(HEARTBEAT)


class OrderBeater:
    """Keeps the beat fresh while one order is handled; `stop()` joins.

    The director used to beat only once per poll and once *before* `handle_order`,
    so a long order looked dead: the watchdog treats a beat older than
    `channel.STALE_HEARTBEAT_SECONDS` as stale and SIGTERMs the rung — killing
    the director mid-order. `stop()` sets the flag *and* joins, because a beater
    that outlives its owner would write its owner's last idea of the world over
    the next iteration's beat (the dispatcher learned this the hard way, #281).
    """

    def __init__(
        self,
        state: str,
        *,
        started_at: str,
        commit: str,
        interval: float = HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        self._state = state
        self._started_at = started_at
        self._commit = commit
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._beat, name="brain-heartbeat", daemon=True)

    def _beat(self) -> None:
        while not self._stop.wait(self._interval):
            write_heartbeat(self._state, started_at=self._started_at, commit=self._commit)

    def start(self) -> OrderBeater:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)


def handle_stop(signum: int, frame: object) -> None:
    """A stopped director exits; it does not die mid-order.

    Without a handler the watchdog's SIGTERM killed the process outright, leaving
    whatever was half-written exactly as it fell. Raising `SystemExit` unwinds
    through the loop's `finally` (stopping the beater), and the sent-marker
    written *before* the send is what makes the restart safe: the order is
    suppressed on restart rather than dispatched a second time (#274).
    """
    print(f"[brain] signal {signum} — stopping cleanly (a restart suppresses any duplicate)", flush=True)
    raise SystemExit(128 + signum)


def install_stop_handlers() -> tuple[int, ...]:
    """Install the loop's stop handlers; returns the signals the loop owns."""
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, handle_stop)
    return (int(signal.SIGTERM), int(signal.SIGINT))


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
    return ROUTING.needs_high_floor(lane, title)


def order_risk(order: dict) -> str:
    """The risk level of an order, decided by the policy's floor rule."""
    return routing.HIGH if needs_high_floor(order) else routing.NORMAL


def choose_model(order: dict) -> tuple[str, str]:
    """The FinOps block for an order: capability-routed, then floored by risk.

    The base block is the policy's answer for the capability the work needs —
    resolved through the registry persona that owns that capability — and the
    principal's own `model` block may raise either value. The risk floor is applied
    last, so it can never be lowered (escalation is a floor, never a ceiling,
    ADR-0012 decision (b)). A capability claim the registry cannot back raises
    `RoutingRefusal`, which `handle_order` reports by name instead of dispatching
    on a default.
    """
    task = order.get("task") or {}
    model = order.get("model") or {}
    risk = order_risk(order)
    tier, thinking = ROUTING.tier_for(ROUTING.capability_for(task), risk)
    tier = str(model.get("tier") or tier)
    thinking = str(model.get("thinking") or thinking)
    if risk == routing.HIGH:
        tier, thinking = ROUTING.floor(tier, thinking)
    return tier, thinking


def routing_summary(order: dict) -> str:
    """The routing decision, as one directive-body line.

    ADR-0012 decision (b): dispatch is capability-routed from the registry
    personas that are actually dispatched. The directive therefore names the
    persona the work resolves to and the registry card that backed it, so the
    executor sees the decision rather than inferring it. A task whose lane and
    title claim no capability says exactly that instead of inventing one.
    """
    decision = ROUTING.route(order.get("task") or {})
    if decision["capability"] is None:
        return "Routing: the lane/title claims no capability — the declared default FinOps block applies"
    return (
        f"Routing: capability={decision['capability']} → persona={decision['persona']} "
        f"(registry card {decision['card']}) → {decision['tier']}/{decision['thinking']} "
        f"(risk {decision['risk']})"
    )


def order_reference(order: dict) -> str:
    """The id the principal can correlate on: the order's id, else its own reference."""
    return str(order.get("id") or order.get("correlation_id") or "")


# Characters that may not appear in a marker filename. The reference arrives in
# principal-supplied JSON and becomes a path component, so `../` must not be able
# to walk out of the marker directory. The rule lives with the state machine that
# builds the path (`markers.safe_reference`, #796) — one sanitizer, one spelling.


def order_marker(order: dict) -> Path | None:
    """The marker path for an order, or None when it carries no reference.

    The sanitizer lives with the state machine (`fleet/markers.py`): the reference
    arrives in principal-supplied JSON and becomes a path component, so `../` must
    not be able to walk out of the marker directory — and the reconciler, which
    derives the same path from the same reference, must agree byte for byte.
    """
    reference = order_reference(order)
    if not reference:
        return None
    return DISPATCH_MARKERS / f"{markers.safe_reference(reference)}.json"


def write_marker(marker: Path, order: dict, state: str, previous: markers.Marker | None = None) -> None:
    """Persist (atomically) what the director has done with this order so far.

    One writer for the marker set: the schema and the atomic write live in
    `fleet/markers.py`, so the director cannot drift from the reconciler that reads
    what it writes.

    Two fields are carried across a send and are NOT the sender's business:
    `sent_at` is stamped here (it is the timestamp the re-arm grace is measured
    from), and `attempts`/`next_attempt_at` — the #796 re-arm budget — survive it,
    because a send that reset the count would be an unbounded retry by
    construction. The one-shot `rearm` token is deliberately NOT carried: this
    write is what consumes it.
    """
    moment = markers.now_iso()
    markers.write(
        marker,
        markers.Marker(
            reference=order_reference(order),
            issue=order_issue(order),
            state=state,
            ts=moment,
            sent_at=moment if state == markers.SENT else (previous.sent_at if previous else None),
            attempts=previous.attempts if previous else 0,
            next_attempt_at=previous.next_attempt_at if previous else None,
            rearm=None,
            reason=previous.reason if previous else None,
            evidence=previous.evidence if previous else None,
            announced=previous.announced if previous else None,
        ),
    )


def directive_identity(order: dict) -> tuple[str | None, str | None]:
    """A deterministic (id, nonce) for the directive an order produces.

    `dispatch()` composed a directive with no id and no nonce, so `channel.send`
    stamped a fresh uuid4 on every call and its replay guard could never fire —
    a restart that re-read the order dispatched it twice (measured
    `['4242', '4242']`, #274). Deriving the identity from the order's own
    reference makes the SAME order produce the SAME directive, so the channel's
    existing replay check (sent/inbox/done) refuses the second send. Hashed
    because the id becomes a filename inside the channel.
    """
    reference = order_reference(order)
    if not reference:
        return None, None
    digest = hashlib.sha256(f"brain-directive:{reference}".encode("utf-8")).hexdigest()[:32]
    return f"brain-directive-{digest}", f"brain-nonce-{digest}"


def build_directive(order: dict) -> dict:
    """Compose the brain-signed directive the dispatcher will execute."""
    task = dict(order.get("task") or {})
    tier, thinking = choose_model(order)
    body = order.get("body") or ""
    # The profile travels with the order: the executor gets the KB it must read
    # and the evidence it must return, so dispatch quality does not depend on the
    # principal remembering to say it.
    kb = "\n".join(f"  - {source}" for source in KB_SOURCES)
    directive = {
        "from": channel.ROLE_DIRECTOR,
        "to": channel.ROLE_DISPATCHER,
        "type": "directive",
        "correlation_id": order_reference(order),
        "task": task,
        "model": {"tier": tier, "thinking": thinking},
        "body": (
            f"Operator order {order_reference(order)}:\n{body}\n\n"
            f"Brain doctrine: {PROFILE['mission']}\n"
            f"{routing_summary(order)}\n"
            f"Read first (fleet KB):\n{kb}\n"
            f"Return: {PROFILE['templates']['report']}"
        ),
    }
    if order.get("control") == "override" or task.get("override"):
        # The principal's override still travels the hierarchy: the principal orders
        # the director, and the director issues the control to the dispatcher.
        directive["control"] = "override"
    # Single-emit (issue #777): the dialect is the RECIPIENT's, negotiated from
    # the dispatcher's live beat — a dispatcher still running a build from before
    # this lane receives the retired spelling it can read, and the downgrade is
    # recorded rather than silent.
    channel.stamp_envelope(directive, recipient=channel.ROLE_DISPATCHER)
    message_id, nonce = directive_identity(order)
    if message_id and nonce:
        # Deterministic identity (see `directive_identity`): the same order must
        # never become a second directive after a restart.
        directive["id"] = message_id
        directive["nonce"] = nonce
    return directive


# -- the closure guard (issue #693) -------------------------------------------
# The director could compose a directive, mark it sent and hand it to the dispatcher for
# an issue that was already CLOSED. Measured: 17 such directives accumulated in
# the inbox and wedged the terminal loop — every one of them naming work the claim
# layer refuses `issue-closed` (`governance/dispatch/order.py`), i.e. a run that
# could never start. The board the director already holds answers the question, so it
# is asked HERE, in the one funnel every directive passes through — before the
# sent-marker is written and before the channel is invoked — and the refusal is
# reported instead of a directive being issued.
ISSUE_CLOSED = "issue-closed"
ISSUE_UNKNOWN = "unknown-issue"
BOARD_UNREADABLE = "cannot-assess"


def board_issue_state(number: int) -> tuple[str, str]:
    """``(state, detail)`` for an issue, read from the COMMITTED board snapshot.

    The states are the snapshot's own: ``open``, or the two refusals below. Both
    absences are refusals rather than defaults, because a silent allow is exactly
    how a dead directive gets in (#693):

    * ``unknown-issue`` — the snapshot does not carry the issue. An absent issue
      is NOT an open issue, and the claim layer refuses a claim for it with this
      same reason (`handle_decompose` says so in as many words: a child absent
      from the snapshot "would be refused `unknown-issue` the moment the wave
      arrives"), so a directive for it is dead on arrival. The detail names the
      remedy: refresh the snapshot.
    * ``cannot-assess`` — the snapshot itself cannot be read. The director does not
      know the state, and not knowing is never a licence to dispatch: the same
      posture as ``advance_ready``, which dispatches nothing when its board fetch
      fails.

    Only ``open`` lets a directive through.
    """
    try:
        board = snapshot_mod.load(BOARD_PATH)
    except (OSError, ValueError) as exc:
        return BOARD_UNREADABLE, f"the committed board snapshot {BOARD_PATH} is unreadable: {exc}"
    issue = board.get(number)
    if issue is None:
        return ISSUE_UNKNOWN, (
            f"#{number} is not in the committed board snapshot {BOARD_PATH} "
            "(refresh it: python3 governance/dispatch/cli.py snapshot --from-github)"
        )
    if issue.closed:
        return ISSUE_CLOSED, f"#{number} is closed on the committed board ({BOARD_PATH})"
    return "open", f"#{number} is open on the committed board ({BOARD_PATH})"


def closure_refusal(number: int) -> str | None:
    """Why no directive may be issued for this issue, or None when one may.

    The state is read from the committed board snapshot once per dispatch. The
    refusal names the state it read, so the principal sees WHICH board said so
    rather than a bare "refused".
    """
    state, detail = board_issue_state(number)
    if state == "open":
        return None
    return (
        f"{state} — {detail}; the brain issued no directive for #{number}: no sent-marker was "
        "written and the channel was not invoked, so nothing entered the sister's inbox"
    )


def dispatch(order: dict) -> tuple[bool, str]:
    """Send the composed directive through the channel; return (ok, message).

    Restart-safe by construction: the sent-marker is persisted *before* the
    channel is invoked, so a director killed mid-send finds it on restart and
    refuses to send the same order twice. Marker first, send second — the reverse
    order loses the at-most-once property this exists for. `(False, ...)` with a
    `DUPLICATE_SUPPRESSED` prefix means "already sent", not "send failed".

    The closure guard (#693) runs between composing the directive and sending it:
    an issue the committed board says is closed (or does not carry at all) is a
    refusal, and nothing is written or sent for it.

    The marker is consulted for its STATE, not merely its existence (#796):
    "already sent" and "finished" are different facts, and the refusal names
    which one holds. A marker whose state is terminal, or whose reality the
    reconciler has not examined, still refuses — at-most-once is intact. Only a
    marker carrying the reconciler's one-shot `rearm` token is sent past, and this
    call consumes it: a restart re-reading a plan, or a principal re-ordering the
    same order, finds no token and is still suppressed.
    """
    marker = order_marker(order)
    record: markers.Marker | None = None
    if marker is not None and marker.exists():
        record = markers.read(marker)
        if record is None:
            # Unreadable is not absent: never send on ignorance.
            return False, (
                f"{DUPLICATE_SUPPRESSED} — {marker.name} exists but cannot be read; the brain "
                "suppresses the order rather than sending it blind"
            )
        if not record.armed:
            if record.terminal:
                return False, (
                    f"{PARKED_SUPPRESSED} — {marker.name} records state={record.state}"
                    f"{' — ' + record.reason if record.reason else ''}; the order is finished, not in "
                    "flight, and stays parked until an operator re-arms it by name: "
                    f"python3 fleet/markers.py rearm --reference {record.reference}"
                )
            return False, (
                f"{DUPLICATE_SUPPRESSED} — {marker.name} records that this order was already sent "
                f"(state={record.state}, attempts={record.attempts})"
            )
    directive = build_directive(order)
    # The closure guard (#693), between composing the directive and sending it. The
    # routing question above is answered first because it asks about the ORDER
    # (can the registry back the capability it claims?); this one asks about the
    # BOARD. Both are refusals, and neither writes a marker nor reaches the
    # channel — so no directive for a closed (or unassessable) issue can enter the
    # dispatcher's inbox. An order that names no issue has no state to look up.
    number = order_issue(order)
    if number is not None:
        refusal = closure_refusal(number)
        if refusal is not None:
            return False, refusal
    # The master-health guard (RCA 2026-09-17 fix #5), between the closure guard
    # and the send: a lane whose issue is open but whose PR could never land
    # because master itself is red gets refused here too, before anything is
    # written or sent — same "no marker, no channel call" refusal shape as the
    # closure guard above.
    refusal = master_health_refusal(order)
    if refusal is not None:
        return False, refusal
    if marker is not None:
        write_marker(marker, order, markers.SENDING, previous=record)
    result = subprocess.run(
        ["python3", CHANNEL, "send", "--message", json.dumps(directive)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    output = (result.stdout + result.stderr).strip()
    ok = result.returncode == 0
    if marker is not None:
        if ok:
            write_marker(marker, order, markers.SENT, previous=record)
        elif record is not None and record.attempts:
            # A RE-ARMED marker is not dropped on a refused re-send. The earlier
            # send is real evidence, and the re-arm budget lives in this very file:
            # unlinking it would reset the count on every refusal and make the
            # bound decorative. The token is consumed (this write drops it) and the
            # refusal is paced by the backoff, so the next attempt is not a spin.
            markers.write(
                marker,
                markers.Marker(
                    **{
                        **record.payload(),
                        "state": markers.SENT,
                        "rearm": None,
                        "reason": f"the re-send was refused: {output[:200]}",
                    }
                ),
            )
        else:
            # The channel answered non-zero, and `cmd_send` refuses *before* it
            # queues anything: nothing left the director, so the marker is dropped
            # (a corrected order must still be able to go out). The pathological
            # "queued, then exited non-zero" case stays covered by the
            # directive's deterministic id, which the channel refuses on replay.
            try:
                marker.unlink()
            except OSError:
                pass
    return ok, output


WAVES = FLEET_DIR / "waves"

# -- the filing seam (issue #320) --------------------------------------------
# Every issue the director files goes through `governance/conformance/filing.py`,
# which derives the declaring labels from the conformance policy and REFUSES a
# filing that cannot derive them — so the director can no longer create an issue the
# conformance gate rejects afterwards (`governance/lifecycle`'s
# `FILING_LABELS_MISSING` only *detects* that result; #174 repairs the legacy
# issues that were filed unclassified, this issue prevents the next one).
CONFORMANCE_DIR = ROOT / "governance" / "conformance"
CONFORMANCE_POLICY = CONFORMANCE_DIR / "policy.yaml"
REPO = "kushin77/agent-orchestrator"
# Child-spec keys a decomposition may declare explicitly. Anything it leaves out is
# derived from the policy, which is what keeps the labels out of this module.
DECLARING_KEYS = ("class", "type", "priority", "area", "gdc", "pillar")


def _conformance():
    """Load the conformance policy loader and the filing seam (issue #320).

    `governance/conformance` uses flat sibling imports by repo convention (`filing`
    imports `model`), and this process already has *another* `model` importable:
    `governance/dispatch/model.py`, which this module put on `sys.path`. Resolving
    the package's flat names against that one would make the seam import the wrong
    model, so the conformance directory goes first, the flat names are taken for the
    duration of the load, and every name the process already had is restored
    afterwards — the filing seam must not depend on, or change, the director's import
    order. The result is cached, so `FilingRefused` keeps one identity per process
    (a caller must be able to catch the refusal the seam raised).
    """
    global _CONFORMANCE
    if _CONFORMANCE:
        return _CONFORMANCE

    directory = str(CONFORMANCE_DIR)
    flat_names = ("model", "checker", "filing")
    added = directory not in sys.path
    stash = {name: sys.modules.pop(name) for name in flat_names if name in sys.modules}
    if added:
        sys.path.insert(0, directory)
    try:
        import checker  # noqa: PLC0415 - imported after the path and names are clear
        import filing  # noqa: PLC0415

        loaded = (checker, filing)
    finally:
        if added and directory in sys.path:
            sys.path.remove(directory)
        for name in flat_names:
            sys.modules.pop(name, None)
        sys.modules.update(stash)
    _CONFORMANCE = loaded
    return loaded


_CONFORMANCE: tuple = ()


def _filing_exception() -> type:
    """`filing.FilingRefused` from the cached seam (see `_conformance`)."""
    return _conformance()[1].FilingRefused


def gh_issue_create(
    title: str, body: str, declaring: dict | None = None
) -> int:
    """File a micro-task child issue; returns its number.

    The declaring labels are *derived* from `governance/conformance/policy.yaml`
    through the single filing seam (issue #320): the caller may name a class or a
    companion, but anything it leaves out comes from the policy's `filing` block, and
    a filing that cannot derive a required label is REFUSED (`FilingRefused`) before
    `gh` runs — nothing is filed, so nothing has to be repaired later (#174 owns the
    legacy repair; prevention is this issue's).

    Raises `RuntimeError` when `gh` itself fails, as before.
    """
    checker, filing = _conformance()
    try:
        policy = checker.load_policy(CONFORMANCE_POLICY)
    except checker.PolicyUnavailable as exc:
        raise filing.FilingRefused(
            "the conformance policy %s cannot be read: %s" % (CONFORMANCE_POLICY, exc)
        ) from exc

    declared = dict(declaring or {})
    request = filing.FilingRequest(
        title=title,
        body=body,
        repo=REPO,
        declared_class=str(declared.pop("class", "") or ""),
        declaring=declared,
    )
    return int(filing.file_issue(request, policy).number)


def file_child_issue(title: str, body: str, declaring: dict) -> int:
    """File one decomposed child through the seam.

    A child spec that declares nothing extra calls the seam with no override at all,
    so the policy alone decides the labels; a spec that declares a class or a
    companion states only that and has the rest derived.
    """
    if declaring:
        return gh_issue_create(title, body, declaring=declaring)
    return gh_issue_create(title, body)


# -- the micro-decomposition POLICY (epic #707 lane F4 / issue #719) -----------
# The director could already *file* a decomposition; what it could not do was refuse
# one that was not a decomposition at all. What follows are the board FACTS the
# policy needs — the rule itself is pure and offline (`fleet/decompose_policy.py`)
# — and every guard runs BEFORE the first `gh issue create`, so a refused wave
# files nothing and therefore has nothing to repair afterwards.
FOCUS_PATH = ROOT / ".board" / "focus.json"


def resolve_active_epic() -> tuple[int | None, str]:
    """``(the ACTIVE epic, the reason when there is none)``.

    Children are bound to the epic the fleet is actually driving (lane F1's
    ``governance/dispatch/focus.py``), never to whichever number the order happens
    to name: filing a child against a closed epic — or against nothing — is the
    orphan the single-epic focus exists to prevent. Offline: the committed board
    snapshot plus the pinned focus, both read from the repo root so the answer does
    not depend on the director's working directory.
    """
    try:
        board = snapshot_mod.load(BOARD_PATH)
    except (OSError, ValueError) as exc:
        return None, f"the committed board snapshot {BOARD_PATH} is unreadable: {exc}"
    try:
        epic = focus_mod.active(board, FOCUS_PATH)
    except focus_mod.FocusInvalid as exc:
        return None, f"the board focus is unreadable: {exc}"
    if epic is None:
        return None, (
            f"the board has no active epic (nothing pinned in {FOCUS_PATH} and no workable "
            "open epic) — children are never filed against nothing"
        )
    return epic.number, ""


def _pinned_wave_cap() -> int | None:
    """The focus's own ``wave_cap``, or ``None`` when the focus is absent/broken."""
    try:
        focus = focus_mod.load(FOCUS_PATH)
    except focus_mod.FocusInvalid:
        return None
    return focus.wave_cap if focus is not None else None


# -- the epic-close ADVANCE (epic #707 lane F5 / issue #720) --------------------
# The director could already decompose the ACTIVE epic into micro-children (F4/#719);
# what it could not do was *leave* an epic. A pinned focus outlives the epic it
# pins — `focus.resolve` falls back to the lowest workable epic only when the
# pinned one has closed — so nothing moved the pin, and a fleet that finished 707
# would keep resolving 707 forever. Advancing is therefore a decision, and it is
# PURE: no clock, no network, no filesystem. The snapshot and the focus come in,
# the next focus or None comes out, so the rule is provable in a unit test rather
# than asserted in a docstring. The board read lives in `advance_epic_focus`.
ADVANCE = "advance"
NO_EPIC = "no-epic"
BOARD_COMPLETE = "board-complete"


def advance_focus(
    snapshot: object,
    focus: "focus_mod.Focus | None",
    *,
    reason: str = ADVANCE,
) -> "focus_mod.Focus | None":
    """The focus to pin after an epic closed — the next resolvable epic, or None.

    ``reason`` labels why the question is being asked (``advance`` after an epic
    closed, ``no-epic`` when a tick found none pinned). It never changes the
    resolution — every rule below is reached irrespective of it — and the caller
    carries it into the note it reports, so the pin keeps the reason it moved.

    Three rules, in order:

    1. **Nothing pinned.** Resolve from scratch: the pool is the only work the
       fleet can see (there is no active epic to exclude), so a workable epic or a
       non-empty pool means carry on; with neither, there is nothing to focus on
       and the board is complete.
    2. **The pinned epic is still open.** The focus does not move on its own — not
       even when no child is open *right now*. The snapshot the director holds has
       already been measured with an empty child set at a legitimate moment (#719's
       seam was tested exactly there), so advancing on "no open children today"
       would drop an epic mid-flight. The advance is driven by the epic's CLOSURE,
       never by an empty wave.
    3. **The pinned epic is gone** (closed, or no longer an epic) — the reason this
       was called. Resolve the next epic; the closing epic is excluded explicitly,
       because a snapshot read before the board is refreshed still shows it open
       and "advance" must not re-pin what just closed. No next epic but a waiting
       pool means the focus holds no active epic and the pool is drained; neither
       means the board is complete.
    """
    if focus is None:
        epic = focus_mod.resolve(snapshot, pinned=None)
        if epic is not None:
            return focus_mod.Focus.pinned(epic.number)
        return focus_mod.Focus.pinned(None) if focus_mod.pooled(snapshot, None) else None

    current = focus_mod.resolve(snapshot, pinned=focus.active_epic)
    if current is not None and current.number == focus.active_epic:
        return focus

    nxt = focus_mod.resolve(snapshot, pinned=None)
    if nxt is not None and nxt.number != focus.active_epic:
        return focus_mod.Focus.pinned(nxt.number)

    # No workable epic left. Keep the pool rather than abandoning it: a pooled
    # issue is never silently dropped (#718/#720).
    pool = focus_mod.pooled(snapshot, focus.active_epic)
    if pool:
        return focus_mod.Focus(active_epic=None, activated_at=focus.activated_at,
                               wave_cap=focus.wave_cap, max_agents=focus.max_agents,
                               pooled=tuple(issue.number for issue in pool))
    return None


def advance_epic_focus() -> tuple[bool, str]:
    """Advance the pinned focus off the epic that closed, and report what moved.

    The effectful half of the epic-close advance: read the committed board and the
    pinned focus, decide with the pure ``advance_focus``, and write the new pin (or
    report ``board-complete``). Offline — the committed snapshot is the board — and
    a focus that is already complete is reported, not rewritten, so a tick that
    changes nothing does not churn the file.
    """
    try:
        board = snapshot_mod.load(BOARD_PATH)
    except (OSError, ValueError) as exc:
        return False, f"the committed board snapshot {BOARD_PATH} is unreadable: {exc}"
    try:
        focus = focus_mod.load(FOCUS_PATH)
    except focus_mod.FocusInvalid as exc:
        return False, f"the board focus is unreadable: {exc}"
    reason = ADVANCE if focus is not None else NO_EPIC
    nxt = advance_focus(board, focus, reason=reason)
    pinned = focus.active_epic if focus is not None else None
    if nxt is None:
        if pinned is None:
            return True, f"{BOARD_COMPLETE} — no workable epic and the pool is empty; nothing is pinned"
        return True, (
            f"{BOARD_COMPLETE} — #{pinned} closed, no workable epic remains and the pool is empty; "
            "the board is complete"
        )
    if focus is not None and nxt == focus:
        return True, f"the focus stays on #{nxt.active_epic} (still open); the board has not moved"
    focus_mod.save(nxt, FOCUS_PATH)
    if nxt.active_epic is None:
        return True, (
            f"advanced off #{pinned}: no workable epic remains, so the focus holds no active epic "
            f"and the pool ({len(nxt.pooled)} waiting) is what there is to drain"
        )
    if pinned is None:
        return True, f"pinned the focus to #{nxt.active_epic} — no epic was activated before"
    return True, f"advanced the focus from #{pinned} to #{nxt.active_epic}; the new pin is written"


def open_board_index() -> list[tuple[int, str, str, str]]:
    """The OPEN issues a duplicate is refused against: ``(number, title, lane, verify)``.

    The committed snapshot stores titles and no bodies, so the lane and the
    ``Verify:`` line are empty here and the title carries the identity; the policy
    still compares lane + ``Verify:`` whenever a caller has them (the live board
    does). An unreadable snapshot yields an EMPTY index rather than an exception —
    the sizing rule and the cap still bind, and the intra-wave duplicate guard
    still catches a child filed twice in one spec.
    """
    try:
        board = snapshot_mod.load(BOARD_PATH)
    except (OSError, ValueError):
        return []
    return [(issue.number, issue.title, "", "") for issue in board.open_issues()]


def decompose_problem(spec: object) -> str | None:
    """Why this decomposition spec cannot be filed, or None when it is well formed.

    Validating up front is the point: `handle_decompose` used a bare subscript for
    `parent_issue`, so a spec that omitted it raised `KeyError` out of `loop` and
    killed the director process (#275) — and `gh_issue_create` raises `RuntimeError`
    on any `gh` failure, the same escape. A malformed order is a refusal.
    """
    if not isinstance(spec, dict):
        return "decompose order carries no decompose object — nothing to file"
    children = spec.get("children")
    if not isinstance(children, list) or not children:
        return "decompose order carries no children — nothing to file"
    if not all(isinstance(child, dict) for child in children):
        return "decompose children must each be an object (title/lane/verify) — nothing to file"
    parent = spec.get("parent_issue")
    if isinstance(parent, bool) or not isinstance(parent, int) or parent < 1:
        return (
            "decompose order names no parent_issue (it must be a positive integer) — "
            "no child was filed and nothing was dispatched"
        )
    return None


def handle_decompose(order: dict) -> tuple[bool, str]:
    """Turn a decomposition spec into child issues and dispatch the ready wave.

    Micro-decomposition: one parent issue becomes N small, collision-free child
    issues (the pmo-sme discipline), filed with a `Parent: #N` marker so the chain
    gate recognises them, and the ready wave is dispatched immediately — the director
    prepares the next waves in advance instead of waiting for the parent.

    The wave is bound to the ACTIVE epic and must pass the micro-decomposition
    policy first (epic #707 lane F4 / issue #719): a child is filed only when it is
    sized as a micro-child (1 lane, 1 runnable `Verify:`, 1 criterion, a non-empty
    `Files:` set), it is not already open on the board, and the wave is within the
    cap. Every guard runs BEFORE the first `gh issue create`, so a refused wave
    files nothing at all.
    """
    spec = (order.get("task") or {}).get("decompose")
    problem = decompose_problem(spec)
    if problem:
        return False, problem

    epic, reason = resolve_active_epic()
    if epic is None:
        return False, f"REFUSED — {reason}; no child was filed and nothing was dispatched"
    named = int(spec["parent_issue"])
    if named != epic:
        return False, (
            f"REFUSED — this order decomposes #{named}, but the ACTIVE epic is #{epic}: children "
            "are bound to the epic the fleet is driving (epic #707 single-epic focus), so nothing "
            f"was filed. Re-issue against #{epic}, or move the focus first."
        )

    try:
        cap = decompose_policy.effective_cap(focus_wave_cap=_pinned_wave_cap())
    except ValueError as exc:
        return False, f"REFUSED — {exc}; the wave cap is not defaulted silently, so no child was filed"
    problems = decompose_policy.wave_problems(spec["children"], open_issues=open_board_index(), cap=cap)
    if problems:
        return False, (
            "REFUSED — the wave violates the micro-decomposition policy: "
            + "; ".join(problems)
            + " — no child was filed"
        )

    parent = epic
    WAVES.mkdir(parents=True, exist_ok=True)
    plan = {"parent": parent, "children": [], "dispatched": []}
    for index, child in enumerate(spec["children"]):
        title = str(child.get("title", "")).strip()
        # The policy has already proved there is exactly one of each; reading them
        # back through it (rather than re-parsing the spec here) keeps the one
        # definition of "the lane/verify/criterion of this child".
        lane = decompose_policy.lanes(child)[0]
        verify = decompose_policy.verify_lines(child)[0]
        criterion = decompose_policy.criteria(child)[0]
        files = ", ".join(decompose_policy.files(child))
        body = (
            f"Parent: #{parent}\n\n"
            f"Lane: {lane}\n\nFiles: {files}\n\nVerify: `{verify}`\n\n"
            f"Criterion: {criterion}\n\n"
            f"Micro-task {index} of #{parent} (decomposed by the brain, pmo-sme discipline)."
        )
        # Whatever this child does not declare is derived from the conformance
        # policy, so a decomposition cannot file an unclassified issue (issue #320).
        declaring = {
            key: str(child[key]) for key in DECLARING_KEYS if child.get(key)
        }
        try:
            number = file_child_issue(title, body, declaring)
        except _filing_exception() as exc:
            # An explicit refusal, reported to the principal, is the whole point: the
            # alternative is filing an issue the conformance gate rejects later.
            return False, exc.loud_message
        plan["children"].append(
            {
                "index": index,
                "issue": number,
                "lane": lane,
                # Stored, not dropped: `dispatch_ready_children` reads it back onto
                # the wave directive, and a directive with `title == ""` defeats
                # the title-based FinOps high-floor detection (#274 review, F10).
                "title": title,
                "verify": verify,
                "depends_on": [int(d) for d in child.get("depends_on", [])],
            }
        )
    (WAVES / f"{parent}.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    # The freshly filed children are absent from the committed snapshot, so a claim
    # would be refused `unknown-issue` the moment the wave arrives. Refresh first.
    refresh = subprocess.run(
        ["python3", str(ROOT / "governance" / "dispatch" / "cli.py"), "snapshot", "--from-github"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if refresh.returncode != 0:
        return False, f"children filed, but the board snapshot failed to refresh: {refresh.stderr.strip()[-300:]}"
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


def board_closed(number: int) -> bool | None:
    """Closed per the COMMITTED board snapshot? None when it cannot be read.

    The offline half of the reality probe: the wave path reconciles a handful of
    child markers per idle tick, and paying a `gh` round trip for each of them
    would be both slow and unnecessary — the committed snapshot already answers
    the question the dispatch-time closure guard (#693) asks. `unknown-issue` and
    an unreadable snapshot both read as None, i.e. CANNOT-ASSESS: never re-arm on
    an unknown.
    """
    state, _ = board_issue_state(number)
    if state == "open":
        return False
    return True if state == ISSUE_CLOSED else None


def dispatch_ready_children(parent: int, plan: dict, probe: markers.Probe | None = None) -> list[int]:
    """Dispatch any child whose dependencies are all closed and not yet dispatched."""
    probe = markers.FleetProbe(closed_lookup=board_closed) if probe is None else probe
    dispatched = []
    for child in plan["children"]:
        if child["issue"] in plan["dispatched"]:
            continue
        deps = [plan["children"][d]["issue"] for d in child["depends_on"] if d < len(plan["children"])]
        if deps and not all(issue_is_closed(d) for d in deps):
            continue
        order = {
            "type": "directive",
            # A stable reference per child: it becomes the directive's identity (and
            # its correlation id), so a director restarted mid-wave suppresses the
            # duplicate instead of dispatching the child a second time.
            "id": f"child-{parent}-{child['issue']}",
            "task": {
                "issue": child["issue"],
                "lane": child["lane"],
                "title": child.get("title") or "",
            },
            "body": f"Micro-task of #{parent}. Verify: {child['verify']}",
        }
        # Reconcile THIS child's marker before dispatching it (#796): a child whose
        # directive died was suppressed for ever, and the wave plan's own
        # `dispatched` list cannot tell a delivered micro-task from a dead one.
        for finding in markers.reconcile_reference(order["id"], child["issue"], probe, directory=DISPATCH_MARKERS):
            print(finding.render(), flush=True)
        ok, message = dispatch(order)
        if suppressed(message):
            # Already out, or finished and parked — either way the plan must stop
            # retrying it, and the principal must SEE which of the two it is (#796).
            plan["dispatched"].append(child["issue"])
            print(f"[brain] wave #{parent}: micro-task #{child['issue']} not dispatched — {message}", flush=True)
        elif ok:
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


def advance_ready() -> list[int]:
    """Dispatch the dependency-free ready set a completion just unlocked (#701).

    The completion signal is the board itself: an issue's parent or blocker has
    closed. The director fetches the live board (the only way it sees a closure),
    recomputes the ready set through the dispatch order rules — graph advance,
    not kanban scavenging (GR-20) — and dispatches each newly-ready issue to the
    dispatcher in the same cycle, so the pipeline stays full without the director acting
    as a serial queue. Each dispatch carries a stable marker, so a later tick
    never re-dispatches an issue that is already out.

    The board is fetched into memory (`github_records` + `build_snapshot`) rather
    than via the `snapshot --from-github` CLI, so a completion-triggered advance
    never rewrites the committed `.board/snapshot.json` as a side effect — that
    file is refreshed by the board-maintenance path, not by this read-only
    recomputation.
    """
    try:
        board = snapshot_mod.build_snapshot(snapshot_mod.github_records(REPO), source=REPO)
    except RuntimeError as exc:
        print(f"[brain] advance: board fetch failed — {exc}", file=sys.stderr, flush=True)
        return []
    live = claims.active_claims(claims.read_ledger())
    claimed = frozenset(live)
    # Reconcile the marker set against reality BEFORE the ready set is walked
    # (#796). The idle path is the only tick that runs without an order, so it is
    # the tick that must notice a marker whose directive died. The board is already
    # in memory here, so the whole marker set is reconciled at no extra cost — and
    # this is what turns "sent once" back into "re-eligible, counted" (or, when the
    # budget is done, into a PARKED finding naming the issue).
    probe = markers.FleetProbe(board=board)
    for finding in markers.reconcile(probe, directory=DISPATCH_MARKERS):
        print(finding.render(), flush=True)
    dispatched = []
    for issue in order.advance_candidates(board, claimed=claimed):
        directive_order = {
            "type": "directive",
            # A stable reference per issue: it becomes the directive's identity
            # (and marker), so a director that re-runs the advance on a later tick
            # suppresses the duplicate instead of dispatching it a second time.
            "id": f"advance-{issue.number}",
            "task": {"issue": issue.number, "lane": "", "title": issue.title},
            "body": (
                f"Completion-triggered advance: #{issue.number} is dependency-free "
                f"(its parent/blockers are closed). Verify per its acceptance criteria."
            ),
        }
        ok, message = dispatch(directive_order)
        if suppressed(message):
            # NEVER a silent continue (#796): the defect was precisely that a
            # suppression read the same whether the directive was in flight or
            # dead. Silence here is what let three ready issues stay invisible.
            print(f"[brain] advance: #{issue.number} not dispatched — {message}", flush=True)
            continue
        if ok:
            dispatched.append(issue.number)
        else:
            print(
                f"[brain] advance: dispatch of #{issue.number} refused: {message}",
                file=sys.stderr,
                flush=True,
            )
    return dispatched


def handle_order(order: dict) -> tuple[bool, str]:
    """One order in, one directive or one refusal out. Never a silent drop."""
    kind = str((order.get("task") or {}).get("kind") or order.get("kind") or "").lower()
    if (order.get("task") or {}).get("decompose"):
        return handle_decompose(order)
    if kind == "steer":
        return handle_steer(order)
    if kind in NON_WORK_KINDS:
        level, reasons = _health()
        return True, f"order kind '{kind}' — no dispatch; fleet health {level}: {'; '.join(reasons)}"

    issue = order_issue(order)
    if issue is None:
        return False, (
            "order names no issue (task.issue missing or not a positive integer) — the brain "
            "dispatches work only for a real issue; nothing was sent to the sister"
        )

    try:
        ok, message = dispatch(order)
    except routing.RoutingRefusal as exc:
        # A routing refusal is a refusal, not a crash: the policy could not back
        # the capability the order claims, so nothing is dispatched (ADR-0012).
        return False, f"dispatch refused for #{issue}: {exc}"
    if not ok:
        if suppressed(message):
            # Not a failure: the directive is already out, or the order is finished
            # and parked. Consuming the order (the loop does that next) is what
            # breaks the duplicate-dispatch cycle; the message names the state.
            return True, f"#{issue} was already dispatched — {message}"
        return False, f"dispatch refused for #{issue}: {message}"
    tier, thinking = choose_model(order)
    return True, f"dispatched #{issue} to the sister at {tier}/{thinking} — {message}"


def handle_steer(order: dict) -> tuple[bool, str]:
    """Relay a principal steer order to the dispatcher through the channel (#367).

    The hierarchy stays intact: the principal never addresses the dispatcher, so a
    mid-run hint travels principal → director → dispatcher as a `steer` message the
    channel validates (brain-signed, correlated to an in-flight directive) and
    the running loop delivers. The director adds no content of its own — it
    authenticates and forwards the order, nothing more.
    """
    task = order.get("task") or {}
    target = str(task.get("directive") or "").strip()
    hint = " ".join(str(order.get("body") or "").split())
    if not target:
        return False, "steer order names no directive (task.directive) — nothing was steered"
    if not channel.DIRECTIVE_ID_RE.fullmatch(target):
        return False, f"steer order names an unsafe directive id {target!r} — nothing was steered"
    if not hint:
        return False, "steer order carries no hint (body) — nothing was steered"
    result = subprocess.run(
        ["python3", CHANNEL, "steer", "--directive", target, "--body", hint],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False, f"steer refused by the channel: {(result.stdout + result.stderr).strip()[-300:]}"
    return True, f"steer relayed to the sister for run {target}"


def safe_handle_order(order: dict) -> tuple[bool, str]:
    """Run `handle_order`, turning ANY handler error into a refusal.

    The director is the fleet's middle rung: a malformed order must cost one
    refusal, not the process. Measured (#275): a decompose order without
    `parent_issue` raised `KeyError` straight out of `loop` and killed the director,
    and nothing restarted it until the next watchdog tick. `SystemExit` — the stop
    handler — is deliberately NOT caught: a stop is a stop.
    """
    try:
        return handle_order(order)
    except Exception as exc:  # noqa: BLE001 — surviving the order IS the point
        print(
            f"[brain] handler error on order {order_reference(order) or '-'}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return False, f"handler error {type(exc).__name__}: {exc} — the order was refused; the loop continues"


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


# --- the context stream (what the principal's director window shows) -------------
#
# The director runs detached and this process's stdout is captured to
# `.fleet/brain.log`, which is what the `brain` window in the `fleet` tmux
# session tails. Without this block that file is empty: the loop printed nothing
# at startup, nothing that identified the order it was handling, and nothing
# while idle — so a working director and a wedged one looked identical from the
# principal's side. Every line below is built by a pure function over plain data,
# so the tests assert the text instead of the principal having to eyeball it.


def read_json(path: Path) -> dict | None:
    """A JSON object from `path`, or None — a missing/corrupt file is not a crash."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _count(directory: Path) -> int:
    return len(list(directory.glob("*.json"))) if directory.exists() else 0


def wave_progress() -> dict[int, list[int]]:
    """Parent issue -> the children already dispatched from that wave plan."""
    progress: dict[int, list[int]] = {}
    if not WAVES.exists():
        return progress
    for path in sorted(WAVES.glob("*.json")):
        plan = read_json(path)
        if plan is None or "parent" not in plan:
            continue
        try:
            progress[int(plan["parent"])] = [int(issue) for issue in plan.get("dispatched") or []]
        except (TypeError, ValueError):
            continue
    return progress


def format_waves(progress: dict[int, list[int]]) -> str:
    """`waves: #219=[232]` — compact, greppable, and empty-safe."""
    if not progress:
        return "waves: none"
    rendered = ", ".join(f"#{parent}={children}" for parent, children in sorted(progress.items()))
    return f"waves: {rendered}"


def held_claims() -> int:
    """Live claims, read from the dispatch ledger's own status output."""
    try:
        result = subprocess.run(
            ["python3", str(ROOT / "governance" / "dispatch" / "cli.py"), "status"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    for line in result.stdout.splitlines():
        if "live claims:" in line:
            fields = line.split("live claims:")[1].strip().split()
            return int(fields[0]) if fields and fields[0].isdigit() else 0
    return 0


def watchdog_state() -> str:
    """The watchdog's own last verdict — who is keeping this rung alive."""
    try:
        lines = (FLEET_DIR / "watchdog.log").read_text(encoding="utf-8").splitlines()[-20:]
    except OSError:
        return "unknown"
    if any("RESPAWN FAILED" in line for line in lines):
        return "failing"
    if any("respawned" in line for line in lines):
        return "recovering"
    if any("healthy" in line for line in lines):
        return "healthy"
    return "unknown"


def fleet_facts() -> dict:
    """The facts every context line reports, gathered once per print."""
    return {
        "orders_pending": _count(channel.BRAIN_INBOX),
        "dispatched": _count(channel.BRAIN_DONE),
        "waves": wave_progress(),
        "claims": held_claims(),
        "head": channel.head_commit(),
        "watchdog": watchdog_state(),
    }


def status_line(facts: dict, *, idle_seconds: int | None = None) -> str:
    """One compact context line: the startup form, or the idle heartbeat.

    `idle_seconds=None` renders the startup form (`up`); a number renders the
    idle form the director repeats every IDLE_HEARTBEAT_SECONDS.
    """
    position = "up" if idle_seconds is None else f"idle {idle_seconds}s"
    return (
        f"[brain] {position} | orders pending={facts.get('orders_pending', 0)} | "
        f"dispatched={facts.get('dispatched', 0)} | {format_waves(facts.get('waves') or {})} | "
        f"claims={facts.get('claims', 0)} | HEAD={facts.get('head', 'unknown')} | "
        f"watchdog={facts.get('watchdog', 'unknown')}"
    )


def status_header(facts: dict, *, pid: int) -> str:
    """The startup banner: what this process is and where its stream goes."""
    return "\n".join(
        (
            "=" * 72,
            "  BRAIN — session fleet operating model (M26)",
            "  Hierarchy: operator -> BRAIN -> sister -> subagents",
            f"  repo     {ROOT}",
            f"  pid      {pid}",
            f"  orders   {channel.BRAIN_INBOX}",
            "           order it: python3 fleet/channel.py order --message '<json>'",
            f"  replies  {channel.BRAIN_OUTBOX}",
            "           read them: python3 fleet/channel.py brain-outbox",
            f"  log      {LOG_PATH}",
            f"  {status_line(facts)}",
            "=" * 72,
        )
    )


def order_line(order: dict) -> str:
    """The order's id, the issue it names, and the task and body it carried."""
    task = json.dumps(order.get("task") or {}, sort_keys=True)
    body = " ".join(str(order.get("body") or "").split())
    return (
        f"[brain] order {order_reference(order) or '-'} ({channel_issue(order)}) "
        f"| task={task} | body={body}"
    )


def outcome_line(order: dict, ok: bool, report: str) -> str:
    """What the director did with the order: dispatched, ack, or the refusal itself."""
    reference = order_reference(order) or "-"
    if not ok:
        return f"[brain] {reference}: → refused: {report}"
    issue = order_issue(order)
    if issue is None:
        return f"[brain] {reference}: → ack (no dispatch): {report}"
    tier, thinking = choose_model(order)
    return f"[brain] {reference}: → dispatched #{issue} at {tier}/{thinking} — {report}"


def wave_line(advanced: list[int]) -> str:
    return f"[brain] → advanced waves: dispatched {advanced}"


def loop(args: argparse.Namespace) -> int:
    if not singleton.guard("brain", "bash fleet/run-fleet.sh (or: bash fleet/brain.sh)"):
        return 1
    install_stop_handlers()
    started_at = now_iso()
    commit = channel.head_commit()
    print(status_header(fleet_facts(), pid=os.getpid()), flush=True)
    idle_since: float | None = None
    last_idle_line = 0.0
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
                print(wave_line(advanced), flush=True)
            # The epic-close advance (epic #707 lane F5/#720). It runs on the idle
            # path, after the wave advance, because that is the only tick that runs
            # without an order: when the fleet is finished, moving to the next epic
            # is the work. A focus that is still open reports the same thing every
            # tick — "the board has not moved" — and is NOT rewritten, so the idle
            # path stays quiet instead of churning `.board/focus.json`.
            moved, note = advance_epic_focus()
            if not moved or (BOARD_COMPLETE in note or "has not moved" not in note):
                print(f"[brain] {note}", flush=True)
            ready = advance_ready()
            if ready:
                print(wave_line(ready), flush=True)
            moment = time.monotonic()
            if idle_since is None:
                idle_since = moment
            # One heartbeat as soon as the director goes idle, then one every
            # IDLE_HEARTBEAT_SECONDS: the principal can tell "waiting for an
            # order" from "stuck" without the log becoming a wall of timestamps.
            if last_idle_line == 0.0 or moment - last_idle_line >= IDLE_HEARTBEAT_SECONDS:
                print(status_line(fleet_facts(), idle_seconds=int(moment - idle_since)), flush=True)
                last_idle_line = moment
            if args.once:
                return 0
            continue
        idle_since = None
        last_idle_line = 0.0
        try:
            order = json.loads(watch.stdout)
        except json.JSONDecodeError:
            print("[brain] unparseable order — refusing", file=sys.stderr, flush=True)
            continue

        order_id = order.get("id", "")
        print(order_line(order), flush=True)
        write_heartbeat("dispatching", started_at=started_at, commit=commit)
        # Beat while the handler runs: a decompose order files N child issues and
        # refreshes the board, which outlives the watchdog's stale threshold — and a
        # stale director is SIGTERMd (#274). `finally` stops the beater before the next
        # iteration's own beat, so the thread cannot outlive its owner.
        beater = OrderBeater(
            "dispatching",
            started_at=started_at,
            commit=commit,
            interval=HEARTBEAT_INTERVAL_SECONDS,
        ).start()
        try:
            ok, report = safe_handle_order(order)
            channel.brain_reply(order, "ack" if ok else "result", report)
            channel.consume_order(order_id)
        finally:
            beater.stop()
        print(outcome_line(order, ok, report), flush=True)
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
