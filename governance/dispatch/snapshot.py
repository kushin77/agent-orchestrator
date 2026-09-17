"""Board snapshot: the committed board state a claim is validated against.

The gate of record runs offline, so it cannot call GitHub. Instead the board
state is a tracked artifact, `.board/snapshot.json`, refreshed explicitly with
``python3 governance/dispatch/cli.py snapshot --from-github`` (the only
network-touching path in this package).

Dependency edges are read from an explicit, documented convention in the issue
body, so a chain is declared rather than guessed:

    Parent: #152          -> this issue is a child of #152
    Part-of: #152         -> same edge, alternate spelling
    Blocked-by: #9, #10   -> this issue cannot start until those are closed
    Blocked-by: kushin77/code-indexing#128
                          -> cross-repo edge, captured as ``cross_refs`` (a
                             foreign board is not gated by this snapshot)

An issue with no declared edges is still eligible as the milestone frontier
(rule "next-in-milestone"); it is never eligible merely because it is visible.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# The fleet's runtime directory is the default HOME of the deferred queue
# (`<fleet>/parked/`), so a park sits beside the runaway guard's dead-letter
# store — the same reason `claims.py` reads `runtime` for its sent mailbox.
if str(ROOT / "fleet") not in sys.path:
    sys.path.insert(0, str(ROOT / "fleet"))

from governance.policy import lease  # noqa: E402
import runtime  # noqa: E402
from model import Issue, Snapshot  # noqa: E402

DEFAULT_PATH = Path(".board/snapshot.json")

#: The repo board a snapshot is refreshed from by default.
DEFAULT_REPO = "kushin77/agent-orchestrator"

# A snapshot older than this is refused as stale (issue #170): the board moves
# faster than an hour-old artifact, and answering confidently from stale data is
# the failure mode the gate exists to prevent. Declared once in
# governance/policy/lease.py.
DEFAULT_STALENESS_MINUTES = lease.SNAPSHOT_STALENESS_MINUTES

_PARENT_RE = re.compile(r"^\s*(?:parent|part[-_ ]of)\s*:\s*#?([0-9]+(?:\s*,\s*#?[0-9]+)*)", re.I | re.M)
_BLOCKED_RE = re.compile(r"^\s*blocked[-_ ]by\s*:\s*#?([0-9]+(?:\s*,\s*#?[0-9]+)*)", re.I | re.M)
_CROSS_REPO_RE = re.compile(r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)#([0-9]+)")
_EDGE_LINE_RE = re.compile(
    r"^[ \t]*(?:parent|part[-_ ]of|blocked[-_ ]by)[ \t]*:[ \t]*(.+?)[ \t]*$", re.I | re.M
)


def _numbers(blob: str) -> list[int]:
    return [int(part) for part in re.findall(r"[0-9]+", blob)]


def parse_edges(body: str) -> tuple[int | None, tuple[int, ...], tuple[str, ...]]:
    """Extract (parent, blocked_by, cross_refs) from an issue body.

    Same-repo edges are unchanged: ``Parent: #152`` and ``Blocked-by: #9, #10``
    yield integer references. A cross-repo reference written ``owner/repo#N``
    (e.g. ``Blocked-by: kushin77/code-indexing#128``) is captured separately in
    ``cross_refs`` and is never coerced into a same-repo number — the foreign
    board is not in this snapshot and cannot be gated here. Cross-repo refs are
    read from ``Parent:``/``Blocked-by:`` lines only, so an issue body that
    merely *mentions* another repo does not acquire chain edges.
    """
    text = body or ""
    parent: int | None = None
    match = _PARENT_RE.search(text)
    if match:
        numbers = _numbers(match.group(1))
        if numbers:
            parent = numbers[0]
    blocked: list[int] = []
    for match in _BLOCKED_RE.finditer(text):
        for number in _numbers(match.group(1)):
            if number not in blocked and number != parent:
                blocked.append(number)

    cross: list[str] = []
    for match in _EDGE_LINE_RE.finditer(text):
        for owner, repo, number in _CROSS_REPO_RE.findall(match.group(1)):
            canonical = f"{owner}/{repo}#{number}"
            if canonical not in cross:
                cross.append(canonical)
    return parent, tuple(sorted(blocked)), tuple(sorted(cross))


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 UTC timestamp (``2026-09-13T17:36:28Z``); naive input is UTC."""
    text = (value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_minutes(snapshot: Snapshot, now: datetime | None = None) -> float:
    """Age of the snapshot in minutes (wall-clock now minus ``generated_at``).

    An empty or unparseable ``generated_at`` reads as ``inf``: fail closed, a
    snapshot whose age cannot be established is never trusted.
    """
    try:
        generated = parse_iso(snapshot.generated_at)
    except ValueError:
        return float("inf")
    moment = now or datetime.now(timezone.utc)
    return max(0.0, (moment - generated).total_seconds() / 60.0)


def is_stale(
    snapshot: Snapshot,
    threshold_minutes: int = DEFAULT_STALENESS_MINUTES,
    now: datetime | None = None,
) -> bool:
    """True when the snapshot is strictly older than ``threshold_minutes``."""
    return age_minutes(snapshot, now) > threshold_minutes


def build_snapshot(records: Iterable[dict[str, Any]], source: str, generated_at: str | None = None) -> Snapshot:
    """Build a Snapshot from GitHub-shaped issue records (``gh issue list --json``)."""
    issues: dict[int, Issue] = {}
    for record in records:
        number = int(record["number"])
        parent, blocked, cross_refs = parse_edges(str(record.get("body", "") or ""))
        milestone = record.get("milestone") or {}
        milestone_title = milestone.get("title", "") if isinstance(milestone, dict) else str(milestone or "")
        labels = record.get("labels") or []
        label_names = tuple(
            sorted(str(label.get("name", "")) if isinstance(label, dict) else str(label) for label in labels)
        )
        issues[number] = Issue(
            number=number,
            title=str(record.get("title", "") or ""),
            state=str(record.get("state", "open") or "open"),
            milestone=milestone_title,
            labels=label_names,
            parent=parent,
            blocked_by=blocked,
            cross_refs=cross_refs,
            closed_at=str(record.get("closedAt") or ""),
        )
    return Snapshot(generated_at=generated_at or now_iso(), source=source, issues=issues)


def github_records(
    repo: str,
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
    timeout: float | None = None,
) -> list[dict[str, Any]]:
    """Fetch issue records with ``gh`` (network). Raises RuntimeError on failure.

    ``timeout`` is the refresh's bounded window (issue #727): a ``gh`` that
    hangs must not hang the loop that ordered the work, so an expired call is
    reported as a RuntimeError like any other refresh failure — a first-class
    outcome, never an unhandled crash. ``None`` keeps the historical unbounded
    call for the callers that pass no window.
    """
    run = runner or subprocess.run
    cmd = [
        "gh",
        "issue",
        "list",
        "--repo",
        repo,
        "--state",
        "all",
        "--limit",
        "1000",
        "--json",
        "number,title,state,milestone,labels,body,closedAt",
    ]
    kwargs: dict[str, Any] = {"capture_output": True, "text": True}
    if timeout is not None:
        kwargs["timeout"] = timeout
    try:
        result = run(cmd, **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"gh issue list exceeded the {timeout}s refresh window (bounded trigger)"
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(f"gh issue list failed ({result.returncode}): {result.stderr.strip()}")
    try:
        records = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise RuntimeError(f"gh issue list returned invalid JSON: {exc}") from exc
    if not isinstance(records, list):  # pragma: no cover - defensive
        raise RuntimeError("gh issue list returned a non-list payload")
    return records


def save(snapshot: Snapshot, path: Path | str = DEFAULT_PATH) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(snapshot.to_json(), indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return target


def load(path: Path | str = DEFAULT_PATH, apply_queue: bool = True) -> Snapshot:
    """Load the committed snapshot. Raises FileNotFoundError/ValueError if unusable.

    ``apply_queue`` overlays the owner's committed dispatch queue (#928,
    ``governance/dispatch/owner_queue.py``) into ``Issue.blocked_by`` before
    returning — this is THE seam every caller shares (``cli.py``'s verbs and
    ``fleet/brain.py``'s live dispatch loop alike both call ``snapshot.load``
    directly), so the queue's implied order cannot be bypassed by a caller
    that loads the board its own way. Pass ``False`` only for a caller that
    must see the raw, un-overlaid board (e.g. re-serializing it unchanged).
    """
    target = Path(path)
    raw = target.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("issues"), list):
        raise ValueError(f"{target}: snapshot must be an object with an 'issues' list")
    issues: dict[int, Issue] = {}
    for entry in data["issues"]:
        if not isinstance(entry, dict) or "number" not in entry:
            raise ValueError(f"{target}: every snapshot issue needs a 'number'")
        number = int(entry["number"])
        blocked = entry.get("blocked_by") or []
        cross_refs = entry.get("cross_refs") or []
        issues[number] = Issue(
            number=number,
            title=str(entry.get("title", "") or ""),
            state=str(entry.get("state", "open") or "open"),
            milestone=str(entry.get("milestone", "") or ""),
            labels=tuple(str(label) for label in (entry.get("labels") or [])),
            parent=int(entry["parent"]) if entry.get("parent") is not None else None,
            blocked_by=tuple(sorted(int(number) for number in blocked)),
            cross_refs=tuple(sorted(str(ref) for ref in cross_refs)),
        )
    snapshot = Snapshot(
        generated_at=str(data.get("generated_at", "") or ""),
        source=str(data.get("source", "") or ""),
        issues=issues,
    )
    if apply_queue:
        import owner_queue  # local import: avoids a load-time cycle (#928)

        snapshot = owner_queue.overlay(snapshot, owner_queue.load())
    return snapshot


def content_sha256(path: Path | str = DEFAULT_PATH) -> str:
    """Hash of the snapshot file, recorded on every claim as provenance."""
    digest = hashlib.sha256()
    digest.update(Path(path).read_bytes())
    return digest.hexdigest()


# --- the stale-snapshot trigger (issue #727) ---------------------------------
#
# WHY THIS EXISTS (measured)
# --------------------------
# ``claims`` refuses a claim validated against a stale snapshot with
# ``snapshot-stale``, and it prints the remedy ("refresh first: ... snapshot
# --from-github") — but nothing ever RAN the remedy. The consumer had no trigger
# contract for that refusal, so a stale snapshot degenerated into a per-cycle
# re-dispatch of the very directive that could not be claimed: the runaway the
# epic #708 exists to bound. A fail-closed refusal is only half a control — name
# the trigger, or park the work.
#
# THE CONTRACT
# ------------
# On ``snapshot-stale`` for a directive:
#
#   1. exactly ONE refresh is attempted, inside a bounded window
#      (``TRIGGER_WINDOW_SECONDS`` bounds the ``gh`` call, and the PARK below is
#      what makes "one" true instead of "one per cycle" — a parked directive is
#      never refreshed again);
#   2. if that refresh does not clear the staleness, the directive is PARKED in
#      the deferred queue (``<fleet>/parked/<directive>.json``) and is not
#      re-dispatched until freshness returns — ``channel watch`` holds it
#      exactly as it already holds a backoff or a dead letter;
#   3. the transition is reported once, naming the snapshot's ``generated_at``
#      and the threshold it tripped.
#
# A PARK IS NOT A DEAD LETTER. The dead letter retires an order FOR EVER and
# MOVES it out of the inbox; a park is the operator's LIVE order waiting on the
# board to move, so the envelope STAYS in the inbox and the park file is only
# the hold. Freshness returning releases it with no operator action — a park is
# a state, not a verdict.
#
# The refresh is the only network-touching step and it is injectable
# (``runner=``), so the whole contract is provable offline; a refused network is
# a first-class OUTCOME (``refresh`` returns False with the reason) and never an
# unhandled crash.

#: How long the ONE refresh may take before it is abandoned — the bounded
#: window. A ``gh`` that hangs must not hang the loop that ordered the work.
TRIGGER_WINDOW_SECONDS = 60

#: The deferred queue's directory name, beside the runaway guard's dead-letter.
PARKED_DIRNAME = "parked"

#: The trigger's outcome for one directive.
ACTION_FRESH = "fresh"          # nothing to do: the snapshot is fresh
ACTION_REFRESHED = "refreshed"  # the ONE refresh cleared the staleness
ACTION_PARKED = "parked"        # refreshed once, still stale — deferred
ACTION_HELD = "held"            # already parked, still stale — still deferred
ACTION_RESUMED = "resumed"      # was parked, the snapshot is fresh again

TRIGGER_ACTIONS = (
    ACTION_FRESH,
    ACTION_REFRESHED,
    ACTION_PARKED,
    ACTION_HELD,
    ACTION_RESUMED,
)

#: The actions for which the directive is NOT dispatchable: the park holds it.
HELD_ACTIONS = (ACTION_PARKED, ACTION_HELD)


@dataclass(frozen=True)
class StaleTrigger:
    """The trigger's verdict for one directive, with the evidence it rests on."""

    directive_id: str
    action: str
    generated_at: str = ""
    threshold_minutes: int = DEFAULT_STALENESS_MINUTES
    age_minutes: float = 0.0
    refreshed: bool = False
    reason: str = ""
    park_path: str = ""

    @property
    def dispatchable(self) -> bool:
        """False while a park holds the directive — the consumer's one question."""
        return self.action not in HELD_ACTIONS

    def to_json(self) -> dict[str, Any]:
        return {
            "directive_id": self.directive_id,
            "action": self.action,
            "generated_at": self.generated_at,
            "threshold_minutes": self.threshold_minutes,
            "age_minutes": round(self.age_minutes, 1),
            "refreshed": self.refreshed,
            "reason": self.reason,
            "park_path": self.park_path,
            "dispatchable": self.dispatchable,
        }


@dataclass(frozen=True)
class _Freshness:
    """Whether the on-disk snapshot is fresh, plus the age/threshold evidence."""

    assessable: bool
    fresh: bool
    generated_at: str
    age_minutes: float
    detail: str = ""


def _fleet_root(base: Path | str | None = None) -> Path:
    """The fleet runtime root: the caller's, else ``runtime.FLEET_DIR``."""
    return Path(base) if base is not None else Path(runtime.FLEET_DIR)


def park_dir(base: Path | str | None = None) -> Path:
    """``<fleet>/parked`` — the deferred queue a stale snapshot fills."""
    return _fleet_root(base) / PARKED_DIRNAME


def park_record(directive_id: str, base: Path | str | None = None) -> dict[str, Any] | None:
    """The park marker for a directive, or None when it is not parked."""
    target = park_dir(base) / f"{directive_id}.json"
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def parked(directive_id: str, base: Path | str | None = None) -> bool:
    """True while the directive's park holds it out of dispatch."""
    return park_record(directive_id, base) is not None


def park(
    directive_id: str,
    *,
    base: Path | str | None = None,
    generated_at: str = "",
    threshold_minutes: int = DEFAULT_STALENESS_MINUTES,
    age_minutes: float = 0.0,
    reason: str = "",
    now: datetime | None = None,
) -> Path:
    """Defer a directive: write the park marker and return its path.

    The order is NOT moved — a park is a hold on the operator's live directive,
    not its retirement (contrast ``runaway.dead_letter``, which moves the order
    out of the inbox for ever). The marker carries the evidence the transition is
    reported with: the snapshot's ``generated_at`` and the threshold it tripped.
    """
    target = park_dir(base) / f"{directive_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    moment = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "directive_id": directive_id,
        "state": "parked",
        "parked_at": moment,
        "generated_at": generated_at,
        "threshold_minutes": threshold_minutes,
        "age_minutes": round(age_minutes, 1),
        "reason": reason,
    }
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)
    return target


def unpark(directive_id: str, base: Path | str | None = None) -> bool:
    """Release a parked directive (freshness returned; no operator action)."""
    try:
        (park_dir(base) / f"{directive_id}.json").unlink()
        return True
    except OSError:
        return False


def refresh(
    path: Path | str = DEFAULT_PATH,
    *,
    repo: str = DEFAULT_REPO,
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
    window_seconds: float | None = None,
) -> tuple[bool, str]:
    """Perform ONE board refresh; return ``(refreshed, detail)``.

    This is the one refresh verb: ``cli.py snapshot --from-github`` and the
    stale-snapshot trigger both call it, so there is exactly one path that
    touches the network and exactly one place the bounded window is applied. A
    refused network, a failing ``gh`` and a call that outlives the window are all
    reported as ``(False, reason)`` — a first-class outcome, never a crash.
    """
    window = TRIGGER_WINDOW_SECONDS if window_seconds is None else float(window_seconds)
    try:
        records = github_records(repo, runner=runner, timeout=window)
    except RuntimeError as exc:
        return False, str(exc)
    built = build_snapshot(records, source=repo)
    save(built, path)
    return True, f"refreshed {len(built.issues)} issue(s) from {repo}"


def _freshness(
    snapshot_path: Path | str,
    threshold_minutes: int,
    now: datetime | None,
) -> _Freshness:
    """Read the on-disk snapshot's freshness. An unreadable file is NOT fresh."""
    try:
        snapshot = load(snapshot_path)
    except (OSError, ValueError) as exc:
        return _Freshness(False, False, "", float("inf"), f"snapshot unreadable ({exc})")
    age = age_minutes(snapshot, now)
    return _Freshness(
        True,
        not is_stale(snapshot, threshold_minutes, now),
        snapshot.generated_at,
        age,
    )


def freshness_restored(
    snapshot_path: Path | str = DEFAULT_PATH,
    threshold_minutes: int = DEFAULT_STALENESS_MINUTES,
    now: datetime | None = None,
) -> bool:
    """True when the on-disk snapshot is usable and not stale — the release test.

    Fail closed: a snapshot that cannot be read cannot be assessed, so a park is
    HELD rather than released on an assumption.
    """
    return _freshness(snapshot_path, threshold_minutes, now).fresh


def _staleness(report: _Freshness, threshold_minutes: int) -> str:
    """The refusal's own words, reused so the park reports the age it tripped."""
    if not report.assessable:
        return report.detail
    return (
        f"snapshot is {report.age_minutes:.1f}m old (threshold {threshold_minutes}m) — "
        "the refresh did not clear it"
    )


def refresh_or_park(
    directive_id: str,
    *,
    snapshot_path: Path | str = DEFAULT_PATH,
    base: Path | str | None = None,
    repo: str = DEFAULT_REPO,
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
    now: datetime | None = None,
    threshold_minutes: int = DEFAULT_STALENESS_MINUTES,
    window_seconds: float | None = None,
) -> StaleTrigger:
    """The trigger contract: ONE refresh on staleness, else PARK the directive.

    Called by the consumer (the fleet loop) when a claim was refused with
    ``snapshot-stale``. Idempotent per stale episode: a directive already parked
    is never refreshed again, which is what makes "exactly one refresh" true
    rather than "one per cycle".
    """
    moment = now or datetime.now(timezone.utc)
    if parked(directive_id, base):
        report = _freshness(snapshot_path, threshold_minutes, moment)
        if report.fresh:
            unpark(directive_id, base)
            return StaleTrigger(
                directive_id,
                ACTION_RESUMED,
                report.generated_at,
                threshold_minutes,
                report.age_minutes,
                reason="freshness restored — the deferred directive is released",
            )
        return StaleTrigger(
            directive_id,
            ACTION_HELD,
            report.generated_at,
            threshold_minutes,
            report.age_minutes,
            reason=f"still parked (no second refresh): {_staleness(report, threshold_minutes)}",
        )

    before = _freshness(snapshot_path, threshold_minutes, moment)
    if before.fresh:
        return StaleTrigger(
            directive_id,
            ACTION_FRESH,
            before.generated_at,
            threshold_minutes,
            before.age_minutes,
            reason="snapshot is fresh — nothing to trigger",
        )

    refreshed, detail = refresh(
        snapshot_path, repo=repo, runner=runner, window_seconds=window_seconds
    )
    after = _freshness(snapshot_path, threshold_minutes, moment)
    if refreshed and after.fresh:
        return StaleTrigger(
            directive_id,
            ACTION_REFRESHED,
            after.generated_at,
            threshold_minutes,
            after.age_minutes,
            refreshed=True,
            reason=detail,
        )

    reason = detail if not refreshed else _staleness(after, threshold_minutes)
    generated_at = after.generated_at or before.generated_at
    target = park(
        directive_id,
        base=base,
        generated_at=generated_at,
        threshold_minutes=threshold_minutes,
        age_minutes=after.age_minutes,
        reason=reason,
        now=moment,
    )
    return StaleTrigger(
        directive_id,
        ACTION_PARKED,
        generated_at,
        threshold_minutes,
        after.age_minutes,
        refreshed=refreshed,
        reason=reason,
        park_path=str(target),
    )
