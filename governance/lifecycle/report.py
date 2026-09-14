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


class IssueFiler(Protocol):
    """The board-write effects. Injected, so reporting is testable offline."""

    def create(self, title: str, body: str, labels: Sequence[str]) -> int:
        """File a new issue; return its number."""

    def comment(self, number: int, body: str) -> None:
        """Add a comment to an existing issue."""


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

    def resolve(self, key: str, *, comment: str = "", apply: bool = False) -> bool:
        """Drop a finding from the ledger: it is no longer unresolved.

        A comment is added to the filed issue only when ``apply`` is true (it is a
        board write). The ledger entry is removed either way, so a genuinely new
        occurrence of the same violation files again.
        """
        data = self._load()
        entry = data.pop(key, None)
        if entry is None:
            return False
        if apply and comment:
            number = entry.get("number")
            if number:
                self.filer.comment(int(number), comment)
        self._save(data)
        return True


def board_report_findings(
    findings: Iterable[FindingLike],
    reporter: BoardReporter,
    *,
    apply: bool = False,
) -> list[BoardReport]:
    """File one board finding per lifecycle violation (deduped by fingerprint).

    ``findings`` are ``Finding``-shaped objects carrying ``code``, ``subject``,
    ``detail`` and ``remediation``; the fingerprint is ``code:subject``, so the
    same broken invariant on the same item is one issue no matter how many
    passes observe it.
    """
    reports: list[BoardReport] = []
    for finding in findings:
        code = str(finding.code)
        subject = str(finding.subject)
        reports.append(
            reporter.report(
                finding_key(f"lifecycle:{code}", subject),
                title=f"[lifecycle] {code} — {subject}",
                body=_finding_body(finding),
                labels=DEFAULT_LABELS,
                apply=apply,
            )
        )
    return reports


def _with_marker(body: str, key: str) -> str:
    return f"{body}\n\n<!-- {MARKER_PREFIX}: {key} -->\n"


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


def _number_from_create_output(stdout: str) -> int:
    """``gh issue create`` prints the new issue's URL; its tail is the number."""
    lines = (stdout or "").strip().splitlines()
    tail = (lines[-1] if lines else "").rstrip("/")
    number = tail.rsplit("/", 1)[-1]
    try:
        return int(number)
    except ValueError:
        raise RuntimeError(f"could not parse an issue number from {tail!r}") from None
