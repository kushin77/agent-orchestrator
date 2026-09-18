"""The upstream control-verb mapping — a mapping, never authority (issue #557).

EPIC #551 gives this fleet its own control vocabulary (RC-2, issue #553): verbs
in ``<family>.<action>`` form declared once in
``control-plane/control/verbs.yaml`` (63 at this writing; the count is read from
the registry below, never pinned in this prose). Upstream paperclip.ing has a control
surface of its own — per-agent and per-heartbeat-run routes on **its** server
(``server/src/routes/agents.ts``, inventoried in
``docs/REMOTE-CONTROL-GAP-ANALYSIS.md`` §3.2). This module is the **correspondence
table** between the two, and the mismatches it records are the point: the two
vocabularies are the same *kind* of thing (ADR-0025 §3.3) and only the reach
differs, so a reader will assume equivalences that do not hold. Every assumption
worth making explicit is a row here.

Three states, and the rule for each — stated once, applied mechanically:

``MAPPED``
    Upstream serves a route with the **same effect on the same kind of object**.
    Ours is fleet-wide, upstream's is per-agent — that divergence is exactly what
    disqualifies a row from ``MAPPED``, so the reads (which carry no blast radius)
    and the one ticket-filing write are what survive it.

``MISMATCH``
    Upstream serves a route for the **same concept** but the effect, the scope or
    the object differs. The correspondence is close enough that someone would wire
    the two together, so it is **recorded** — never silently mapped, never
    omitted. This list is the artifact RC-7 (issue #558) folds into the seam
    document's §5.

``UNMAPPED``
    Upstream serves nothing for this concept. The row still exists (an omitted
    row is silence, which this module refuses) and its ``why`` names the nearest
    upstream route whenever one is close enough to be worth refuting.

**This grants no authority.** A row names a route; naming one is not a licence to
call it. The module implements **no** method that mutates upstream: its only
transport call is ``GET <api>/openapi.json``, which reads the peer's own
declaration of its surface so the table can be checked offline. The one write
that maps (``fleet.override`` → ``POST /companies/:companyId/issues``) is the
seam's *existing* single outbound write (ADR-0025 D1.2); the mapping adds no
caller and no capability.

Consumed at runtime, never copied. The verb side of every row is walked out of
``control-plane/control/verbs.yaml`` through RC-2's own loader
(``control-plane/control/cli.py::load_registry``), so a verb added to the
registry cannot leave this table quietly stale: :func:`build_table` refuses a
registry verb it has no row for **by name**. The route side is static data — a
route is a fact about upstream, and upstream cannot change under us silently
because the served OpenAPI document is what :func:`probe_routes` checks.

Import direction obeys ADR-0016: this is the seam (``integrations/paperclip/``),
so it imports its siblings and may never import ``adapters/**``.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Protocol, Tuple

from .client import API_PREFIX, MUTATING_METHODS, Transport

__all__ = (
    "MAPPED",
    "MISMATCH",
    "UNMAPPED",
    "STATES",
    "READ_ONLY_METHODS",
    "UPSTREAM_ROUTES",
    "MISMATCHES",
    "MappingRow",
    "Mismatch",
    "ProbeResult",
    "UpstreamRoute",
    "UnclassifiedControlVerb",
    "build_table",
    "load_verbs",
    "mapped_routes",
    "normalize",
    "registry_verbs",
    "probe_routes",
    "read_only_method",
    "route",
    "route_keys",
    "stale_rows",
    "unmapped_upstream_routes",
)

#: The three row states. Closed set: a row is in exactly one of them.
MAPPED = "MAPPED"
MISMATCH = "MISMATCH"
UNMAPPED = "UNMAPPED"
STATES: Tuple[str, ...] = (MAPPED, MISMATCH, UNMAPPED)

#: The methods this module is allowed to use. ``GET`` only — the mapping exists
#: to *describe* upstream's control routes, not to press them.
READ_ONLY_METHODS: Tuple[str, ...] = ("GET",)

#: Where the RC-2 vocabulary lives, relative to the repo root.
CONTROL_CLI = ("control-plane", "control", "cli.py")
VERBS_YAML = ("control-plane", "control", "verbs.yaml")

#: The repo root, derived from this file (``integrations/paperclip/x.py``), so the
#: defaults below work whatever directory pytest was started from.
ROOT = Path(__file__).resolve().parents[2]

#: The upstream route-file inventory this table cites. Measured (not re-derived)
#: from ``docs/REMOTE-CONTROL-GAP-ANALYSIS.md`` §3.2, which reads each row out of
#: the route file named beside it; the API-prefixed routes are the ones the seam
#: already calls (``integrations/paperclip/client.py``) and the offline fixture
#: already replays. Upstream is cited **by path** and never vendored (GR-10, NG4).
AGENTS_TS = "server/src/routes/agents.ts"
DASHBOARD_TS = "server/src/routes/dashboard.ts"
SEAM_CLIENT = "integrations/paperclip/client.py"


@dataclass(frozen=True)
class UpstreamRoute:
    """One real upstream control route.

    ``key`` is the route as upstream declares it (``METHOD /path``, express-style
    ``:param`` segments), which is also how the issue and the gap analysis write
    it. The seam reaches the same route with the ``/api`` prefix applied, so
    :func:`normalize` strips that prefix before comparing the two.
    """

    method: str
    path: str
    source: str
    effect: str
    scope: str
    audit_action: Optional[str] = None
    note: str = ""

    @property
    def key(self) -> str:
        return f"{self.method.upper()} {self.path}"


#: Every upstream control route this table is allowed to name. A row that names a
#: route outside this tuple is a finding (the route would be invented, or the
#: inventory would have drifted) — ``test_every_named_route_is_in_the_inventory``.
UPSTREAM_ROUTES: Tuple[UpstreamRoute, ...] = (
    # -- agents.ts: the per-agent lifecycle -------------------------------
    UpstreamRoute(
        "POST", "/agents/:id/pause", AGENTS_TS, "hold", "agent",
        audit_action="agent.paused",
        note="also cancels that agent's active heartbeats (heartbeat.cancelActiveForAgent)",
    ),
    UpstreamRoute(
        "POST", "/agents/:id/resume", AGENTS_TS, "hold", "agent",
        audit_action="agent.resumed",
        note="refuses 409 when the org chain is invalid rather than resuming a broken chain",
    ),
    UpstreamRoute(
        "POST", "/agents/:id/clear-error", AGENTS_TS, "hold", "agent",
        audit_action="agent.error_cleared",
    ),
    UpstreamRoute(
        "POST", "/agents/:id/approve", AGENTS_TS, "hold", "agent",
        audit_action="agent.approved",
    ),
    UpstreamRoute(
        "POST", "/agents/:id/terminate", AGENTS_TS, "irreversible", "agent",
        audit_action="agent.terminated",
        note="cancels the agent's runs and wakeups and invalidates its descendants",
    ),
    UpstreamRoute("DELETE", "/agents/:id", AGENTS_TS, "irreversible", "agent",
                  audit_action="agent.deleted"),
    UpstreamRoute("POST", "/agents/:id/keys", AGENTS_TS, "irreversible", "agent",
                  note="board-gated (assertBoard)"),
    UpstreamRoute("POST", "/agents/:id/wakeup", AGENTS_TS, "hold", "agent",
                  note="wakes one agent so a heartbeat runs"),
    UpstreamRoute("POST", "/agents/:id/heartbeat/invoke", AGENTS_TS, "hold", "agent",
                  note="invokes one heartbeat run on one agent"),
    UpstreamRoute("POST", "/agents/:id/config-revisions/:revisionId/rollback",
                  AGENTS_TS, "irreversible", "agent"),
    UpstreamRoute("POST", "/agents/:id/runtime-state/reset-session", AGENTS_TS,
                  "hold", "agent"),
    # -- agents.ts: the per-run control surface ---------------------------
    UpstreamRoute("POST", "/heartbeat-runs/:runId/cancel", AGENTS_TS, "stop", "run",
                  note="cancels one named heartbeat run"),
    UpstreamRoute("POST", "/heartbeat-runs/:runId/runtime-requests/:requestId/resolve",
                  AGENTS_TS, "hold", "run"),
    UpstreamRoute("POST", "/heartbeat-runs/:runId/watchdog-decisions", AGENTS_TS,
                  "hold", "run"),
    UpstreamRoute("GET", "/heartbeat-runs/:runId/log", AGENTS_TS, "read", "run"),
    UpstreamRoute("GET", "/heartbeat-runs/:runId/events", AGENTS_TS, "read", "run"),
    UpstreamRoute("GET", "/heartbeat-runs/:runId/provider-trace", AGENTS_TS, "read",
                  "run"),
    # -- the company-scoped reads and the ticket graph --------------------
    UpstreamRoute("GET", "/companies/:companyId/dashboard", DASHBOARD_TS, "read",
                  "company"),
    UpstreamRoute("GET", "/companies/:companyId/issues", SEAM_CLIENT, "read",
                  "company"),
    UpstreamRoute("POST", "/companies/:companyId/issues", SEAM_CLIENT, "irreversible",
                  "company",
                  note="the seam's existing single outbound write (client.create_issue)"),
    UpstreamRoute("PATCH", "/issues/:id", SEAM_CLIENT, "irreversible", "company",
                  note="a mutable field write on an existing issue (client.update_issue)"),
    # -- the seam's own probes --------------------------------------------
    UpstreamRoute("GET", "/health", SEAM_CLIENT, "read", "service"),
    UpstreamRoute("GET", "/openapi.json", SEAM_CLIENT, "read", "service",
                  note="the peer's declaration of its own surface"),
)

_ROUTE_BY_KEY: Dict[str, UpstreamRoute] = {r.key: r for r in UPSTREAM_ROUTES}


@dataclass(frozen=True)
class MappingRow:
    """One row of the table: one RC-2 verb, and where it stands against upstream."""

    verb: str
    state: str
    upstream: Optional[UpstreamRoute]
    why: str


@dataclass(frozen=True)
class Mismatch:
    """One recorded mismatch — the list the seam document §5 must carry.

    ``fleet_verb`` is ``None`` for a mismatch that exists on the **upstream** side
    alone: a route with no counterpart in our vocabulary at all.
    """

    id: str
    kind: str
    fleet_verb: Optional[str]
    upstream: Optional[UpstreamRoute]
    why: str


#: The recorded mismatch list. Two of these were named in the lane brief and are
#: asserted individually, by id, in the suite; the rest were found by walking the
#: whole vocabulary against the route inventory and are recorded for the same
#: reason (a mismatch found and not written down is a mismatch someone re-derives).
MISMATCHES: Tuple[Mismatch, ...] = (
    Mismatch(
        "M-CTL-1", "no-fleet-counterpart", None, _ROUTE_BY_KEY["POST /agents/:id/terminate"],
        "Upstream terminate ends an agent irreversibly: it cancels that agent's runs "
        "and wakeups and invalidates its descendants. No verb in our vocabulary ends "
        "an agent — fleet.stop/kill/halt take down the local loop, and our only "
        "irreversible verb (fleet.override) files a ticket. Recorded rather than "
        "mapped onto fleet.stop, which would claim a blast radius we do not have.",
    ),
    Mismatch(
        "M-CTL-2", "scope", "fleet.pause", _ROUTE_BY_KEY["POST /agents/:id/pause"],
        "Our pause is fleet-wide: the loop stops pulling new orders and the run in "
        "flight finishes. Upstream's is per-agent and also cancels that agent's "
        "active heartbeats. A per-agent verb is not the counterpart of a fleet-wide "
        "one, so the name match is recorded as a mismatch.",
    ),
    Mismatch(
        "M-CTL-3", "scope", "fleet.resume", _ROUTE_BY_KEY["POST /agents/:id/resume"],
        "The symmetric half of M-CTL-2: ours lets the loop pull orders again, "
        "upstream's resumes one agent. The brief named pause only; the divergence is "
        "identical on resume, so it is recorded rather than left to be assumed.",
    ),
    Mismatch(
        "M-CTL-4", "effect", "fleet.stop", _ROUTE_BY_KEY["POST /agents/:id/terminate"],
        "Ours is graceful and reversible: the loop exits between runs and "
        "fleet.start/restart brings it back. Upstream's terminate is irreversible. "
        "The nearest upstream route to our stop is the one route we must never "
        "present it as.",
    ),
    Mismatch(
        "M-CTL-5", "scope+effect", "fleet.halt",
        _ROUTE_BY_KEY["POST /agents/:id/terminate"],
        "The broadest local stop (the whole directive loop) against the irreversible "
        "per-agent terminate: both the scope and the effect diverge.",
    ),
    Mismatch(
        "M-CTL-6", "scope", "fleet.kill",
        _ROUTE_BY_KEY["POST /heartbeat-runs/:runId/cancel"],
        "Upstream cancels one named heartbeat run. Ours takes the loop down, and its "
        "handler releases the in-flight run's claim — a superset of one run, which is "
        "why it is recorded and not mapped.",
    ),
    Mismatch(
        "M-CTL-7", "scope", "fleet.poke", _ROUTE_BY_KEY["POST /agents/:id/wakeup"],
        "Correspondence by concept, not by name: both nudge a worker to pick up work. "
        "Ours writes a control message the loop acks on its next poll; upstream's "
        "wakes one agent so a heartbeat runs.",
    ),
    Mismatch(
        "M-CTL-8", "scope", "recover.sweep",
        _ROUTE_BY_KEY["POST /heartbeat-runs/:runId/cancel"],
        "Ours reclaims every orphaned session whose beat passed the TTL in one pass; "
        "upstream cancels one named run per call. A batch sweep is not a single cancel.",
    ),
    Mismatch(
        "M-CTL-9", "effect", "closure.close", _ROUTE_BY_KEY["PATCH /issues/:id"],
        "Ours closes a work item against the closure invariants and is irreversible in "
        "the append-only rail. Upstream's is a mutable field write on the same issue, "
        "and the same endpoint can reopen it.",
    ),
)


class UnclassifiedControlVerb(RuntimeError):
    """A verb the RC-2 registry declares that this mapping has no row for.

    Raised by :func:`build_table`. A verb added to the vocabulary must be
    classified here deliberately: defaulting to an ``UNMAPPED`` row would make
    "no upstream counterpart" and "nobody has looked at this yet" the same
    statement, which is the silence this module exists to refuse.
    """


#: ``fleet verb -> the mismatch entry recording it``. A mismatch row and its
#: table row carry the same reason from the same place, so the two cannot drift.
_MISMATCH_BY_VERB: Dict[str, Mismatch] = {
    m.fleet_verb: m for m in MISMATCHES if m.fleet_verb is not None
}


#: `<family>.<action> -> (state, upstream route key | None, why)`.
#:
#: The verb side is checked against the RC-2 registry on every build; the route
#: keys are checked against :data:`UPSTREAM_ROUTES`. Nothing here is a copy of the
#: registry — a verb that is not in ``verbs.yaml`` is a stale row
#: (:func:`stale_rows`), and a verb in ``verbs.yaml`` with no entry here is a
#: refusal (:class:`UnclassifiedControlVerb`).
_OPINIONS: Dict[str, Tuple[str, Optional[str], str]] = {
    # -- reads that map ---------------------------------------------------
    "fleet.status": (
        MAPPED, "GET /companies/:companyId/dashboard",
        "The operator's aggregate state read on each side: ours reports the rungs, "
        "flags and tracked runs, upstream's reports the company's agents and work. "
        "Read-only, so no blast radius diverges.",
    ),
    "fleet.health": (
        MAPPED, "GET /health",
        "A liveness probe is the same read on both sides of the boundary: each "
        "answers for the process it guards (ours reports the local rungs' heartbeat "
        "freshness).",
    ),
    "fleet.verbs": (
        MAPPED, "GET /openapi.json",
        "Both serve the declared surface so a client renders the vocabulary instead "
        "of hard-coding one.",
    ),
    "fleet.debug": (
        MAPPED, "GET /heartbeat-runs/:runId/log",
        "The worker's log tail: ours reads `.fleet/<rung>.log`, upstream serves one "
        "run's log.",
    ),
    "fleet.watch": (
        MAPPED, "GET /heartbeat-runs/:runId/events",
        "The live follow of a worker's output: ours follows the channel, upstream "
        "streams one run's events.",
    ),
    "channel.listen": (
        MAPPED, "GET /heartbeat-runs/:runId/events",
        "The same live follow, reached through the steering transport instead of the "
        "control terminal — three of our verbs are one upstream read.",
    ),
    "channel.watch": (
        MAPPED, "GET /heartbeat-runs/:runId/events",
        "The unbounded form of channel.listen; the same upstream read.",
    ),
    "channel.follow": (
        MAPPED, "GET /heartbeat-runs/:runId/log",
        "Tails one directive's live log stream (the `follow` half of `listen "
        "--directive`). Upstream serves one run's log, so this is the same read "
        "reached per named unit — the correspondence fleet.debug already carries.",
    ),
    # -- the one write that maps ------------------------------------------
    "fleet.override": (
        MAPPED, "POST /companies/:companyId/issues",
        "Both append a work item to the ticket graph, and nothing else acts: ours "
        "files an operator override as a ticket (ADR-0014's single join node), "
        "upstream's creates an issue. This is the seam's existing single outbound "
        "write — naming it grants no caller and no capability.",
    ),
    # -- the recorded mismatches ------------------------------------------
    "fleet.pause": (MISMATCH, "POST /agents/:id/pause", _MISMATCH_BY_VERB["fleet.pause"].why),
    "fleet.resume": (
        MISMATCH, "POST /agents/:id/resume", _MISMATCH_BY_VERB["fleet.resume"].why,
    ),
    "fleet.stop": (
        MISMATCH, "POST /agents/:id/terminate", _MISMATCH_BY_VERB["fleet.stop"].why,
    ),
    "fleet.halt": (
        MISMATCH, "POST /agents/:id/terminate", _MISMATCH_BY_VERB["fleet.halt"].why,
    ),
    "fleet.kill": (
        MISMATCH, "POST /heartbeat-runs/:runId/cancel",
        _MISMATCH_BY_VERB["fleet.kill"].why,
    ),
    "fleet.poke": (
        MISMATCH, "POST /agents/:id/wakeup", _MISMATCH_BY_VERB["fleet.poke"].why,
    ),
    "recover.sweep": (
        MISMATCH, "POST /heartbeat-runs/:runId/cancel",
        _MISMATCH_BY_VERB["recover.sweep"].why,
    ),
    "closure.close": (
        MISMATCH, "PATCH /issues/:id", _MISMATCH_BY_VERB["closure.close"].why,
    ),
    # -- no upstream counterpart ------------------------------------------
    "fleet.cron": (
        UNMAPPED, None,
        "The local crontab substrate (cron.py owns install/status/run). Upstream "
        "serves no cron surface.",
    ),
    "fleet.live": (
        UNMAPPED, None,
        "Renders the tmux layout and attaches a tty. The upstream operator surface is "
        "a UI, not a route (ADR-0025 D1.3), and the verb is withheld for the same "
        "reason: a remote caller has no tty.",
    ),
    "fleet.attach": (
        UNMAPPED, None,
        "Alias of fleet.live, withheld for the same reason.",
    ),
    "fleet.start": (
        UNMAPPED, None,
        "Upstream serves no start route: a worker begins by being hired and woken. "
        "The nearest write (POST /agents/:id/wakeup) invokes one run rather than "
        "starting a worker, so it is not a counterpart.",
    ),
    "fleet.restart": (
        UNMAPPED, None,
        "Signal the loop, wait for its claim release, relaunch it. Upstream serves no "
        "restart of a worker.",
    ),
    "fleet.refresh": (
        UNMAPPED, None,
        "A local `git pull --ff-only` plus the board snapshot and `make verify`. No "
        "upstream route does work on this checkout.",
    ),
    "fleet.update": (
        UNMAPPED, None,
        "refresh plus a local knowledge-index rebuild; same reason.",
    ),
    "fleet.drop": (
        UNMAPPED, None,
        "Dead-letters a NAMED directive: the sister moves the order out of "
        "`.fleet/inbox/` into `.fleet/dead-letter/`, records why and who dropped "
        "it, and acks the sender. Upstream has no directive queue to dead-letter "
        "from and records no reason for retiring a unit — its nearest route "
        "(POST /heartbeat-runs/:runId/cancel) retires one RUNNING run, a different "
        "object from a queued order, which is why the resemblance is refuted "
        "rather than mapped.",
    ),
    "fleet.dead-letter": (
        UNMAPPED, None,
        "Lists or inspects the dead-letter mailbox — the read side of fleet.drop. "
        "Upstream keeps no dropped-order store to read; its nearest list "
        "(GET /companies/:companyId/issues) enumerates live tickets.",
    ),
    "channel.verify": (
        UNMAPPED, None,
        "Validates our own message contract. A remote caller has no message to "
        "validate (and the verb is withheld).",
    ),
    "channel.status": (
        UNMAPPED, None,
        "Reports our mailbox's inbox/sent/outbox/done state. Upstream serves no "
        "mailbox.",
    ),
    "channel.send": (
        UNMAPPED, None,
        "Writes to our file mailbox between sessions; ADR-0011 keeps steering local "
        "and upstream serves no message bus.",
    ),
    "channel.order": (
        UNMAPPED, None,
        "Orders the BRAIN, our own hierarchy node, which then issues the control. "
        "Upstream has no equivalent intermediary.",
    ),
    "channel.escalate": (
        UNMAPPED, None,
        "Raises an escalation into the local channel. Upstream's nearest write "
        "resolves a runtime request (POST /heartbeat-runs/:runId/runtime-requests/"
        ":requestId/resolve) — the opposite direction, so it is not a counterpart.",
    ),
    "channel.report": (
        UNMAPPED, None,
        "A local progress report written to the mailbox; no upstream route.",
    ),
    "channel.wait": (
        UNMAPPED, None,
        "Blocks on our mailbox for a message. Upstream serves no blocking read.",
    ),
    "channel.brain-inbox": (
        UNMAPPED, None,
        "Reads the brain's mailbox; no upstream route.",
    ),
    "channel.brain-outbox": (
        UNMAPPED, None,
        "Reads the brain's outbox; no upstream route.",
    ),
    "channel.head-commit": (
        UNMAPPED, None,
        "Reports the commit a rung is on. Upstream serves no VCS read.",
    ),
    "channel.consume": (
        UNMAPPED, None,
        "Consumes a mailbox message locally; no upstream route.",
    ),
    "channel.log": (
        UNMAPPED, None,
        "APPENDS one event to a directive's own live log stream (fleet/channel.py "
        "cmd_log writes the per-directive log the sister and subagents feed). "
        "Upstream's nearest route (GET /heartbeat-runs/:runId/log) READS its own "
        "run log and writes nothing, and no upstream route appends into a "
        "caller's stream, so there is no counterpart — the registry declares this "
        "verb read-class while its handler appends, and both halves of that "
        "divergence land on this same row.",
    ),
    "channel.kb": (
        UNMAPPED, None,
        "Answers a query from OUR institutional index (governance/knowledge/, and "
        "CANNOT-ASSESS when the catalogue is absent). Upstream serves no knowledge "
        "route: its reads report agent and company state, never recorded lessons.",
    ),
    "channel.steer": (
        UNMAPPED, None,
        "Queues a mid-run steering hint into our own message bus "
        "(`.fleet/steers/`), delivered to one in-flight directive without a kill "
        "or re-dispatch. Upstream serves no message bus (ADR-0011 keeps steering "
        "local); its nearest route "
        "(POST /heartbeat-runs/:runId/runtime-requests/:requestId/resolve) "
        "answers a request the run itself raised, keyed by a requestId our verb "
        "never has — an input we cannot even address, so it is refuted rather "
        "than recorded as a mismatch.",
    ),
    "board.status": (
        UNMAPPED, None,
        "The dispatch frontier and the live claims. Upstream can list work items "
        "(GET /companies/:companyId/issues) but serves no eligibility or claim view, "
        "so there is no counterpart for this read.",
    ),
    "board.held": (
        UNMAPPED, None,
        "The live claim holder of one issue. Upstream's claim authority lives in the "
        "runner's semantic-action catalog, which is a module and not an HTTP route.",
    ),
    "board.eligible": (
        UNMAPPED, None,
        "The dependency-ordered eligibility rule (GR-20) is ours; upstream serves no "
        "eligibility route.",
    ),
    "board.audit": (
        UNMAPPED, None,
        "The claim-ledger audit. Upstream's activity log is append-only and immutable "
        "but records activity entries, not dispatch claims — a different object.",
    ),
    "board.liveness": (
        UNMAPPED, None,
        "Whether the board's freshness contract has an installed producer, read "
        "from the LIVE crontab. Upstream serves no view of a host's scheduler: its "
        "nearest route (GET /health) answers for a process it guards, not for a "
        "scheduled job that is supposed to exist.",
    ),
    "board.dangling": (
        UNMAPPED, None,
        "The open issues whose declared `Parent:` is CLOSED — a dependency-integrity "
        "read over the epic graph, which the claim path refuses as `epic-closed`. "
        "Upstream can list work items (GET /companies/:companyId/issues) but serves "
        "no parent or dependency view.",
    ),
    "board.focus": (
        UNMAPPED, None,
        "Resolves the ONE active epic, its open children and the pooled set (epic "
        "#707) — our focus rule, not upstream's. Upstream's issue list answers no "
        "'which epic is active' question.",
    ),
    "board.pool": (
        UNMAPPED, None,
        "The out-of-epic pool epic focus parked (`.board/pool.jsonl`). Upstream "
        "serves no pool: the parking decision is ours, so the view is ours.",
    ),
    "board.claim": (
        UNMAPPED, None,
        "Claiming an issue before work is our board protocol (GR-3/GR-20). Upstream's "
        "runner carries claim authority but serves no claim route.",
    ),
    "board.dispatch": (
        UNMAPPED, None,
        "The A2A arbitration that proves issue -> epic -> lane ownership before a "
        "directive is routed, and names the evidence it checked. Upstream's "
        "equivalent authority lives in the runner's semantic-action catalog — a "
        "module, not an HTTP route — the same reason board.held is unmapped.",
    ),
    "board.release": (
        UNMAPPED, None,
        "Releases a claim; the mirror of board.claim, and no upstream route.",
    ),
    "board.reap": (
        UNMAPPED, None,
        "Releases claims wedged by dead agents. The upstream watchdog is a server "
        "module (active-run-watchdog), not a route.",
    ),
    "board.snapshot": (
        UNMAPPED, None,
        "Refreshes `.board/snapshot.json` from GitHub. Upstream serves no route into "
        "that file.",
    ),
    "board.trigger": (
        UNMAPPED, None,
        "Performs the ONE bounded board refresh the `snapshot-stale` refusal names "
        "as its remedy, and parks the directive when freshness does not return. It "
        "is board.snapshot's other entry point, so it is unmapped for the same "
        "reason: upstream serves no route into `.board/snapshot.json`.",
    ),
    "board.queue": (
        UNMAPPED, None,
        "Reports and validates the owner's committed dispatch queue (duplicates, "
        "unknown numbers, cycles). Upstream serves no dispatch queue; the write "
        "that moves an issue through ours is the claim path (board.claim).",
    ),
    "board.freshness": (
        UNMAPPED, None,
        "Checks whether `.board/snapshot.json` is stale against the schedule's job "
        "set, the read the `snapshot-stale` refusal is named for. Upstream serves "
        "no route into that file; the remedy is our own board.trigger/board.snapshot "
        "pair, not an upstream call.",
    ),
    "recover.status": (
        UNMAPPED, None,
        "The orphan/suspect state of our sessions. Upstream serves no orphan view "
        "(its reconciliation is server-side, not a route).",
    ),
    "recover.stamp": (
        UNMAPPED, None,
        "Stamps one session as reconciled. The nearest upstream record is a watchdog "
        "decision on a run (POST /heartbeat-runs/:runId/watchdog-decisions), which is "
        "a run-scoped decision record — a different object from a session stamp.",
    ),
    "recover.clear": (
        UNMAPPED, None,
        "Clears a reconcile stamp. Upstream's clear-error clears an agent's error "
        "state, a different object from a marker on our session row. The name "
        "similarity is why it is written down.",
    ),
    "recover.watch": (
        UNMAPPED, None,
        "The reconcile worker's loop, driven by cron locally. Upstream serves no "
        "worker view.",
    ),
    "closure.status": (
        UNMAPPED, None,
        "Work-item lifecycle state. Upstream lists work items but serves no lifecycle "
        "invariant view.",
    ),
    "closure.audit": (
        UNMAPPED, None,
        "Audits the closure invariants themselves. Upstream's activity log records "
        "actions, not invariants.",
    ),
    "closure.collect": (
        UNMAPPED, None,
        "Collects a work item's terminal artifacts. Upstream serves no equivalent.",
    ),
    "closure.retire": (
        UNMAPPED, None,
        "Retires a `.fleet/sent/` directive whose issue closed without a change of "
        "its own, moving the record to `.fleet/done/` stamped with the reason and "
        "the superseding issue. Upstream keeps no sent-directive store; its nearest "
        "route (PATCH /issues/:id, the mutable field write closure.close is already "
        "measured against) acts on the issue row, not on our record of what was "
        "dispatched.",
    ),
}


def route(key: str) -> UpstreamRoute:
    """The inventory entry for ``key``; a missing key is a loud miss, never ``None``."""
    try:
        return _ROUTE_BY_KEY[key]
    except KeyError as exc:
        raise KeyError(f"{key!r} is not in UPSTREAM_ROUTES") from exc


def route_keys() -> Tuple[str, ...]:
    """Every route key in the inventory, in declaration order."""
    return tuple(r.key for r in UPSTREAM_ROUTES)


_PARAM = re.compile(r"(?<=/)(?::[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\})(?=/|$)")


def normalize(route_text: str) -> str:
    """``METHOD`` + path with every parameter segment collapsed to ``*``.

    Two spellings are the same route: upstream declares ``POST /agents/:id/pause``
    in its route file, the seam reaches it as ``/api/agents/{agentId}/pause``, and
    an OpenAPI document writes the parameter as ``{agentId}``. All three normalize
    to ``POST /agents/*/pause`` — a leading ``/api`` prefix and the parameter's
    *name* are not part of a route's identity, so comparing without doing this
    would report a mismatch that is only a spelling.
    """
    method, _, path = route_text.partition(" ")
    if path.startswith(API_PREFIX + "/"):
        path = path[len(API_PREFIX):]
    elif path == API_PREFIX:
        path = "/"
    return f"{method.upper()} {_PARAM.sub('*', path)}"


# --------------------------------------------------------------------------
# The verb side: read from the RC-2 registry, never copied
# --------------------------------------------------------------------------


def load_verbs(root: Any) -> Dict[str, Any]:
    """Load ``verbs.yaml`` through RC-2's own validator (``control/`` cli).

    Reusing RC-2's loader rather than parsing the YAML here is deliberate: the
    registry has exactly one reader of record, so the rules it enforces (closed
    sets, the audit rule, the cross-reference) are the rules this table is built
    on. The module is loaded by path because ``control-plane`` is not a valid
    Python package name — the same route its own suite takes.
    """
    root_path = Path(root).resolve()
    cli_path = root_path.joinpath(*CONTROL_CLI)
    expected_registry = root_path.joinpath(*VERBS_YAML)
    if not cli_path.is_file():
        raise FileNotFoundError(f"the RC-2 loader is missing: {cli_path}")
    if not expected_registry.is_file():
        raise FileNotFoundError(f"the RC-2 registry is missing: {expected_registry}")

    spec = importlib.util.spec_from_file_location("rc2_control_verbs_cli", cli_path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {cli_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    resolved = Path(module.REGISTRY).resolve()
    if resolved != expected_registry:
        raise FileNotFoundError(
            f"the loader at {cli_path} reads {resolved}, not {expected_registry}"
        )
    return module.load_registry()


def registry_verbs(registry: Dict[str, Any]) -> Tuple[Tuple[str, Dict[str, Any]], ...]:
    """``(id, entry)`` for every declared verb, in declaration order."""
    return tuple((str(v["id"]), v) for v in (registry.get("verbs") or []))


def build_table(
    root: Any = None, *, registry: Optional[Dict[str, Any]] = None
) -> Tuple[MappingRow, ...]:
    """The table: one row per declared RC-2 verb, in the registry's own order.

    With no ``registry`` argument the vocabulary is read from ``root`` (the repo
    root by default) through :func:`load_verbs`; passing one is how the suite
    drives the refusal below, and how a reader can render the table for a
    vocabulary other than the live one.
    """
    doc = registry if registry is not None else load_verbs(ROOT if root is None else root)
    rows: List[MappingRow] = []
    for verb_id, entry in registry_verbs(doc):
        opinion = _OPINIONS.get(verb_id)
        if opinion is None:
            raise UnclassifiedControlVerb(
                f"{verb_id!r} (effect_class {entry.get('effect_class')!r}) is declared "
                f"by the RC-2 registry and has no row in control_mapping._OPINIONS. "
                f"Classify it as {MAPPED}, {MISMATCH} or {UNMAPPED} — a new verb "
                f"cannot inherit 'no upstream counterpart' by default."
            )
        state, route_key, why = opinion
        if state not in STATES:
            raise ValueError(f"{verb_id}: {state!r} is not one of {STATES}")
        upstream = None if route_key is None else route(route_key)
        if state == UNMAPPED and upstream is not None:
            raise ValueError(f"{verb_id}: an {UNMAPPED} row names no route")
        if state in (MAPPED, MISMATCH) and upstream is None:
            raise ValueError(f"{verb_id}: a {state} row must name its route")
        if not why:
            raise ValueError(f"{verb_id}: a {state} row must say why")
        rows.append(MappingRow(verb_id, state, upstream, why))
    return tuple(rows)


def stale_rows(registry: Dict[str, Any]) -> Tuple[str, ...]:
    """Verb ids this mapping classifies that the registry no longer declares.

    The other direction of the same invariant: a row whose verb has been removed
    from the vocabulary describes a lever that no longer exists.
    """
    declared = {str(v["id"]) for v in (registry.get("verbs") or [])}
    return tuple(sorted(set(_OPINIONS) - declared))


def _named_routes(rows: Iterable[MappingRow]) -> Tuple[UpstreamRoute, ...]:
    """The distinct routes the table **names**, in first-seen order.

    "Named" is wider than "mapped" on purpose: a ``MISMATCH`` row names the route
    it refuses, and the probe must check that route is real (a mismatch recorded
    against a route upstream does not serve would be an invented finding).
    """
    seen: Dict[str, UpstreamRoute] = {}
    for row in rows:
        if row.upstream is not None:
            seen.setdefault(row.upstream.key, row.upstream)
    for mismatch in MISMATCHES:
        if mismatch.upstream is not None:
            seen.setdefault(mismatch.upstream.key, mismatch.upstream)
    return tuple(seen.values())


def mapped_routes(
    rows: Optional[Tuple[MappingRow, ...]] = None,
) -> Tuple[UpstreamRoute, ...]:
    """The distinct routes a ``MAPPED`` row names — the routes we do correspond to.

    A ``MISMATCH`` row does **not** count: the whole content of such a row is that
    the route is *not* the counterpart of that verb.
    """
    table = build_table() if rows is None else rows
    seen: Dict[str, UpstreamRoute] = {}
    for row in table:
        if row.state == MAPPED and row.upstream is not None:
            seen.setdefault(row.upstream.key, row.upstream)
    return tuple(seen.values())


def unmapped_upstream_routes(
    rows: Optional[Tuple[MappingRow, ...]] = None,
) -> Tuple[UpstreamRoute, ...]:
    """Upstream routes that **no** ``MAPPED`` row corresponds to — the reverse table.

    ``POST /agents/:id/terminate`` is the one the lane brief named: upstream can
    end an agent irreversibly and we have no verb that does it. The per-agent
    ``POST /agents/:id/pause`` is here too, for the other half of the brief: our
    only pause is fleet-wide, so upstream's per-agent pause has no counterpart
    verb either. The list is computed, not asserted, so a later lane that really
    maps one of these routes removes it rather than leaving a stale claim behind.
    """
    mapped = {r.key for r in mapped_routes(rows)}
    return tuple(r for r in UPSTREAM_ROUTES if r.key not in mapped)


# --------------------------------------------------------------------------
# The offline probe: read the peer's declaration of its own surface
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeResult:
    """What the peer's served OpenAPI document says about the routes we name."""

    checked: Tuple[str, ...]
    declared: frozenset
    missing: Tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.missing


def probe_routes(
    transport: Transport, rows: Optional[Tuple[MappingRow, ...]] = None
) -> ProbeResult:
    """Check every named route against the peer's served surface — offline.

    This is the **only** transport call in the module, and it is a read:
    ``GET <api>/openapi.json``, upstream's own declaration of what it serves
    (``client.openapi()``). The gate exercises it through ``FixtureTransport``, so
    no network is touched, and the routes the table names are checked against a
    document rather than against our belief about it.

    It reports what is missing rather than raising: a route absent from the served
    document is a finding about the *mapping* (or about the peer's doc), and the
    caller decides what that means.
    """
    table = build_table() if rows is None else rows
    response = transport.request(
        read_only_method("GET"), f"{API_PREFIX}/openapi.json"
    )
    document = response.body if isinstance(response.body, dict) else {}
    paths = document.get("paths")
    paths = paths if isinstance(paths, dict) else {}
    declared = frozenset(
        normalize(f"{method} {path}")
        for path, operations in paths.items()
        for method in (operations or {})
        if isinstance(method, str)
    )
    checked = tuple(r.key for r in _named_routes(table))
    missing = tuple(key for key in checked if normalize(key) not in declared)
    return ProbeResult(checked=checked, declared=declared, missing=missing)


def read_only_method(method: str) -> str:
    """The method, if this module is allowed to use it.

    The mapping describes upstream's control routes and must never press them, so
    the one call site in the module cannot be handed a mutating method: it asks
    here first. A mutating method is refused by name rather than passed through.
    """
    if method.upper() not in READ_ONLY_METHODS:
        mutating = (
            "a mutating method"
            if method.upper() in MUTATING_METHODS
            else "not a method this seam uses"
        )
        raise ValueError(
            f"{method!r} is {mutating}: this module maps upstream's control routes "
            f"and may only issue {READ_ONLY_METHODS}"
        )
    return method.upper()
