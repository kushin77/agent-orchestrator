"""The ACTIVE-EPIC resolver — single-epic-focused BAU (epic #707, lane F1/#716).

---knowledge---
module_id: governance.dispatch.focus
system: governance
app: dispatch
solution_class: enterprise
patterns: [no-false-green, fail-closed, offline-hermetic, bounded-work]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [FocusInvalid, Focus, validate_schema, load, save, resolve, self_heal, active, open_children, pooled, (+1 more)]
invariants: ""
gotchas: ""
related: ["#707", "#716", "#718", "#719", "#900", "#1189"]
do_not_duplicate: null
---knowledge---

The chronological-dispatch rule (GR-20) makes dispatch *ordered*; this module
makes it *coherent*: at any moment the fleet focuses on exactly ONE epic, works
its children, and only when that epic closes does it move on. A milestone
frontier can interleave work from several epics and never drives any of them
end-to-end; the focus resolver prevents that.

Resolution rule (the contract every other lane builds on):

1. the **pinned** epic named in ``.board/focus.json`` while it is still open;
2. else the **lowest-numbered** open ``type:epic`` whose ``Blocked-by`` blockers
   are all closed;
3. else ``None`` — the board has no workable epic.

Everything here is stdlib-only and offline: the board state is the committed
``.board/snapshot.json`` and the pinned focus is ``.board/focus.json``. A
malformed focus file is *refused* (``FocusInvalid``), never silently defaulted,
so a corrupt board cannot quietly disable the focus.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from model import Issue, Snapshot

DEFAULT_PATH = Path(".board/focus.json")

#: Default number of micro-children one decomposition wave may mint (lane F4/#719).
DEFAULT_WAVE_CAP = 12
#: ``0`` means "the pool" — lane F3/#718 resolves it against the capacity ceiling.
DEFAULT_MAX_AGENTS = 0

#: The required shape of ``.board/focus.json``. ``bool`` is *not* an ``int`` here.
FOCUS_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "board focus",
    "type": "object",
    "required": ["active_epic", "activated_at", "wave_cap", "max_agents", "pooled"],
    "additionalProperties": False,
    "properties": {
        "active_epic": {"type": ["integer", "null"]},
        "activated_at": {"type": "string"},
        "wave_cap": {"type": "integer", "minimum": 1},
        "max_agents": {"type": "integer", "minimum": 0},
        "pooled": {"type": "array", "items": {"type": "integer"}},
    },
}


class FocusInvalid(ValueError):
    """``.board/focus.json`` is absent-where-required, malformed, or out of range."""


def _is_int(value: Any) -> bool:
    """A real integer, never a ``bool`` (``True`` is an ``int`` in Python)."""
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True)
class Focus:
    """The pinned focus: which epic the fleet is driving, and its wave budget."""

    active_epic: int | None = None
    activated_at: str = ""
    wave_cap: int = DEFAULT_WAVE_CAP
    max_agents: int = DEFAULT_MAX_AGENTS
    pooled: tuple[int, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "active_epic": self.active_epic,
            "activated_at": self.activated_at,
            "wave_cap": self.wave_cap,
            "max_agents": self.max_agents,
            "pooled": list(self.pooled),
        }

    @classmethod
    def from_json(cls, obj: Any, where: str = "focus") -> "Focus":
        """Validate ``obj`` against the focus schema; raise ``FocusInvalid``."""
        if not isinstance(obj, dict):
            raise FocusInvalid(f"{where}: must be a JSON object, got {type(obj).__name__}")
        problems = validate_schema(obj)
        if problems:
            raise FocusInvalid(f"{where}: {problems[0]}")
        return cls(
            active_epic=obj["active_epic"],
            activated_at=obj["activated_at"],
            wave_cap=obj["wave_cap"],
            max_agents=obj["max_agents"],
            pooled=tuple(obj["pooled"]),
        )

    @classmethod
    def pinned(cls, epic: int | None, now: datetime | None = None) -> "Focus":
        """A fresh focus pinning ``epic`` (stamped now)."""
        moment = now or datetime.now(timezone.utc)
        return cls(active_epic=epic, activated_at=moment.strftime("%Y-%m-%dT%H:%M:%SZ"))


def validate_schema(obj: Any) -> list[str]:
    """Structural validation equivalent to ``FOCUS_SCHEMA`` (stdlib, offline)."""
    if not isinstance(obj, dict):
        return [f"focus must be a JSON object, got {type(obj).__name__}"]
    problems: list[str] = []
    for key in ("active_epic", "activated_at", "wave_cap", "max_agents", "pooled"):
        if key not in obj:
            problems.append(f"missing required field '{key}'")
    extra = sorted(set(obj) - set(FOCUS_SCHEMA["properties"]))
    for key in extra:
        problems.append(f"unexpected field '{key}'")
    if problems:
        return problems
    if not (obj["active_epic"] is None or _is_int(obj["active_epic"])):
        problems.append("field 'active_epic' must be an integer or null")
    elif _is_int(obj["active_epic"]) and obj["active_epic"] <= 0:
        problems.append("field 'active_epic' must be a positive issue number")
    if not isinstance(obj["activated_at"], str):
        problems.append("field 'activated_at' must be a string")
    if not _is_int(obj["wave_cap"]):
        problems.append("field 'wave_cap' must be an integer")
    elif obj["wave_cap"] < 1:
        problems.append("field 'wave_cap' must be >= 1")
    if not _is_int(obj["max_agents"]):
        problems.append("field 'max_agents' must be an integer")
    elif obj["max_agents"] < 0:
        problems.append("field 'max_agents' must be >= 0")
    if not isinstance(obj["pooled"], list):
        problems.append("field 'pooled' must be an array")
    elif any(not _is_int(n) for n in obj["pooled"]):
        problems.append("field 'pooled' must contain only issue numbers")
    return problems


def load(path: Path | str = DEFAULT_PATH, *, required: bool = False) -> Focus | None:
    """Read the pinned focus. ``None`` when the file is absent (nothing pinned).

    Raises ``FocusInvalid`` when the file exists but is malformed, or when it is
    absent and ``required`` — an unreadable focus is never treated as "no focus".
    """
    target = Path(path)
    if not target.exists():
        if required:
            raise FocusInvalid(f"{target} is missing")
        return None
    try:
        obj = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FocusInvalid(f"{target}: unreadable ({exc})") from exc
    return Focus.from_json(obj, where=str(target))


def save(focus: Focus, path: Path | str = DEFAULT_PATH) -> Path:
    """Write the pinned focus atomically (tmp + rename) and return the path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(focus.to_json(), indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)
    return target


def resolve(snapshot: Snapshot, pinned: int | None = None) -> Issue | None:
    """The active epic: the pinned one while open, else the lowest open epic.

    A pinned epic that has closed (or was never an epic) does not stick — the
    fleet advances to the next workable epic instead of focusing on nothing.
    """
    if pinned is not None:
        issue = snapshot.get(pinned)
        if issue is not None and not issue.closed and issue.is_epic:
            return issue
    candidates = [issue for issue in snapshot.open_issues() if issue.is_epic and not snapshot.blockers_open(issue)]
    candidates.sort(key=lambda issue: issue.number)
    return candidates[0] if candidates else None


#: The committed ``.board/focus.json`` can rot the same way the committed
#: ``.board/snapshot.json`` does (issue #1717, mirroring #1692/#1189): its pin
#: is a point-in-time GitHub write nobody commits. 72h matches the tolerance
#: this repo already declares for the sibling artifact (queue_freshness.py /
#: ticket/freshness.py DEFAULT_MAX_AGE_HOURS) — same artifact class, same bound.
STALE_TOLERANCE_HOURS = 72.0

#: The ONE refresh verb this self-heal names (RCA-0014: the remedy travels
#: with the finding).
REFRESH_COMMAND = "python3 governance/dispatch/cli.py focus --from-github"


def _pin_stale(loaded: Focus, snapshot: Snapshot, now: datetime | None) -> bool:
    """The committed pin is stale: its epic closed, or its stamp is too old."""
    if loaded.active_epic is None:
        return False
    epic = snapshot.get(loaded.active_epic)
    if epic is None or epic.closed or not epic.is_epic:
        return True
    try:
        stamp = datetime.strptime(loaded.activated_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    moment = now or datetime.now(timezone.utc)
    age_hours = (moment - stamp).total_seconds() / 3600.0
    return age_hours > STALE_TOLERANCE_HOURS


def _refresh(path: Path | str) -> tuple[bool, str]:
    """Run the ONE refresh verb as a subprocess (shared shape with #1692)."""
    import subprocess
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from governance import board_selfheal

    cli_path = Path(__file__).resolve().parent / "cli.py"
    return board_selfheal.refresh(command=[sys.executable, str(cli_path), "focus", "--from-github"])


def self_heal(snapshot: Snapshot, path: Path | str = DEFAULT_PATH, *, now: datetime | None = None) -> tuple[bool, str]:
    """Refresh the committed ``.board/focus.json`` in place when its pin is stale
    (issue #1717, mirroring the ``.board/snapshot.json`` self-heal, #1692).

    A caller runs this ONCE, before reading the focus for a real decision (the
    CLI entry points do, right after loading the board snapshot). A fresh, or
    unpinned, focus is untouched: ``(True, "")``. A stale pin runs the ONE
    refresh verb and overwrites ``path`` with the live active epic on success:
    ``(True, detail)``. When refresh is impossible (offline, no ``gh`` auth,
    ...) this fails closed, by name: ``(False, detail)`` — the caller decides
    how to surface that as CANNOT-ASSESS, never silently falls through to
    "no focus, everything pooled".
    """
    loaded = load(path)
    if loaded is None or not _pin_stale(loaded, snapshot, now):
        return True, ""
    ok, detail = _refresh(path)
    if not ok:
        return False, (
            f"pinned epic #{loaded.active_epic} is stale and could not be refreshed "
            f"({detail}); refresh it with: {REFRESH_COMMAND}"
        )
    return True, detail


def active(snapshot: Snapshot, path: Path | str = DEFAULT_PATH, *, pinned: int | None = None) -> Issue | None:
    """Resolve the active epic from the pinned focus at ``path`` (or ``pinned``)."""
    if pinned is None:
        focus = load(path)
        pinned = focus.active_epic if focus is not None else None
    return resolve(snapshot, pinned)


def open_children(snapshot: Snapshot, epic: int | None) -> list[Issue]:
    """The epic's still-open children (empty when there is no epic)."""
    if epic is None:
        return []
    return [issue for issue in snapshot.children_of(epic) if not issue.closed]


def pooled(snapshot: Snapshot, epic: int | None) -> list[Issue]:
    """Open, non-epic issues outside the active epic — the pooled queue (lane F6).

    The active epic itself and its children are excluded; epics are never work.
    With no active epic the whole non-epic board is pooled (focus == None drains
    the pool by construction).
    """
    result = []
    for issue in snapshot.open_issues():
        if issue.is_epic:
            continue
        if epic is not None and issue.number == epic:
            continue
        if epic is not None and issue.parent == epic:
            continue
        result.append(issue)
    result.sort(key=lambda issue: issue.number)
    return result


def _control_snapshot() -> Snapshot:
    """A synthetic board for the anti-formality self-control."""
    def issue(number: int, *, epic: bool = False, closed: bool = False, parent: int | None = None,
              blocked_by: tuple[int, ...] = ()) -> Issue:
        return Issue(
            number=number,
            title=f"synthetic #{number}",
            state="closed" if closed else "open",
            labels=("type:epic",) if epic else ("type:task",),
            parent=parent,
            blocked_by=blocked_by,
        )

    issues = [
        issue(900, epic=True),               # lowest open epic
        issue(901, epic=True, blocked_by=(910,)),
        issue(902, epic=True, closed=True),
        issue(910),                          # an open blocker of 901
        issue(9001, parent=900),             # a child of epic 900
        issue(9101),                         # out-of-epic work
    ]
    return Snapshot(generated_at="2026-01-01T00:00:00Z", source="self-control",
                    issues={i.number: i for i in issues})


def self_control() -> list[str]:
    """Prove the resolver and the schema checker can fail (anti-formality).

    Every mutant must be caught and every positive control must be clean; a
    mismatch is returned as a problem, so the gate fails: a resolver that only
    ever returns an id is a formality (GR-12 / AO-GR-19).
    """
    problems: list[str] = []
    snapshot = _control_snapshot()

    def expect(name: str, condition: bool, detail: str) -> None:
        if not condition:
            problems.append(f"self-control['{name}']: {detail}")

    picked = resolve(snapshot)
    expect("lowest-open-epic", picked is not None and picked.number == 900,
           f"expected #900, got {picked.number if picked else None}")

    expect("pinned-still-open", getattr(resolve(snapshot, pinned=901), "number", None) in (901,),
           "a pinned open epic must stay active even when a lower epic is open")

    expect("pinned-closed-falls-back", getattr(resolve(snapshot, pinned=902), "number", None) == 900,
           "a pinned but closed epic must fall back to the lowest workable epic")

    expect("blocked-epic-skipped", getattr(resolve(snapshot), "number", None) != 901,
           "an epic with an open blocker must never be selected")

    # The mutant that matters: a resolver returning a non-open / non-epic id.
    bad = resolve(snapshot, pinned=910)  # 910 is open but NOT an epic
    expect("pinned-non-epic-refused", bad is None or bad.number != 910,
           "a pinned non-epic id must never be returned as the active epic")
    closed = resolve(snapshot, pinned=902)
    expect("never-closed", closed is None or not closed.closed,
           "a closed epic must never be returned")

    # No epic at all -> None (never a fabricated id).
    empty = Snapshot(generated_at="2026-01-01T00:00:00Z", source="self-control",
                     issues={910: snapshot.get(910)})
    expect("no-epic-none", resolve(empty) is None, "with no open epic the resolver must return None")

    children = open_children(snapshot, 900)
    expect("children", [i.number for i in children] == [9001], f"expected [9001], got {[i.number for i in children]}")
    pooled_numbers = [i.number for i in pooled(snapshot, 900)]
    expect("pooled-excludes-epic-and-child", pooled_numbers == [910, 9101],
           f"unexpected pooled set {pooled_numbers}")

    # Schema mutants: each malformed focus must be refused, never defaulted.
    good = {"active_epic": 900, "activated_at": "2026-01-01T00:00:00Z", "wave_cap": 12, "max_agents": 0, "pooled": []}
    expect("schema-accepts-valid", validate_schema(good) == [], f"valid focus rejected ({validate_schema(good)})")
    mutants = {
        "missing-active_epic": {k: v for k, v in good.items() if k != "active_epic"},
        "extra-field": {**good, "bogus": 1},
        "bool-cap": {**good, "wave_cap": True},
        "zero-cap": {**good, "wave_cap": 0},
        "str-epic": {**good, "active_epic": "900"},
        "pooled-str": {**good, "pooled": ["900"]},
        "negative-max": {**good, "max_agents": -1},
    }
    for name, mutant in mutants.items():
        expect(f"schema-rejects-{name}", validate_schema(mutant) != [], f"mutant '{name}' passed validation")
        try:
            Focus.from_json(mutant, where=name)
        except FocusInvalid:
            pass
        else:
            problems.append(f"self-control['from_json-{name}']: malformed focus was accepted")
    return problems
