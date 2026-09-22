"""Heartbeat adapter — synthesize the upstream wake/delta/outcome from rung activity.

The fleet's beat is a flat process-liveness record: ``fleet/monitor.py``,
``fleet/brain.py`` and ``fleet/terminal.py`` each ``write_heartbeat``
``{pid, state, started_at, commit, ts}``. It carries **no** ``wake.cause``, **no**
``wake.delta`` and **no** ``outcome``, and no explicit tick or cadence
(``docs/PAPERCLIP-ING-INTEGRATION.md`` §5 mismatches #1–#3).

This adapters *derives* those fields — it never rewrites the fleet's beat
(ADR-0012: map the policy, do not couple the runtime). It reads the fleet's own
stores, read-only:

* the rung's beat, ``.fleet/<rung>.heartbeat.json`` — liveness and, for the
  sister, the issue/agent a run owns;
* the claim ledger, ``.board/claims.jsonl`` + ``.board/claims/`` — the claim
  events that turn into ``claimed`` / ``released`` deltas and the ``assigned``
  wake;
* the board snapshot, ``.board/snapshot.json`` — the ``blocked_by`` edges and
  ``closed_at`` stamps that turn into ``blocked`` / ``unblocked`` / ``closed``;
* the rung's own mailbox, ``.fleet/inbox`` / ``.fleet/brain/inbox`` — the
  directives, results and acks that turn into ``assigned`` /
  ``review-requested`` / ``commented``.

The cadence constants are **read from their owners**, never re-declared:
``fleet/monitor.py`` ``POLL_SECONDS`` for ``cadence_seconds`` and
``governance/policy/lease.py`` ``RUNG_HEARTBEAT_SECONDS`` for the staleness
ceiling. A source that cannot be read fails closed — the adapter does not invent
a number.

Determinism: the adapter is a pure function of the tree it is given plus an
explicit ``history`` (the beats already emitted) and an explicit ``now``. Same
inputs, byte-identical output.

Fail-closed (never fabricate a cause it cannot source):

* a rung beat whose ``state`` is outside the known liveness vocabulary is
  *unattributable* — the adapter refuses, naming the offending state;
* a beat that carries no ``wake.delta``, an unknown ``wake.cause``, or a
  ``blocked`` outcome with no ``owner`` is refused by name by
  :func:`validate_heartbeat`.

---knowledge---
module_id: integrations.paperclip.adapters.heartbeat.adapter
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: [HeartbeatRefused, Rung, parse_iso, format_iso, read_int_constant, cadence_seconds, staleness_ceiling_seconds, policy, (+12 more)]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── the frozen contract this adapter fills ──────────────────────────────────

#: The upstream wake vocabulary (heartbeat.schema.json ``wake.cause`` enum).
WAKE_CAUSES: tuple[str, ...] = (
    "assigned",
    "commented",
    "unblocked",
    "review-requested",
    "scheduled",
)

#: The outcome vocabulary (heartbeat.schema.json ``outcome.status`` enum).
OUTCOME_STATUSES: tuple[str, ...] = ("progress", "blocked")

#: The frozen heartbeat schema the emitted record must satisfy.
SCHEMA_REL = "docs/contracts/paperclip/heartbeat.schema.json"

# ── the fleet stores this adapter derives from ──────────────────────────────

#: The claim ledger (one file per event + the frozen single-file history).
LEDGER_JSONL_REL = ".board/claims.jsonl"
LEDGER_DIR_REL = ".board/claims"
SNAPSHOT_REL = ".board/snapshot.json"

#: The rung-liveness vocabulary the fleet actually publishes. A state outside
#: this set cannot be attributed to a wake cause, so it fails closed.
PROGRESS_STATES: tuple[str, ...] = ("healthy", "idle", "dispatching", "working")
BLOCKED_STATES: tuple[str, ...] = ("paused", "stopping", "stopped")
KNOWN_STATES: tuple[str, ...] = PROGRESS_STATES + BLOCKED_STATES

#: The synthesized state name for a rung that has published no beat at all: it
#: is provably down, so the adapter reports a scheduled tick ending in a named
#: blocker instead of pretending it saw activity.
NO_BEAT_STATE = "no-heartbeat"

#: The source modules that OWN the cadence constants (read, never re-declared).
CADENCE_MODULE_REL = "fleet/monitor.py"
CADENCE_CONSTANT = "POLL_SECONDS"
CEILING_MODULE_REL = "governance/policy/lease.py"
CEILING_CONSTANT = "RUNG_HEARTBEAT_SECONDS"

#: The claim-ledger events, grouped by what they do to the held set.
HELD_EVENTS = ("claim", "take-over")
RELEASED_EVENTS = ("release", "reap")

#: The message types a rung's mailbox can carry (fleet/schema/message.schema.json).
DIRECTIVE_TYPE = "directive"
RESULT_TYPE = "result"


class HeartbeatRefused(Exception):
    """The adapter refused to emit a beat. ``reason`` is machine-readable."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class Rung:
    """One rung that publishes a beat, and where its activity lives."""

    name: str
    beat: str
    inbox: str
    owner: str


#: The rungs, their beats, their mailboxes and their accountable owner. The
#: owner is who fixes the rung when it is down: the watchdog respawns the
#: monitor, the operator drives the brain, the brain owns the sister.
RUNGS: tuple[Rung, ...] = (
    Rung("monitor", ".fleet/monitor.heartbeat.json", "", "fleet/watchdog"),
    Rung("brain", ".fleet/brain.heartbeat.json", ".fleet/brain/inbox", "operator"),
    Rung("sister", ".fleet/sister.heartbeat.json", ".fleet/inbox", "brain"),
)


def _rung(name: str) -> Rung:
    for rung in RUNGS:
        if rung.name == name:
            return rung
    known = ", ".join(r.name for r in RUNGS)
    raise HeartbeatRefused("unknown-rung", f"{name!r} is not a heartbeat rung ({known})")


# ── time helpers ────────────────────────────────────────────────────────────


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 stamp to an aware UTC datetime, or fail closed."""
    text = str(value or "").strip()
    if not text:
        raise HeartbeatRefused("unreadable-timestamp", "empty timestamp")
    normalised = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        moment = datetime.fromisoformat(normalised)
    except ValueError as exc:
        raise HeartbeatRefused("unreadable-timestamp", f"{value!r}: {exc}") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def format_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── reading the fleet stores ────────────────────────────────────────────────


def read_int_constant(source_root: Path, rel: str, name: str) -> int:
    """Read an integer module constant from its owning source file.

    The cadence and the staleness ceiling are declared once each (in
    ``fleet/monitor.py`` and ``governance/policy/lease.py``). Re-declaring them
    here would let the seam drift from the runtime, so this reads the owner and
    fails closed when the constant is absent.
    """
    path = Path(source_root) / rel
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HeartbeatRefused("source-unreadable", f"{rel}: {exc}") from exc
    match = re.search(rf"^{re.escape(name)}\s*=\s*(\d+)\s*$", text, re.MULTILINE)
    if not match:
        raise HeartbeatRefused(
            "source-unreadable", f"{rel}: no integer constant {name!r} on its own line"
        )
    return int(match.group(1))


def cadence_seconds(source_root: Path | None = None) -> int:
    """``cadence_seconds`` = ``fleet/monitor.py`` ``POLL_SECONDS`` (the tick).

    The cadence is a property of the fleet code, so by default it is read from
    the adapter's own tree — the repo that declares it — not from the data tree
    a caller hands in.
    """
    return read_int_constant(source_root or ROOT, CADENCE_MODULE_REL, CADENCE_CONSTANT)


def staleness_ceiling_seconds(source_root: Path | None = None) -> int:
    """The staleness ceiling = ``lease.RUNG_HEARTBEAT_SECONDS`` (declared once)."""
    return read_int_constant(source_root or ROOT, CEILING_MODULE_REL, CEILING_CONSTANT)


def policy(source_root: Path | None = None) -> dict[str, Any]:
    """The cadence policy, with its ordering invariant checked."""
    source = source_root or ROOT
    cadence = cadence_seconds(source)
    ceiling = staleness_ceiling_seconds(source)
    if cadence >= ceiling:
        raise HeartbeatRefused(
            "cadence-invariant",
            f"cadence {cadence}s must be below the staleness ceiling {ceiling}s",
        )
    return {
        "cadence_seconds": cadence,
        "staleness_ceiling_seconds": ceiling,
        "cadence_within_ceiling": cadence < ceiling,
        "cadence_source": f"{CADENCE_MODULE_REL}:{CADENCE_CONSTANT}",
        "ceiling_source": f"{CEILING_MODULE_REL}:{CEILING_CONSTANT}",
    }


def read_beat(root: Path, rung: Rung) -> dict[str, Any]:
    """The rung's beat, or ``{}`` when none has been published yet."""
    path = Path(root) / rung.beat
    if not path.exists():
        return {}
    try:
        beat = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HeartbeatRefused("beat-unreadable", f"{rung.beat}: {exc}") from exc
    if not isinstance(beat, dict):
        raise HeartbeatRefused("beat-unreadable", f"{rung.beat}: not a JSON object")
    return beat


def _event(where: str, obj: Any) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise HeartbeatRefused("ledger-unreadable", f"{where}: not a JSON object")
    for key in ("event", "issue", "at"):
        if key not in obj:
            raise HeartbeatRefused("ledger-unreadable", f"{where}: missing {key!r}")
    try:
        issue = int(obj["issue"])
    except (TypeError, ValueError) as exc:
        raise HeartbeatRefused("ledger-unreadable", f"{where}: bad issue {obj['issue']!r}") from exc
    return {
        "event": str(obj["event"]),
        "issue": issue,
        "agent": str(obj.get("agent") or ""),
        "at": str(obj["at"]),
        "base_commit": str(obj.get("base_commit") or ""),
        "reason": str(obj.get("reason") or ""),
    }


def read_ledger(root: Path) -> list[dict[str, Any]]:
    """Replay the claim ledger in temporal order (frozen file, then directory)."""
    events: list[dict[str, Any]] = []
    legacy = Path(root) / LEDGER_JSONL_REL
    if legacy.is_file():
        for lineno, line in enumerate(legacy.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise HeartbeatRefused(
                    "ledger-unreadable", f"{LEDGER_JSONL_REL}:{lineno}: {exc.msg}"
                ) from exc
            events.append(_event(f"{LEDGER_JSONL_REL}:{lineno}", obj))
    claims_dir = Path(root) / LEDGER_DIR_REL
    if claims_dir.is_dir():
        for path in sorted(claims_dir.glob("*.json")):
            try:
                obj = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise HeartbeatRefused("ledger-unreadable", f"{path}: {exc}") from exc
            events.append(_event(str(path.relative_to(root)), obj))
    events.sort(key=lambda e: e["at"])
    return events


def read_snapshot(root: Path) -> dict[int, dict[str, Any]]:
    """The board snapshot's issues keyed by number (empty when absent)."""
    path = Path(root) / SNAPSHOT_REL
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HeartbeatRefused("snapshot-unreadable", f"{SNAPSHOT_REL}: {exc}") from exc
    if not isinstance(data, dict):
        raise HeartbeatRefused("snapshot-unreadable", f"{SNAPSHOT_REL}: not a JSON object")
    issues: dict[int, dict[str, Any]] = {}
    for item in data.get("issues") or []:
        if not isinstance(item, dict) or "number" not in item:
            continue
        try:
            issues[int(item["number"])] = item
        except (TypeError, ValueError):
            continue
    return issues


def read_inbox(root: Path, rung: Rung, after: datetime | None) -> list[dict[str, Any]]:
    """Inbound messages to a rung that arrived strictly after ``after``.

    A message with no readable ``ts`` cannot be ordered, so it fails closed
    rather than being silently counted as new (or silently dropped).
    """
    if not rung.inbox:
        return []
    directory = Path(root) / rung.inbox
    if not directory.is_dir():
        return []
    messages: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HeartbeatRefused("inbox-unreadable", f"{path}: {exc}") from exc
        if not isinstance(obj, dict):
            raise HeartbeatRefused("inbox-unreadable", f"{path}: not a JSON object")
        sent_at = parse_iso(obj.get("ts") or "")
        if after is not None and sent_at <= after:
            continue
        messages.append({"type": str(obj.get("type") or ""), "ts": obj.get("ts"), "id": obj.get("id")})
    messages.sort(key=lambda m: str(m["ts"]))
    return messages


# ── deriving activity and the delta ─────────────────────────────────────────


@dataclass(frozen=True)
class Activity:
    """What was observably true about an agent's scope at one instant."""

    held: frozenset[int]
    closed: frozenset[int]
    blocked: frozenset[int]


def _is_closed_asof(issue: Mapping[str, Any] | None, at: datetime | None) -> bool:
    if not issue:
        return False
    if str(issue.get("state") or "").strip().lower() != "closed":
        return False
    stamp = str(issue.get("closed_at") or "")
    if not stamp:
        # Closed with no recorded time: true for every instant, so it is never a
        # *new* closure and never manufactures a delta.
        return True
    if at is None:
        return True
    return parse_iso(stamp) <= at


def _is_blocked_asof(
    issues: Mapping[int, Mapping[str, Any]], number: int, at: datetime | None
) -> bool:
    """True when ``number`` is open and has at least one blocker still open."""
    issue = issues.get(number)
    if not issue or _is_closed_asof(issue, at):
        return False
    for blocker in issue.get("blocked_by") or []:
        try:
            blocker_number = int(blocker)
        except (TypeError, ValueError):
            continue
        blocker_issue = issues.get(blocker_number)
        if blocker_issue is None or not _is_closed_asof(blocker_issue, at):
            return True
    return False


def activity(
    issues: Mapping[int, Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    scope: Iterable[int],
    at: datetime | None,
) -> Activity:
    """Reconstruct the agent's held/closed/blocked sets as of ``at``.

    ``at is None`` means "before anything was observed" — the empty baseline a
    first beat is diffed against.
    """
    scope_set = set(scope)
    if at is None:
        return Activity(frozenset(), frozenset(), frozenset())
    held: dict[int, str] = {}
    for event in events:
        if event["issue"] not in scope_set:
            continue
        if parse_iso(event["at"]) > at:
            continue
        if event["event"] in HELD_EVENTS:
            held[event["issue"]] = event["agent"]
        elif event["event"] in RELEASED_EVENTS:
            held.pop(event["issue"], None)
    closed = {n for n in scope_set if _is_closed_asof(issues.get(n), at)}
    blocked = {n for n in scope_set if _is_blocked_asof(issues, n, at)}
    return Activity(frozenset(held), frozenset(closed), frozenset(blocked))


def _scope(events: Sequence[Mapping[str, Any]], agent_id: str, issue: Any) -> set[int]:
    scope = {e["issue"] for e in events if e["agent"] == agent_id}
    if issue is not None:
        try:
            scope.add(int(issue))
        except (TypeError, ValueError) as exc:
            raise HeartbeatRefused("bad-issue", f"rung beat issue {issue!r} is not an integer") from exc
    return scope


def _previous_state(previous: Mapping[str, Any] | None) -> str | None:
    if not previous:
        return None
    delta = (previous.get("wake") or {}).get("delta") or {}
    state = delta.get("state")
    if isinstance(state, dict):
        to = state.get("to")
        return str(to) if to is not None else None
    return None


def build_delta(
    *,
    previous: Mapping[str, Any] | None,
    previous_at: datetime | None,
    current: Activity,
    prior: Activity,
    state: str,
    tick: int,
) -> dict[str, Any]:
    """The change since the last beat — a delta, not a state dump.

    Always carries ``since`` (the beat it is relative to), ``tick`` (the emit
    advance) and ``state`` (the rung's transition); the change lists appear only
    when non-empty, so the object stays small and a consumer needs no history
    re-read.
    """
    previous_tick = previous.get("tick") if previous else None
    delta: dict[str, Any] = {
        "since": format_iso(previous_at) if previous_at is not None else None,
        "tick": {"from": previous_tick, "to": tick},
        "state": {"from": _previous_state(previous) or "absent", "to": state},
    }
    for key, changed in (
        ("claimed", current.held - prior.held),
        ("released", prior.held - current.held),
        ("closed", current.closed - prior.closed),
        ("blocked", current.blocked - prior.blocked),
        ("unblocked", prior.blocked - current.blocked),
    ):
        if changed:
            delta[key] = sorted(changed)
    return delta


# ── attribution and outcome ─────────────────────────────────────────────────


def attribute_cause(
    delta: Mapping[str, Any], state: str, messages: Sequence[Mapping[str, Any]]
) -> str:
    """Map real activity onto the closed ``wake.cause`` vocabulary.

    Precedence (first match wins), each anchored to a real source:

    ================== ==================================================
    cause              source
    ================== ==================================================
    ``unblocked``      a blocker of the agent's issue closed (``unblocked`` delta)
    ``review-requested`` a ``result`` message landed in the rung's mailbox
    ``assigned``       a new claim, or a ``directive`` landed in the mailbox
    ``commented``      any other inbound message (ack / escalate / halt)
    ``scheduled``      a cadence tick with no attributable event
    ================== ==================================================

    A beat that cannot be attributed (a state outside the known vocabulary)
    fails closed rather than being reported as ``scheduled``.
    """
    if delta.get("unblocked"):
        return "unblocked"
    if any(m["type"] == RESULT_TYPE for m in messages):
        return "review-requested"
    if delta.get("claimed") or any(m["type"] == DIRECTIVE_TYPE for m in messages):
        return "assigned"
    if messages:
        return "commented"
    if state in KNOWN_STATES or state == NO_BEAT_STATE:
        return "scheduled"
    raise HeartbeatRefused(
        "unattributable",
        f"state {state!r} is outside the known liveness vocabulary {KNOWN_STATES} "
        "and no wake event could be sourced",
    )


def decide_outcome(
    *,
    delta: Mapping[str, Any],
    state: str,
    rung: Rung,
    has_beat: bool,
    owner: str,
    tick: int,
) -> dict[str, Any]:
    """``progress`` or a ``blocked`` outcome with a named owner.

    A dead rung (no beat) and a rung in a non-progress state both end in a named
    blocker, never in unevidenced progress.
    """
    if not has_beat:
        return {
            "status": "blocked",
            "detail": f"rung {rung.name} published no beat at {rung.beat}",
            "owner": rung.owner,
        }
    if delta.get("claimed") or delta.get("closed") or delta.get("unblocked") or delta.get("released"):
        moved = [k for k in ("claimed", "closed", "unblocked", "released") if delta.get(k)]
        return {
            "status": "progress",
            "detail": f"rung {rung.name} advanced ({', '.join(moved)}) on tick {tick}",
            "owner": owner,
        }
    if delta.get("blocked"):
        blocked = ", ".join(str(n) for n in delta["blocked"])
        return {
            "status": "blocked",
            "detail": f"rung {rung.name} is blocked by open dependency on issue(s) {blocked}",
            "owner": owner or rung.owner,
        }
    if state in PROGRESS_STATES:
        return {
            "status": "progress",
            "detail": f"rung {rung.name} live ({state}) on scheduled tick {tick}",
            "owner": owner,
        }
    return {
        "status": "blocked",
        "detail": f"rung {rung.name} is not making progress (state={state})",
        "owner": rung.owner,
    }


# ── the public derivation ───────────────────────────────────────────────────


def derive_heartbeat(
    root: Path,
    rung_name: str,
    *,
    session_id: str,
    agent_id: str | None = None,
    history: Sequence[Mapping[str, Any]] = (),
    now: str | None = None,
) -> dict[str, Any]:
    """Derive one upstream heartbeat from the rung's real activity.

    ``history`` is the beats already emitted for this agent (most recent last);
    the emitted ``tick`` is the actual emit count, and the delta is taken
    relative to the previous beat. ``now`` supplies the timestamp only when the
    rung has published no beat of its own.
    """
    root = Path(root)
    rung = _rung(rung_name)
    # The cadence policy is a property of the fleet code, read from the tree that
    # declares it (the adapter's own repo), never from the data tree.
    cadence = cadence_seconds()
    ceiling = staleness_ceiling_seconds()
    if cadence >= ceiling:
        raise HeartbeatRefused(
            "cadence-invariant",
            f"cadence {cadence}s must be below the staleness ceiling {ceiling}s",
        )

    tick = len(history)
    previous = history[-1] if history else None
    previous_at = parse_iso(previous["ts"]) if previous else None

    beat = read_beat(root, rung)
    if beat:
        state = str(beat.get("state") or "")
        if state not in KNOWN_STATES:
            raise HeartbeatRefused(
                "unknown-state",
                f"rung {rung.name} beat state {state!r} is outside {KNOWN_STATES}; "
                "cannot attribute the wake",
            )
    else:
        state = NO_BEAT_STATE

    agent = agent_id or str(beat.get("agent") or "") or rung.name
    ts = str(beat.get("ts") or "")
    if not ts:
        if now is None:
            raise HeartbeatRefused(
                "no-timestamp",
                f"rung {rung.name} published no beat and no 'now' was supplied",
            )
        ts = now
    parse_iso(ts)

    events = read_ledger(root)
    issues = read_snapshot(root)
    scope = _scope(events, agent, beat.get("issue"))
    current = activity(issues, events, scope, parse_iso(ts))
    prior = activity(issues, events, scope, previous_at)
    delta = build_delta(
        previous=previous,
        previous_at=previous_at,
        current=current,
        prior=prior,
        state=state,
        tick=tick,
    )
    messages = read_inbox(root, rung, previous_at)
    cause = attribute_cause(delta, state, messages)
    outcome = decide_outcome(
        delta=delta,
        state=state,
        rung=rung,
        has_beat=bool(beat),
        owner=agent,
        tick=tick,
    )

    record = {
        "agent_id": agent,
        "session_id": session_id,
        "tick": tick,
        "cadence_seconds": cadence,
        "wake": {"cause": cause, "delta": delta},
        "outcome": outcome,
        "ts": ts,
    }
    findings = validate_heartbeat(record)
    if findings:
        raise HeartbeatRefused(
            "invalid-beat",
            "derived beat violates the frozen contract: " + "; ".join(findings),
        )
    return record


# ── the contract validator (the gate's subject) ─────────────────────────────


def load_schema(source_root: Path | None = None) -> dict[str, Any]:
    path = Path(source_root or ROOT) / SCHEMA_REL
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HeartbeatRefused("schema-unreadable", f"{SCHEMA_REL}: {exc}") from exc


_DATE_TIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$"
)


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _validate(instance: Any, schema: Mapping[str, Any], path: str, findings: list[str]) -> None:
    expected = schema.get("type")
    if expected is not None and not _type_ok(instance, expected):
        findings.append(f"{path}: expected {expected}, got {type(instance).__name__}")
        return
    if "enum" in schema and instance not in schema["enum"]:
        findings.append(f"{path}: value {instance!r} is outside the closed vocabulary {schema['enum']}")
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            findings.append(f"{path}: length {len(instance)} is below minLength {schema['minLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            findings.append(f"{path}: {instance!r} does not match pattern {schema['pattern']}")
        if schema.get("format") == "date-time" and not _DATE_TIME_RE.match(instance):
            findings.append(f"{path}: {instance!r} is not an ISO-8601 date-time")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            findings.append(f"{path}: {instance} is below minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            findings.append(f"{path}: {instance} is above maximum {schema['maximum']}")
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                findings.append(f"{path}: required field '{key}' is missing")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in instance:
                if key not in properties:
                    findings.append(f"{path}: unexpected field '{key}' (additionalProperties: false)")
        for key, sub in properties.items():
            if key in instance:
                _validate(instance[key], sub, f"{path}.{key}", findings)
    if isinstance(instance, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for index, element in enumerate(instance):
                _validate(element, items, f"{path}[{index}]", findings)


def validate_heartbeat(record: Any, source_root: Path | None = None) -> list[str]:
    """Every way a heartbeat can be refused, by name.

    Beyond the frozen schema this enforces the three rules the seam adds on top
    of it: a beat must carry a delta, a ``wake.cause`` must be in the closed
    vocabulary, and a ``blocked`` outcome must name an owner. Each finding names
    the offending field, so a gate can refuse it by name (GR-12).
    """
    findings: list[str] = []
    if not isinstance(record, dict):
        return ["$: a heartbeat must be a JSON object"]
    findings.extend(_schema_findings(source_root, record))

    wake = record.get("wake")
    delta = wake.get("delta") if isinstance(wake, dict) else None
    if not isinstance(delta, dict) or not delta:
        findings.append(
            "wake.delta: a beat that carries no delta is refused — a heartbeat must carry "
            "the change since the last beat"
        )
    cause = wake.get("cause") if isinstance(wake, dict) else None
    if cause not in WAKE_CAUSES:
        findings.append(f"wake.cause: {cause!r} is an unknown wake cause (want one of {WAKE_CAUSES})")

    outcome = record.get("outcome")
    status = outcome.get("status") if isinstance(outcome, dict) else None
    if status not in OUTCOME_STATUSES:
        findings.append(
            f"outcome.status: {status!r} ends in neither progress nor blocked (want one of {OUTCOME_STATUSES})"
        )
    if status == "blocked":
        owner = outcome.get("owner") if isinstance(outcome, dict) else None
        if not (isinstance(owner, str) and owner.strip()):
            findings.append("outcome.owner: a blocked outcome must name an owner")
    return findings


def _schema_findings(source_root: Path | None, record: Mapping[str, Any]) -> list[str]:
    try:
        schema = load_schema(source_root)
    except HeartbeatRefused as exc:
        return [f"$: {exc}"]
    findings: list[str] = []
    _validate(record, schema, "$", findings)
    return findings
