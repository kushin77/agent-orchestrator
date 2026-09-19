"""Board reporting — a governance finding reaches the board as an issue (#321).

The reconciliation worker deliberately shelves a lane whose unmerged work exists
nowhere else, and the lifecycle audit finds items whose artifacts never reached a
terminal state. Both used to report into a log line nobody reads. This module is
the shared mechanism that turns such a finding into a board artifact — a GitHub
issue — with the properties a fleet with no human in the loop can rely on:

* **idempotent** — one issue per *fingerprint* (the identity of "the same
  unresolved finding"), deduped through a local ledger, so repeated passes do not
  spam the board for the same unresolved lane or the same broken invariant;
* **dry-run by default** — no board write happens unless ``apply`` is true, so a
  scheduled hook never files an issue without an explicit ``--apply``;
* **offline-testable** — the board effects are an injected port (``IssueFiler``),
  so the reporting logic is exercised in tests with no network.

The dedupe ledger is a JSON document keyed by fingerprint. A finding that is no
longer unresolved (a shelved lane whose work landed) is dropped via ``resolve``:
the entry is removed, so a genuinely new occurrence of the same violation files
again rather than being silently swallowed.

``resolve`` is the finding's **terminal move** (issue #1299), and it is held to
the same two rules as ``report``: ``apply`` gates *every* write — the board
comment or close AND the ledger save — and the move is terminal, so the board
issue is closed (``close=True``), not merely commented, when the subject the
finding names has itself closed. Measured before this rule: ``lifecycle audit``
without ``--apply`` re-observed a ``VERIFY_EVIDENCE_MISSING`` finding for a
closed subject, ``resolve`` popped the fingerprint and saved the ledger while
skipping the comment, so the filed issue stayed open with no note and a genuine
recurrence would file a second issue — a dry run that wrote, and a resolution
that was not terminal.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol, Sequence

#: Where the dedupe ledger lives, relative to the repository root.
DEFAULT_LEDGER_RELPATH = ".fleet/board-reports.json"

#: What one report call did with one finding.
FILED = "filed"
DEDUPED = "deduped"
DRY_RUN = "dry-run"

#: The marker embedded in a filed issue's body, so a future pass (or a lost
#: ledger) can recognise the same finding on GitHub rather than only locally.
MARKER_PREFIX = "board-finding-key"

#: Classification a filed finding must declare (conformance gate, issue #140).
DEFAULT_LABELS: tuple[str, ...] = (
    "type:governance",
    "pillar:autonomous-ops",
    "area:board",
    "class:enterprise",
)

#: What one report call did when a finding turned out to be obsolete-by-close
#: rather than filed or deduped (issue #1266).
OBSOLETE_BY_CLOSE = "obsolete-by-close"

#: Invariant codes that only warrant a live board issue while the item's own
#: GitHub issue is still OPEN (issue #1266). `VERIFY_EVIDENCE_MISSING` is the
#: one measured case: 27 squash merges landed on 2026-09-18 and closed 0
#: issues, and the auto-filer went on to raise fresh `VERIFY_EVIDENCE_MISSING`
#: board issues (#992, #1247, #1251) against items whose GitHub issue was
#: ALREADY closed by hand — an evidence gap on a closed item is history, not
#: something anyone can act on without reopening the issue first.
OPEN_ONLY_CODES: frozenset[str] = frozenset({"VERIFY_EVIDENCE_MISSING"})


class IssueFiler(Protocol):
    """The board-write effects. Injected, so reporting is testable offline."""

    def create(self, title: str, body: str, labels: Sequence[str]) -> int:
        """File a new issue; return its number."""

    def comment(self, number: int, body: str) -> None:
        """Add a comment to an existing issue."""

    def close(self, number: int, comment: str) -> None:
        """Close an existing issue with ``comment`` as its closing evidence.

        One call on purpose (issue #1299): a comment written first and a close
        that then failed would leave an open issue whose text says it is resolved.
        """


class FindingLike(Protocol):
    """What the lifecycle audit emits: a named violation with its remediation."""

    code: str
    subject: str
    detail: str
    remediation: str


@dataclass(frozen=True)
class BoardReport:
    """What one report call did with one finding."""

    key: str
    action: str
    number: int | None = None


def finding_key(kind: str, subject: str) -> str:
    """The identity of "the same unresolved finding": its kind plus its subject.

    Two different subjects failing the same rule are two findings; the same
    subject failing repeatedly across passes is one finding that is not re-filed.
    """
    return f"{kind}:{subject}"


class BoardReporter:
    """Files findings as board issues, deduped by fingerprint through a ledger.

    The ledger is the dedupe authority: a fingerprint already in it is reported
    as ``deduped`` and not written again. ``apply`` gates every *board* write (an
    issue create or a comment); without it a call is a ``dry-run`` that reports
    what would happen and writes nothing, ledger included.
    """

    def __init__(self, filer: IssueFiler, ledger: Path | None = None) -> None:
        self.filer = filer
        self.ledger = ledger or Path(DEFAULT_LEDGER_RELPATH)

    # -- ledger ------------------------------------------------------------

    def _load(self) -> dict:
        if not self.ledger.exists():
            return {}
        try:
            return json.loads(self.ledger.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        self.ledger.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    # -- reporting ---------------------------------------------------------

    def report(
        self,
        key: str,
        *,
        title: str,
        body: str,
        labels: Sequence[str] = (),
        apply: bool = False,
    ) -> BoardReport:
        data = self._load()
        existing = data.get(key)
        if existing is not None:
            number = existing.get("number")
            return BoardReport(key, DEDUPED, int(number) if number else None)
        if not apply:
            return BoardReport(key, DRY_RUN)
        number = self.filer.create(title, _with_marker(body, key), list(labels))
        data[key] = {"number": number, "title": title}
        self._save(data)
        return BoardReport(key, FILED, number)

    def resolve(
        self, key: str, *, comment: str = "", apply: bool = False, close: bool = False
    ) -> bool:
        """The finding's terminal move: retire it from the ledger and from the board.

        Returns whether an entry for ``key`` exists (and so was — or on a dry run,
        would be — resolved). ``apply`` gates **every** write, the ledger save
        included: a dry run reports and changes nothing, exactly as ``report``
        does. Retiring the fingerprint without ``apply`` was measured to drop the
        dedupe entry while the filed issue stayed open and uncommented (#1299) —
        a genuine recurrence then filed a second issue beside the stale one.

        ``close`` makes the move terminal on the board: the filed issue is closed
        with ``comment`` as its closing evidence (one call, so a comment can never
        outlive a failed close). Without it the issue is only commented, which is
        what a caller wants when the finding is retired but the item it names is
        still being driven elsewhere. The board write runs **before** the ledger
        save, so a lost write keeps the fingerprint and the next pass retries it
        rather than leaving an open issue nobody would look at again.
        """
        data = self._load()
        entry = data.get(key)
        if entry is None:
            return False
        if not apply:
            return True
        number = entry.get("number")
        if number:
            if close:
                self.filer.close(int(number), comment or _closing_comment(key))
            elif comment:
                self.filer.comment(int(number), comment)
        data.pop(key, None)
        self._save(data)
        return True


def board_report_findings(
    findings: Iterable[FindingLike],
    reporter: BoardReporter,
    *,
    apply: bool = False,
    closed_subjects: frozenset[str] = frozenset(),
) -> list[BoardReport]:
    """File one board finding per lifecycle violation (deduped by fingerprint).

    ``findings`` are ``Finding``-shaped objects carrying ``code``, ``subject``,
    ``detail`` and ``remediation``; the fingerprint is ``code:subject``, so the
    same broken invariant on the same item is one issue no matter how many
    passes observe it.

    ``closed_subjects`` names every subject (``"#<n>"``) whose OWN GitHub issue
    is closed, as read from the SAME lifecycle record the findings came from —
    this function never re-derives GitHub state itself (issue #1266). A
    finding whose code is in :data:`OPEN_ONLY_CODES` and whose subject is in
    ``closed_subjects`` is REFUSED as a new board issue, by name: it is never
    handed to ``reporter.report`` (so ``filer.create`` is never called for it),
    whatever ``apply`` is. Any board issue already filed for that same
    ``code:subject`` from an earlier, still-open pass is resolved instead, so
    it does not rot on the board beside a finding nobody can act on without
    reopening the issue first.
    """
    reports: list[BoardReport] = []
    for finding in findings:
        code = str(finding.code)
        subject = str(finding.subject)
        key = finding_key(f"lifecycle:{code}", subject)

        if code in OPEN_ONLY_CODES and subject in closed_subjects:
            # The terminal move (#1299): the finding resolves when its subject
            # closes — the filed board issue is CLOSED with the measurement as
            # evidence, under the same ``apply`` gate as every other board write.
            reporter.resolve(
                key,
                comment=(
                    f"obsolete-by-close: {subject} is closed, so this {code} finding is no "
                    f"longer a live board item.\n\n- detail: {finding.detail}\n\n"
                    "Closed by `governance/lifecycle` (issue #1299): the fingerprint is "
                    "retired from the dedupe ledger, so a genuine recurrence on a reopened "
                    "item files afresh rather than being swallowed by this issue.\n"
                ),
                apply=apply,
                close=True,
            )
            reports.append(BoardReport(key, OBSOLETE_BY_CLOSE))
            continue

        reports.append(
            reporter.report(
                key,
                title=f"[lifecycle] {code} — {subject}",
                body=_finding_body(finding),
                labels=DEFAULT_LABELS,
                apply=apply,
            )
        )
    return reports


def _with_marker(body: str, key: str) -> str:
    return f"{body}\n\n<!-- {MARKER_PREFIX}: {key} -->\n"


def _closing_comment(key: str) -> str:
    """The evidence a close carries when the caller supplied none (#1299)."""
    return (
        f"Resolved: the finding `{key}` is no longer unresolved, and its fingerprint is "
        "retired from the dedupe ledger so a genuine recurrence files afresh.\n\n"
        "Closed by `governance/lifecycle` (issue #1299).\n"
    )


def _finding_body(finding: FindingLike) -> str:
    return (
        "A lifecycle finding has not reached a terminal state.\n\n"
        f"- item: {finding.subject}\n"
        f"- violation: `{finding.code}`\n"
        f"- detail: {finding.detail}\n"
        f"- remediation: {finding.remediation}\n\n"
        "Reported by `governance/lifecycle` (issue #321).\n"
    )


class GhFiler:
    """The real board effects through ``gh``, on the repository of the cwd."""

    def __init__(self, repo: str | None = None) -> None:
        self.repo = repo

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(args, capture_output=True, text=True)

    def _args(self, base: list[str]) -> list[str]:
        if self.repo:
            return [*base, "--repo", self.repo]
        return base

    def create(self, title: str, body: str, labels: Sequence[str]) -> int:
        cmd = self._args(["gh", "issue", "create", "--title", title, "--body", body])
        for label in labels:
            cmd.extend(["--label", label])
        result = self._run(cmd)
        if result.returncode != 0:
            raise RuntimeError(
                f"gh issue create failed ({result.returncode}): "
                f"{(result.stderr or result.stdout).strip()[-200:]}"
            )
        return _number_from_create_output(result.stdout)

    def comment(self, number: int, body: str) -> None:
        cmd = self._args(["gh", "issue", "comment", str(number), "--body", body])
        result = self._run(cmd)
        if result.returncode != 0:
            raise RuntimeError(
                f"gh issue comment failed ({result.returncode}): "
                f"{(result.stderr or result.stdout).strip()[-200:]}"
            )

    def close(self, number: int, comment: str) -> None:
        """``gh issue close --comment``: one call, so the comment cannot outlive a
        failed close (#1299) — the same shape ``governance/reconcile/findings.py``'s
        ``GhCloser`` uses, so the two terminal moves cannot drift apart."""
        cmd = self._args(["gh", "issue", "close", str(number), "--comment", comment])
        result = self._run(cmd)
        if result.returncode != 0:
            raise RuntimeError(
                f"gh issue close failed ({result.returncode}): "
                f"{(result.stderr or result.stdout).strip()[-200:]}"
            )


def _number_from_create_output(stdout: str) -> int:
    """``gh issue create`` prints the new issue's URL; its tail is the number."""
    lines = (stdout or "").strip().splitlines()
    tail = (lines[-1] if lines else "").rstrip("/")
    number = tail.rsplit("/", 1)[-1]
    try:
        return int(number)
    except ValueError:
        raise RuntimeError(f"could not parse an issue number from {tail!r}") from None
