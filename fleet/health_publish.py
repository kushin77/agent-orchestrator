#!/usr/bin/env python3
"""Publish the fleet's own health signals to the monitoring plane (issue #498).

The fleet computes verdicts about itself and they die inside the process: which
rung is up, how old its beat is, what a reconciliation sweep did with an orphaned
lane, what the watchdog found missing. None of it leaves the host. This module is
the exit.

**The transport is ADR-0022's, consumed, not re-decided.** Push (OTLP/HTTP), the
plane holds the endpoint and any credential, and the producer is **inert when
unconfigured**: no endpoint means no export, no retry storm, and — the rule that
matters — **never a fabricated healthy signal**. `plan` renders and sends nothing;
`push` refuses unless the surface's feature flag is ON *and* an endpoint is
configured.

**The shape is `fleet/health_signals.py`'s, enforced there, not here.** This
module collects facts, turns them into validated `Signal`s, and renders them. It
cannot widen the label set: a `Signal` whose labels carry a session id, a worktree
path, a branch or a commit does not construct.

**The verdicts are the fleet's own, rendered verbatim.** Rung state comes from
`fleet/watchdog.decide` (never re-derived), the capability verdict from
`fleet/channel.capability_finding` (#319), and the reconciliation outcomes from
`governance/reconcile`'s `SweepReport` — so a **stale** beat leaves as `stale`
and an orphan with unmerged work leaves as **`shelved`**, never as `reclaimed`.

**No new always-on service** (GR-5). There is no daemon here: the pass is one
invocation, run by the same code-native cron that owns reconcile/watchdog, and it
is inert until an operator promotes the surface's flag.

Usage::

    python3 fleet/health_publish.py plan            # render, send nothing (read-only)
    python3 fleet/health_publish.py plan --json     # the exact OTLP/HTTP payload
    python3 fleet/health_publish.py push            # needs the flag ON + an endpoint

Exit codes are the repo tri-state: 0 OK / 1 NOT-OK (a delivery failed) /
2 CANNOT-ASSESS (the surface is OFF or has no endpoint — nothing to establish).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

ROOT = Path(__file__).resolve().parent.parent
for _entry in (str(ROOT / "fleet"), str(ROOT)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import channel  # noqa: E402
import health_signals  # noqa: E402
import runtime  # noqa: E402
import watchdog  # noqa: E402
from governance.reconcile.sweep import RepoOps, sweep  # noqa: E402
from health_signals import (  # noqa: E402
    BUCKET_FRESH,
    BUCKET_STALE,
    LABEL_BUCKET,
    LABEL_OUTCOME,
    LABEL_RUNG,
    LABEL_STATE,
    LABEL_VERDICT,
    NO_DATA,
    SIGNAL_KINDS,
    SIGNAL_RECONCILE_OUTCOME,
    SIGNAL_RUNG_BEAT_AGE,
    SIGNAL_RUNG_STATE,
    SIGNAL_WATCHDOG_VERDICT,
)

#: The plane's endpoint (GR-6: environment or a secret manager, never committed,
#: never defaulted to a real URL in code).
ENDPOINT_ENV = "AO_MONITORING_ENDPOINT"

#: The promotion surface, normally read from the flag registry. The environment
#: override exists for the gate and for a rehearsal; it is not a second registry.
FLAG_ENV = "AO_FLEET_HEALTH_EXPORT"

#: The entry this surface reads in `infra/feature-flags/registry.yaml`. The file
#: is owned by another lane this wave (#497); this module only READS it, and an
#: absent entry is OFF (deny by default), which is why no flag edit is required to
#: ship this inert.
FLAG_SURFACE = "fleet_health_export"
FLAG_SECTION = "surfaces"
REGISTRY = ROOT / "infra" / "feature-flags" / "registry.yaml"

#: The OTLP instrumentation-scope name the payload declares.
SCOPE_NAME = "fleet.health"

ON = "on"
OFF = "off"
_TRUTHY = frozenset({"on", "1", "true", "yes"})
_FALSY = frozenset({"off", "0", "false", "no"})

#: A push must not hang a cron tick; short, and a failure is reported, not hidden.
TIMEOUT_SECONDS = 5.0

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2

#: "the caller did not supply a report", distinct from "there is no report".
UNSET: object = object()


# ── the promotion gate (flag-gated OFF, GR-5) ───────────────────────────────


def surface_flag(
    *,
    env: Mapping[str, str] | None = None,
    registry: Path | str | None = None,
) -> tuple[str, str]:
    """Whether this surface is promoted: ``(state, why)``, denying by default.

    Every path that is not an explicit, readable `on` is OFF: an unreadable
    registry, an unparseable one, a missing entry, a missing PyYAML and an
    unrecognised override all refuse. That is the whole point of a flag-gated
    surface — promotion is a reviewed declaration, and its absence is not a
    promotion.
    """
    env = os.environ if env is None else env
    override = str(env.get(FLAG_ENV, "")).strip().lower()
    if override:
        if override in _TRUTHY:
            return ON, f"{FLAG_ENV}={override} (environment promotion)"
        if override in _FALSY:
            return OFF, f"{FLAG_ENV}={override} (environment override)"
        return OFF, f"{FLAG_ENV}={override!r} is not a recognised promotion value — refused"

    path = Path(registry) if registry is not None else REGISTRY
    try:
        import yaml
    except ImportError:
        return OFF, f"PyYAML is not installed, so {path} cannot be read — refused (deny by default)"
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return OFF, f"{path} cannot be read ({type(exc).__name__}) — refused (deny by default)"
    except yaml.YAMLError:
        return OFF, f"{path} is not valid YAML — refused (deny by default)"
    entry = None
    if isinstance(document, dict):
        section = document.get(FLAG_SECTION)
        if isinstance(section, dict):
            entry = section.get(FLAG_SURFACE)
    if not isinstance(entry, dict):
        return OFF, (
            f"no `{FLAG_SECTION}.{FLAG_SURFACE}` entry in {path} — the surface ships OFF; "
            "promotion is a reviewed registry change, not a code default"
        )
    default = entry.get("default")
    # PyYAML resolves an unquoted `on` / `off` to the BOOLEAN True / False (YAML
    # 1.1), and the shipped registry writes `default: off` unquoted. Accept both
    # spellings; anything else that is not an unambiguous promotion is OFF.
    if isinstance(default, bool):
        promoted = default
    elif isinstance(default, str):
        promoted = default.strip().lower() in _TRUTHY
    else:
        promoted = False
    if promoted:
        return ON, f"{FLAG_SECTION}.{FLAG_SURFACE}.default={default!r}"
    return OFF, f"{FLAG_SECTION}.{FLAG_SURFACE}.default={default!r}"


def endpoint_from_env(env: Mapping[str, str] | None = None) -> str:
    """The plane's endpoint, or an empty string when the plane has not named one."""
    env = os.environ if env is None else env
    return str(env.get(ENDPOINT_ENV, "")).strip()


# ── the fleet's own verdicts, gathered read-only ────────────────────────────


@dataclass(frozen=True)
class RungFacts:
    """What is observable about one rung, with nothing interpreted."""

    name: str
    pid: int | None
    beat: dict | None


@dataclass(frozen=True)
class Observation:
    """One read-only pass over the fleet's own state."""

    store_present: bool
    facts: tuple[RungFacts, ...]
    head: str
    report: object | None
    report_error: str
    timestamp_ns: int


def _beat_paths() -> dict[str, Path]:
    """The beat each rung publishes — read through ``channel`` so a redirect applies."""
    return {watchdog.MONITOR_NAME: runtime.FLEET_DIR / "monitor.heartbeat.json"} | {
        name: path for name, path in (("brain", channel.BRAIN_HEARTBEAT), ("sister", channel.HEARTBEAT))
    }


def _rung_patterns() -> dict[str, str]:
    return {
        watchdog.MONITOR_NAME: watchdog.MONITOR_PATTERN,
        "brain": "fleet/brain.py",
        "sister": "fleet/terminal.py",
    }


def observe(
    *,
    head: str | None = None,
    beats: Mapping[str, dict | None] | None = None,
    pids: Mapping[str, int | None] | None = None,
    fleet_dir: Path | str | None = None,
    root: Path | str = ROOT,
    ops: object | None = None,
    sweep_report: object = UNSET,
    sweep_error: str = "",
    timestamp_ns: int | None = None,
) -> Observation:
    """Gather the fleet's own facts. Never writes, never signals, never restarts.

    Everything is injectable so the proof can run offline; the defaults read the
    live tree the same way `fleet/watchdog.py` and `governance/reconcile` do.
    ``sweep_report=None`` says explicitly "no report is available" (an unreadable
    store), which is why the default is a sentinel rather than ``None``.
    """
    directory = Path(fleet_dir) if fleet_dir is not None else runtime.FLEET_DIR
    # The store's presence is the honest-emptiness switch: with no runtime
    # directory there is nothing to measure, and "nothing to measure" must never
    # be published as a healthy fleet.
    store_present = directory.exists()

    if beats is None:
        paths = _beat_paths()
        beats = {name: watchdog.read_beat(path) for name, path in paths.items()}
    if pids is None:
        pids = {name: watchdog.loop_pid(pattern) for name, pattern in _rung_patterns().items()}

    facts = tuple(
        RungFacts(name=name, pid=pids.get(name), beat=beats.get(name))
        for name in health_signals.RUNG_NAMES
    )

    report = None
    if sweep_report is not UNSET:
        report = sweep_report
    elif store_present:
        # A DRY RUN: `apply=False` computes each lane's disposition and performs
        # nothing, so reading the reconciliation state cannot disturb a live lane.
        try:
            report = sweep(root, apply=False, ops=ops if ops is not None else RepoOps(root))
        except Exception as exc:  # noqa: BLE001 - an unreadable store is a signal, not a crash
            sweep_error = f"{type(exc).__name__}: {exc}"[:300]

    return Observation(
        store_present=store_present,
        facts=facts,
        head=head if head is not None else channel.head_commit(),
        report=report,
        report_error=sweep_error,
        timestamp_ns=timestamp_ns if timestamp_ns is not None else time.time_ns(),
    )


# ── facts -> signals (verbatim verdicts, closed labels) ─────────────────────


def rung_state_signals(observation: Observation) -> list[health_signals.Signal]:
    """One `rung_state` point per rung, carrying `decide`'s token unchanged."""
    signals = []
    for fact in observation.facts:
        if not observation.store_present:
            state = NO_DATA
        elif fact.name == watchdog.MONITOR_NAME:
            # The watchdog's own monitor rule is presence-only ("monitor: healthy"
            # / "monitor: missing — respawned"), so presence is the verdict here —
            # this module does not invent a beat-age rule the watchdog never applies.
            state = watchdog.HEALTHY if fact.pid is not None else watchdog.MISSING
        else:
            state, _reason = watchdog.decide(fact.pid, fact.beat, observation.head)
        signals.append(health_signals.make(SIGNAL_RUNG_STATE, 1, **{LABEL_RUNG: fact.name, LABEL_STATE: state}))
    return signals


def beat_age_signals(observation: Observation) -> list[health_signals.Signal]:
    """The beat age of each rung whose beat is readable, bucketed by the TTL.

    A rung with no readable beat gets **no age point**: the age is unknown, and
    writing a number (0 would read as "just beat") would be the fabrication this
    family exists to avoid. The fact is not lost — `rung_state` publishes the
    watchdog's own measured verdict for that rung in the same payload.
    """
    if not observation.store_present:
        return []
    signals = []
    for fact in observation.facts:
        if fact.name == watchdog.MONITOR_NAME or fact.beat is None:
            continue
        age = channel.heartbeat_age_seconds(fact.beat)
        if age is None:
            continue
        bucket = BUCKET_STALE if age > channel.STALE_HEARTBEAT_SECONDS else BUCKET_FRESH
        signals.append(
            health_signals.make(
                SIGNAL_RUNG_BEAT_AGE,
                round(float(age), 3),
                **{LABEL_RUNG: fact.name, LABEL_BUCKET: bucket},
            )
        )
    return signals


def reconcile_signals(observation: Observation) -> list[health_signals.Signal]:
    """Counts per reconciliation status and outcome — never per lane.

    Every declared status/outcome is published on every readable pass, zeros
    included: a zero is a real reading ("this pass saw no shelved lane") and is
    precisely what must stay distinguishable from `no-data` ("this pass could not
    read the store"). When the store could not be read, exactly one point is
    published with the `no-data` member and value 1 — it counts the *family*, not
    lanes, because there is one fact here and it is that nothing could be
    established.
    """
    report = observation.report
    if report is None:
        return [
            health_signals.make(SIGNAL_RECONCILE_OUTCOME, 1, **{LABEL_OUTCOME: NO_DATA}),
        ]

    counts: dict[str, int] = {token: 0 for token in health_signals.RECONCILE_OUTCOMES if token != NO_DATA}
    for action in report.actions:
        # `status` is the judge's verdict, `outcome` is what the sweep did with
        # the work. Both are consumed verbatim, so a shelved lane's count lands
        # under `shelved` and never under `reclaimed`.
        for token in (action.status, action.outcome):
            if token in counts:
                counts[token] += 1
    return [
        health_signals.make(SIGNAL_RECONCILE_OUTCOME, counts[token], **{LABEL_OUTCOME: token})
        for token in sorted(counts)
    ]


def watchdog_signals(observation: Observation) -> list[health_signals.Signal]:
    """The capability verdict per rung, straight from `channel.capability_finding`."""
    signals = []
    for fact in observation.facts:
        if fact.name not in channel.CAPABILITY_RUNGS:
            # The watchdog declares capabilities for the loop rungs only; a verdict
            # for a rung it makes no declaration about would be invented.
            continue
        if not observation.store_present:
            verdict = NO_DATA
        else:
            verdict = channel.capability_finding(fact.name, fact.beat, observation.head).kind
        signals.append(
            health_signals.make(SIGNAL_WATCHDOG_VERDICT, 1, **{LABEL_RUNG: fact.name, LABEL_VERDICT: verdict})
        )
    return signals


def signals(observation: Observation) -> list[health_signals.Signal]:
    """Every signal one pass publishes, in the declared kind order."""
    return (
        rung_state_signals(observation)
        + beat_age_signals(observation)
        + reconcile_signals(observation)
        + watchdog_signals(observation)
    )


# ── rendering: OTLP/HTTP JSON ───────────────────────────────────────────────


def render(observation: Observation, *, signals_: list[health_signals.Signal] | None = None) -> dict:
    """Render the pass as an OTLP/HTTP JSON payload (ADR-0022's shipped encoding).

    One resource, one scope, one gauge metric per declared signal kind; the labels
    are the data points' attributes and nothing else. Built by hand with the
    standard library: the repo ships no OTLP SDK dependency and adding one for a
    flag-gated-OFF surface would be a dependency for a surface that may never be
    promoted.
    """
    collected = signals(observation) if signals_ is None else signals_
    stamp = str(observation.timestamp_ns)
    grouped: dict[str, list[health_signals.Signal]] = {}
    for signal in collected:
        grouped.setdefault(signal.kind, []).append(signal)

    metrics = []
    for kind in SIGNAL_KINDS:
        group = grouped.get(kind)
        if not group:
            continue
        metrics.append(
            {
                "name": health_signals.metric_name(kind),
                "unit": group[0].unit,
                "gauge": {
                    "dataPoints": [
                        {
                            "asDouble": float(signal.value),
                            "timeUnixNano": stamp,
                            "attributes": signal.as_attributes(),
                        }
                        for signal in group
                    ]
                },
            }
        )
    return {
        "resourceMetrics": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": health_signals.SERVICE}}
                    ]
                },
                "scopeMetrics": [{"scope": {"name": SCOPE_NAME}, "metrics": metrics}],
            }
        ]
    }


# ── delivery: honest, bounded, and never a silent success ───────────────────


@dataclass(frozen=True)
class Delivery:
    """What happened on the wire, including "nothing, and why"."""

    attempted: bool
    ok: bool
    status: int | None
    detail: str

    def __str__(self) -> str:
        if not self.attempted:
            return f"not sent ({self.detail})"
        return f"sent ({self.status})" if self.ok else f"FAILED ({self.status}): {self.detail}"


def _http_post(endpoint: str, body: bytes, timeout: float) -> tuple[int, str]:
    """POST the payload; a transport failure is a status, never an exception.

    A delivery failure is data (ADR-0022: the local stores are the truth and the
    export is a feed) — so it is returned and reported, not raised into a cron
    tick that would then look like a fleet problem.
    """
    request = urllib.request.Request(
        endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), ""
    except urllib.error.HTTPError as exc:
        return int(exc.code), f"HTTP {exc.code} {exc.reason}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def deliver(
    payload: dict,
    endpoint: str,
    *,
    post: Callable[[str, bytes, float], tuple[int, str]] = _http_post,
    timeout: float = TIMEOUT_SECONDS,
) -> Delivery:
    """Send one payload, or refuse honestly when the plane has named no endpoint."""
    if not endpoint:
        return Delivery(False, False, None, f"no {ENDPOINT_ENV} configured — inert by design")
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    try:
        status, detail = post(endpoint, body, timeout)
    except Exception as exc:  # noqa: BLE001 - a broken transport is reported, not raised
        return Delivery(True, False, None, f"{type(exc).__name__}: {exc}")
    ok = 200 <= int(status) < 300
    return Delivery(True, ok, int(status), detail or ("accepted" if ok else "non-2xx response"))


# ── CLI ─────────────────────────────────────────────────────────────────────


def _observation(args: argparse.Namespace) -> Observation:
    return observe(root=args.root)


def cmd_plan(args: argparse.Namespace) -> int:
    """Render the pass and send nothing at all."""
    observation = _observation(args)
    collected = signals(observation)
    if args.json:
        print(json.dumps(render(observation, signals_=collected), indent=2, sort_keys=True))
    else:
        for signal in collected:
            print(f"  {signal}")
        print(f"plan: {len(collected)} signal(s) across {len(SIGNAL_KINDS)} declared kind(s); nothing sent")
    if observation.report_error:
        print(f"plan: reconciliation store unreadable — {observation.report_error}", file=sys.stderr)
    return EXIT_OK


def cmd_push(args: argparse.Namespace) -> int:
    """Push the pass — only when the surface is promoted and the plane has an endpoint."""
    state, why = surface_flag()
    if state != ON:
        print(f"push: CANNOT-ASSESS — the {FLAG_SURFACE} surface is OFF ({why})", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    endpoint = args.endpoint or endpoint_from_env()
    if not endpoint:
        print(
            f"push: CANNOT-ASSESS — the flag is ON but {ENDPOINT_ENV} names no endpoint "
            "(ADR-0022: the plane names it; inert until then)",
            file=sys.stderr,
        )
        return EXIT_CANNOT_ASSESS
    observation = _observation(args)
    delivery = deliver(render(observation), endpoint, timeout=args.timeout)
    print(f"push: {delivery}", flush=True)
    if not delivery.ok:
        print(
            "push: NOT-OK — the export failed; the local stores remain the truth "
            "(ADR-0022: a delivery failure is reported, it is not an SLO verdict)",
            file=sys.stderr,
        )
        return EXIT_NOT_OK
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleet-health-publish", description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(ROOT), help="the repository whose fleet state is read")
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="render the pass and send nothing (read-only)")
    plan.add_argument("--json", action="store_true", help="print the exact OTLP/HTTP payload")
    plan.set_defaults(func=cmd_plan)

    push = sub.add_parser("push", help="render and push the pass to the plane's endpoint")
    push.add_argument(
        "--endpoint",
        default="",
        help=f"the plane's endpoint (default: ${ENDPOINT_ENV}; the plane owns this value)",
    )
    push.add_argument("--timeout", type=float, default=TIMEOUT_SECONDS, help="delivery timeout in seconds")
    push.set_defaults(func=cmd_push)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
