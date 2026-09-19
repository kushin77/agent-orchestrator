"""A filed finding reaches a terminal state — or it is named, never written off (#973).

``governance/lifecycle/report.py`` files a governance finding on the board once per
fingerprint and dedupes repeats through ``.fleet/board-reports.json``. Filing is
idempotent; **unfiling was not**. Nothing in the fleet retired a lifecycle
fingerprint: the only ``resolve`` call site in the repository was the sweep's own
``shelved:`` key, so a ``lifecycle:*`` entry lived in the ledger forever.

Measured (2026-09-17) on issue #973: the board carried
``[lifecycle] LANE_NOT_RECLAIMED — #241`` while the item it named was fully
reclaimed — #241's lane record, worktree, claim, branch and heartbeat were all
gone, and the live audit charged it **0 findings out of 715** in the record. The
finding was true when it was filed and false from then on, and nothing could say
so.

Two harms follow, and the second is the one that matters:

* **stale board noise** — an open issue describing a violation that no longer
  exists, which reads as a live defect to everyone who opens it;
* **a fail-open dedupe** — because the fingerprint never left the ledger, a
  *genuine* recurrence of the same violation on the same subject is reported as
  ``deduped`` against the stale issue and **never filed**. A control that cannot
  fail is a formality; one that fails open is worse than none.

So the reconciler re-measures each filed finding and drives it to a terminal state.
It is the right owner: ``governance/reconcile/cli.py watch --once --apply`` is the
only scheduled worker, and this package already reads and writes that ledger. Per
entry:

* the invariant is **still charged** for that subject → the entry stays, and the
  finding is named with the live detail;
* the subject **cannot be measured** — the board could not be read, the subject is
  not one the audit can speak about, or the write itself failed → the entry stays
  and that is named. Absence of evidence is never read as absence of the
  violation, which is the fail-open this module exists to close;
* the invariant is **no longer charged** for that subject → the board artifact is
  closed with the re-measurement as its evidence, and only then is the ledger
  entry retired, so a new occurrence files afresh.

**Only ``apply`` resolves anything.** ``BoardReporter.resolve`` used to retire the
ledger entry whether or not ``apply`` was set — it guarded the *board* write, not
the ledger save — so a dry run that called it dropped the dedupe entry with no
comment and no close: the finding vanished silently, which is the defect this
module removes rather than a way to remove it. Since #1299 ``resolve`` itself
gates the ledger save on ``apply`` too; every retirement here still goes through
:func:`resolve_key`, which refuses while ``apply`` is false, so this module's
guarantee does not depend on the reporter's.

**The order is load-bearing.** The board close happens *before* the ledger is
retired. The ledger entry is what makes the finding retryable, so retiring it first
would turn a failed board write into a permanently open issue nobody would look at
again — the same reasoning that puts close-out before lane teardown (#786).

**The board read is bounded, and it runs last.** The collection shells out to
``gh`` — which has no timeout of its own — and, once per item, to
``git ls-remote``. Measured 2026-09-17 on a box running ~40 lanes: **~2.5 minutes**
for one collection. Two consequences are designed for, not discovered later:

* the recheck runs **after** every session decision in the pass, so a slow board
delays the *next* pass and never the teardown of an orphan;
* the collection runs as a child process under an explicit timeout
  (:data:`COLLECT_TIMEOUT_SECONDS`), because a worker that can wait for ever also
  stops reconciling orphans. A timeout is an *unmeasured* board — nothing is
  resolved, which is the same verdict as a board that could not be read.

Making the recheck *cadence* its own declared control (rather than once per pass) is
the obvious next step and is deliberately left out of this change: it is a policy
value, not part of the defect.

The re-measurement is the audit's own, not a re-implementation of it: the same
``audit(record, quarantine)`` call ``governance/lifecycle/cli.py audit`` makes, over
the same collected record. A second implementation of the invariants would drift,
and the two would then disagree about whether a finding is owed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, Sequence

from governance.lifecycle.report import BoardReporter

#: The prefix ``board_report_findings`` gives every lifecycle fingerprint. The
#: general shape is ``<kind>:<subject>``, and the kind is ``lifecycle:<CODE>``.
LIFECYCLE_PREFIX = "lifecycle:"

#: What one pass did with one filed finding.
RESOLVED = "resolved"
WOULD_RESOLVE = "would-resolve"
STILL_OWED = "still-owed"
UNMEASURED = "unmeasured"
FAILED = "failed"


@dataclass(frozen=True)
class Entry:
    """One lifecycle finding in the ledger, split into the facts it carries."""

    key: str
    code: str
    subject: str
    number: int | None

    def to_json(self) -> dict:
        return {"key": self.key, "code": self.code, "subject": self.subject, "number": self.number}


@dataclass(frozen=True)
class FindingState:
    """The disposition of one filed finding on this pass."""

    key: str
    outcome: str
    detail: str
    code: str = ""
    subject: str = ""
    number: int | None = None

    def __str__(self) -> str:
        return f"{self.outcome:<15} {self.key} — {self.detail}"

    def to_json(self) -> dict:
        return {
            "key": self.key,
            "outcome": self.outcome,
            "detail": self.detail,
            "code": self.code,
            "subject": self.subject,
            "number": self.number,
        }


@dataclass(frozen=True)
class Charged:
    """One invariant the live audit charges, as the re-measurement reads it.

    A neutral record rather than the audit's own ``Finding``, because the two paths
    that produce it differ: a test injects a record and audits it in-process, while
    the live path reads the finding through a child process and gets plain JSON
    back. Only the three fields the decision needs are carried across either.
    """

    code: str
    subject: str
    detail: str


class FindingCloser(Protocol):
    """The board effect that makes a filed finding terminal.

    Injected, so the re-measurement is proven offline — the same seam the board
    *filer* already sits behind.
    """

    def close(self, number: int, comment: str) -> None: ...


class GhCloser:
    """The real effect through ``gh``, on the repository of the cwd.

    ``gh issue close --comment`` is one call on purpose: a comment written first
    and a close that then failed would leave the issue open carrying text that
    says it is resolved, which is a worse artifact than no comment at all.
    """

    def __init__(self, repo: str | None = None) -> None:
        self.repo = repo

    def close(self, number: int, comment: str) -> None:
        cmd = ["gh", "issue", "close", str(number), "--comment", comment]
        if self.repo:
            cmd.extend(["--repo", self.repo])
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"gh issue close failed ({result.returncode}): "
                f"{(result.stderr or result.stdout).strip()[-200:]}"
            )


def read_ledger(reporter: BoardReporter) -> dict:
    """The dedupe ledger, tolerating an absent or unreadable one.

    An unreadable ledger yields no entries, so nothing is resolved and nothing is
    reported as clear: the ledger is the only record of what was filed, and
    guessing at its contents is the one thing this module must not do.
    """
    path = Path(reporter.ledger)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def lifecycle_entries(ledger: dict) -> tuple[list[Entry], list[str]]:
    """Split a ledger into lifecycle findings and keys that cannot be read.

    The second list is reported, never dropped: a fingerprint the module cannot
    parse is a finding nobody can re-measure, and silence about it would look
    exactly like a clean board.
    """
    entries: list[Entry] = []
    unreadable: list[str] = []
    for key in sorted(ledger):
        if not str(key).startswith(LIFECYCLE_PREFIX):
            continue
        parts = str(key).split(":", 2)
        if len(parts) != 3 or not parts[1].strip() or not parts[2].strip():
            unreadable.append(str(key))
            continue
        payload = ledger.get(key) or {}
        number = payload.get("number") if isinstance(payload, dict) else None
        entries.append(
            Entry(key=str(key), code=parts[1], subject=parts[2], number=int(number) if number else None)
        )
    return entries, unreadable


def resolve_key(reporter: BoardReporter, key: str, *, comment: str, apply: bool) -> bool:
    """Retire one dedupe entry — **only** under ``apply``.

    ``BoardReporter.resolve`` once saved the ledger unconditionally and gated only
    the comment, so calling it on a dry run dropped the entry with no board write
    at all — a silent suppression of a finding. The reporter now gates its own
    save (#1299); this wrapper stays the only way this package retires an entry,
    so the refusal is provable here without trusting the reporter's.
    """
    if not apply:
        return False
    return reporter.resolve(key, comment=comment, apply=True)


#: How long the board read may take before it counts as unmeasured. The
#: collection shells out to `gh` (no timeout of its own) and once per item to
#: `git ls-remote`; measured on 2026-09-17 under ~40 concurrent lanes, one
#: collection took ~150s. The bound sits inside the pass's own nominal budget
#: (`cli.py`: `RECONCILE_LEASE_TTL_SECONDS` is twice a 450s pass), so a stalled
#: board degrades to CANNOT-ASSESS instead of wedging the fleet's only scheduled
#: reconciler — and it is generous enough that a loaded read still lands.
COLLECT_TIMEOUT_SECONDS = 300.0

#: The collector, run as a CHILD so the timeout above can actually be enforced:
#: ``subprocess.run(timeout=…)`` is the only mechanism that bounds a call which
#: itself blocks inside ``subprocess``. It reads the runtime state under the root it
#: is handed, while ``gh`` resolves the repository from the code's own checkout —
#: the same distinction ``collect_from_github(root)`` already draws.
_COLLECTOR = """
import json, sys
from pathlib import Path
from governance.lifecycle.audit import audit, load_quarantine
from governance.lifecycle.cli import collect_from_github, load_baseline
root = Path(sys.argv[1])
record = collect_from_github(root)
quarantine = load_quarantine(load_baseline(root))
print(json.dumps({
    "record": record,
    "findings": [
        {"code": f.code, "subject": f.subject, "detail": f.detail}
        for f in audit(record, quarantine)
    ],
}))
"""


def _measure(root: Path | str, *, timeout: float = COLLECT_TIMEOUT_SECONDS) -> tuple[list, dict] | None:
    """The live audit's findings and the record they came from, or ``None``.

    ``None`` means the board could not be read. It is deliberately not an empty
    finding list: "no findings" and "I could not look" must not share a value, or an
    unreachable board would resolve every finding on it. A timeout, a spawn failure,
    a non-zero exit and a half-written answer are all *could not look*.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _COLLECTOR, str(root)],
            cwd=str(Path(__file__).resolve().parents[2]),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:  # noqa: BLE001 - a timeout or a spawn failure is an unmeasured board
        return None
    if proc.returncode != 0:
        return None
    try:
        payload = json.loads(proc.stdout)
        return [Charged(**entry) for entry in payload["findings"]], payload["record"]
    except Exception:  # noqa: BLE001 - a half-written answer is not an answer
        return None


def _speakable(record: dict, quarantine: Sequence | None = None) -> set[str]:
    """Every subject the audit can be asked about in this record.

    An item it collected, plus a quarantine entry it will rule on. A subject
    outside this set is one the audit cannot speak about, so its silence means
    nothing and the finding stays.
    """
    subjects = {f"#{item.get('issue')}" for item in record.get("items") or []}
    for entry in quarantine or ():
        subject = getattr(entry, "subject", None)
        if subject:
            subjects.add(str(subject))
    return subjects


def _resolved_comment(entry: Entry, record: dict, findings: Sequence) -> str:
    """The evidence a resolution carries: the re-measurement, and what changed."""
    where = str(record.get("collected_at") or "") or "(no commit recorded)"
    for_subject = sum(1 for finding in findings if finding.subject == entry.subject)
    return (
        "Resolved: the lifecycle invariant this finding names is no longer charged.\n\n"
        f"- fingerprint: `{entry.key}`\n"
        f"- invariant: `{entry.code}`\n"
        f"- item: {entry.subject}\n"
        f"- re-measured at: `{where}`\n"
        f"- finding(s) charged for {entry.subject} in the live record: {for_subject}\n"
        f"- finding(s) charged in the whole record: {len(findings)}\n\n"
        f"`{entry.code}` is not among them, so the violation is no longer owed for "
        f"{entry.subject}, and the fingerprint is retired from the dedupe ledger. A "
        "genuine recurrence of this violation on this subject now files as a new "
        "finding rather than being swallowed by this one.\n\n"
        "Reported by `governance/reconcile` (issue #973).\n"
    )


def recheck_findings(
    reporter: BoardReporter,
    *,
    root: Path | str,
    apply: bool = False,
    record: dict | None = None,
    quarantine: Sequence | None = None,
    closer: FindingCloser | None = None,
    measure: Callable[[Path | str], tuple[list, dict] | None] | None = None,
) -> list[FindingState]:
    """Re-measure every filed lifecycle finding and drive it to a terminal state.

    ``record`` / ``quarantine`` / ``measure`` are injection seams: a test supplies
    the board it wants audited instead of reading the real one, exactly as ``sweep``
    takes an operations port. With no lifecycle finding in the ledger this is a
    local file read and nothing else — no board, no network — so a scratch fixture
    and the cron's own dry run pay nothing for it.
    """
    entries, unreadable = lifecycle_entries(read_ledger(reporter))
    states = [
        FindingState(
            key=key,
            outcome=UNMEASURED,
            detail="the fingerprint could not be split into an invariant and a subject",
        )
        for key in unreadable
    ]
    if not entries:
        return states
    if apply and closer is None:
        raise ValueError("recheck_findings requires a closer when apply is true (see GhCloser)")

    measured: tuple[list, dict] | None
    if record is not None:
        from governance.lifecycle.audit import audit as run_audit  # noqa: PLC0415 - optional at import time

        charged_now = run_audit(record, quarantine or ())
        measured = (
            [Charged(str(finding.code), str(finding.subject), str(finding.detail)) for finding in charged_now],
            record,
        )
    else:
        measured = (measure or _measure)(root)

    if measured is None:
        states.extend(
            FindingState(
                key=entry.key,
                outcome=UNMEASURED,
                code=entry.code,
                subject=entry.subject,
                number=entry.number,
                detail=(
                    "the lifecycle record could not be read, so whether "
                    f"{entry.code} is still owed for {entry.subject} is unmeasured — "
                    "nothing is resolved on an unread board"
                ),
            )
            for entry in entries
        )
        return states

    findings, collected = measured
    charged = {(str(finding.code), str(finding.subject)) for finding in findings}
    speakable = _speakable(collected, quarantine)

    for entry in entries:
        if (entry.code, entry.subject) in charged:
            live = next(
                (str(finding.detail) for finding in findings
                 if finding.code == entry.code and finding.subject == entry.subject),
                "still charged",
            )
            states.append(
                FindingState(
                    key=entry.key,
                    outcome=STILL_OWED,
                    code=entry.code,
                    subject=entry.subject,
                    number=entry.number,
                    detail=f"still charged — {live}",
                )
            )
            continue
        if entry.subject not in speakable:
            states.append(
                FindingState(
                    key=entry.key,
                    outcome=UNMEASURED,
                    code=entry.code,
                    subject=entry.subject,
                    number=entry.number,
                    detail=(
                        f"the audit cannot speak about {entry.subject} — it is not in the collected "
                        "record, so its silence is not evidence the finding cleared"
                    ),
                )
            )
            continue

        comment = _resolved_comment(entry, collected, findings)
        if not apply:
            states.append(
                FindingState(
                    key=entry.key,
                    outcome=WOULD_RESOLVE,
                    code=entry.code,
                    subject=entry.subject,
                    number=entry.number,
                    detail=(
                        f"{entry.code} is no longer charged for {entry.subject}; a pass with --apply "
                        f"closes #{entry.number} and retires the fingerprint"
                    ),
                )
            )
            continue
        try:
            if entry.number:
                # Guarded above: a resolve without a closer would retire the
                # fingerprint while leaving the board artifact open.
                assert closer is not None
                closer.close(entry.number, comment)
            resolve_key(reporter, entry.key, comment="", apply=True)
        except Exception as exc:  # noqa: BLE001 - a lost board write is data, not a crash
            states.append(
                FindingState(
                    key=entry.key,
                    outcome=FAILED,
                    code=entry.code,
                    subject=entry.subject,
                    number=entry.number,
                    detail=(
                        f"{type(exc).__name__}: {exc}; the fingerprint is kept, so the next pass "
                        "retries it rather than leaving an open issue nobody would look at again"
                    )[:300],
                )
            )
            continue
        states.append(
            FindingState(
                key=entry.key,
                outcome=RESOLVED,
                code=entry.code,
                subject=entry.subject,
                number=entry.number,
                detail=(
                    f"{entry.code} is no longer charged for {entry.subject}"
                    + (f"; closed #{entry.number}" if entry.number else "")
                    + " and the fingerprint is retired"
                ),
            )
        )
    return states


def counts(states: Sequence[FindingState]) -> dict[str, int]:
    """How many findings reached each outcome, in a stable order."""
    return {
        outcome: sum(1 for state in states if state.outcome == outcome)
        for outcome in (RESOLVED, WOULD_RESOLVE, STILL_OWED, UNMEASURED, FAILED)
    }
