#!/usr/bin/env python3
"""Steering channel CLI — the transport between the brain and the fleet.

Operating model (M26, issue #160): the **brain** (advisor session) issues
directives; the **sister** session (a dumb terminal on DeepSeek v4.1 Flash, no
thinking) executes them by spawning epic-focused subagents; subagents report
results back through the sister. This CLI is the file-mailbox transport for
that loop — localhost mechanics (GR-21), no network, no daemons.

Mailbox layout (runtime state, gitignored):

    .fleet/inbox/    messages for the sister to drain (written by brain send)
    .fleet/sent/     the brain's own copy of everything it sent
    .fleet/outbox/   acks and results written back for the brain
    .fleet/done/     directives answered and consumed

Messages are validated against `fleet/schema/message.schema.json` semantics
before they move. The topology, the directive vocabulary and the trust rules the
validator enforces are the normative contract in `fleet/CONTRACT.md`, and the
transport itself is decided by ADR-0011 (docs/decision-records/) — this module is
the machine that runs that contract. Issue #367 extends the machine additively
with three live capabilities over the same mailbox: per-directive live log
streams (`log` / `follow`), KB access (`kb`, against governance/knowledge/), and
mid-run steering (`steer`, drained by the sister loop each cycle). Exit codes
are the repo tri-state: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import runtime
import runaway

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance.policy import lease  # noqa: E402

FLEET_DIR = runtime.FLEET_DIR
SCHEMA_PATH = ROOT / "fleet" / "schema" / "message.schema.json"
INBOX = FLEET_DIR / "inbox"
BRAIN_INBOX = FLEET_DIR / "brain" / "inbox"
BRAIN_OUTBOX = FLEET_DIR / "brain" / "outbox"
BRAIN_DONE = FLEET_DIR / "brain" / "done"
BRAIN_SENT = FLEET_DIR / "brain" / "sent"
SENT = FLEET_DIR / "sent"
OUTBOX = FLEET_DIR / "outbox"
DONE = FLEET_DIR / "done"
SLOG = FLEET_DIR / "slog.jsonl"
HEARTBEAT = FLEET_DIR / "sister.heartbeat.json"
BRAIN_HEARTBEAT = FLEET_DIR / "brain.heartbeat.json"

# --- the A2A transport extension (issue #367) --------------------------------
# Three capabilities the discrete-mailbox transport could not carry: LIVE agent
# logs, KB access, and mid-run steering. All are additive verbs over the same
# file mailbox (GR-21: localhost, file-based, no daemons — "live" is
# short-poll/long-poll over `.fleet/`).
#
# ``LOGS`` holds one append-only JSONL stream per directive
# (`.fleet/runs/<directive>.log`, beside the run marker), written by the loop
# that owns the run and tailed by the `follow` verb. ``STEERS`` is the brain's
# outbound steering queue (`.fleet/brain/steer/<directive>.json`): one pending
# steer per in-flight directive, drained by the sister loop every cycle.
LOGS = FLEET_DIR / "runs"
STEERS = FLEET_DIR / "brain" / "steer"
# A directive id becomes a mailbox filename component, so a steer/log/follow
# argument must be a safe name — `../` must not be able to walk out.
DIRECTIVE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")

# The FinOps vocabulary is harvested, not invented (issue #164) and is declared
# once in governance/finops/policy.json: tiers from capital-underwriting
# config/leaderboard/tier-policy.json, thinking effort from leaderboard
# lib/fleet-roster.sh role_effort(). scripts/check-finops-chooser.sh fails if
# these constants, the message schema and the policy stop agreeing.
MESSAGE_TYPES = ("directive", "ack", "result", "halt", "escalate", "steer")
SEVERITIES = ("info", "warn", "critical")
CONTROL_ACTIONS = (
    "poke",
    "status",
    "pause",
    "resume",
    "refresh",
    "restart",
    "stop",
    "kill",
    "halt",
    "override",
)
# Controls that act on the loop process itself rather than on the work queue.
PROCESS_CONTROLS = ("refresh", "restart", "stop", "kill", "halt")
# Control actions whose whole point is to re-dispatch a named issue.
TASK_CONTROLS = ("override",)
MODEL_TIERS = ("flash", "pro", "auditor")
THINKING_LEVELS = ("none", "low", "medium", "high")
# Order kinds (schema v1, additive): `work` needs an issue; the others are
# answered by the brain without dispatching anything to the sister.
TASK_KINDS = ("work", "status", "report", "ping", "steer")
NON_WORK_KINDS = ("status", "report", "ping", "steer")
_ROLE_RE = re.compile(r"^(operator|brain|sister|subagent(-[a-z0-9]+)?)$")

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2

# A beat older than this means the loop died rather than that it is busy: the
# loop beats every poll cycle (default 30s) and before each directive. The value
# is declared once in governance/policy/lease.py with its ordering invariants.
STALE_HEARTBEAT_SECONDS = lease.RUNG_HEARTBEAT_SECONDS

# A directive the sister never drains is abandoned after this long; `status`
# reports the abandoned ones rather than counting them as queued.
DIRECTIVE_LIFETIME_SECONDS = lease.DIRECTIVE_LIFETIME_SECONDS


# ── the declared capability set (issue #319) ────────────────────────────────
# A control that ships but is not live is a silently absent control: after
# isolation (#263), lifecycle (#269) and reconciliation (#304) merged, the
# running sister kept executing pre-merge code and the only signal was
# `watchdog decide() == "drifted"` — which compares *commits*, is reported per
# rung, and never says WHICH capability is missing. An operator cannot tell
# "the loop is old" from "the loop is old and therefore lanes are not being
# beat, so orphans will never be flagged".
#
# So the repository declares, once, every control the fleet's rungs must have
# live (below), a rung declares the set its build implements (in its beat), and
# the watchdog compares the two. Each capability is anchored at the commit that
# first provided it — `since` — so a rung that predates it is reported by NAME.
CAPABILITY_VOCABULARY_VERSION = 1

#: The rungs the watchdog supervises with a heartbeat. A capability naming any
#: other rung could never be compared, so `scripts/check-fleet-runbook.sh`
#: refuses a declaration that does.
CAPABILITY_RUNGS = ("brain", "sister")


@dataclass(frozen=True)
class Capability:
    """One control the fleet must have live: where it lives, and since when."""

    name: str
    version: int
    rungs: tuple[str, ...]
    since: str
    evidence: str
    summary: str

    @property
    def id(self) -> str:
        return f"{self.name}@{self.version}"


#: The repository's declaration: the capability set the code provides. `since`
#: is the commit that first provided it and `evidence` the path that implements
#: it; the gate proves both (the commit is an ancestor of HEAD and it touches
#: that path), so a capability declared ahead of its code is a gate finding.
CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        name="steering-channel",
        version=1,
        rungs=("brain", "sister"),
        since="01e1b4edee4a9f7b83d68b4cdcc98638ee3b5b44",
        evidence="fleet/channel.py",
        summary="a validated file mailbox carries every directive between the rungs",
    ),
    Capability(
        name="brain-decompose",
        version=1,
        rungs=("brain",),
        since="9c559f6825594726f95725181d19c51fc49ef652",
        evidence="fleet/brain.py",
        summary="the brain decomposes an epic into child issues and dispatches the ready wave",
    ),
    Capability(
        name="lane-isolation",
        version=1,
        rungs=("sister",),
        since="8b97ab612e4f7235796cf7817a8e56169091e4c0",
        evidence="governance/isolation/cli.py",
        summary="every dispatch mints a session identity and its own lane worktree",
    ),
    Capability(
        name="lifecycle-closeout",
        version=1,
        rungs=("sister",),
        since="eb081c86f0c52b0f7d052c8c0a6db6f840c2563b",
        evidence="governance/lifecycle/cli.py",
        summary="a merged work item is driven to terminal state instead of left half-closed",
    ),
    Capability(
        name="orphan-reconcile",
        version=1,
        rungs=("sister",),
        since="0ff8adf59e32f39292d8188c45249a55ddb53a0e",
        evidence="governance/reconcile/cli.py",
        summary="a session beats while it runs and its orphan is swept, parked or shelved",
    ),
    Capability(
        name="run-pool",
        version=1,
        rungs=("sister",),
        since="1a97c1f25b202efe2f5f42beefc204ace0992862",
        evidence="fleet/terminal.py",
        summary="each dispatch is a run marker with its own beat, so N runs read as N beats",
    ),
)

# The three cases the signal must separate, each with its OWN remediation (the
# issue's acceptance criterion). A case label is part of the operator contract:
# `scripts/check-fleet-runbook.sh` requires each one to appear in a provoked
# report, so a label cannot be renamed out of existence silently.
KIND_CURRENT = "current"
KIND_DOWN = "down"
KIND_DRIFTED = "drifted"
KIND_CAPABILITY_STALE = "capability-stale"
KIND_UNKNOWN = "unknown"

CASE_LABELS = {
    KIND_CURRENT: "capabilities current",
    KIND_DOWN: "rung DOWN",
    KIND_DRIFTED: "rung on DRIFTED CODE",
    KIND_CAPABILITY_STALE: "rung on CURRENT code MISSING a declared capability",
    KIND_UNKNOWN: "capability declaration UNKNOWN",
}

REMEDIATION_DOWN = (
    "start the rung: bash fleet/run-fleet.sh (one brain + one sister), or respawn just this one "
    "with python3 fleet/watchdog.py run"
)
REMEDIATION_DRIFTED = (
    "restart the rung BETWEEN runs so it loads HEAD: bash fleet/terminal.sh (sister) / "
    "bash fleet/brain.sh (brain). python3 fleet/watchdog.py run respawns an idle drifted rung "
    "and deliberately leaves a run in flight alone"
)
REMEDIATION_CAPABILITY_STALE = (
    "a restart will NOT fix this: the rung is already on HEAD and does not implement what the "
    "repository declares, so the owning lane must wire the capability (or correct the "
    "declaration) and the restart then loads the fix"
)
REMEDIATION_UNKNOWN = (
    "cannot assess the rung's declaration: fix the vocabulary version it reports (or the commit "
    "it beats with) and re-run"
)


@dataclass(frozen=True)
class CapabilityFinding:
    """What one rung implements, measured against the repository's declaration."""

    rung: str
    kind: str
    missing: tuple[str, ...] = ()
    undeclared: tuple[str, ...] = ()
    detail: str = ""
    remediation: str = ""

    @property
    def case(self) -> str:
        return CASE_LABELS[self.kind]

    @property
    def stale(self) -> bool:
        """True when a declared capability is provably missing on this rung."""
        return bool(self.missing)


@dataclass(frozen=True)
class CapabilityDeclaration:
    """A rung's own declaration of what it implements, and where it came from."""

    ids: frozenset[str] | None
    source: str  # "beat" (the rung declared a list) or "commit" (derived)
    version: int | None = None

    @property
    def assessable(self) -> bool:
        return self.ids is not None


#: `git` answers per commit are cached: one watchdog pass resolves a rung's
#: commit once, and `status` re-reads the same beats within a tick.
_COMMIT_CAPABILITIES: dict[str, frozenset[str] | None] = {}


def _git(args: list[str]) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            ["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None


def capabilities_for_rung(rung: str) -> tuple[Capability, ...]:
    """The capability set the repository declares for one rung."""
    return tuple(cap for cap in CAPABILITIES if rung in cap.rungs)


def commit_capabilities(commit: str) -> frozenset[str] | None:
    """The capability ids the code at `commit` provides, or None if unassessable.

    A capability is provided at a commit when the commit it shipped in (`since`)
    is an ancestor of it — the rung that declares that commit loaded a build
    containing the capability's code. An unknown or malformed commit answers
    None (CANNOT-ASSESS) rather than "missing nothing", so it can never be read
    as a pass.
    """
    if not commit or commit == "unknown":
        return None
    if commit in _COMMIT_CAPABILITIES:
        return _COMMIT_CAPABILITIES[commit]
    verified = _git(["rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}"])
    if verified is None or verified.returncode != 0:
        _COMMIT_CAPABILITIES[commit] = None
        return None
    provided: set[str] = set()
    for cap in CAPABILITIES:
        result = _git(["merge-base", "--is-ancestor", cap.since, commit])
        if result is None or result.returncode not in (0, 1):
            _COMMIT_CAPABILITIES[commit] = None
            return None
        if result.returncode == 0:
            provided.add(cap.id)
    frozen = frozenset(provided)
    _COMMIT_CAPABILITIES[commit] = frozen
    return frozen


def rung_declaration(
    beat: dict | None,
    *,
    resolve: Callable[[str], frozenset[str] | None] = commit_capabilities,
) -> CapabilityDeclaration:
    """Read what a rung says it implements — its own declaration, twice over.

    A beat may carry `capabilities` (the versioned list its build implements)
    and `capabilities_version` (the vocabulary it speaks). That field is
    ADDITIVE: every beat written before it shipped carries no list, and those
    rungs declare themselves through the `commit` they beat with, which is a
    declaration the running build writes about itself. Either way the set is
    the rung's, never the watchdog's.
    """
    if beat is None:
        return CapabilityDeclaration(ids=None, source="beat")
    entry = beat.get("capabilities")
    if entry is not None:
        version = beat.get("capabilities_version")
        if version != CAPABILITY_VOCABULARY_VERSION:
            return CapabilityDeclaration(ids=None, source="beat", version=version)
        if not isinstance(entry, (list, tuple)) or any(not isinstance(item, str) for item in entry):
            return CapabilityDeclaration(ids=None, source="beat", version=version)
        return CapabilityDeclaration(ids=frozenset(entry), source="beat", version=version)
    return CapabilityDeclaration(ids=resolve(str(beat.get("commit", "unknown"))), source="commit")


def capability_finding(
    rung: str,
    beat: dict | None,
    head: str,
    *,
    resolve: Callable[[str], frozenset[str] | None] = commit_capabilities,
) -> CapabilityFinding:
    """Compare what a rung implements against what the repository declares.

    Separates the three cases the fleet must not conflate, each with its own
    remediation: a rung that is DOWN (no beat), a rung on DRIFTED CODE (a
    different commit from HEAD), and a rung on CURRENT code that does not
    implement a capability the repository declares — the one a commit
    comparison calls healthy.
    """
    declared = capabilities_for_rung(rung)
    if not declared:
        return CapabilityFinding(rung=rung, kind=KIND_CURRENT, detail="no capability is declared for this rung")
    if beat is None:
        return CapabilityFinding(rung=rung, kind=KIND_DOWN, detail="no heartbeat", remediation=REMEDIATION_DOWN)
    if head in ("", "unknown"):
        return CapabilityFinding(
            rung=rung,
            kind=KIND_UNKNOWN,
            detail="HEAD cannot be read, so the repository's live declaration cannot be compared",
            remediation=REMEDIATION_UNKNOWN,
        )
    running = str(beat.get("commit", "unknown"))
    drifted = running != head
    if not drifted and beat.get("capabilities") is None:
        # The rung beats HEAD's commit and declares no list of its own: its build
        # IS the build HEAD describes, so it provides every declared capability
        # (the gate proves each `since` is an ancestor of HEAD). No ancestry walk
        # is needed — and a short sha in a beat is therefore not "unreadable".
        return CapabilityFinding(
            rung=rung,
            kind=KIND_CURRENT,
            detail=f"on HEAD {head}; its build is the declared build",
        )
    declaration = rung_declaration(beat, resolve=resolve)
    if not declaration.assessable:
        detail = (
            f"running {running}, HEAD {head}: the beat's capability list is not readable"
            if declaration.source == "beat"
            else f"running {running}, HEAD {head}: its commit cannot be resolved to a capability set"
        )
        return CapabilityFinding(
            rung=rung,
            kind=KIND_UNKNOWN,
            detail=detail,
            remediation=REMEDIATION_UNKNOWN,
        )
    declared_ids = declaration.ids
    if declared_ids is None:  # `assessable` already refused this; kept as the type narrowing
        return CapabilityFinding(
            rung=rung,
            kind=KIND_UNKNOWN,
            detail=f"running {running}, HEAD {head}: the rung's declaration could not be read",
            remediation=REMEDIATION_UNKNOWN,
        )
    missing = tuple(cap.id for cap in declared if cap.id not in declared_ids)
    known = {cap.id for cap in CAPABILITIES}
    undeclared = tuple(sorted(declared_ids - known))
    if drifted:
        return CapabilityFinding(
            rung=rung,
            kind=KIND_DRIFTED,
            missing=missing,
            undeclared=undeclared,
            detail=f"running {running}, HEAD {head} (declared from its {declaration.source})",
            remediation=REMEDIATION_DRIFTED,
        )
    if missing:
        declares = len(declared) - len(missing)
        return CapabilityFinding(
            rung=rung,
            kind=KIND_CAPABILITY_STALE,
            missing=missing,
            undeclared=undeclared,
            detail=f"on HEAD {head} yet declaring {declares} of {len(declared)} ({declaration.source})",
            remediation=REMEDIATION_CAPABILITY_STALE,
        )
    return CapabilityFinding(
        rung=rung,
        kind=KIND_CURRENT,
        undeclared=undeclared,
        detail=f"on HEAD {head}, declaring all {len(declared)}",
    )


def capability_line(finding: CapabilityFinding) -> str:
    """One operator line per rung: the case, every missing capability, the fix."""
    prefix = f"{finding.rung}: {finding.case}"
    if finding.kind == KIND_CURRENT:
        extra = ""
        if finding.undeclared:
            extra = (
                f" — WARNING: it declares {', '.join(finding.undeclared)}, which the repository does "
                "not declare (the declaration is behind the code)"
            )
        return f"{prefix} — {finding.detail}{extra}"
    if finding.missing:
        return (
            f"{prefix} — CAPABILITY STALE: missing {', '.join(finding.missing)} "
            f"({finding.detail}); remediation: {finding.remediation}"
        )
    return f"{prefix} — {finding.detail}; remediation: {finding.remediation}"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: str) -> bool:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        datetime.fromisoformat(text)
        return True
    except ValueError:
        return False


def validate(message: dict) -> list[str]:
    """Return every contract violation; empty list means the message is valid."""
    problems: list[str] = []
    if not isinstance(message, dict):
        return ["message must be a JSON object"]
    message_type = message.get("type")
    if message_type not in MESSAGE_TYPES:
        problems.append(f"type must be one of {', '.join(MESSAGE_TYPES)}")
    for field in ("from", "to"):
        value = message.get(field)
        if not isinstance(value, str) or not _ROLE_RE.match(value):
            problems.append(f"{field} must match operator|brain|sister|subagent(-name)?")
    if "id" in message and (not isinstance(message["id"], str) or not message["id"].strip()):
        problems.append("id must be a non-empty string")
    if "ts" in message and (not isinstance(message["ts"], str) or not _parse_ts(message["ts"])):
        problems.append("ts must be an ISO-8601 timestamp")
    if "correlation_id" in message and not isinstance(message["correlation_id"], str):
        problems.append("correlation_id must be a string")
    if "nonce" in message and (not isinstance(message["nonce"], str) or not message["nonce"].strip()):
        problems.append("nonce must be a non-empty string (the anti-replay token)")
    if message_type == "directive" and message.get("to") not in ("sister", "brain"):
        problems.append("directives are addressed to the sister (from the brain) or to the brain (from the operator)")
    # Hierarchy (contract §4, rule 1b): the operator commands the brain, and the
    # brain commands the sister. Neither step may be skipped — an operator that
    # could address the sister directly would make the brain advisory. Reports
    # and escalations still travel *up* to the brain, so the rule is scoped to
    # the operator's own traffic and to directives addressed to the brain.
    if message.get("from") == "operator":
        if message.get("to") != "brain":
            problems.append(
                "the operator does not address the sister: it orders the brain, and the brain orders the sister"
            )
        elif message_type != "directive":
            problems.append("an operator order to the brain must be a directive")
    if message.get("to") == "brain" and message_type == "directive" and message.get("from") != "operator":
        problems.append("the brain takes orders only from the operator")
    if message_type == "directive" and message.get("to") == "sister" and message.get("from") != "brain":
        problems.append("only the brain may issue directives to the sister")
    if message.get("from") == "sister" and message_type == "directive":
        problems.append("the sister is a dumb terminal: it cannot issue directives")
    if message_type in ("ack", "result") and not message.get("correlation_id"):
        problems.append(f"{message_type} must carry correlation_id (the directive it answers)")
    if message_type in ("ack", "result") and message.get("from") == "brain" and message.get("to") != "operator":
        problems.append("the brain does not ack or report on its own directives (only back to the operator)")
    if message_type == "halt" and message.get("from") != "brain":
        problems.append("only the brain may issue a halt")
    if message_type == "steer":
        # Mid-run steering (issue #367): the brain injects a hint into a live
        # run, so it stays inside the hierarchy — only the brain steers, and it
        # steers the sister (the loop that owns the run), never a subagent.
        if not message.get("correlation_id"):
            problems.append("steer must carry correlation_id (the in-flight directive it steers)")
        if message.get("from") != "brain":
            problems.append("only the brain may steer a run mid-flight")
        if message.get("to") != "sister":
            problems.append("steer is addressed to the sister (the loop that owns the run)")
        target = message.get("correlation_id")
        if isinstance(target, str) and not DIRECTIVE_ID_RE.fullmatch(target):
            problems.append("steer names a directive id that is not a safe mailbox name")
    if message_type == "escalate":
        if not message.get("correlation_id"):
            problems.append("escalate must carry correlation_id (the directive that hit trouble)")
        if message.get("from") == "brain":
            if message.get("to") != "operator":
                problems.append("a brain escalation is addressed to the operator (the next level up)")
        elif message.get("to") != "brain":
            problems.append("escalations go up: subagents and the sister escalate to the brain")
        severity = message.get("severity")
        if severity is not None and severity not in SEVERITIES:
            problems.append(f"severity must be one of {', '.join(SEVERITIES)}")
    control = message.get("control")
    if control is not None:
        if message.get("from") != "brain":
            problems.append("only the brain may issue control")
        if control not in CONTROL_ACTIONS:
            problems.append(f"control must be one of {', '.join(CONTROL_ACTIONS)}")
        if control in TASK_CONTROLS and not (message.get("task") or {}).get("issue"):
            problems.append(f"control '{control}' must name the task.issue it overrides")
    model = message.get("model")
    if model is not None:
        if not isinstance(model, dict):
            problems.append("model must be an object")
        else:
            if model.get("tier") not in MODEL_TIERS:
                problems.append(f"model.tier must be one of {', '.join(MODEL_TIERS)}")
            if model.get("thinking") not in THINKING_LEVELS:
                problems.append(f"model.thinking must be one of {', '.join(THINKING_LEVELS)}")
    task = message.get("task")
    if task is not None:
        if not isinstance(task, dict):
            problems.append("task must be an object")
        else:
            kind = task.get("kind")
            if kind is not None and kind not in TASK_KINDS:
                problems.append(f"task.kind must be one of {', '.join(TASK_KINDS)}")
            issue = task.get("issue")
            # A decompose order carries task.decompose (a micro-task plan) instead of
            # an issue; like a non-work kind, it needs no issue of its own.
            carries_decompose = isinstance(task.get("decompose"), dict) and bool(task["decompose"].get("children"))
            if kind not in NON_WORK_KINDS and not carries_decompose and (
                not isinstance(issue, int) or isinstance(issue, bool) or issue < 1
            ):
                problems.append("task.issue must be a positive integer")
            for field in ("epic",):
                if field in task and (not isinstance(task[field], int) or isinstance(task[field], bool) or task[field] < 1):
                    problems.append(f"task.{field} must be a positive integer")
    if "body" in message and not isinstance(message["body"], str):
        problems.append("body must be a string")
    return problems


def load_message(source: Path | str) -> dict:
    """Accept a path to a JSON file *or* inline JSON.

    The brain is a live terminal, so forcing it to write a temp file for every
    directive is pure friction — and a bare JSON argument was previously read as
    a filename (``File name too long``). Inline JSON is the natural form.
    """
    text = str(source)
    if text.lstrip().startswith("{"):
        raw, label = text, "<inline message>"
    else:
        target = Path(text)
        label = str(target)
        try:
            raw = target.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"channel: CANNOT-ASSESS — cannot read {target}: {exc}", file=sys.stderr)
            raise SystemExit(EXIT_CANNOT_ASSESS)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"channel: CANNOT-ASSESS — {label} is not valid JSON: {exc.msg}", file=sys.stderr)
        raise SystemExit(EXIT_CANNOT_ASSESS)
    return data


def log_stream_path(directive_id: str) -> Path:
    """The per-directive live log stream one run's stdout/events append to."""
    if not DIRECTIVE_ID_RE.fullmatch(directive_id):
        raise ValueError(f"directive id {directive_id!r} is not a safe mailbox name")
    return LOGS / f"{directive_id}.log"


def append_directive_log(directive_id: str, line: str, source: str = "sister") -> Path:
    """Append one event line to a directive's live log stream (issue #367).

    One line per ``os.write`` with O_APPEND: writers (the loop's event stream
    and the subagent-stdout pump) cannot interleave inside a line. The same
    artifact `follow` tails, so the transport is the mailbox, not a socket.
    """
    path = log_stream_path(directive_id)
    entry = json.dumps(
        {"ts": now_iso(), "directive": directive_id, "source": source, "line": str(line)[:1000]}
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, (entry + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    return path


def pending_steers() -> list[Path]:
    """The brain's undelivered steering messages, oldest first."""
    if not STEERS.exists():
        return []
    return sorted(STEERS.glob("*.json"), key=lambda path: path.stat().st_mtime)


def consume_steer(path: Path) -> None:
    """A steer that reached its run leaves the queue; delivery is the log entry."""
    try:
        path.unlink()
    except OSError:
        pass


def cmd_log(args: argparse.Namespace) -> int:
    """Append one event to a directive's live log stream (sister/subagent side)."""
    if not DIRECTIVE_ID_RE.fullmatch(args.directive):
        print(f"channel log: REFUSED — {args.directive!r} is not a safe directive id", file=sys.stderr)
        return EXIT_NOT_OK
    path = append_directive_log(args.directive, args.line, source=args.source or "sister")
    print(f"channel log: OK — appended to {path}")
    return EXIT_OK


def cmd_follow(args: argparse.Namespace) -> int:
    """Tail one directive's live log stream — the `follow`/`listen` verb (#367).

    Prints what the stream already holds, then keeps printing new lines as the
    run writes them. ``--timeout-seconds 0`` follows forever (the operator's
    live view); ``--max-lines`` bounds it for tests.
    """
    if not DIRECTIVE_ID_RE.fullmatch(args.directive):
        print(f"channel follow: REFUSED — {args.directive!r} is not a safe directive id", file=sys.stderr)
        return EXIT_NOT_OK
    path = log_stream_path(args.directive)
    deadline = time.monotonic() + args.timeout_seconds if args.timeout_seconds > 0 else None
    seen = 0
    offset = 0 if args.from_start else (path.stat().st_size if path.exists() else 0)
    while True:
        if path.exists():
            with open(path, encoding="utf-8") as handle:
                handle.seek(offset)
                lines = handle.read().splitlines()
                offset = handle.tell()
            for line in lines:
                if not line.strip():
                    continue
                print(f"channel follow [{args.directive}]: {line}", flush=True)
                seen += 1
                if args.max_lines and seen >= args.max_lines:
                    return EXIT_OK
        if deadline is not None and time.monotonic() >= deadline:
            print(
                f"channel follow: IDLE — {seen} line(s) seen for {args.directive} in window",
                file=sys.stderr,
            )
            return EXIT_OK
        nap = args.interval
        if deadline is not None:
            nap = min(nap, max(0.0, deadline - time.monotonic()))
        time.sleep(nap)


def cmd_kb(args: argparse.Namespace) -> int:
    """Query the institutional KB (governance/knowledge/) — the `kb` verb (#367).

    A running agent can pull the KB/lessons it needs mid-run instead of only the
    static paths a directive attached. Answers from the recorded catalogue
    (``governance/knowledge/catalog.json``), the same artifact
    ``governance/knowledge/cli.py query`` reads, with source-backed evidence
    per hit.
    """
    directory = str(ROOT / "governance" / "knowledge")
    # The knowledge tooling is flat (namespace module, no package — like this
    # file), so its transitive imports (`sources`, `secretpolicy`, `crossref`)
    # land in sys.modules under their bare names too. Stash and restore the
    # whole family so a query cannot leak a flat module into the caller's
    # namespace and shadow a real package later.
    flat_names = ("model", "query", "indexer", "sources", "secretpolicy", "crossref")
    stash = {name: sys.modules.pop(name) for name in flat_names if name in sys.modules}
    sys.path.insert(0, directory)
    try:
        import indexer as kb_indexer  # noqa: PLC0415 - scoped to this verb
        import model as kb_model  # noqa: PLC0415
        import query as kb_query  # noqa: PLC0415
    finally:
        if directory in sys.path:
            sys.path.remove(directory)
        for name in flat_names:
            sys.modules.pop(name, None)
        sys.modules.update(stash)
    catalog = kb_indexer.load_catalog(ROOT / "governance" / "knowledge" / kb_indexer.CATALOG_FILENAME)
    if catalog is None:
        print("channel kb: CANNOT-ASSESS — no knowledge catalogue (run the knowledge-index build)", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    index = kb_model.Index.from_dict(catalog)
    results = kb_query.query(index, text=args.text, kind=args.kind, owner=args.owner, tag=args.tag, limit=args.limit)
    if args.json:
        print(json.dumps(kb_query.summarize(index, results), indent=2, sort_keys=True))
    else:
        print(f"channel kb: {len(results)} hit(s) of {len(index.items)} indexed item(s)")
        for result in results:
            print(
                f"  {result.item.id}  [{result.item.kind}]  {result.item.title}  "
                f"(matched: {', '.join(result.matched_fields)})"
            )
    if not results:
        print(f"channel kb: no matches for {args.text or '-'} (NOT-OK)", file=sys.stderr)
        return EXIT_NOT_OK
    return EXIT_OK


def cmd_steer(args: argparse.Namespace) -> int:
    """Brain side: queue a mid-run steering hint for one in-flight directive (#367).

    The sister loop drains the queue every cycle and delivers the hint to the
    live run (its stdin and its log stream) without killing or re-dispatching
    anything. One pending steer per directive: a newer hint replaces an
    undelivered older one, and every steer is audited in the slog.
    """
    if args.message is not None:
        message = load_message(args.message)
        message.setdefault("from", "brain")
        message.setdefault("to", "sister")
        message.setdefault("type", "steer")
    else:
        message = {
            "from": "brain",
            "to": "sister",
            "type": "steer",
            "correlation_id": args.directive,
            "body": args.body,
        }
    problems = validate(message)
    if problems:
        print(f"channel steer: REFUSED ({len(problems)} violation(s))", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return EXIT_NOT_OK
    message["id"] = str(uuid.uuid4())
    message["ts"] = now_iso()
    message["nonce"] = str(uuid.uuid4())
    target = str(message["correlation_id"])
    STEERS.mkdir(parents=True, exist_ok=True)
    (STEERS / f"{target}.json").write_text(json.dumps(message, indent=2) + "\n", encoding="utf-8")
    _slog(message)
    print(f"channel steer: OK — steering hint queued for run {target}")
    return EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    message = load_message(args.message)
    problems = validate(message)
    if problems:
        print(f"channel verify: REFUSED ({len(problems)} violation(s))", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return EXIT_NOT_OK
    print("channel verify: OK")
    return EXIT_OK


def replay_conflict(message: dict, directories: tuple[Path, ...] | None = None) -> str | None:
    """Reason this message replays an earlier delivery, or None when it is fresh.

    A directive is identified by its ``id`` and carries a ``nonce`` as its
    anti-replay token (contract §3). Either one recurring in the mailboxes this
    call path owns means the same order is being pushed twice — which the contract
    refuses rather than silently overwriting the queued copy.

    The mailbox set is a parameter, not a constant, because the two paths write to
    *different* mailboxes: ``send`` (brain → sister) writes ``SENT``/``INBOX`` and
    ``order`` (operator → brain) writes ``BRAIN_SENT``/``BRAIN_INBOX``. Scanning
    the sister's mailboxes from ``order`` made that guard unreachable — measured,
    an identical operator order was accepted twice (#278). ``None`` keeps the
    default a run-time lookup of the sister's mailboxes.
    """
    if not message.get("id") and not message.get("nonce"):
        return None
    for directory in (SENT, INBOX, DONE) if directories is None else directories:
        if not directory.exists():
            continue
        for path in directory.glob("*.json"):
            try:
                prior = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if message.get("id") and prior.get("id") == message["id"]:
                return f"id {message['id']} was already sent"
            if message.get("nonce") and prior.get("nonce") == message["nonce"]:
                return f"nonce {message['nonce']} was already used"
    return None


def _slog(message: dict) -> None:
    """Append one structured line to .fleet/slog.jsonl — the audit + live tail."""
    task = message.get("task") or {}
    entry = {
        "ts": message.get("ts") or now_iso(),
        "id": message.get("id", ""),
        "from": message.get("from", ""),
        "to": message.get("to", ""),
        "type": message.get("type", ""),
        "correlation_id": message.get("correlation_id", ""),
        "issue": message.get("issue") or task.get("issue"),
        "severity": message.get("severity", ""),
        "body": (message.get("body") or "")[:200],
    }
    SLOG.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(SLOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, (json.dumps(entry) + "\n").encode("utf-8"))
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def cmd_send(args: argparse.Namespace) -> int:
    message = load_message(args.message)
    problems = validate(message)
    if problems:
        print(f"channel send: REFUSED ({len(problems)} violation(s))", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return EXIT_NOT_OK
    if not message.get("id"):
        message["id"] = str(uuid.uuid4())
    if not message.get("ts"):
        message["ts"] = now_iso()
    if not message.get("nonce"):
        message["nonce"] = str(uuid.uuid4())
    conflict = replay_conflict(message, (SENT, INBOX, DONE))
    if conflict:
        print(f"channel send: REFUSED — replay detected ({conflict})", file=sys.stderr)
        return EXIT_NOT_OK
    message_id = message["id"]
    SENT.mkdir(parents=True, exist_ok=True)
    INBOX.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(message, indent=2) + "\n"
    for directory in (SENT, INBOX):
        (directory / f"{message_id}.json").write_text(payload, encoding="utf-8")
    _slog(message)
    print(f"channel send: OK — {message_id} queued for the sister")
    return EXIT_OK


def head_commit() -> str:
    """The current HEAD sha — what a freshly started loop would be running."""
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def read_heartbeat() -> dict | None:
    try:
        return json.loads(HEARTBEAT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def heartbeat_age_seconds(beat: dict, moment: float | None = None) -> float | None:
    stamp = beat.get("ts")
    if not stamp:
        return None
    try:
        seen = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    reference = datetime.fromtimestamp(moment, tz=timezone.utc) if moment is not None else datetime.now(timezone.utc)
    return (reference - seen).total_seconds()


def running_loop_pids() -> list[int]:
    """PIDs of live `fleet/terminal.py` loops, to disambiguate NO-HEARTBEAT."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "fleet/terminal.py"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(line) for line in result.stdout.split() if line.strip().isdigit()]


def report_rung(name: str, heartbeat_path: Path, process: str, start_cmd: str) -> bool:
    """Report one rung's liveness, code drift and capability set; True when it is live and current.

    Shared by the brain and the sister so neither can be silently absent from
    `status`, and so a rung running merged-but-unrestarted code is reported as
    such instead of looking dead. Issue #319 adds the third question a commit
    comparison cannot answer: does the running rung implement the controls the
    repository declares? A rung that is *current* and still missing a capability
    is reported by name, because a restart cannot fix it.
    """
    try:
        beat = json.loads(heartbeat_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        beat = None
    if beat is None:
        try:
            alive = subprocess.run(
                ["pgrep", "-f", process], capture_output=True, text=True, timeout=10
            ).returncode == 0
        except (OSError, subprocess.SubprocessError):
            alive = False
        if alive:
            print(
                f"{name}: NO HEARTBEAT from a loop that IS running — it is executing a build older "
                f"than the heartbeat check, so merged fixes are not live. Restart: {start_cmd}"
            )
        else:
            print(f"{name}: NO HEARTBEAT and no process — this rung is down (start: {start_cmd})")
        print(capability_line(capability_finding(name, None, head_commit())))
        return False

    age = heartbeat_age_seconds(beat)
    state = beat.get("state", "?")
    if age is None:
        print(f"{name}: heartbeat present (pid {beat.get('pid', '?')}, state {state}) but undated")
        return False
    verdict = "live" if age <= STALE_HEARTBEAT_SECONDS else f"STALE ({int(age)}s since last beat)"
    running = str(beat.get("commit", "unknown"))
    current = head_commit()
    print(f"{name}: {verdict} — pid {beat.get('pid', '?')}, state {state}, last beat {int(age)}s ago")
    print(f"{name}: running commit {running} | HEAD {current}")
    drifted = current != "unknown" and running != current
    if drifted:
        print(
            f"{name}: CODE DRIFT — it is running {running}, not HEAD {current}; merged fixes are not "
            f"live. Restart: {start_cmd}"
        )
    finding = capability_finding(name, beat, current)
    print(capability_line(finding))
    return (
        age <= STALE_HEARTBEAT_SECONDS
        and not drifted
        and finding.kind not in (KIND_CAPABILITY_STALE, KIND_UNKNOWN)
    )


def expired_directives(directory: Path, moment: float | None = None) -> list[Path]:
    """Pending directives older than the declared directive lifetime.

    A directive the sister never consumes is abandoned after
    `DIRECTIVE_LIFETIME_SECONDS` (governance/policy/lease.py). Past that age it is
    stale mail, not queued work, and `status` says so.
    """
    reference = time.time() if moment is None else moment
    stale: list[Path] = []
    if not directory.exists():
        return stale
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        stamp = payload.get("ts")
        if not isinstance(stamp, str):
            continue
        try:
            seen = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        age = (datetime.fromtimestamp(reference, tz=timezone.utc) - seen).total_seconds()
        if age > DIRECTIVE_LIFETIME_SECONDS:
            stale.append(path)
    return stale


def cmd_status(args: argparse.Namespace) -> int:
    def count(directory: Path) -> int:
        return len(list(directory.glob("*.json"))) if directory.exists() else 0

    print(
        f"inbox: {count(INBOX)} pending | sent: {count(SENT)} | outbox: {count(OUTBOX)}\n"
        f"brain: {count(BRAIN_INBOX)} order(s) pending | {count(BRAIN_DONE)} dispatched | "
        f"{count(BRAIN_OUTBOX)} reply(ies) | {len(pending_steers())} steer(s) awaiting a live run"
    )
    abandoned = expired_directives(INBOX)
    if abandoned:
        print(
            f"inbox: {len(abandoned)} directive(s) older than the declared directive lifetime "
            f"({int(DIRECTIVE_LIFETIME_SECONDS)}s) — abandoned, not queued"
        )
    guard = runaway.inventory(FLEET_DIR)
    if guard["counters"] or guard["dead_letters"]:
        print(
            f"runaway: {len(guard['counters'])} directive(s) carrying an attempt budget | "
            f"{len(guard['dead_letters'])} dead-lettered (never dispatched again; "
            "`python3 fleet/runaway.py show --directive <id>`)"
        )
    brain_ok = report_rung("brain", BRAIN_HEARTBEAT, "fleet/brain.py", "bash fleet/brain.sh")
    sister_ok = report_rung("sister", HEARTBEAT, "fleet/terminal.py", "bash fleet/terminal.sh")
    return EXIT_OK if (brain_ok and sister_ok) else EXIT_NOT_OK


def cmd_order(args: argparse.Namespace) -> int:
    """Top of the hierarchy: the operator orders the *brain*, never the sister.

    The operator trigger exists so the chain is real code — operator → brain →
    sister — rather than a convention the transport cannot enforce. `send` is
    brain→sister and refuses an operator sender, so this is the only way in.

    The anti-replay scan covers the mailboxes *this* path writes
    (``BRAIN_SENT``/``BRAIN_INBOX``/``BRAIN_DONE``): the shared default scans the
    sister's mailboxes, so the guard could never fire here and an identical order
    was accepted twice (#278).
    """
    message = load_message(args.message)
    message.setdefault("from", "operator")
    message.setdefault("to", "brain")
    message.setdefault("type", "directive")
    problems = validate(message)
    if problems:
        print(f"channel order: REFUSED ({len(problems)} violation(s))", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return EXIT_NOT_OK
    if not message.get("id"):
        message["id"] = str(uuid.uuid4())
    if not message.get("ts"):
        message["ts"] = now_iso()
    if not message.get("nonce"):
        message["nonce"] = str(uuid.uuid4())
    conflict = replay_conflict(message, (BRAIN_SENT, BRAIN_INBOX, BRAIN_DONE))
    if conflict:
        print(f"channel order: REFUSED — replay detected ({conflict})", file=sys.stderr)
        return EXIT_NOT_OK
    message_id = message["id"]
    payload = json.dumps(message, indent=2) + "\n"
    for directory in (BRAIN_SENT, BRAIN_INBOX):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{message_id}.json").write_text(payload, encoding="utf-8")
    _slog(message)
    print(f"channel order: OK — {message_id} queued for the brain")
    return EXIT_OK


def cmd_brain_inbox(args: argparse.Namespace) -> int:
    """The brain's own watch: the oldest order from the operator, if any."""
    deadline = time.monotonic() + args.timeout_seconds if args.timeout_seconds > 0 else None
    while True:
        pending = sorted(BRAIN_INBOX.glob("*.json")) if BRAIN_INBOX.exists() else []
        if pending:
            print(pending[0].read_text(encoding="utf-8"), flush=True)
            return EXIT_OK
        if deadline is not None and time.monotonic() >= deadline:
            print("channel brain-inbox: IDLE — no order", file=sys.stderr)
            return EXIT_NOT_OK
        time.sleep(args.interval)


def consume_order(message_id: str) -> bool:
    """The brain has dispatched (or refused) the order: move it to done/."""
    source = BRAIN_INBOX / f"{message_id}.json"
    if not source.exists():
        return False
    BRAIN_DONE.mkdir(parents=True, exist_ok=True)
    source.replace(BRAIN_DONE / source.name)
    return True


def brain_reply(order: dict, message_type: str, body: str) -> None:
    """Answer the operator in the brain outbox — the report the operator reads."""
    message = {
        "from": "brain",
        "to": "operator",
        "type": message_type,
        "correlation_id": str(order.get("id") or order.get("correlation_id") or ""),
        "id": str(uuid.uuid4()),
        "ts": now_iso(),
        "nonce": str(uuid.uuid4()),
        "body": body[:2000],
    }
    BRAIN_OUTBOX.mkdir(parents=True, exist_ok=True)
    (BRAIN_OUTBOX / f"{message['id']}.json").write_text(json.dumps(message, indent=2) + "\n", encoding="utf-8")
    _slog(message)


def cmd_brain_outbox(args: argparse.Namespace) -> int:
    """Operator side: read the brain's replies (acks and refusals), newest last.

    Ordered by the message timestamp, not by filename: ids are uuid4, so a
    filename sort returns the replies in arbitrary order and the operator reads
    a stale answer as if it were the current one (observed live).
    """

    def by_time(path: Path) -> float:
        try:
            message = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return 0.0
        stamp = str(message.get("ts") or "")
        try:
            seen = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return path.stat().st_mtime if path.exists() else 0.0
        return seen.timestamp()

    replies = sorted(BRAIN_OUTBOX.glob("*.json"), key=by_time) if BRAIN_OUTBOX.exists() else []
    if not replies:
        print("channel brain-outbox: no replies from the brain yet")
        return EXIT_NOT_OK
    for path in replies[-args.limit :] if args.limit else replies:
        try:
            message = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"channel brain-outbox: unreadable {path.name}: {exc}", file=sys.stderr)
            continue
        print(
            f"{message.get('ts', '-')} {message.get('type', '-')} "
            f"(order {message.get('correlation_id', '-')}): {message.get('body', '')}"
        )
    return EXIT_OK


def cmd_consume(args: argparse.Namespace) -> int:
    """Mark a directive handled without reporting a result.

    Process controls (`kill`, `halt`, `refresh`, `restart`) act on the loop
    itself, so there is no result to report — but the message must still leave the
    inbox. A control that acted and stayed pending re-fires against the next loop:
    measured, an unconsumed `kill` would have taken down every loop started after
    it.
    """
    if consume_directive(args.id):
        print(f"channel consume: OK — {args.id} handled (moved to done)")
        return EXIT_OK
    print(f"channel consume: nothing to consume for {args.id}", file=sys.stderr)
    return EXIT_NOT_OK


def cmd_head_commit(args: argparse.Namespace) -> int:
    print(head_commit())
    return EXIT_OK


def consume_directive(message_id: str) -> bool:
    """Mark a directive complete: move it out of the inbox into .fleet/done/.

    Reporting the result IS the completion, so the inbox count is always the
    number of outstanding orders — a directive that was answered is gone.

    The directive's runaway-guard counter is dropped with it (issue #723): the
    order is finished, so its budget is history. The TERMINAL artifact of a
    retired directive is deliberately NOT dropped — a dead letter is evidence,
    and the guard keeps it until an operator re-arms the directive by name.
    """
    source = INBOX / f"{message_id}.json"
    if not source.exists():
        return False
    DONE.mkdir(parents=True, exist_ok=True)
    source.replace(DONE / source.name)
    runaway.forget(message_id, base=INBOX.parent)
    return True


def cmd_report(args: argparse.Namespace) -> int:
    """Executor side: write an ack/result answering a directive into the outbox."""
    message = {
        "from": args.from_role,
        "to": "brain",
        "type": args.type,
        "correlation_id": args.correlation,
    }
    if args.body:
        message["body"] = args.body
    problems = validate(message)
    if problems:
        print(f"channel report: REFUSED ({len(problems)} violation(s))", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return EXIT_NOT_OK
    message["id"] = str(uuid.uuid4())
    message["ts"] = now_iso()
    OUTBOX.mkdir(parents=True, exist_ok=True)
    (OUTBOX / f"{message['id']}.json").write_text(json.dumps(message, indent=2) + "\n", encoding="utf-8")
    consumed = consume_directive(args.correlation)
    suffix = " (directive consumed)" if consumed else ""
    _slog(message)
    print(f"channel report: OK — {message['id']} answers {args.correlation}{suffix}")
    return EXIT_OK


def cmd_escalate(args: argparse.Namespace) -> int:
    """Sister/subagent side: raise a problem to the brain for elite steering."""
    message = {
        "from": args.from_role,
        "to": "brain",
        "type": "escalate",
        "correlation_id": args.correlation,
        "severity": args.severity,
    }
    if args.body:
        message["body"] = args.body
    problems = validate(message)
    if problems:
        print(f"channel escalate: REFUSED ({len(problems)} violation(s))", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return EXIT_NOT_OK
    message["id"] = str(uuid.uuid4())
    message["ts"] = now_iso()
    OUTBOX.mkdir(parents=True, exist_ok=True)
    (OUTBOX / f"{message['id']}.json").write_text(json.dumps(message, indent=2) + "\n", encoding="utf-8")
    _slog(message)
    print(f"channel escalate: OK — {message['id']} escalated to the brain ({args.severity})")
    return EXIT_OK


def cmd_listen(args: argparse.Namespace) -> int:
    """Brain side: tail the slog stream, printing each message as it flows through.

    This is the idle-watch: run with `--timeout-seconds 0` and the terminal
    blocks, printing every directive/ack/result/escalate the moment it lands —
    an escalation from the sister pings the brain here. `--max-messages` bounds
    it for tests.

    Issue #367 extends the verb additively: `listen --directive <id>` switches
    to the per-directive live log view (`follow`), the `follow`/`listen
    --directive` pair the issue names for tailing one run's stream. The
    argumentless form keeps its old meaning.
    """
    if getattr(args, "directive", None):
        follow_args = argparse.Namespace(
            directive=args.directive,
            timeout_seconds=args.timeout_seconds,
            interval=args.interval,
            max_lines=getattr(args, "max_lines", 0),
            from_start=getattr(args, "from_start", False),
        )
        return cmd_follow(follow_args)
    deadline = time.monotonic() + args.timeout_seconds if args.timeout_seconds > 0 else None
    seen = 0
    offset = 0 if getattr(args, "from_start", False) else (SLOG.stat().st_size if SLOG.exists() else 0)
    while True:
        if SLOG.exists():
            with open(SLOG, encoding="utf-8") as handle:
                handle.seek(offset)
                lines = handle.read().splitlines()
                offset = handle.tell()
            for line in lines:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    print(f"channel listen: {line}", flush=True)
                else:
                    print(
                        f"channel listen: {entry.get('ts', '-')} {entry.get('type', '-')} "
                        f"from {entry.get('from', '-')} -> {entry.get('to', '-')} "
                        f"(issue {entry.get('issue') or (entry.get('task') or {}).get('issue') or '-'}, "
                        f"severity {entry.get('severity') or '-'})",
                        flush=True,
                    )
                    print(json.dumps(entry, indent=2), flush=True)
                seen += 1
                if args.max_messages and seen >= args.max_messages:
                    return EXIT_OK
        if deadline is not None and time.monotonic() >= deadline:
            print(f"channel listen: IDLE — {seen} message(s) in window", file=sys.stderr)
            return EXIT_OK
        nap = args.interval
        if deadline is not None:
            nap = min(nap, max(0.0, deadline - time.monotonic()))
        time.sleep(nap)


def cmd_wait(args: argparse.Namespace) -> int:
    """Brain side: block until a result for this id (or correlation) lands in the outbox.

    This is the completion trigger of the operating model: push a directive with
    ``send``, then ``wait`` until the executor answers. A timeout is NOT-OK (1),
    never a silent pass.
    """
    target = args.id
    deadline = time.monotonic() + args.timeout_seconds if args.timeout_seconds > 0 else None
    OUTBOX.mkdir(parents=True, exist_ok=True)
    while True:
        for path in sorted(OUTBOX.glob("*.json")):
            try:
                message = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if message.get("id") == target or message.get("correlation_id") == target:
                print(json.dumps(message, indent=2))
                print(f"channel wait: TRIGGERED — {target} answered by {message.get('from')}")
                return EXIT_OK
        if deadline is not None and time.monotonic() >= deadline:
            print(f"channel wait: TIMEOUT — no result for {target} after {args.timeout_seconds}s", file=sys.stderr)
            return EXIT_NOT_OK
        nap = args.interval
        if deadline is not None:
            nap = min(nap, max(0.0, deadline - time.monotonic()))
        time.sleep(nap)


def ordered_by_time(directory: Path) -> list[Path]:
    """Messages in the order they were sent, not in uuid order.

    Ids are uuid4, so a filename sort is arbitrary: measured, the sister took a
    `resume` before the `pause` it was meant to lift and the fetched order changed
    run to run. The envelope's `ts` is the only ordering the transport has.
    """

    def sent_at(path: Path) -> tuple[str, str]:
        try:
            message = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return ("", path.name)
        return (str(message.get("ts") or ""), path.name)

    return sorted(directory.glob("*.json"), key=sent_at)


def cmd_watch(args: argparse.Namespace) -> int:
    """Sister side listener: return the oldest pending directive, or block for one.

    This is what makes the sister a dumb terminal with a pulse: it runs
    ``watch`` in a loop, executes the directive it prints, reports the result
    (which consumes the directive), then runs ``watch`` again. Exit 0 = a
    directive was returned; 1 = IDLE (nothing arrived before the timeout);
    2 = CANNOT-ASSESS (the inbox is unusable).

    ``--skip ID`` (repeatable) removes directives the caller has already
    dispatched but not yet consumed, so a pool of N concurrent workers can keep
    draining the inbox past the directives still in flight instead of re-reading
    the oldest one forever.

    The runaway guard (issue #723) filters on the same seam: a directive whose
    order was RETIRED to ``<fleet>/dead-letter/`` is never returned again, and
    one whose exponential backoff has not elapsed is held — still in the inbox,
    still the operator's record that the work was ordered, but not dispatched
    early. That is what stops a refused or crashed order from being re-read every
    cycle, without the loop sleeping on it.
    """
    INBOX.mkdir(parents=True, exist_ok=True)
    skip = set(getattr(args, "skip", None) or [])
    guard_base = INBOX.parent
    deadline = time.monotonic() + args.timeout_seconds if args.timeout_seconds > 0 else None
    while True:
        held: list[str] = []
        pending = []
        for path in ordered_by_time(INBOX):
            if path.stem in skip:
                continue
            if not runaway.dispatchable(path.stem, base=guard_base):
                held.append(path.stem)
                continue
            pending.append(path)
        if pending:
            try:
                message = json.loads(pending[0].read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                print(f"channel watch: CANNOT-ASSESS — {pending[0]} is unreadable ({exc})", file=sys.stderr)
                return EXIT_CANNOT_ASSESS
            print(json.dumps(message, indent=2))
            print(f"channel watch: DIRECTIVE {message.get('id')} — {len(pending)} pending")
            return EXIT_OK
        if deadline is not None and time.monotonic() >= deadline:
            held_note = f" — {len(held)} held by the runaway guard (backoff or dead-letter)" if held else ""
            print(f"channel watch: IDLE — no directive within {args.timeout_seconds}s{held_note}", file=sys.stderr)
            return EXIT_NOT_OK
        nap = args.interval
        if deadline is not None:
            nap = min(nap, max(0.0, deadline - time.monotonic()))
        time.sleep(nap)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleet-channel", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    verify = sub.add_parser("verify", help="validate a message against the contract")
    verify.add_argument("--message", required=True)
    verify.set_defaults(func=cmd_verify)

    send = sub.add_parser("send", help="validate and queue a message to the sister inbox")
    send.add_argument("--message", required=True)
    send.set_defaults(func=cmd_send)

    status = sub.add_parser("status", help="mailbox counts")
    status.set_defaults(func=cmd_status)

    report = sub.add_parser("report", help="write an ack/result answering a directive (executor side)")
    report.add_argument("--from", dest="from_role", required=True)
    report.add_argument("--type", choices=("ack", "result"), required=True)
    report.add_argument("--correlation", required=True)
    report.add_argument("--body", default=None)
    report.set_defaults(func=cmd_report)

    wait = sub.add_parser("wait", help="block until the outbox answers this id/correlation (brain side)")
    wait.add_argument("--id", required=True)
    wait.add_argument("--timeout-seconds", type=float, default=300.0)
    wait.add_argument("--interval", type=float, default=0.5)
    wait.set_defaults(func=cmd_wait)

    watch = sub.add_parser("watch", help="return the next pending directive, or block for one (sister side)")
    watch.add_argument("--timeout-seconds", type=float, default=600.0)
    watch.add_argument("--interval", type=float, default=1.0)
    watch.add_argument("--skip", action="append", default=[], help="directive ids to skip (already dispatched)")
    watch.set_defaults(func=cmd_watch)

    escalate = sub.add_parser("escalate", help="raise a problem to the brain (sister/subagent side)")
    escalate.add_argument("--from", dest="from_role", required=True)
    escalate.add_argument("--correlation", required=True)
    escalate.add_argument("--severity", choices=SEVERITIES, required=True)
    escalate.add_argument("--body", default=None)
    escalate.set_defaults(func=cmd_escalate)

    listen = sub.add_parser("listen", help="tail the slog stream (brain side, idle-watch); --directive tails one run's live log instead (issue #367)")
    listen.add_argument("--timeout-seconds", type=float, default=0.0)
    listen.add_argument("--interval", type=float, default=1.0)
    listen.add_argument("--max-messages", type=int, default=0)
    listen.add_argument("--from-start", action="store_true", help="replay the whole slog instead of tailing from now")
    listen.add_argument("--directive", default=None, help="tail this directive's live log stream instead of the slog (the follow view, issue #367)")
    listen.add_argument("--max-lines", type=int, default=0)
    listen.set_defaults(func=cmd_listen)

    order = sub.add_parser("order", help="operator side: order the BRAIN (never the sister)")
    order.add_argument("--message", required=True)
    order.set_defaults(func=cmd_order)

    brain_inbox = sub.add_parser("brain-inbox", help="brain side: the oldest operator order, or block for one")
    brain_inbox.add_argument("--timeout-seconds", type=float, default=0.0)
    brain_inbox.add_argument("--interval", type=float, default=1.0)
    brain_inbox.set_defaults(func=cmd_brain_inbox)

    brain_outbox = sub.add_parser("brain-outbox", help="operator side: the brain's replies")
    brain_outbox.add_argument("--limit", type=int, default=10)
    brain_outbox.set_defaults(func=cmd_brain_outbox)

    head = sub.add_parser("head-commit", help="print the commit a freshly started loop would run")
    head.set_defaults(func=cmd_head_commit)

    consume = sub.add_parser("consume", help="mark a directive handled without a result (process controls)")
    consume.add_argument("--id", required=True)
    consume.set_defaults(func=cmd_consume)

    log = sub.add_parser("log", help="append one event to a directive's live log stream (issue #367)")
    log.add_argument("--directive", required=True)
    log.add_argument("--line", required=True)
    log.add_argument("--source", default=None, help="who wrote the line (sister/subagent/steer/brain)")
    log.set_defaults(func=cmd_log)

    follow = sub.add_parser("follow", help="tail a directive's live log stream (issue #367)")
    follow.add_argument("--directive", required=True)
    follow.add_argument("--timeout-seconds", type=float, default=0.0)
    follow.add_argument("--interval", type=float, default=0.5)
    follow.add_argument("--max-lines", type=int, default=0)
    follow.add_argument("--from-start", action="store_true", help="replay the whole stream instead of tailing from now")
    follow.set_defaults(func=cmd_follow)

    kb = sub.add_parser("kb", help="query the institutional KB (governance/knowledge/, issue #367)")
    kb.add_argument("--text", default=None)
    kb.add_argument("--kind", default=None)
    kb.add_argument("--owner", default=None)
    kb.add_argument("--tag", default=None)
    kb.add_argument("--limit", type=int, default=None)
    kb.add_argument("--json", action="store_true", help="print the source-backed summary as JSON")
    kb.set_defaults(func=cmd_kb)

    steer = sub.add_parser("steer", help="queue a mid-run steering hint for an in-flight directive (brain side, issue #367)")
    steer.add_argument("--directive", default=None, help="the in-flight directive's id (the steer's correlation_id)")
    steer.add_argument("--body", default=None, help="the steering hint")
    steer.add_argument("--message", default=None, help="a full JSON steer message (takes precedence over --directive/--body)")
    steer.set_defaults(func=cmd_steer)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
