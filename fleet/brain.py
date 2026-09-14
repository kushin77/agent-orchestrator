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
* it routes the order by the capability the work needs, through the routing
  policy that consumes the vendored orchestration contract (ADR-0012; #300,
  #301) — the brain keeps no second copy of that vocabulary, so dispatch and the
  declared contract cannot drift apart;
* it answers the operator in `.fleet/brain/outbox` — an `ack` naming the
  directive it issued, or a `result` reporting a refusal with the exact reason;
* it publishes `.fleet/brain.heartbeat.json` so `status`/`health` can tell a
  live brain from a dead one and catch code drift, and it keeps that beat fresh
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
  or asks for a tier below the floor that work requires.

It reaches the sister only through `channel.py send`, so the contract's trust
rules (only a brain-signed directive reaches the sister) hold by construction.

Usage:
    python3 fleet/brain.py run [--once]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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

import channel  # noqa: E402
import routing  # noqa: E402
import runtime  # noqa: E402
import singleton  # noqa: E402

# The completion-triggered advance (#701) reads the board through the dispatch
# package: `order.advance_candidates` is the graph-advance recomputation, and
# `snapshot`/`claims` load the committed board and the live claim ledger. These
# are flat sibling imports, so they go on the path first (see `_conformance` for
# how the conformance seam's own flat `model` is kept separate).
import claims  # noqa: E402
import order  # noqa: E402
import snapshot as snapshot_mod  # noqa: E402

FLEET_DIR = runtime.FLEET_DIR
HEARTBEAT = FLEET_DIR / "brain.heartbeat.json"
# Where this process's stdout is captured. The watchdog owns the spawn and opens
# exactly this path (`fleet/watchdog.py`); it is named here only so the startup
# header can tell the operator where the stream they are reading came from.
LOG_PATH = FLEET_DIR / "brain.log"
CHANNEL = str(ROOT / "fleet" / "channel.py")
PROFILE_PATH = ROOT / "fleet" / "profiles" / "brain.profile.json"
# Where the brain records that a directive has been *sent* for an order. The
# marker is written BEFORE the channel is invoked (#274): a brain killed between
# the dispatch and `channel.consume_order` used to re-read the order on restart
# and send it a second time (measured: `['4242', '4242']`). On restart the marker
# suppresses the duplicate instead of repeating it.
DISPATCH_MARKERS = FLEET_DIR / "brain" / "dispatched"
# The prefix `dispatch()` returns when the marker says the order is already out.
# Callers report "already dispatched" and never send a second directive.
DUPLICATE_SUPPRESSED = "duplicate suppressed"
# How often the beat is refreshed while an order is being handled. The watchdog
# SIGTERMs a rung whose beat is older than `channel.STALE_HEARTBEAT_SECONDS`
# (120s), and a decompose order outlives that easily; the sister beats every 15s
# for the same reason (`fleet/terminal.py`).
HEARTBEAT_INTERVAL_SECONDS = 15.0
# How often the idle path repeats its heartbeat line. The loop blocks up to
# `--watch-timeout` (30s) on each poll, so one line per idle tick is one line per
# ~30s: enough to prove the brain is alive, not enough to bury an order in noise.
IDLE_HEARTBEAT_SECONDS = 30.0


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

# Capability routing is a CONSUMPTION of the vendored orchestration contract
# (ADR-0012; #300, #301), not a dialect grown here: the vocabulary lives in
# `fleet/profiles/routing.policy.json` and `fleet/routing.py` is its only reader.
# The brain consults that module and keeps NO second copy of the mapping — the
# floors and defaults below are DERIVED from it, and the profile's own
# declaration is checked against the policy, so the doctrine and the machine
# cannot drift apart (the parity idiom the registry uses for its catalog and
# schema). A disagreement refuses the start rather than routing on a half-loaded
# contract, the same posture as `load_profile`.
try:
    ROUTING = routing.load()
except routing.RoutingRefusal as exc:
    raise SystemExit(f"[brain] REFUSED — cannot load the routing policy: {exc}")

# The FinOps floor the brain enforces when it dispatches. Security, secrets,
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
    """The KB list a directive hands to the subagent.

    ``own_repo`` paths are repo-relative (loadable from the worktree root);
    ``enterprise_instructions`` carry the operator's own instruction stack
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


class OrderBeater:
    """Keeps the beat fresh while one order is handled; `stop()` joins.

    The brain used to beat only once per poll and once *before* `handle_order`,
    so a long order looked dead: the watchdog treats a beat older than
    `channel.STALE_HEARTBEAT_SECONDS` as stale and SIGTERMs the rung — killing
    the brain mid-order. `stop()` sets the flag *and* joins, because a beater
    that outlives its owner would write its owner's last idea of the world over
    the next iteration's beat (the sister learned this the hard way, #281).
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
    """A stopped brain exits; it does not die mid-order.

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
    operator's own `model` block may raise either value. The risk floor is applied
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
    subagent sees the decision rather than inferring it. A task whose lane and
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
    """The id the operator can correlate on: the order's id, else its own reference."""
    return str(order.get("id") or order.get("correlation_id") or "")


# Characters that may not appear in a marker filename. The reference arrives in
# operator-supplied JSON and becomes a path component, so `../` must not be able
# to walk out of the marker directory.
_MARKER_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def order_marker(order: dict) -> Path | None:
    """The sent-marker path for an order, or None when it carries no reference."""
    reference = order_reference(order)
    if not reference:
        return None
    return DISPATCH_MARKERS / f"{_MARKER_UNSAFE.sub('_', reference)[:120]}.json"


def write_marker(marker: Path, order: dict, state: str) -> None:
    """Persist (atomically) what the brain has done with this order so far."""
    marker.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "reference": order_reference(order),
        "issue": order_issue(order),
        "state": state,
        "ts": now_iso(),
    }
    tmp = marker.with_suffix(".tmp")
    tmp.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    tmp.replace(marker)


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
            f"{routing_summary(order)}\n"
            f"Read first (fleet KB):\n{kb}\n"
            f"Return: {PROFILE['templates']['report']}"
        ),
    }
    if order.get("control") == "override" or task.get("override"):
        # The operator's override still travels the hierarchy: the operator orders
        # the brain, and the brain issues the control to the sister.
        directive["control"] = "override"
    message_id, nonce = directive_identity(order)
    if message_id and nonce:
        # Deterministic identity (see `directive_identity`): the same order must
        # never become a second directive after a restart.
        directive["id"] = message_id
        directive["nonce"] = nonce
    return directive


def dispatch(order: dict) -> tuple[bool, str]:
    """Send the composed directive through the channel; return (ok, message).

    Restart-safe by construction: the sent-marker is persisted *before* the
    channel is invoked, so a brain killed mid-send finds it on restart and
    refuses to send the same order twice. Marker first, send second — the reverse
    order loses the at-most-once property this exists for. `(False, ...)` with a
    `DUPLICATE_SUPPRESSED` prefix means "already sent", not "send failed".
    """
    marker = order_marker(order)
    if marker is not None and marker.exists():
        return False, f"{DUPLICATE_SUPPRESSED} — {marker.name} records that this order was already sent"
    directive = build_directive(order)
    if marker is not None:
        write_marker(marker, order, "sending")
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
            write_marker(marker, order, "sent")
        else:
            # The channel answered non-zero, and `cmd_send` refuses *before* it
            # queues anything: nothing left the brain, so the marker is dropped
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
# Every issue the brain files goes through `governance/conformance/filing.py`,
# which derives the declaring labels from the conformance policy and REFUSES a
# filing that cannot derive them — so the brain can no longer create an issue the
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
    afterwards — the filing seam must not depend on, or change, the brain's import
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


def decompose_problem(spec: object) -> str | None:
    """Why this decomposition spec cannot be filed, or None when it is well formed.

    Validating up front is the point: `handle_decompose` used a bare subscript for
    `parent_issue`, so a spec that omitted it raised `KeyError` out of `loop` and
    killed the brain process (#275) — and `gh_issue_create` raises `RuntimeError`
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
    gate recognises them, and the ready wave is dispatched immediately — the brain
    prepares the next waves in advance instead of waiting for the parent.
    """
    spec = (order.get("task") or {}).get("decompose")
    problem = decompose_problem(spec)
    if problem:
        return False, problem
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
        # Whatever this child does not declare is derived from the conformance
        # policy, so a decomposition cannot file an unclassified issue (issue #320).
        declaring = {
            key: str(child[key]) for key in DECLARING_KEYS if child.get(key)
        }
        try:
            number = file_child_issue(title, body, declaring)
        except _filing_exception() as exc:
            # An explicit refusal, reported to the operator, is the whole point: the
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
            # A stable reference per child: it becomes the directive's identity (and
            # its correlation id), so a brain restarted mid-wave suppresses the
            # duplicate instead of dispatching the child a second time.
            "id": f"child-{parent}-{child['issue']}",
            "task": {
                "issue": child["issue"],
                "lane": child["lane"],
                "title": child.get("title") or "",
            },
            "body": f"Micro-task of #{parent}. Verify: {child['verify']}",
        }
        ok, message = dispatch(order)
        if message.startswith(DUPLICATE_SUPPRESSED):
            # Already out (a restart re-read the plan): stop retrying it, but do not
            # report it as a wave this idle tick advanced.
            plan["dispatched"].append(child["issue"])
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
    closed. The brain fetches the live board (the only way it sees a closure),
    recomputes the ready set through the dispatch order rules — graph advance,
    not kanban scavenging (GR-20) — and dispatches each newly-ready issue to the
    sister in the same cycle, so the pipeline stays full without the brain acting
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
    dispatched = []
    for issue in order.advance_candidates(board, claimed=claimed):
        directive_order = {
            "type": "directive",
            # A stable reference per issue: it becomes the directive's identity
            # (and marker), so a brain that re-runs the advance on a later tick
            # suppresses the duplicate instead of dispatching it a second time.
            "id": f"advance-{issue.number}",
            "task": {"issue": issue.number, "lane": "", "title": issue.title},
            "body": (
                f"Completion-triggered advance: #{issue.number} is dependency-free "
                f"(its parent/blockers are closed). Verify per its acceptance criteria."
            ),
        }
        ok, message = dispatch(directive_order)
        if message.startswith(DUPLICATE_SUPPRESSED):
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
        if message.startswith(DUPLICATE_SUPPRESSED):
            # Not a failure: the directive is already out. Consuming the order (the
            # loop does that next) is what breaks the duplicate-dispatch cycle.
            return True, f"#{issue} was already dispatched — {message}"
        return False, f"dispatch refused for #{issue}: {message}"
    tier, thinking = choose_model(order)
    return True, f"dispatched #{issue} to the sister at {tier}/{thinking} — {message}"


def handle_steer(order: dict) -> tuple[bool, str]:
    """Relay an operator steer order to the sister through the channel (#367).

    The hierarchy stays intact: the operator never addresses the sister, so a
    mid-run hint travels operator → brain → sister as a `steer` message the
    channel validates (brain-signed, correlated to an in-flight directive) and
    the running loop delivers. The brain adds no content of its own — it
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

    The brain is the fleet's middle rung: a malformed order must cost one
    refusal, not the process. Measured (#275): a decompose order without
    `parent_issue` raised `KeyError` straight out of `loop` and killed the brain,
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


# --- the context stream (what the operator's brain window shows) -------------
#
# The brain runs detached and this process's stdout is captured to
# `.fleet/brain.log`, which is what the `brain` window in the `fleet` tmux
# session tails. Without this block that file is empty: the loop printed nothing
# at startup, nothing that identified the order it was handling, and nothing
# while idle — so a working brain and a wedged one looked identical from the
# operator's side. Every line below is built by a pure function over plain data,
# so the tests assert the text instead of the operator having to eyeball it.


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
    idle form the brain repeats every IDLE_HEARTBEAT_SECONDS.
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
    """What the brain did with the order: dispatched, ack, or the refusal itself."""
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
            ready = advance_ready()
            if ready:
                print(wave_line(ready), flush=True)
            moment = time.monotonic()
            if idle_since is None:
                idle_since = moment
            # One heartbeat as soon as the brain goes idle, then one every
            # IDLE_HEARTBEAT_SECONDS: the operator can tell "waiting for an
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
        # stale brain is SIGTERMd (#274). `finally` stops the beater before the next
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
