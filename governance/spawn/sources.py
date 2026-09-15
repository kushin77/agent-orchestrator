"""Where every envelope field comes from — one reader each, no second copy.

The envelope is only worth anything if its fields are read from the REAL
institutions rather than restated. So each field is read from the module that
already owns it:

======================  ==================================================
field                   source
======================  ==================================================
`issue`, `lane`         the directive
`worktree`, `session`,  `governance/isolation` — the session identity the lane
`trailer`               was minted with, including its commit trailer
`claim`                 the claim ledger (`governance/dispatch/claims.py`)
`focus`                 the issue's epic (board snapshot) + the pinned focus
`capacity`              `fleet/capacity.py` (the admission) + the gate permit
                        (`fleet/gatelock.py`)
`budget`                the attempt budget (`fleet/runaway.py`, issue #723)
`gate`                  the gate of record + AO-GR-22's one-gate bound
`verify`                the issue's own `Verify:` clause
======================  ==================================================

Nothing here decides anything: it reads, and it reports what it could not read
as an empty field, so `governance/spawn/model.py` refuses by name instead of
this module inventing a value.

Stdlib only, offline. The board snapshot and the claim ledger are committed (or
live on disk), so a spawn can be described without the network.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
for _extra in (ROOT, ROOT / "fleet", ROOT / "governance" / "dispatch"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

import capacity  # noqa: E402  (fleet/capacity.py — the fan-out admission)
import claims as claim_ledger  # noqa: E402  (governance/dispatch/claims.py)
import focus as focus_policy  # noqa: E402  (governance/dispatch/focus.py)
import gatelock  # noqa: E402  (fleet/gatelock.py — the gate permit, #724)
import runaway  # noqa: E402  (fleet/runaway.py — the attempt budget, #723)

#: The gate of record. Never inferred from prose.
GATE_OF_RECORD = "make verify"
#: The bound issue #793 was filed about: it was DECLARED in docs/GOLDEN-RULES.md
#: and enforced nowhere, so one lane ran 26 gates while every other lane ran one.
GATE_BOUND = "at most one composite gate per worktree, bounded box-wide (AO-GR-22)"
#: The entrypoint that enforces the bound (`scripts/verify.sh` calls it before it
#: discovers a check or touches `.verify/`).
GATE_ENTRY = "scripts/gate-lock.sh"
#: The lane a claim is recorded under when the spawn declares none.
DEFAULT_CLAIM_LANE = "fleet"

FOCUS_PATH = Path(".board") / "focus.json"
SNAPSHOT_PATH = Path(".board") / "snapshot.json"
CLAIMS_DIR = Path(".board") / "claims"
LEDGER_PATH = Path(".board") / "claims.jsonl"

#: A ``Verify:`` line is only executed when it is command-shaped: its first token
#: must be an executable the fleet can run. Issue bodies mix real commands with
#: prose ("Verify: the new test fails against today's code"), and running a
#: sentence as a command would manufacture the false verdict this code removes.
COMMAND_PREFIXES = frozenset(
    {
        "make", "bash", "sh", "python3", "python", "pytest", "node", "npm",
        "npx", "git", "gh", "go", "cargo", "terraform", "docker",
    }
)

#: ``Verify:`` on its own line, tolerating the markdown emphasis the issue bodies
#: actually use (``**Verify:**``) and a quoting backtick around the label.
_VERIFY_CLAUSE_RE = re.compile(r"^[\s>*_`-]*Verify:\s*(.*)$", re.IGNORECASE | re.MULTILINE)
_RUNNABLE_RE = re.compile(r"^[\s>*`-]*Verify:\s*(.*)$", re.IGNORECASE | re.MULTILINE)


# --- tolerant readers --------------------------------------------------------


def read_json(path: Path | str) -> Any:
    """Parse `path`, or None when it is missing, unreadable or not JSON."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _first_backtick_span(text: str) -> str | None:
    """The first non-empty ``` `...` ``` span in `text`, or None.

    The label's own closing backtick (`` `Verify:` ``) forms a blank pair, so a
    blank pair is skipped rather than returned — which is what lets both
    `` `Verify:` `cmd` `` and ``**Verify:** `cmd` plus prose`` yield ``cmd``.
    """
    index = text.find("`")
    while index != -1:
        end = text.find("`", index + 1)
        if end == -1:
            return None
        candidate = text[index + 1:end].strip()
        if candidate:
            return candidate
        index = text.find("`", end + 1)
    return None


def verify_text(body: str) -> str | None:
    """The issue's own ``Verify:`` clause as TEXT, for the envelope.

    Deliberately more tolerant than :func:`verify_command`, which decides whether
    a declared value is *runnable* and whose result a shell executes. Here the
    clause is carried to the subagent and the gate, so a body that writes
    ``**Verify:** ...`` or backticks the command and then adds prose still has
    its clause carried instead of silently dropped.
    """
    match = _VERIFY_CLAUSE_RE.search(body or "")
    if not match:
        return None
    raw = match.group(1)
    if not raw.strip():
        for line in (body or "")[match.end():].splitlines():
            if line.strip():
                raw = line
                break
    text = raw.strip()
    if not text:
        return None
    span = _first_backtick_span(text)
    if span:
        return span
    text = re.sub(r"^[\s*_`]+", "", text)
    text = re.sub(r"[\s*_`]+$", "", text)
    return text or None


def verify_command(body: str) -> str | None:
    """The issue's own ``Verify:`` command — only when it is command-shaped.

    A body that declares no runnable command (or declares prose) yields None, so
    the caller falls back to the gate of record rather than executing a sentence.
    """
    match = _RUNNABLE_RE.search(body or "")
    if not match:
        return None
    candidate = re.sub(r"^[`\s]+|[`\s]+$", "", match.group(1))
    if not candidate:
        for line in (body or "")[match.end():].splitlines():
            candidate = re.sub(r"^[`\s]+|[`\s]+$", "", line)
            if candidate:
                break
    if not candidate or "\n" in candidate:
        return None
    first = candidate.split()[0]
    if first not in COMMAND_PREFIXES and not first.startswith(("./", "/")):
        return None
    return candidate


def verify_field(body: str | None) -> dict[str, Any]:
    """The envelope's `verify` field: the clause, and which source produced it.

    It is never empty. An issue that declares no clause still carries the gate of
    record, because "no evidence was demanded" is not a thing a spawn may say.
    """
    if body:
        runnable = verify_command(body)
        if runnable:
            return {"command": runnable, "source": "issue-verify-clause"}
        text = verify_text(body)
        if text:
            return {"command": text, "source": "issue-verify-prose"}
    return {"command": GATE_OF_RECORD, "source": "gate-of-record"}


# --- the board ---------------------------------------------------------------


def snapshot_index(root: Path | str = ROOT) -> dict[int, dict[str, Any]]:
    """The committed board snapshot keyed by issue number ({} when unreadable)."""
    payload = read_json(Path(root) / SNAPSHOT_PATH)
    issues = payload.get("issues") if isinstance(payload, dict) else None
    index: dict[int, dict[str, Any]] = {}
    for entry in issues or []:
        number = entry.get("number") if isinstance(entry, dict) else None
        if isinstance(number, int) and not isinstance(number, bool):
            index[number] = entry
    return index


def focus_field(
    issue: int | None,
    *,
    root: Path | str = ROOT,
    snapshot: Mapping[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Issue -> epic -> lane, plus the pinned focus this spawn runs under.

    The issue's own epic is the first source (`governance/dispatch/claims.py`
    arbitrates the same relation before a claim is recorded, GR-20); the pinned
    focus is the fallback and is always reported alongside, so a spawn that
    serves an epic nobody pinned is visible rather than inferred.
    """
    index = dict(snapshot) if snapshot is not None else snapshot_index(root)
    try:
        pinned = focus_policy.load(Path(root) / FOCUS_PATH)
    except focus_policy.FocusInvalid:
        # A malformed focus is a finding for the epic-focus check, not a reason
        # for a spawn to invent an epic. It is reported as "no focus" here and
        # the refusal (if any) names the field.
        pinned = None
    entry = index.get(issue) if isinstance(issue, int) else None
    parent = entry.get("parent") if isinstance(entry, Mapping) else None
    if isinstance(parent, int) and not isinstance(parent, bool) and parent > 0:
        epic, source = parent, "board-snapshot-parent"
    elif pinned is not None and isinstance(pinned.active_epic, int):
        epic, source = pinned.active_epic, "pinned-focus"
    else:
        epic, source = None, "none"
    return {
        "epic": epic,
        "source": source,
        "pinned_epic": pinned.active_epic if pinned is not None else None,
        "pinned_at": pinned.activated_at if pinned is not None else "",
        "wave_cap": pinned.wave_cap if pinned is not None else None,
        "max_agents": pinned.max_agents if pinned is not None else None,
    }


# --- identity, claim, permit, budget, gate -----------------------------------


def session_field(env: Mapping[str, str] | None) -> dict[str, Any]:
    """The session identity `governance/isolation` exports, as the envelope's own.

    Read from the environment rather than re-minted: the lane's signature was
    already written into that worktree's config by the minting step, and a second
    mint would be a second identity for one lane — the exact violation
    `scripts/check-session-isolation.sh` refuses.
    """
    source = dict(env or {})
    issue = source.get("AO_ISSUE", "")
    slug = source.get("AO_REPO_SLUG", "")
    return {
        "id": source.get("AO_SESSION_ID", ""),
        "issue": issue,
        "agent": source.get("AO_AGENT_ID", ""),
        "lane": source.get("AO_LANE", ""),
        "branch": source.get("AO_BRANCH", ""),
        "worktree": source.get("AO_WORKTREE", ""),
        "repo_slug": slug,
        "author_name": source.get("GIT_AUTHOR_NAME", ""),
        "author_email": source.get("GIT_AUTHOR_EMAIL", ""),
    }


def trailer_field(issue: int | None, repo_slug: str) -> str:
    """The trailer every commit in this lane must carry (GR-15 / issue #263)."""
    if not repo_slug or not isinstance(issue, int) or isinstance(issue, bool):
        return ""
    return f"Refs {repo_slug}#{issue}"


def claim_field(issue: int | None, *, root: Path | str = ROOT) -> dict[str, Any]:
    """Who holds issue #issue in the claim ledger, or an empty claim.

    Read from the SAME ledger `governance/dispatch/cli.py claim` writes, so the
    envelope cannot claim an ownership the ledger does not record — and an
    unclaimed spawn is refused by name rather than spawned unowned.
    """
    if not isinstance(issue, int) or isinstance(issue, bool):
        return {"owner": "", "state": "", "lane": "", "at": ""}
    base = Path(root)
    events = claim_ledger.read_ledger(base / CLAIMS_DIR)
    active = claim_ledger.active_claims(events)
    event = active.get(issue)
    if event is None:
        return {"owner": "", "state": "unclaimed", "lane": "", "at": ""}
    return {
        "owner": event.agent,
        "state": event.event,
        "lane": event.lane,
        "at": event.at,
        "directive": event.directive_id,
    }


def gate_field(worktree: str) -> dict[str, Any]:
    """The gate bound this spawn is subject to, and the permit it will take.

    Before #793 this bound existed only as a sentence in a doc, so nothing could
    refuse the 26th gate in one worktree. Here it is carried by every spawn, and
    `scripts/check-spawn-envelope.sh` provokes a REAL second gate in the
    worktree the envelope names and requires it to be refused.
    """
    return {
        "of_record": GATE_OF_RECORD,
        "bound": GATE_BOUND,
        "entry": GATE_ENTRY,
        "max_concurrent": gatelock.max_concurrent(),
        "ttl_seconds": gatelock.ttl_seconds(),
    }


def budget_field(directive_id: str, *, base: Path | str | None = None) -> dict[str, Any]:
    """The attempt budget that bounds this spawn (issue #723, AO-GR-21).

    A directive with no failed attempt yet is a budget that has not been spent,
    not an absent budget: it reports the cap and zero attempts. The store is the
    env-aware one (`AO_FLEET_DIR`, default `<repo>/.fleet`), so a spawn reads the
    same counters the runaway guard writes.
    """
    cap = runaway.cap_or_default()
    if not directive_id:
        return {"attempts": 0, "cap": cap, "state": "pending", "next_attempt_at": None}
    record = runaway.load(directive_id, base=base)
    if record is None:
        return {"attempts": 0, "cap": cap, "state": "pending", "next_attempt_at": None}
    return {
        "attempts": record.attempts,
        "cap": record.cap,
        "state": record.state,
        "next_attempt_at": record.next_attempt_at,
    }


def capacity_field(
    *,
    lane: str,
    issue: int | None,
    worktree: str,
    env: Mapping[str, str] | None = None,
    files: Sequence[str] | None = None,
    root: Path | str = ROOT,
) -> dict[str, Any]:
    """The admission this spawn was granted, plus the gate permit it holds.

    Two bounds, one object, because a spawn that cannot be carried is not a
    spawn: the fan-out ceiling (`fleet/capacity.py`) decides whether the box can
    take another lane, and the permit (`fleet/gatelock.py`) is the one composite
    gate that lane may run. `assessed` is part of the document on purpose — an
    UNMEASURABLE ceiling is refused, never rounded up to "probably fine".
    """
    lane_id = lane or (f"#{issue}" if issue else "unnamed-lane")
    lane_obj = capacity.Lane(id=lane_id, issue=issue, files=tuple(files or ()) or None)
    resolved: capacity.Capacity | None = None
    problems: tuple[str, ...] = ()
    try:
        resolved = capacity.resolve_capacity(
            ready=(lane_obj,), env=env, focus_path=Path(root) / FOCUS_PATH
        )
    except capacity.CapacityConfigError as exc:
        problems = (str(exc),)
    permit = {
        "store": str(gatelock.store_root()),
        "worktree_key": gatelock.worktree_key(worktree),
        "lock": str(gatelock.worktree_lock_path(worktree)),
        "max_concurrent": gatelock.max_concurrent(),
    }
    if resolved is None:
        return {
            "effective": 0,
            "binding": "cannot-assess",
            "assessed": False,
            "bounds": [],
            "problems": list(problems),
            "permit": permit,
        }
    return {
        "effective": resolved.effective,
        "binding": resolved.binding,
        "assessed": resolved.assessed,
        "line": resolved.line(),
        "bounds": [
            {"name": bound.name, "limit": bound.limit, "why": bound.why}
            for bound in resolved.bounds
        ],
        "problems": list(resolved.problems),
        "permit": permit,
    }


# --- the collection ----------------------------------------------------------


def collect(
    *,
    issue: int | None,
    lane: str = "",
    agent_id: str = "",
    path: str = "fleet",
    directive_id: str = "",
    env: Mapping[str, str] | None = None,
    worktree: str = "",
    body: str | None = None,
    files: Sequence[str] | None = None,
    root: Path | str = ROOT,
    snapshot: Mapping[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Every field the envelope needs, read from its owner. Never a guess.

    The caller supplies only what it already holds (the directive's issue/lane,
    the minted worktree and env, the issue body). Everything else is read here,
    so the remote loop and a local spawn cannot disagree about what a spawn
    carries — they call this same function.
    """
    session = session_field(env)
    repo_slug = session.get("repo_slug") or ""
    resolved_worktree = worktree or str(session.get("worktree") or "")
    return {
        "issue": issue,
        "lane": lane or str(session.get("lane") or ""),
        "worktree": resolved_worktree,
        "session": session,
        "trailer": trailer_field(issue, repo_slug),
        "claim": claim_field(issue, root=root),
        "focus": focus_field(issue, root=root, snapshot=snapshot),
        "capacity": capacity_field(
            lane=lane or str(session.get("lane") or ""),
            issue=issue,
            worktree=resolved_worktree,
            env=env,
            files=files,
            root=root,
        ),
        "budget": budget_field(directive_id),
        "gate": gate_field(resolved_worktree),
        "verify": verify_field(body),
        "spawn": {
            "path": path,
            "agent": agent_id or str(session.get("agent") or ""),
            "directive": directive_id,
        },
    }


def worktree_files(raw: Any) -> tuple[str, ...]:
    """The file set a directive declared for its lane (`task.files`), or ().

    Same normalisation `fleet/capacity.py` uses, so the envelope's capacity
    decision and the dispatcher's disjointness bound read one shape.
    """
    return tuple(capacity.declared_files(raw) or ())


def env_root(root: Path | str | None = None) -> Path:
    """The repository root a caller means: `root` when given, else this checkout.

    Deliberately NOT read from the environment: `AO_FLEET_DIR` names the fleet's
    RUNTIME directory (run markers, attempts, the dead-letter store), not the
    checkout a board and a ledger live in, and conflating the two would let a
    spawn read one repository's claims while writing another's worktree.
    """
    return Path(root) if root is not None else ROOT
