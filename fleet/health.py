#!/usr/bin/env python3
"""Fleet health signal — cmr-style healthy/degraded/failing (issue #163).

Harvested pattern (docs/CANNIBALIZATION.md #163): `leaderboard/docker/worker-fleet/personas.yaml`
ships a `fleet-health` persona ("periodic health reports") as prior art for a
health check separate from the dispatch loop itself. This module is that
check for this repo's file-mailbox transport: it never spawns anything, it
only reads state the loop already writes (the rung heartbeats, the claim
ledger, the running processes) and reports a tri-state signal.

Both rungs are probed (issue #277). The brain is the middle rung and the only
thing that turns an operator order into a directive, so a dead brain is not
healthy even while the sister still beats — this is the same both-rungs truth
`channel.report_rung` reports. Freshness is read from the rung heartbeat's *age*,
not `.fleet/slog.jsonl`'s mtime: the log is only written when a message moves, so
an idle-but-healthy fleet must not read as degraded.

The queue-liveness facet and its LATCH (issue #728) are below: this module also
measures inbox depth, the oldest pending directive's age and the dead-letter
count, and a runaway condition it finds is *latched* — raised and then kept
raised, naming the directives and worktrees responsible, until an operator
acknowledges it. See the block comment above `QueueReport`.

Signal:
    0 healthy  — the sister and the brain are running current builds with fresh
                 heartbeats, no claim is wedged past the staleness window, and
                 no runaway alarm is raised or latched.
    1 degraded — a rung is down (at least degraded for a dead brain), running
                 stale code, or its heartbeat is stale; or a claim is held past
                 the staleness window.
    2 failing  — the sister loop is not running at all, or a runaway alarm is
                 raised (or still latched from an earlier excursion).

Usage:
    python3 fleet/health.py check [--stale-minutes 30]
    python3 fleet/health.py alarm   # measure the queue; raise/latch a runaway
    python3 fleet/health.py ack     # acknowledge the latch (the only clear)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import runtime

ROOT = Path(__file__).resolve().parent.parent
# The two probe targets, mirroring `channel.report_rung`: the sister loop and the
# brain. Resolved on every call (see `rungs`) so a redirected heartbeat path is
# honoured.
SISTER_PROCESS = "fleet/terminal.py"
BRAIN_PROCESS = "fleet/brain.py"
SISTER_HEARTBEAT = runtime.FLEET_DIR / "sister.heartbeat.json"
BRAIN_HEARTBEAT = runtime.FLEET_DIR / "brain.heartbeat.json"

sys.path.insert(0, str(ROOT / "governance" / "dispatch"))
sys.path.insert(0, str(ROOT / "fleet"))
import channel  # noqa: E402
import claims  # noqa: E402
import runaway  # noqa: E402
from snapshot import now_iso, parse_iso  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

HEALTHY, DEGRADED, FAILING = 0, 1, 2
LABELS = {HEALTHY: "healthy", DEGRADED: "degraded", FAILING: "failing"}


def process_running(pattern: str) -> bool:
    """True when a live process matches `pattern` (pgrep exit 0)."""
    try:
        result = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def loop_running() -> bool:
    """The sister loop — the never-idle dispatcher."""
    return process_running(SISTER_PROCESS)


def brain_running() -> bool:
    """The brain — the middle rung; no operator order is dispatched without it."""
    return process_running(BRAIN_PROCESS)


def read_beat(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def rungs() -> tuple[tuple[str, Path, str, Callable[[], bool]], ...]:
    """(name, heartbeat path, start command, liveness probe) per rung.

    Resolved on every call, not at import, so the heartbeat paths follow the
    module constants rather than a snapshot taken when the module loaded.
    """
    return (
        ("sister", SISTER_HEARTBEAT, "bash fleet/terminal.sh", loop_running),
        ("brain", BRAIN_HEARTBEAT, "bash fleet/brain.sh", brain_running),
    )


def stalest_claim_minutes(ledger_path: Path) -> float | None:
    if not Path(ledger_path).exists():
        return None
    live = claims.active_claims(claims.read_ledger(ledger_path))
    if not live:
        return None
    now = datetime.now(timezone.utc)
    ages = [(now - parse_iso(claim.at)).total_seconds() / 60.0 for claim in live.values()]
    return max(ages) if ages else None


def rung_health(
    name: str, heartbeat_path: Path, start_cmd: str, probe: Callable[[], bool], baseline: str
) -> tuple[int, str | None]:
    """One rung's level + reason; down, unreported, stale, drifted or unjudgeable is degraded.

    Mirrors `channel.report_rung`'s two truths — the rung must be alive *and*
    running current code — so health cannot disagree with `channel.py status`
    about a rung. The commit it is judged against is the **remote** baseline
    (`origin/master`), never the local checkout: the checkout is routinely the
    stale side, and comparing a loop's commit to it makes a loop on pre-fix code
    report healthy (#739, AO-GR-25). An unreadable baseline is DEGRADED, not
    healthy — health must not fail open either.
    """
    if not probe():
        return DEGRADED, f"{name}: not running — this rung is down (start: {start_cmd})"
    beat = read_beat(heartbeat_path)
    if beat is None:
        return DEGRADED, (
            f"{name}: no heartbeat file — it is running a build older than the heartbeat "
            f"check, so merged fixes are not live (restart: {start_cmd})"
        )
    age = channel.heartbeat_age_seconds(beat)
    if age is None:
        return DEGRADED, f"{name}: heartbeat present but undated"
    if age > channel.STALE_HEARTBEAT_SECONDS:
        return DEGRADED, (
            f"{name}: heartbeat stale ({int(age)}s since the last beat > "
            f"{channel.STALE_HEARTBEAT_SECONDS}s) — the loop is not making progress"
        )
    running = str(beat.get("commit", "unknown"))
    drift_state, drift_reason = channel.classify_drift(running, baseline)
    if drift_state == channel.DRIFT_DRIFTED:
        return DEGRADED, (
            f"{name}: {drift_reason} — merged fixes are not live (restart: {start_cmd})"
        )
    if drift_state == channel.DRIFT_CANNOT_ASSESS:
        return DEGRADED, f"{name}: CANNOT ASSESS DRIFT — {drift_reason}"
    return HEALTHY, None


def evaluate(stale_minutes: float, ledger_path: Path) -> tuple[int, list[str]]:
    reasons: list[str] = []
    if not loop_running():
        return FAILING, ["fleet/terminal.py is not running — the never-idle loop is dead"]

    level = HEALTHY
    # The drift baseline is the REMOTE (`.fleet` heartbeat vs `origin/master`),
    # never the local checkout — see `rung_health` (#739, AO-GR-25).
    baseline = channel.remote_head_commit()
    # Probe BOTH rungs, exactly as `channel.py status` does: a dead brain while the
    # sister still beats is at least degraded, never healthy. Freshness is the rung
    # heartbeat's age, not `.fleet/slog.jsonl`'s mtime — an idle-but-healthy fleet
    # writes no messages, and reading that as degraded was the second half of #277.
    for name, beat_path, start_cmd, probe in rungs():
        rung_level, rung_reason = rung_health(name, beat_path, start_cmd, probe, baseline)
        level = max(level, rung_level)
        if rung_reason:
            reasons.append(rung_reason)

    claim_age = stalest_claim_minutes(ledger_path)
    if claim_age is not None and claim_age > stale_minutes:
        level = max(level, DEGRADED)
        reasons.append(f"a claim has been held {claim_age:.1f}m (> {stale_minutes:.0f}m stale window)")

    if not reasons:
        reasons.append("sister and brain running current builds with fresh heartbeats, no wedged claims")
    return level, reasons


# ── the queue-liveness facet and the runaway LATCH (issue #728) ─────────────
# WHY (measured): the runaway that motivated the attempt cap (#723) — 49
# concurrent `make verify` runs on one box — was caught by a human *noticing*
# it. Detection has to be a signal, and an alarm has to be a SIGNAL: an
# excursion that clears itself before anyone looks is indistinguishable from
# one that never happened. So this facet measures the work queue's liveness —
# inbox depth, the oldest pending directive's age, the dead-letter count — and a
# runaway condition it finds is LATCHED. Once raised it stays raised, naming the
# directives and the worktrees responsible, until an operator acknowledges it
# with `python3 fleet/health.py ack`. That is the difference between an alarm
# and a dashboard.
#
# WHERE IT IS EMITTED: by the gate of record, not only by an ad-hoc command.
# `scripts/check-fleet-channel.sh` provokes the condition, proves the alarm
# latches when the excursion is gone, and proves `ack` clears it. `check`
# REPORTS the latch (read-only) so every health read carries it whatever else it
# is measuring; `alarm` RAISES it. Neither writes outside the fleet directory it
# was given, and neither writes at all when the condition is clear.
#
# WHY THE NAMES ARE NOT METRIC LABELS: the directives and worktrees responsible
# are per-session identity, which `fleet/health_signals.py` refuses as a label
# by name (ADR-0022 D5 / kushin77/monitoring-stack#178). The alarm therefore
# names them in the local signal and its latch artifact — where an operator
# reads them — and publishes only the counts.
#
# THE KNOBS are configurable and their defaults are documented here and nowhere
# else:
#
# ================ ==================== ======= ================================
# knob             env                  default meaning
# ================ ==================== ======= ================================
# inbox depth      ``AO_RUNAWAY_INBOX_DEPTH``   ``24``  pending directives the
#                                              inbox may hold before the alarm
#                                              is raised
# oldest age       ``AO_RUNAWAY_OLDEST_MINUTES`` ``120`` minutes a pending
#                                              directive may sit undrained
# dead letters     ``AO_RUNAWAY_DEAD_LETTERS`` ``5``  dead-lettered directives
#                                              that mean the queue is not moving
# ================ ==================== ======= ================================
#
# An unreadable threshold is REFUSED (`AlarmConfigError`), following
# `fleet/runaway.py`: silently substituting a default for a typo is how an alarm
# becomes decorative, and silently substituting ``0`` is how it is disabled.

DEFAULT_MAX_INBOX_DEPTH = 24
DEFAULT_MAX_OLDEST_MINUTES = 120.0
DEFAULT_MAX_DEAD_LETTERS = 5

ENV_MAX_INBOX_DEPTH = "AO_RUNAWAY_INBOX_DEPTH"
ENV_MAX_OLDEST_MINUTES = "AO_RUNAWAY_OLDEST_MINUTES"
ENV_MAX_DEAD_LETTERS = "AO_RUNAWAY_DEAD_LETTERS"

#: How many directive ids the alarm names. An alarm that renders an unbounded
#: list of names is itself a (small) runaway, and the latch artifact is read by
#: an operator, not a machine.
ALARM_NAME_LIMIT = 20

#: `alarm`'s exit code is read with THIS module's tri-state: 0 clear / 2 raised.
#: 1 is reserved for a refusal (an unreadable threshold), never for a reading —
#: the same asymmetry `fleet/runaway.py` declares.
EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_RAISED = 2


class AlarmConfigError(ValueError):
    """A declared threshold cannot be read — refuse, never guess."""


def _threshold(name: str, default: float, *, integral: bool) -> float:
    """Read one threshold from the environment; an absent value is the default."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    text = raw.strip()
    try:
        value: float = int(text) if integral else float(text)
    except ValueError:
        raise AlarmConfigError(
            f"{name}={text!r} is not a number — the runaway alarm refuses an unreadable "
            "threshold rather than silently disabling its own trigger"
        ) from None
    if value < 1:
        raise AlarmConfigError(f"{name}={value} must be >= 1")
    return value


def thresholds() -> tuple[int, float, int]:
    """(max inbox depth, max oldest-directive age in minutes, max dead letters)."""
    return (
        int(_threshold(ENV_MAX_INBOX_DEPTH, DEFAULT_MAX_INBOX_DEPTH, integral=True)),
        _threshold(ENV_MAX_OLDEST_MINUTES, DEFAULT_MAX_OLDEST_MINUTES, integral=False),
        int(_threshold(ENV_MAX_DEAD_LETTERS, DEFAULT_MAX_DEAD_LETTERS, integral=True)),
    )


def read_json_object(path: Path | str) -> dict | None:
    """Parse a JSON object; absent, unreadable or non-object reads as None."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def directive_issue(envelope: dict) -> int | None:
    """The issue a directive names; None when it is not executable work.

    The same read ``fleet/terminal.py::directive_issue`` makes on the same
    envelope, so the alarm and the loop cannot disagree about which issue a
    pending directive is holding up.
    """
    task = envelope.get("task")
    task = task if isinstance(task, dict) else {}
    issue = task.get("issue")
    if isinstance(issue, bool) or not isinstance(issue, int) or issue < 1:
        return None
    return issue


@dataclass(frozen=True)
class QueueReport:
    """One measurement of the work queue, and the runaway findings it yields.

    Counts and names, never a judgement: ``raised`` is exactly "the findings
    list is non-empty", so a caller cannot report a runaway it did not measure.
    """

    inbox_depth: int
    oldest_directive_id: str | None
    oldest_directive_age_minutes: float | None
    dead_letters: int
    directives: tuple[str, ...]
    issues: tuple[int, ...]
    findings: tuple[str, ...]

    @property
    def raised(self) -> bool:
        return bool(self.findings)

    def as_dict(self) -> dict:
        return {
            "inbox_depth": self.inbox_depth,
            "oldest_directive": self.oldest_directive_id,
            "oldest_directive_age_minutes": (
                None if self.oldest_directive_age_minutes is None else round(self.oldest_directive_age_minutes, 1)
            ),
            "dead_letters": self.dead_letters,
        }


def pending_directives(fleet_dir: Path | str) -> list[tuple[Path, dict]]:
    """The pending inbox as (path, envelope) in send order, oldest first.

    Ordering and path layout are ``channel.ordered_by_time`` / ``runaway``'s, so
    the alarm cannot invent a second mailbox layout.
    """
    entries: list[tuple[Path, dict]] = []
    for path in channel.ordered_by_time(runaway.inbox_dir(fleet_dir)):
        envelope = read_json_object(path)
        entries.append((path, envelope if envelope is not None else {}))
    return entries


def queue_report(
    fleet_dir: Path | str,
    *,
    max_inbox_depth: int = DEFAULT_MAX_INBOX_DEPTH,
    max_oldest_minutes: float = DEFAULT_MAX_OLDEST_MINUTES,
    max_dead_letters: int = DEFAULT_MAX_DEAD_LETTERS,
    now: datetime | None = None,
) -> QueueReport:
    """Measure the queue and name what is holding it up.

    The dead-letter count is read through ``runaway.inventory`` rather than by
    re-globbing the store, so ``status``, the guard and this signal cannot drift
    apart.
    """
    moment = now or datetime.now(timezone.utc)
    entries = pending_directives(fleet_dir)
    depth = len(entries)

    oldest_id: str | None = None
    oldest_age: float | None = None
    if entries:
        oldest_path, oldest_envelope = entries[0]
        oldest_id = str(oldest_envelope.get("id") or oldest_path.stem)
        stamp = oldest_envelope.get("ts")
        if isinstance(stamp, str) and stamp.strip():
            try:
                oldest_age = max(0.0, (moment - parse_iso(stamp)).total_seconds() / 60.0)
            except ValueError:
                oldest_age = None

    dead_letters = len(runaway.inventory(fleet_dir)["dead_letters"])

    findings: list[str] = []
    named: list[str] = []
    if depth > max_inbox_depth:
        findings.append(
            f"the inbox holds {depth} pending directive(s) (> the {max_inbox_depth:g} depth cap)"
        )
        named = [str(envelope.get("id") or path.stem) for path, envelope in entries]
    if oldest_age is not None and oldest_age > max_oldest_minutes:
        findings.append(
            f"the oldest pending directive has been queued {oldest_age:.1f}m "
            f"(> the {max_oldest_minutes:g}m age cap)"
        )
        if oldest_id and oldest_id not in named:
            named.insert(0, oldest_id)
    if dead_letters >= max_dead_letters:
        findings.append(
            f"{dead_letters} directive(s) are dead-lettered (>= the {max_dead_letters:g} dead-letter cap)"
        )
        for name in runaway.inventory(fleet_dir)["dead_letters"]:
            if name not in named:
                named.append(name)

    issues: list[int] = []
    for _path, envelope in entries:
        issue = directive_issue(envelope)
        if issue is not None and issue not in issues:
            issues.append(issue)

    return QueueReport(
        inbox_depth=depth,
        oldest_directive_id=oldest_id,
        oldest_directive_age_minutes=oldest_age,
        dead_letters=dead_letters,
        directives=tuple(named[:ALARM_NAME_LIMIT]),
        issues=tuple(issues),
        findings=tuple(findings),
    )


def responsible_worktrees(
    report: QueueReport,
    *,
    ledger_path: Path | str,
    repo_root: Path | str | None = None,
) -> tuple[str, ...]:
    """The worktrees holding the named directives, from reads that exist.

    Two declared joins, never a guess: a live claim is linked to the directive it
    was taken for (``ClaimEvent.directive_id``, #723), and a lane record carries
    the worktree its session was provisioned into (``governance/isolation``).
    A directive that names an issue no lane has claimed still resolves through
    the lane record for that issue, so a queue blocked *before* the claim is
    taken is named too. Both reads are local and bounded.
    """
    wanted = set(report.directives)
    issues = set(report.issues)
    try:
        events = claims.read_ledger(ledger_path)
    except (OSError, ValueError):
        events = []
    for claim in claims.active_claims(events).values():
        if claim.directive_id and claim.directive_id in wanted:
            issues.add(claim.issue)

    from governance.isolation.worktree import list_records  # noqa: PLC0415 (local, see docstring)

    root = ROOT if repo_root is None else Path(repo_root)
    return tuple(sorted({str(record.worktree) for record in list_records(root) if record.issue in issues}))


@dataclass(frozen=True)
class Latch:
    """The persisted alarm: raised once, kept raised, cleared only by an ack."""

    raised: bool
    raised_at: str | None = None
    acknowledged_at: str | None = None
    acknowledged_by: str | None = None
    note: str = ""
    findings: tuple[str, ...] = ()
    directives: tuple[str, ...] = ()
    worktrees: tuple[str, ...] = ()

    @property
    def latched(self) -> bool:
        """Raised and never acknowledged — the state that must not clear itself."""
        return self.raised and self.acknowledged_at is None

    def as_dict(self) -> dict:
        return {
            "raised": self.raised,
            "raised_at": self.raised_at,
            "acknowledged_at": self.acknowledged_at,
            "acknowledged_by": self.acknowledged_by,
            "note": self.note,
            "findings": list(self.findings),
            "directives": list(self.directives),
            "worktrees": list(self.worktrees),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Latch":
        def names(key: str) -> tuple[str, ...]:
            value = data.get(key)
            return tuple(str(item) for item in value) if isinstance(value, list) else ()

        def text(key: str) -> str | None:
            value = data.get(key)
            return str(value) if isinstance(value, str) and value else None

        return cls(
            raised=bool(data.get("raised")),
            raised_at=text("raised_at"),
            acknowledged_at=text("acknowledged_at"),
            acknowledged_by=text("acknowledged_by"),
            note=str(data.get("note") or ""),
            findings=names("findings"),
            directives=names("directives"),
            worktrees=names("worktrees"),
        )


def latch_path(fleet_dir: Path | str | None = None) -> Path:
    """``<fleet>/health/alarm.json`` — the latch artifact (declared in runtime.py)."""
    root = Path(fleet_dir) if fleet_dir is not None else runtime.FLEET_DIR
    return root / runtime.HEALTH.name / runtime.ALARM.name


def read_latch(fleet_dir: Path | str | None = None) -> Latch | None:
    data = read_json_object(latch_path(fleet_dir))
    return Latch.from_dict(data) if data is not None else None


def _write_latch(fleet_dir: Path | str | None, latch: Latch) -> Path:
    """Persist the latch atomically (tmp + rename), so a reader never sees a torn one."""
    path = latch_path(fleet_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(latch.as_dict(), indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def _union(first: tuple[str, ...], second: tuple[str, ...]) -> tuple[str, ...]:
    """`first` then anything new from `second`, bounded by ``ALARM_NAME_LIMIT``."""
    merged = list(first)
    for item in second:
        if item not in merged:
            merged.append(item)
    return tuple(merged[:ALARM_NAME_LIMIT])


def raise_latch(
    fleet_dir: Path | str | None,
    report: QueueReport,
    worktrees: tuple[str, ...] = (),
) -> Latch:
    """Latch the alarm: keep the first ``raised_at`` and the union of the evidence.

    A second excursion while the alarm is still latched is the SAME alarm — it
    must not overwrite the moment the condition began, or the operator loses the
    only fact that says how long the fleet has been stuck. A recurrence after an
    ack is a NEW alarm and gets a new ``raised_at``.
    """
    existing = read_latch(fleet_dir)
    if existing is not None and existing.latched:
        latch = Latch(
            raised=True,
            raised_at=existing.raised_at,
            note=existing.note,
            findings=_union(existing.findings, report.findings),
            directives=_union(existing.directives, report.directives),
            worktrees=_union(existing.worktrees, worktrees),
        )
    else:
        latch = Latch(
            raised=True,
            raised_at=now_iso(),
            findings=tuple(report.findings),
            directives=tuple(report.directives[:ALARM_NAME_LIMIT]),
            worktrees=tuple(worktrees[:ALARM_NAME_LIMIT]),
        )
    _write_latch(fleet_dir, latch)
    return latch


def acknowledge_latch(
    fleet_dir: Path | str | None,
    *,
    by: str = "operator",
    note: str = "",
) -> Latch | None:
    """Clear the latch — the ONLY route, and deliberately a human verb.

    The record is kept (``raised: false`` with the acknowledgee, the time and the
    original evidence) rather than deleted, because the question an operator asks
    afterwards is "was this alarmed, and who cleared it", and a deleted file
    cannot answer it. Returns None when there is nothing raised to acknowledge.
    """
    existing = read_latch(fleet_dir)
    if existing is None or not existing.raised:
        return None
    latch = Latch(
        raised=False,
        raised_at=existing.raised_at,
        acknowledged_at=now_iso(),
        acknowledged_by=by,
        note=note or existing.note,
        findings=existing.findings,
        directives=existing.directives,
        worktrees=existing.worktrees,
    )
    _write_latch(fleet_dir, latch)
    return latch


def alarm_reasons(report: QueueReport, latch: Latch | None, worktrees: tuple[str, ...]) -> list[str]:
    """The operator-facing reasons: what broke, what is named, and whether it is latched."""
    reasons: list[str] = []
    if report.raised:
        for finding in report.findings:
            reasons.append(f"runaway: {finding}")
    elif latch is not None and latch.latched:
        reasons.append(
            "runaway: the condition is no longer measured, but the alarm is still LATCHED "
            f"(raised {latch.raised_at}) — a transient excursion does not clear it"
        )
    if report.raised or (latch is not None and latch.latched):
        named = latch.directives if latch is not None else report.directives
        tree_names = latch.worktrees if latch is not None else worktrees
        if named:
            reasons.append(f"runaway: directive(s) responsible — {', '.join(named)}")
        if tree_names:
            reasons.append(f"runaway: worktree(s) responsible — {', '.join(tree_names)}")
        if not tree_names:
            reasons.append(
                "runaway: no worktree named — no live claim or lane record links a named directive "
                "to a lane (acknowledge with `python3 fleet/health.py ack` once the queue is drained)"
            )
    return reasons


def alarm_payload(
    fleet_dir: Path | str | None,
    *,
    ledger_path: Path | str,
    repo_root: Path | str | None = None,
    latch: bool = False,
    now: datetime | None = None,
) -> tuple[int, dict]:
    """(exit code, payload) for the queue facet. ``latch=True`` RAISES the alarm.

    Exit code is this module's tri-state read for the facet alone: 0 clear,
    2 raised or latched. The rung signal is deliberately excluded — ``alarm``
    answers "is the queue running away", and must be answerable on a box where
    the loop under test is not running.
    """
    fleet = fleet_dir if fleet_dir is not None else runtime.FLEET_DIR
    max_depth, max_age, max_dead = thresholds()
    report = queue_report(
        fleet,
        max_inbox_depth=max_depth,
        max_oldest_minutes=max_age,
        max_dead_letters=max_dead,
        now=now,
    )
    worktrees = (
        responsible_worktrees(report, ledger_path=ledger_path, repo_root=repo_root)
        if report.raised
        else ()
    )
    state = read_latch(fleet)
    if report.raised and latch:
        state = raise_latch(fleet, report, worktrees)
    latched = bool(state is not None and state.latched)
    raised = report.raised or latched
    payload = {
        "status": "raised" if raised else "clear",
        "queue": report.as_dict(),
        "alarm": {
            "raised": raised,
            "measured": report.raised,
            "latched": latched,
            "raised_at": state.raised_at if state is not None else None,
            "acknowledged_at": state.acknowledged_at if state is not None else None,
            "acknowledged_by": state.acknowledged_by if state is not None else None,
            "findings": list(report.findings if report.raised else (state.findings if state else ())),
            "directives": list(report.directives if report.raised else (state.directives if state else ())),
            "worktrees": list(worktrees if report.raised else (state.worktrees if state else ())),
        },
        "thresholds": {"inbox_depth": max_depth, "oldest_minutes": max_age, "dead_letters": max_dead},
        "reasons": alarm_reasons(report, state, worktrees),
    }
    return (EXIT_RAISED if raised else EXIT_OK), payload


def cmd_check(args: argparse.Namespace) -> int:
    level, reasons = evaluate(args.stale_minutes, Path(args.ledger))
    fleet = getattr(args, "fleet_dir", None)
    # The latch is REPORTED here, never raised: `check` is the read the operator
    # and the gate run freely, and a read that writes is a read with a side
    # effect. Raising is `alarm`'s job.
    try:
        _code, alarm = alarm_payload(
            fleet,
            ledger_path=Path(getattr(args, "ledger", None) or claims.DEFAULT_LEDGER),
            repo_root=getattr(args, "repo_root", None),
        )
    except AlarmConfigError as exc:
        level = max(level, DEGRADED)
        alarm = {"raised": False, "latched": False, "measured": False, "findings": [], "directives": [], "worktrees": []}
        reasons.append(f"runaway alarm misconfigured — {exc}")
    else:
        reasons.extend(alarm["reasons"])
        # A LATCHED alarm is failing; a merely measured one is degraded, because
        # `check` does not raise the latch — `alarm` does, and only the latch is
        # the confirmed signal that must survive the excursion.
        if alarm["alarm"]["latched"]:
            level = max(level, FAILING)
        elif alarm["alarm"]["measured"]:
            level = max(level, DEGRADED)
    print(
        json.dumps(
            {"signal": level, "status": LABELS[level], "reasons": reasons, "queue": alarm["queue"], "alarm": alarm["alarm"]}
        )
    )
    return level


def cmd_alarm(args: argparse.Namespace) -> int:
    try:
        code, payload = alarm_payload(
            getattr(args, "fleet_dir", None),
            ledger_path=Path(args.ledger),
            repo_root=getattr(args, "repo_root", None),
            latch=True,
        )
    except AlarmConfigError as exc:
        print(json.dumps({"status": "cannot-assess", "reasons": [f"runaway alarm misconfigured — {exc}"]}))
        return EXIT_NOT_OK
    print(json.dumps(payload, indent=2))
    return code


def cmd_ack(args: argparse.Namespace) -> int:
    try:
        latch = acknowledge_latch(getattr(args, "fleet_dir", None), by=args.by, note=args.note)
    except AlarmConfigError as exc:  # a threshold typo must not make an ack impossible
        print(json.dumps({"acknowledged": False, "reasons": [f"runaway alarm misconfigured — {exc}"]}))
        return EXIT_NOT_OK
    if latch is None:
        print(json.dumps({"acknowledged": False, "reasons": ["no raised runaway alarm to acknowledge"]}))
        return EXIT_NOT_OK
    print(
        json.dumps(
            {
                "acknowledged": True,
                "raised_at": latch.raised_at,
                "acknowledged_at": latch.acknowledged_at,
                "acknowledged_by": latch.acknowledged_by,
                "directives": list(latch.directives),
                "worktrees": list(latch.worktrees),
            },
            indent=2,
        )
    )
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleet-health", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="print the health signal and exit with it (0/1/2)")
    check.add_argument("--stale-minutes", type=float, default=30.0)
    check.add_argument("--ledger", default=str(claims.DEFAULT_LEDGER))
    check.add_argument("--fleet-dir", default=None, help="the fleet runtime dir (default AO_FLEET_DIR)")
    check.add_argument("--repo-root", default=None, help="the repo whose lane records name worktrees")
    check.set_defaults(func=cmd_check)

    alarm = sub.add_parser("alarm", help="measure the queue and raise/latch a runaway alarm")
    alarm.add_argument("--ledger", default=str(claims.DEFAULT_LEDGER))
    alarm.add_argument("--fleet-dir", default=None, help="the fleet runtime dir (default AO_FLEET_DIR)")
    alarm.add_argument("--repo-root", default=None, help="the repo whose lane records name worktrees")
    alarm.set_defaults(func=cmd_alarm)

    ack = sub.add_parser("ack", help="acknowledge the latch — the only way it clears")
    ack.add_argument("--by", default="operator", help="who acknowledged it")
    ack.add_argument("--note", default="", help="why, for the record")
    ack.add_argument("--fleet-dir", default=None, help="the fleet runtime dir (default AO_FLEET_DIR)")
    ack.set_defaults(func=cmd_ack)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
