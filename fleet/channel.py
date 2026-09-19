#!/usr/bin/env python3
"""Steering channel CLI — the transport between the director and the fleet.

Operating model (M26, issue #160): the **director** (advisor session) issues
directives; the **dispatcher** session (a dumb terminal on DeepSeek v4.1 Flash, no
thinking) executes them by spawning epic-focused executors; executors report
results back through the dispatcher. This CLI is the file-mailbox transport for
that loop — localhost mechanics (GR-21), no network, no daemons.

Mailbox layout (runtime state, gitignored):

    .fleet/inbox/    messages for the dispatcher to drain (written by director send)
    .fleet/sent/     the director's own copy of everything it sent
    .fleet/outbox/   acks and results written back for the director
    .fleet/done/     directives answered and consumed

Messages are validated against `fleet/schema/message.schema.json` semantics
before they move. The topology, the directive vocabulary and the trust rules the
validator enforces are the normative contract in `fleet/CONTRACT.md`, and the
transport itself is decided by ADR-0011 (docs/decision-records/) — this module is
the machine that runs that contract. Issue #367 extends the machine additively
with three live capabilities over the same mailbox: per-directive live log
streams (`log` / `follow`), KB access (`kb`, against governance/knowledge/), and
mid-run steering (`steer`, drained by the dispatcher loop each cycle). Exit codes
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
import runtimes

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance.policy import lease  # noqa: E402

# The stale-snapshot trigger's deferred queue (issue #727). `watch` holds a
# PARKED directive exactly as it already holds a backoff or a dead letter, and
# releases it with no principal action the moment the board is fresh again.
#
# The module is resolved LAZILY, through a function rather than a module-level
# `import`: the trigger is one verb on a large CLI, and a hard import would make
# this file unloadable wherever `governance/dispatch/` is not a sibling — the
# gate scripts copy `fleet/` alone into a scratch tree to provoke a mutation, and
# a module-level import turned that copy into a crash instead of a test (the
# sibling provocation reported `failed, but not for the reason ... targets`).
_BOARD_SNAPSHOT = None


def _dispatch_root() -> Path | None:
    """The tree holding ``governance/dispatch`` — searched upward from ``__file__``.

    A scratch copy of ``fleet/`` alone (``cp -R fleet <scratch>``, how the gate
    scripts stage a mutation) has no ``governance/`` child, but its parent chain
    does reach the checkout the copy was taken from; walking up finds it. When
    nothing on the chain has the dispatch tree the trigger is simply not
    deployed here and callers degrade — the module must never fail to import for
    a feature one verb uses.
    """
    for base in Path(__file__).resolve().parents:
        if (base / "governance" / "dispatch" / "snapshot.py").is_file():
            return base / "governance" / "dispatch"
    return None


def board_snapshot():
    """The board trigger module, or ``None`` when this checkout does not ship it."""
    global _BOARD_SNAPSHOT
    if _BOARD_SNAPSHOT is None:
        directory = _dispatch_root()
        if directory is None:
            return None
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
        import snapshot as module  # noqa: PLC0415 - resolved on first use, by design

        _BOARD_SNAPSHOT = module
    return _BOARD_SNAPSHOT


#: The board a stale snapshot is refreshed from. A snapshot of the default is
#: read from the module lazily; the fallback keeps this constant usable when the
#: dispatch tree is absent (the constant is data, not a code path).
BOARD_REPO = "kushin77/agent-orchestrator"

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
# that owns the run and tailed by the `follow` verb. ``STEERS`` is the director's
# outbound steering queue (`.fleet/brain/steer/<directive>.json`): one pending
# steer per in-flight directive, drained by the dispatcher loop every cycle.
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
    # Dead-lettering by protocol (issue #754). `drop` retires a NAMED directive
    # to the terminal mailbox through the same implementation the automatic
    # budget-exhaustion path uses; `dead-letter` reads the mailbox back. They
    # exist because the alternative — a principal or peer `mv`-ing a file out of
    # `.fleet/inbox/` while the loop reads it — races the reader, loses the
    # attempt history, bypasses the channel and cannot be done by an agent at all.
    "drop",
    "dead-letter",
)
# Controls that act on the loop process itself rather than on the work queue.
PROCESS_CONTROLS = ("refresh", "restart", "stop", "kill", "halt")
# Control actions whose whole point is to re-dispatch a named issue.
TASK_CONTROLS = ("override",)
# Control actions that must name the directive they act on (not their own id).
DIRECTIVE_CONTROLS = ("drop",)
MODEL_TIERS = ("flash", "pro", "auditor")
THINKING_LEVELS = ("none", "low", "medium", "high")
# Order kinds (schema v1, additive): `work` needs an issue; the others are
# answered by the director without dispatching anything to the dispatcher.
TASK_KINDS = ("work", "status", "report", "ping", "steer")
NON_WORK_KINDS = ("status", "report", "ping", "steer")

# ── per-runtime allowlists for verbs, skills and secrets (issue #1273, parent
# #1268) ─────────────────────────────────────────────────────────────────────
# The closed runtime vocabulary, READ from its one authority (#1412): the contract
# `fleet/runtimes.yaml`, through `fleet/runtimes.py`. It used to be a literal
# seven-tuple here — "the wire-value contract `message.schema.json::runtime` also
# names" — which is exactly how a second list is born: the notices rule (#1385)
# carried five ids against this contract's seven, and every lane record validated
# against whichever list its caller happened to reach. A literal that agrees today
# is not one list; the contract is, and `fleet/tests/test_runtime_vocabulary.py`
# proves every consumer FOLLOWS it.
#
# Read LAZILY and cached, not at import: this module is copied into scratch trees
# by gate fixtures that provoke a mutation of the levers
# (`scripts/check-control-verbs.sh`), and an import-time read of a contract those
# trees do not carry would make `channel` unloadable there. A registry that cannot
# be read is REFUSED, never treated as an empty vocabulary (an empty list would
# make every runtime-bearing message valid or invalid by accident).
_RUNTIME_IDS: tuple[str, ...] | None = None


def runtime_ids() -> tuple[str, ...]:
    """The registered runtime ids (the wire values `runtime`/`on_behalf_of` carry)."""
    global _RUNTIME_IDS
    if _RUNTIME_IDS is None:
        _RUNTIME_IDS = runtimes.ids(ROOT)
    return _RUNTIME_IDS


#: `RUNTIME_IDS` is the name #1273 declared for this vocabulary, and it is still
#: the name `governance/spawn/admission.py::_allowlists` reads (#1413/#1421: the
#: allowlist tables must come from THIS module's one closed function set, so the
#: consumer names the reader here rather than re-deriving an id list of its own).
#: The name therefore has to exist — and it has to be the SAME list, not a second
#: copy: a resolved-to-equal twin would satisfy #1421 and still be the very thing
#: this issue exists to remove (#1385's five ids against the contract's seven).
#: `__getattr__` (PEP 562) resolves the name through `runtime_ids()`, so a reader
#: of `channel.RUNTIME_IDS` gets the one list's object, while the read stays lazy
#: for the scratch-tree copies described above. Any OTHER unknown name is still an
#: `AttributeError` — a resolver that answered everything would turn a typo into a
#: silent empty vocabulary, which is the failure mode this section refuses.
#: `fleet/tests/test_runtime_vocabulary.py` carries this name as a consumer, so it
#: is proved to FOLLOW the contract by mutation like every other reader.
def __getattr__(name: str) -> object:
    if name == "RUNTIME_IDS":
        return runtime_ids()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_VERBS_YAML = ROOT / "control-plane" / "control" / "verbs.yaml"
_SKILLS_REGISTRY = ROOT / "integrations" / "paperclip" / "adapters" / "skills" / "registry.json"
_SECRETS_CATALOG = ROOT / "integrations" / "paperclip" / "adapters" / "secrets" / "catalog" / "secrets.json"

_VERB_ALLOWLIST: dict[str, tuple[str, ...]] | None = None
_SKILL_ALLOWLIST: dict[str, tuple[str, ...]] | None = None
_SECRET_ALLOWLIST: dict[str, tuple[str, ...]] | None = None


def _verb_allowlist() -> dict[str, tuple[str, ...]]:
    """``{verb id: allowed_runtimes}`` from control-plane/control/verbs.yaml.

    Resolved lazily and cached, mirroring ``board_snapshot()`` above: this
    module must still import when ``control-plane/`` is not a sibling (a
    scratch copy of ``fleet/`` alone), and a verb absent from the registry (or
    the registry itself absent) is simply not restricted — the allowlist is an
    ADDITIVE narrowing on top of ``capability``, never the only gate.
    """
    global _VERB_ALLOWLIST
    if _VERB_ALLOWLIST is None:
        allowlist: dict[str, tuple[str, ...]] = {}
        if _VERBS_YAML.is_file():
            try:
                import yaml  # noqa: PLC0415 - optional dependency, resolved on demand

                doc = yaml.safe_load(_VERBS_YAML.read_text(encoding="utf-8")) or {}
                for entry in doc.get("verbs") or []:
                    vid = entry.get("id")
                    runtimes = entry.get("allowed_runtimes")
                    if isinstance(vid, str) and isinstance(runtimes, list):
                        allowlist[vid] = tuple(runtimes)
            except Exception:  # noqa: BLE001 - degrade to unrestricted, never crash
                allowlist = {}
        _VERB_ALLOWLIST = allowlist
    return _VERB_ALLOWLIST


def _skill_allowlist() -> dict[str, tuple[str, ...]]:
    """``{skill id: allowed_runtimes}`` from the paperclip skills registry."""
    global _SKILL_ALLOWLIST
    if _SKILL_ALLOWLIST is None:
        allowlist: dict[str, tuple[str, ...]] = {}
        if _SKILLS_REGISTRY.is_file():
            try:
                doc = json.loads(_SKILLS_REGISTRY.read_text(encoding="utf-8"))
                for entry in doc.get("declarations") or []:
                    sid = entry.get("id")
                    runtimes = entry.get("allowed_runtimes")
                    if isinstance(sid, str) and isinstance(runtimes, list):
                        allowlist[sid] = tuple(runtimes)
            except Exception:  # noqa: BLE001
                allowlist = {}
        _SKILL_ALLOWLIST = allowlist
    return _SKILL_ALLOWLIST


def _secret_allowlist() -> dict[str, tuple[str, ...]]:
    """``{gsm_path: allowed_runtimes}`` from the paperclip secrets catalog."""
    global _SECRET_ALLOWLIST
    if _SECRET_ALLOWLIST is None:
        allowlist: dict[str, tuple[str, ...]] = {}
        if _SECRETS_CATALOG.is_file():
            try:
                doc = json.loads(_SECRETS_CATALOG.read_text(encoding="utf-8"))
                for entry in doc.get("secrets") or []:
                    path = entry.get("gsm_path")
                    runtimes = entry.get("allowed_runtimes")
                    if isinstance(path, str) and isinstance(runtimes, list):
                        allowlist[path] = tuple(runtimes)
            except Exception:  # noqa: BLE001
                allowlist = {}
        _SECRET_ALLOWLIST = allowlist
    return _SECRET_ALLOWLIST


def reset_runtime_allowlist_cache() -> None:
    """Test/self-test hook: force the three allowlists above to reload."""
    global _VERB_ALLOWLIST, _SKILL_ALLOWLIST, _SECRET_ALLOWLIST
    _VERB_ALLOWLIST = _SKILL_ALLOWLIST = _SECRET_ALLOWLIST = None

# ── the declared role vocabulary (issue #777) ───────────────────────────────
# The role names are WIRE VALUES, not comments: the envelope carries them, this
# module refuses any sender or recipient outside the closed set, and
# `fleet/terminal.py` / `fleet/brain.py` branch on them. Renaming them is
# therefore a protocol migration rather than a find-and-replace — and it cannot
# be done by restarting the fleet, because a live loop is reading these names.
#
# So the migration is versioned, and the two dialects are named here:
#
#   schema 1 (retired): principal · director · dispatcher · executor[-<name>]
#   schema 2 (current): principal · director · dispatcher · executor[-<name>]
#
# The SINGLE AUTHORITY for this vocabulary is `governance/vocabulary/fleet.yaml`
# (the glossary). The constants below are the code's copy of it, and
# `scripts/check-fleet-vocabulary.sh` — wired into `make verify` — fails when the
# two stop agreeing, so a lane cannot mint a third name by editing one side. This
# is the same declared-policy-plus-code-plus-agreeing-gate pattern
# `scripts/check-finops-chooser.sh` already uses for the FinOps vocabulary (#164).
SCHEMA_VERSION_LEGACY = 1
SCHEMA_VERSION_CURRENT = 2
SCHEMA_VERSIONS = (SCHEMA_VERSION_LEGACY, SCHEMA_VERSION_CURRENT)

#: Current (schema 2) role names — the function in the chain, not the metaphor.
ROLES_CURRENT = ("principal", "director", "dispatcher", "executor")
#: Retired (schema 1) role names. Still ACCEPTED on read (dual-accept) and still
#: emitted to a recipient that has not declared schema 2, for the deprecation
#: window declared in the glossary.
ROLES_LEGACY = ("operator", "brain", "sister", "subagent")
#: Retired name -> current name. The one mapping that makes the rename total.
ROLE_RENAME = {
    "operator": "principal",
    "brain": "director",
    "sister": "dispatcher",
    "subagent": "executor",
}
#: Roles whose name may carry an instance suffix (`executor-<name>`; schema 1
#: spelled the same seat `subagent-<name>`). The suffix names an instance, not a
#: role, so it is preserved across the rename.
INSTANCED_ROLES = ("subagent", "executor")

#: The named roles the trust rules are stated on, so the rules are readable and
#: nothing compares a bare literal.
ROLE_PRINCIPAL, ROLE_DIRECTOR, ROLE_DISPATCHER, ROLE_EXECUTOR = ROLES_CURRENT


def _role_pattern(names: tuple[str, ...]) -> str:
    """A single-role-alternation regex body for ``names`` (instance-aware).

    Built from the declared names rather than hand-written, so the regex cannot
    drift from the vocabulary it enforces.
    """
    parts = [
        f"{name}(-[a-z0-9]+)?" if name in INSTANCED_ROLES else name for name in names
    ]
    return "(" + "|".join(parts) + ")"


#: Every role name this channel accepts on READ — both dialects (dual-accept).
_ROLE_RE = re.compile("^" + _role_pattern(ROLES_CURRENT + ROLES_LEGACY) + "$")
#: A retired (schema 1) role name — refused in a schema-2 envelope, by name.
_RETIRED_ROLE_RE = re.compile("^" + _role_pattern(ROLES_LEGACY) + "$")
#: A current (schema 2) role name.
_CURRENT_ROLE_RE = re.compile("^" + _role_pattern(ROLES_CURRENT) + "$")

#: The env seam that forces an emitting dialect. `auto` (the default) negotiates
#: from the recipient rung's live heartbeat; `1`/`2` pin it, which is what a gate
#: or a principal uses to prove a single dialect end to end.
ENVELOPE_SCHEMA_ENV = "AO_FLEET_ENVELOPE_SCHEMA"
ENVELOPE_SCHEMA_AUTO = "auto"

#: The heartbeat key by which a rung declares it speaks schema 2. A rung whose
#: beat predates this field declares nothing, which is the honest reading of "an
#: older build": it is the RUNNING loop's shape while this lane lands.
ENVELOPE_SCHEMA_BEAT_KEY = "envelope_schema"


def role_base(role: str) -> str:
    """The role name without its instance suffix (``executor-x`` -> ``executor``)."""
    return role.split("-", 1)[0]


def current_role(role: str) -> str:
    """The schema-2 spelling of ``role``, preserving any instance suffix.

    Idempotent for a name already in the current dialect, so callers can
    normalise unconditionally before applying a trust rule.
    """
    base = role_base(role)
    mapped = ROLE_RENAME.get(base, base)
    suffix = role[len(base) :]
    return mapped + suffix


def is_retired_role(role: str) -> bool:
    """True when ``role`` is a schema-1 name (including an instanced one)."""
    return bool(_RETIRED_ROLE_RE.match(role))


def is_current_role(role: str) -> bool:
    """True when ``role`` is a schema-2 name (including an instanced one)."""
    return bool(_CURRENT_ROLE_RE.match(role))

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2

# A beat older than this means the loop died rather than that it is busy: the
# loop beats every poll cycle (default 30s) and before each directive. The value
# is declared once in governance/policy/lease.py with its ordering invariants.
STALE_HEARTBEAT_SECONDS = lease.RUNG_HEARTBEAT_SECONDS

# A directive the dispatcher never drains is abandoned after this long; `status`
# reports the abandoned ones rather than counting them as queued.
DIRECTIVE_LIFETIME_SECONDS = lease.DIRECTIVE_LIFETIME_SECONDS


# ── the declared capability set (issue #319) ────────────────────────────────
# A control that ships but is not live is a silently absent control: after
# isolation (#263), lifecycle (#269) and reconciliation (#304) merged, the
# running dispatcher kept executing pre-merge code and the only signal was
# `watchdog decide() == "drifted"` — which compares *commits*, is reported per
# rung, and never says WHICH capability is missing. A principal cannot tell
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
# issue's acceptance criterion). A case label is part of the principal contract:
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
    """One principal line per rung: the case, every missing capability, the fix."""
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


# ── the emitting dialect: negotiated, never assumed (issue #777) ─────────────
# An emitter emits ONE dialect per envelope. Which one is a property of the
# RECIPIENT, not of the emitter: a rung that is still running an older build can
# only read the names it was built with, and a rename that ignored that would
# break the fleet the moment it landed — the running loop reads these names.
#
# The recipient declares what it speaks in its own heartbeat, so the emitter has
# an observable fact to decide on rather than a guess. When a rung restarts on
# this build its beat starts declaring `envelope_schema: 2`, and the emitter
# switches for it automatically: the deprecation window is closed by the
# respawn, per rung, with nothing to remember and no date to pin.

def recipient_beat(recipient: str) -> Path | None:
    """The heartbeat that declares what ``recipient`` can read, or ``None``.

    A FUNCTION over this module's own constants, never a dict frozen at import:
    ``HEARTBEAT`` and ``BRAIN_HEARTBEAT`` are redirection seams — a second fleet
    re-bases them through ``fleet/runtime.py``, and the test corpus monkeypatches
    them — so a mapping captured at import time would keep reading the
    PRE-redirect path and decide the dialect from a file the caller never wrote.
    The whole negotiation would then be a no-op that silently always downgrades.
    """
    return {
        ROLE_DIRECTOR: BRAIN_HEARTBEAT,
        ROLE_DISPATCHER: HEARTBEAT,
    }.get(role_base(current_role(recipient)))


def declared_envelope_schema(recipient: str) -> int | None:
    """The schema the recipient rung declares it speaks, or None when unknown.

    None means "not declared" — an absent beat, an unreadable beat, or a beat
    written by a build that predates the field. All three are the same fact for
    this decision, and all three must be read as "cannot confirm schema 2", never
    as "assume current": a downgrade to the retired dialect is always readable by
    both sides, whereas the reverse is not.
    """
    # The recipient is normalised first: an emitter that names its recipient in
    # the retired dialect must still find that recipient's beat, or the
    # negotiation would be skipped exactly where it matters most.
    path = recipient_beat(recipient)
    if path is None:
        return None
    try:
        beat = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = beat.get(ENVELOPE_SCHEMA_BEAT_KEY) if isinstance(beat, dict) else None
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def dialect_for(recipient: str) -> tuple[int, str]:
    """The envelope schema this emitter must use for ``recipient``, and why.

    Returns ``(schema, reason)``. ``AO_FLEET_ENVELOPE_SCHEMA`` pins the answer
    (a gate or a principal proving one dialect end to end); ``auto`` — the default
    — negotiates it from the recipient's beat.
    """
    pinned = (os.environ.get(ENVELOPE_SCHEMA_ENV) or "").strip()
    if pinned and pinned != ENVELOPE_SCHEMA_AUTO:
        try:
            version = int(pinned)
        except ValueError:
            version = SCHEMA_VERSION_LEGACY
        if version in SCHEMA_VERSIONS:
            return version, f"pinned by {ENVELOPE_SCHEMA_ENV}={version}"
        return SCHEMA_VERSION_LEGACY, f"{ENVELOPE_SCHEMA_ENV}={pinned} is not a declared version"
    declared = declared_envelope_schema(recipient)
    if declared is not None and declared >= SCHEMA_VERSION_CURRENT:
        return (
            SCHEMA_VERSION_CURRENT,
            f"recipient '{current_role(recipient)}' declares "
            f"{ENVELOPE_SCHEMA_BEAT_KEY}={declared} in its heartbeat",
        )
    return (
        SCHEMA_VERSION_LEGACY,
        f"recipient '{current_role(recipient)}' does not declare "
        f"{ENVELOPE_SCHEMA_BEAT_KEY}>={SCHEMA_VERSION_CURRENT} in its heartbeat",
    )


def stamp_envelope(message: dict, *, recipient: str | None = None) -> dict:
    """Stamp the emitting dialect on ``message``, in place, and return it.

    This is the SINGLE write seam: every emitter calls it, so single-emit is a
    property of the transport rather than a habit each emitter has to remember.
    It sets ``schema``, rewrites ``from``/``to`` into that dialect, and — when a
    recipient it cannot confirm as schema 2 forces the retired spelling — RECORDS
    the downgrade by name on stderr. A downgrade is reported, never silent: that
    is the difference between a deprecation window and two vocabularies both
    being quietly authoritative.

    An explicit ``schema`` on the message is honoured rather than overridden — a
    caller that declares a version has declared its intent, and `validate` then
    refuses an envelope that mixes a version with the other dialect's roles. It is
    still single-emit: one dialect per envelope.
    """
    target = recipient or (message.get("to") if isinstance(message.get("to"), str) else "")
    declared = message.get("schema")
    if (
        isinstance(declared, int)
        and not isinstance(declared, bool)
        and declared in SCHEMA_VERSIONS
    ):
        version, reason = declared, f"declared by the message (schema {declared})"
    elif target:
        version, reason = dialect_for(target)
    else:
        version, reason = SCHEMA_VERSION_LEGACY, "no recipient to negotiate with"
    message["schema"] = version
    for field in ("from", "to"):
        value = message.get(field)
        if not isinstance(value, str):
            continue
        if version == SCHEMA_VERSION_CURRENT and is_retired_role(value):
            message[field] = current_role(value)
        elif version == SCHEMA_VERSION_LEGACY and is_current_role(value):
            message[field] = _legacy_role(value)
    if version == SCHEMA_VERSION_LEGACY:
        print(
            f"channel: legacy-dialect — emitting schema {SCHEMA_VERSION_LEGACY} role names to "
            f"'{target or '-'}': {reason}",
            file=sys.stderr,
        )
    return message


def _legacy_role(role: str) -> str:
    """The schema-1 spelling of a current role, preserving an instance suffix."""
    base = role_base(role)
    for legacy, current in ROLE_RENAME.items():
        if current == base:
            return legacy + role[len(base) :]
    return role


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
            problems.append(
                f"{field} must name a role in a declared dialect "
                f"({'|'.join(ROLES_CURRENT)} for schema {SCHEMA_VERSION_CURRENT}, "
                f"{'|'.join(ROLES_LEGACY)} for schema {SCHEMA_VERSION_LEGACY}; "
                f"governance/vocabulary/fleet.yaml)"
            )
    # ── the envelope version, and the one thing it must never carry ─────────
    # Dual-accept on READ: both dialects are understood, so a message in flight
    # or a mailbox entry written by an older build is still valid. What is
    # refused is the MIXTURE — a current envelope carrying a retired role. That
    # is the state ADR-0012 warns about (two things quietly both being
    # authoritative), and it is why this is a versioned migration rather than a
    # silent accept of both forever.
    envelope_schema = message.get("schema", SCHEMA_VERSION_LEGACY)
    if isinstance(envelope_schema, bool) or envelope_schema not in SCHEMA_VERSIONS:
        problems.append(
            "schema must be one of "
            + ", ".join(str(version) for version in SCHEMA_VERSIONS)
            + f" (the envelope version; absent means {SCHEMA_VERSION_LEGACY})"
        )
    elif envelope_schema == SCHEMA_VERSION_CURRENT:
        for field in ("from", "to"):
            value = message.get(field)
            if isinstance(value, str) and is_retired_role(value):
                problems.append(
                    f"{field} is '{value}', a retired schema-{SCHEMA_VERSION_LEGACY} role in a "
                    f"schema-{SCHEMA_VERSION_CURRENT} envelope: schema {SCHEMA_VERSION_CURRENT} "
                    f"emits '{current_role(value)}' only (a schema-2 emitter must not emit a "
                    f"schema-1 role)"
                )
    # The trust rules below are stated on the CURRENT names and are applied to the
    # NORMALISED roles, so a message cannot slip past a rule by spelling its
    # sender or recipient in the other dialect.
    sender = current_role(message["from"]) if isinstance(message.get("from"), str) else None
    recipient = current_role(message["to"]) if isinstance(message.get("to"), str) else None
    if "id" in message and (not isinstance(message["id"], str) or not message["id"].strip()):
        problems.append("id must be a non-empty string")
    if "ts" in message and (not isinstance(message["ts"], str) or not _parse_ts(message["ts"])):
        problems.append("ts must be an ISO-8601 timestamp")
    if "correlation_id" in message and not isinstance(message["correlation_id"], str):
        problems.append("correlation_id must be a string")
    if "nonce" in message and (not isinstance(message["nonce"], str) or not message["nonce"].strip()):
        problems.append("nonce must be a non-empty string (the anti-replay token)")
    if message_type == "directive" and recipient not in (ROLE_DISPATCHER, ROLE_DIRECTOR):
        problems.append(
            "directives are addressed to the dispatcher (from the director) or to the "
            "director (from the principal)"
        )
    # Hierarchy (contract §4, rule 1b): the principal commands the director, and
    # the director commands the dispatcher. Neither step may be skipped — a
    # principal that could address the dispatcher directly would make the
    # director advisory. Reports and escalations still travel *up* to the
    # director, so the rule is scoped to the principal's own traffic and to
    # directives addressed to the director.
    if sender == ROLE_PRINCIPAL:
        if recipient != ROLE_DIRECTOR:
            problems.append(
                "the principal does not address the dispatcher: it orders the director, and the "
                "director orders the dispatcher"
            )
        elif message_type != "directive":
            problems.append("a principal order to the director must be a directive")
    if recipient == ROLE_DIRECTOR and message_type == "directive" and sender != ROLE_PRINCIPAL:
        problems.append("the director takes orders only from the principal")
    if message_type == "directive" and recipient == ROLE_DISPATCHER and sender != ROLE_DIRECTOR:
        problems.append("only the director may issue directives to the dispatcher")
    if sender == ROLE_DISPATCHER and message_type == "directive":
        problems.append("the dispatcher never picks work: it cannot issue directives")
    if message_type in ("ack", "result") and not message.get("correlation_id"):
        problems.append(f"{message_type} must carry correlation_id (the directive it answers)")
    if message_type in ("ack", "result") and sender == ROLE_DIRECTOR and recipient != ROLE_PRINCIPAL:
        problems.append("the director does not ack or report on its own directives (only back to the principal)")
    if message_type == "halt" and sender != ROLE_DIRECTOR:
        problems.append("only the director may issue a halt")
    if message_type == "steer":
        # Mid-run steering (issue #367): the director injects a hint into a live
        # run, so it stays inside the hierarchy — only the director steers, and
        # it steers the dispatcher (the loop that owns the run), never an executor.
        if not message.get("correlation_id"):
            problems.append("steer must carry correlation_id (the in-flight directive it steers)")
        if sender != ROLE_DIRECTOR:
            problems.append("only the director may steer a run mid-flight")
        if recipient != ROLE_DISPATCHER:
            problems.append("steer is addressed to the dispatcher (the loop that owns the run)")
        target = message.get("correlation_id")
        if isinstance(target, str) and not DIRECTIVE_ID_RE.fullmatch(target):
            problems.append("steer names a directive id that is not a safe mailbox name")
    if message_type == "escalate":
        if not message.get("correlation_id"):
            problems.append("escalate must carry correlation_id (the directive that hit trouble)")
        if sender == ROLE_DIRECTOR:
            if recipient != ROLE_PRINCIPAL:
                problems.append("a director escalation is addressed to the principal (the next level up)")
        elif recipient != ROLE_DIRECTOR:
            problems.append("escalations go up: executors and the dispatcher escalate to the director")
        severity = message.get("severity")
        if severity is not None and severity not in SEVERITIES:
            problems.append(f"severity must be one of {', '.join(SEVERITIES)}")
    control = message.get("control")
    if control is not None:
        if sender != ROLE_DIRECTOR:
            problems.append("only the director may issue control")
        if control not in CONTROL_ACTIONS:
            problems.append(f"control must be one of {', '.join(CONTROL_ACTIONS)}")
        if control in TASK_CONTROLS and not (message.get("task") or {}).get("issue"):
            problems.append(f"control '{control}' must name the task.issue it overrides")
        if control in DIRECTIVE_CONTROLS:
            # `drop` acts on a NAMED directive, and the target is a filename
            # component — so it must be present and a safe mailbox name, or the
            # control cannot be honoured (and must be reported, never a no-op).
            target = (message.get("task") or {}).get("directive")
            if not isinstance(target, str) or not target.strip():
                problems.append(f"control '{control}' must name the task.directive it acts on")
            elif not DIRECTIVE_ID_RE.fullmatch(target.strip()):
                problems.append(
                    f"control '{control}' names a directive that is not a safe mailbox name"
                )
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
            # A `drop` control names the DIRECTIVE it retires, not an issue of its
            # own (issue #754): the target order already carries the issue, and
            # demanding a second, possibly different one here would be a way to
            # dead-letter the wrong thing while the transport called it valid.
            carries_directive_control = message.get("control") in DIRECTIVE_CONTROLS
            if (
                kind not in NON_WORK_KINDS
                and not carries_decompose
                and not carries_directive_control
                and (not isinstance(issue, int) or isinstance(issue, bool) or issue < 1)
            ):
                problems.append("task.issue must be a positive integer")
            for field in ("epic",):
                if field in task and (not isinstance(task[field], int) or isinstance(task[field], bool) or task[field] < 1):
                    problems.append(f"task.{field} must be a positive integer")
    if "body" in message and not isinstance(message["body"], str):
        problems.append("body must be a string")

    # ── per-runtime allowlists for verbs, skills and secrets (issue #1273) ──
    # `runtime` is the SENDER's runtime id; `on_behalf_of` names a second
    # runtime the action is really being carried out for (the laundering
    # case — a denied runtime asking a permitted one to do it instead). Both
    # are optional: a message naming neither is judged only on role/hierarchy,
    # exactly as before this issue.
    runtime = message.get("runtime")
    on_behalf_of = message.get("on_behalf_of")
    try:
        known_runtimes = runtime_ids()
        vocabulary_problem = ""
    except runtimes.RegistryRefused as exc:
        # Fail-closed: a vocabulary that cannot be read cannot clear a runtime.
        known_runtimes = ()
        vocabulary_problem = f"runtime-vocabulary-unreadable: {exc}"
    if vocabulary_problem and (runtime is not None or on_behalf_of is not None):
        problems.append(vocabulary_problem)
    if runtime is not None and runtime not in known_runtimes:
        problems.append(f"runtime must be one of {', '.join(known_runtimes)}")
    if on_behalf_of is not None and on_behalf_of not in known_runtimes:
        problems.append(f"on_behalf_of must be one of {', '.join(known_runtimes)}")
    if isinstance(task, dict) and runtime in known_runtimes:
        verb = task.get("verb")
        if isinstance(verb, str):
            allowed = _verb_allowlist().get(verb)
            if allowed is not None and runtime not in allowed:
                problems.append(f"verb-not-allowed:{runtime}:{verb}")
            if (
                on_behalf_of in known_runtimes
                and allowed is not None
                and on_behalf_of not in allowed
            ):
                # Laundering: the runtime actually asking (on_behalf_of) is not
                # itself allowed the verb, even though the sender/intermediary
                # is. Refused under its OWN name, not the intermediary's.
                problems.append(f"laundering:{on_behalf_of}:{verb}")
        skill = task.get("skill")
        if isinstance(skill, str):
            allowed = _skill_allowlist().get(skill)
            if allowed is not None and runtime not in allowed:
                problems.append(f"skill-not-allowed:{runtime}:{skill}")
            if on_behalf_of in known_runtimes and allowed is not None and on_behalf_of not in allowed:
                problems.append(f"laundering:{on_behalf_of}:{skill}")
        secret = task.get("secret")
        if isinstance(secret, str):
            allowed = _secret_allowlist().get(secret)
            if allowed is not None and runtime not in allowed:
                problems.append(f"secret-not-allowed:{runtime}:{secret}")
            if on_behalf_of in known_runtimes and allowed is not None and on_behalf_of not in allowed:
                problems.append(f"laundering:{on_behalf_of}:{secret}")
    return problems


def load_message(source: Path | str) -> dict:
    """Accept a path to a JSON file *or* inline JSON.

    The director is a live terminal, so forcing it to write a temp file for every
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
    """The director's undelivered steering messages, oldest first."""
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
    run writes them. ``--timeout-seconds 0`` follows forever (the principal's
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
    """Director side: queue a mid-run steering hint for one in-flight directive (#367).

    The dispatcher loop drains the queue every cycle and delivers the hint to the
    live run (its stdin and its log stream) without killing or re-dispatching
    anything. One pending steer per directive: a newer hint replaces an
    undelivered older one, and every steer is audited in the slog.
    """
    if args.message is not None:
        message = load_message(args.message)
        message.setdefault("from", ROLE_DIRECTOR)
        message.setdefault("to", ROLE_DISPATCHER)
        message.setdefault("type", "steer")
    else:
        message = {
            "from": ROLE_DIRECTOR,
            "to": ROLE_DISPATCHER,
            "type": "steer",
            "correlation_id": args.directive,
            "body": args.body,
        }
    stamp_envelope(message, recipient=ROLE_DISPATCHER)
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
    *different* mailboxes: ``send`` (director → dispatcher) writes ``SENT``/``INBOX`` and
    ``order`` (principal → director) writes ``BRAIN_SENT``/``BRAIN_INBOX``. Scanning
    the dispatcher's mailboxes from ``order`` made that guard unreachable — measured,
    an identical principal order was accepted twice (#278). ``None`` keeps the
    default a run-time lookup of the dispatcher's mailboxes.
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
    stamp_envelope(message)
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


def _rev_parse(revision: str) -> str | None:
    """`git rev-parse --short <revision>` in this checkout; None when unreadable.

    Shared by `head_commit` and `remote_head_commit` so both agree on what
    "unreadable" means. The distinction the two callers draw is *which*
    revision they name, and that distinction is the whole of #739.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", revision],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def head_commit() -> str:
    """The LOCAL working-tree HEAD sha — what a freshly started loop would be running.

    Local, and deliberately so: this is the commit a loop started *here and now*
    would execute, which is the right question for "will a respawn pick up my
    fix?". It is the **wrong** baseline for drift detection — the local checkout
    is frequently itself the stale side (#739), so comparing a loop's commit to
    this can compare stale-to-stale and report `healthy` for a loop running
    pre-fix code. Drift is measured against `remote_head_commit`.
    """
    return _rev_parse("HEAD") or "unknown"


def remote_head_commit(revision: str = "origin/master") -> str:
    """The `origin/master` sha — the drift baseline, or "unknown".

    This is the honest baseline for "is the running and `remote` in sync?": the
    local checkout may be behind (or ahead of) `origin/master` at any moment,
    and a lane running old code is exactly the situation drift detection exists
    to catch (#739, AO-GR-25).

    **Tradeoff, deliberate: this reads the already-fetched remote-tracking ref
    and never fetches.** A watchdog tick runs every 2 minutes under cron, and a
    `git fetch` per tick would add network latency to a pass that is otherwise
    local, fail behind a proxy, and — worst — turn an unreachable remote into
    either a slowed fleet watchdog or, if the failure were swallowed, a silently
    disabled control. Reading `refs/remotes/origin/master` costs nothing and
    cannot fail open. The *refresh* is the lane's job: every lane fetches before
    it cuts a worktree (and `governance/isolation ... --fetch`), so the ref
    tracks the remote as closely as the fleet actually pulls. The staleness of
    this ref is therefore bounded by "how recently a lane fetched", and it is
    named in the watchdog line so a principal can see which two commits were
    compared. A ref that cannot be read yields "unknown", which the caller MUST
    treat as CANNOT-ASSESS — never as healthy.
    """
    return _rev_parse(revision) or "unknown"


#: Drift classes, deliberately a superset of the rung states in fleet/watchdog.py
#: (`missing`/`stale`/`drifted`/`checkout-behind`/`healthy`) so a single classifier
#: answers both "is there a live loop?" and "is it running current code?".
DRIFT_OK = "ok"
DRIFT_DRIFTED = "drifted"
#: A THIRD case, distinct from `DRIFT_DRIFTED` because its REMEDY is (#773,
#: AO-GR-21): the running commit equals the *local* checkout's HEAD, so the rung
#: is not running stale code relative to what a respawn would give it — the
#: CHECKOUT is behind `origin/master`. A respawn re-executes the same checkout
#: and so cannot change the value being compared; the remedy is a fast-forward.
#: Reporting this as plain `drifted` is what produced 132 respawn decisions and
#: 45 clean stops in one night on the live fleet.
DRIFT_CHECKOUT_BEHIND = "checkout-behind"
DRIFT_CANNOT_ASSESS = "cannot-assess"


def classify_drift(
    running: str,
    baseline: str,
    baseline_name: str = "origin/master",
    local_head: str | None = None,
) -> tuple[str, str]:
    """Classify a running loop's commit against the drift baseline. Never fails open.

    Tri-state contract (the repo's gate convention — see `EXIT_OK` /
    `EXIT_NOT_OK` / `EXIT_CANNOT_ASSESS`):

      * `DRIFT_OK`          — both commits known and equal; the loop runs current code.
      * `DRIFT_DRIFTED`     — the loop runs code the checkout no longer holds, so
                              it must be respawned to load HEAD.
      * `DRIFT_CHECKOUT_BEHIND` — the loop runs the *local* HEAD, but `origin/master`
                              is ahead (#773). The rung is current *relative to the
                              checkout*; the checkout itself is stale. The remedy is
                              a fast-forward, NOT a respawn — a respawn re-runs the
                              same checkout and cannot change this value. Only the
                              caller that knows the local HEAD (`local_head`) can
                              reach this state, so a caller that passes no
                              `local_head` keeps the pre-#773 verdict.
      * `DRIFT_CANNOT_ASSESS` — either side unreadable. This is the fail-closed
                              branch: the previous rule was `if baseline != "unknown"
                              and running != baseline`, which read an unreadable
                              baseline as *healthy* and silently disabled drift
                              detection entirely (#739, AO-GR-25). "I cannot see
                              the baseline" is not "there is no drift".

    Returns `(state, reason)`; the reason always names both commits so a reader can
    audit the comparison rather than trust the verdict.
    """
    known_running = running not in ("", "unknown")
    known_baseline = baseline not in ("", "unknown")
    if not known_baseline:
        return DRIFT_CANNOT_ASSESS, f"no readable {baseline_name} baseline ({baseline_name} unreadable)"
    if not known_running:
        return DRIFT_CANNOT_ASSESS, f"loop reports no commit, cannot compare against {baseline_name} {baseline}"
    if running == baseline:
        return DRIFT_OK, f"running {running} = {baseline_name} {baseline}"
    if local_head not in (None, "", "unknown") and local_head == running:
        return (
            DRIFT_CHECKOUT_BEHIND,
            f"the checkout is behind: running {running} = local HEAD, {baseline_name} {baseline}",
        )
    return DRIFT_DRIFTED, f"running {running}, {baseline_name} {baseline}"


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

    Shared by the director and the dispatcher so neither can be silently absent from
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
    # Drift is measured against the REMOTE baseline, never the local checkout —
    # the local checkout can be (and in this fleet routinely is) the stale side
    # (#739, AO-GR-25). Comparing to `head_commit()` made a loop on pre-fix code
    # read as current whenever the checkout was also behind.
    baseline = remote_head_commit()
    drift_state, drift_reason = classify_drift(running, baseline)
    print(f"{name}: {verdict} — pid {beat.get('pid', '?')}, state {state}, last beat {int(age)}s ago")
    print(f"{name}: running commit {running} | origin/master {baseline}")
    if drift_state == DRIFT_DRIFTED:
        print(
            f"{name}: CODE DRIFT — it is running {running}, not origin/master {baseline}; merged "
            f"fixes are not live. Restart: {start_cmd}"
        )
    elif drift_state == DRIFT_CANNOT_ASSESS:
        # Fail-closed: an unreadable baseline is not evidence of health. The old
        # rule returned True here, which is how an unreadable HEAD silently
        # disabled drift detection entirely.
        print(f"{name}: CANNOT ASSESS DRIFT — {drift_reason}")
    # The capability declaration is read from the commit the loop is *running*,
    # so it still keys off `running`; the baseline only decides drift.
    finding = capability_finding(name, beat, running)
    print(capability_line(finding))
    return (
        age <= STALE_HEARTBEAT_SECONDS
        and drift_state == DRIFT_OK
        and finding.kind not in (KIND_CAPABILITY_STALE, KIND_UNKNOWN)
    )


def expired_directives(directory: Path, moment: float | None = None) -> list[Path]:
    """Pending directives older than the declared directive lifetime.

    A directive the dispatcher never consumes is abandoned after
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
    """Top of the hierarchy: the principal orders the *director*, never the dispatcher.

    The principal trigger exists so the chain is real code — principal → director →
    dispatcher — rather than a convention the transport cannot enforce. `send` is
    director→dispatcher and refuses a principal sender, so this is the only way in.

    The anti-replay scan covers the mailboxes *this* path writes
    (``BRAIN_SENT``/``BRAIN_INBOX``/``BRAIN_DONE``): the shared default scans the
    dispatcher's mailboxes, so the guard could never fire here and an identical order
    was accepted twice (#278).
    """
    message = load_message(args.message)
    message.setdefault("from", ROLE_PRINCIPAL)
    message.setdefault("to", ROLE_DIRECTOR)
    message.setdefault("type", "directive")
    stamp_envelope(message, recipient=ROLE_DIRECTOR)
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
    """The director's own watch: the oldest order from the principal, if any."""
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
    """The director has dispatched (or refused) the order: move it to done/."""
    source = BRAIN_INBOX / f"{message_id}.json"
    if not source.exists():
        return False
    BRAIN_DONE.mkdir(parents=True, exist_ok=True)
    source.replace(BRAIN_DONE / source.name)
    return True


def brain_reply(order: dict, message_type: str, body: str) -> None:
    """Answer the principal in the director outbox — the report the principal reads."""
    message = {
        "from": ROLE_DIRECTOR,
        "to": ROLE_PRINCIPAL,
        "type": message_type,
        "correlation_id": str(order.get("id") or order.get("correlation_id") or ""),
        "id": str(uuid.uuid4()),
        "ts": now_iso(),
        "nonce": str(uuid.uuid4()),
        "body": body[:2000],
    }
    stamp_envelope(message, recipient=ROLE_PRINCIPAL)
    BRAIN_OUTBOX.mkdir(parents=True, exist_ok=True)
    (BRAIN_OUTBOX / f"{message['id']}.json").write_text(json.dumps(message, indent=2) + "\n", encoding="utf-8")
    _slog(message)


def cmd_brain_outbox(args: argparse.Namespace) -> int:
    """Principal side: read the director's replies (acks and refusals), newest last.

    Ordered by the message timestamp, not by filename: ids are uuid4, so a
    filename sort returns the replies in arbitrary order and the principal reads
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
    and the guard keeps it until a principal re-arms the directive by name.
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
        "to": ROLE_DIRECTOR,
        "type": args.type,
        "correlation_id": args.correlation,
    }
    if args.body:
        message["body"] = args.body
    stamp_envelope(message, recipient=ROLE_DIRECTOR)
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
    """Sister/subagent side: raise a problem to the director for elite steering."""
    message = {
        "from": args.from_role,
        "to": ROLE_DIRECTOR,
        "type": "escalate",
        "correlation_id": args.correlation,
        "severity": args.severity,
    }
    if args.body:
        message["body"] = args.body
    stamp_envelope(message, recipient=ROLE_DIRECTOR)
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
    """Director side: tail the slog stream, printing each message as it flows through.

    This is the idle-watch: run with `--timeout-seconds 0` and the terminal
    blocks, printing every directive/ack/result/escalate the moment it lands —
    an escalation from the dispatcher pings the director here. `--max-messages` bounds
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
    """Director side: block until a result for this id (or correlation) lands in the outbox.

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

    Ids are uuid4, so a filename sort is arbitrary: measured, the dispatcher took a
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
    """Dispatcher side listener: return the oldest pending directive, or block for one.

    This is what makes the dispatcher a dumb terminal with a pulse: it runs
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
    still the principal's record that the work was ordered, but not dispatched
    early. That is what stops a refused or crashed order from being re-read every
    cycle, without the loop sleeping on it.

    The board trigger (issue #727) filters on it too: a directive PARKED because
    the board snapshot was stale is the principal's live order waiting on the
    board, so it is held while the snapshot is stale and released the moment
    freshness returns — no principal action, and never a re-dispatch per cycle.
    """
    INBOX.mkdir(parents=True, exist_ok=True)
    skip = set(getattr(args, "skip", None) or [])
    guard_base = INBOX.parent
    # Resolved once per watch, not once per directive: the hot loop below reads a
    # plain local. `None` means this checkout ships no dispatch tree, so there is
    # no park to filter on and the loop behaves exactly as it did before #727.
    trigger = board_snapshot()
    snapshot_path = getattr(args, "snapshot", None)
    stale_minutes = getattr(args, "stale_minutes", None)
    if trigger is not None:
        snapshot_path = snapshot_path or trigger.DEFAULT_PATH
        stale_minutes = stale_minutes or trigger.DEFAULT_STALENESS_MINUTES
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
            if trigger is not None and trigger.parked(path.stem, base=guard_base):
                if not trigger.freshness_restored(snapshot_path, stale_minutes):
                    held.append(path.stem)
                    continue
                trigger.unpark(path.stem, base=guard_base)
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
            held_note = f" — {len(held)} held (backoff, dead-letter or a stale-board park)" if held else ""
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
    watch.add_argument("--snapshot", default="", help="the board snapshot whose freshness releases a park (issue #727)")
    watch.add_argument("--stale-minutes", type=int, default=None, help="the staleness threshold a park is held against")
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
