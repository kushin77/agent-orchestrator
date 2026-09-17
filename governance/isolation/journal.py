"""The append-only audit-verdict trail — a durable record next to `audit.py` (issue #885).

`audit.py` re-derives isolation from git and the filesystem every time it is
asked, and `cli.py cmd_audit` prints the verdict once, to a terminal a caller
may not have kept. This module gives every audited verdict a durable record: a
JSON Lines file under the lane-record tree (``.fleet/lanes/journal.jsonl``,
alongside ``speculative``'s own namespace under ``.fleet/lanes``) that is
**appended to, never rewritten** — `append()` opens the file in append mode and
writes exactly one line, so a prior verdict can never be edited or dropped by a
later run.

Every entry validates against ``isolation.schema.json``'s ``#/$defs/journal_entry``
(``governance/isolation/tests/test_schema.py``), and `cli.py cmd_audit` writes
one entry per audited lane on every run — `scripts/check-session-isolation.sh`
provokes a missing/short-circuited write and a schema-invalid entry, and both
must be refused by name.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .violation import Violation

JOURNAL_RELPATH = ".fleet/lanes/journal.jsonl"

SCHEMA = "ao.isolation/journal-entry-v1"


@dataclass(frozen=True)
class JournalEntry:
    """One audited verdict for one lane, at one point in time."""

    schema: str
    ts: float
    session_id: str
    ok: bool
    codes: tuple

    def to_json(self) -> dict:
        return {
            "schema": self.schema,
            "ts": self.ts,
            "session_id": self.session_id,
            "ok": self.ok,
            "codes": list(self.codes),
        }

    @classmethod
    def from_json(cls, payload: dict) -> "JournalEntry":
        return cls(
            schema=str(payload["schema"]),
            ts=float(payload["ts"]),
            session_id=str(payload["session_id"]),
            ok=bool(payload["ok"]),
            codes=tuple(str(code) for code in payload.get("codes", [])),
        )


def journal_path(main: Path | str) -> Path:
    return Path(main) / JOURNAL_RELPATH


def append(main: Path | str, session_id: str, problems: Sequence[Violation]) -> JournalEntry:
    """Append exactly one record for this verdict. Never rewrites a prior line.

    Called once per audited lane, from ``cli.py cmd_audit`` — the refusal (or
    clean pass) site, not a test reconstructing it after the fact.
    """
    entry = JournalEntry(
        schema=SCHEMA,
        ts=time.time(),
        session_id=session_id,
        ok=not problems,
        codes=tuple(problem.code for problem in problems),
    )
    path = journal_path(main)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry.to_json(), sort_keys=True) + "\n")
    return entry


def read_all(main: Path | str) -> list[JournalEntry]:
    """Every record ever appended, oldest first. Empty when nothing was audited."""
    path = journal_path(main)
    if not path.exists():
        return []
    entries: list[JournalEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        entries.append(JournalEntry.from_json(json.loads(line)))
    return entries


def last_for(main: Path | str, session_id: str) -> JournalEntry | None:
    """The most recent recorded verdict for one lane, or ``None`` if never audited."""
    last = None
    for entry in read_all(main):
        if entry.session_id == session_id:
            last = entry
    return last
