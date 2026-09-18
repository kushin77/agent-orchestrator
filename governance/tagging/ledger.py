"""The tag authority's append-only ledger (issue #1175).

Every decision the authority makes — a plan derived, a board judged — is recorded
as one row, in order, and never rewritten. The ledger is what turns "the tagging
system says this tag set owes these gates" from an assertion into a record anyone
can re-read: a lane that derives a plan and then lands a diff that owes fewer
gates than the record says is visible, rather than a matter of trust.

Three disciplines, matching the sibling governance surfaces:

* **the shape is frozen and validated BEFORE the write.** A row is checked
  against the `ledger_row` shape in `tagging.schema.json` first, so a malformed
  record is refused rather than becoming a permanently unreadable line — an audit
  trail with one bad row is not an audit trail.
* **the location is outside the working tree's tracked files.** The default is
  `.fleet/tagging/ledger.jsonl` (gitignored runtime state, like `.fleet/runs/`),
  overridable with ``AO_TAGGING_LEDGER`` so a gate can drive a round trip in a
  scratch directory and leave the tree clean.
* **reads are bounded and total.** :func:`read` returns every well-formed row and
  REPORTS the malformed ones by line number instead of raising, because a ledger
  you cannot read past is a ledger you cannot audit.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import schema as _schema  # noqa: E402

LEDGER_REL = Path(".fleet") / "tagging" / "ledger.jsonl"
ENV_LEDGER = "AO_TAGGING_LEDGER"

KINDS = ("plan", "check", "board", "lift")
DECISIONS = ("ok", "refused", "cannot-assess")


class LedgerRefused(Exception):
    """A row was refused before it was written. The shape is frozen."""


def ledger_path(root: Path | None = None) -> Path:
    """Where the ledger lives: the environment override, else the repo default."""
    override = os.environ.get(ENV_LEDGER, "").strip()
    if override:
        return Path(override)
    base = Path(root) if root else Path.cwd()
    return base / LEDGER_REL


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def row(
    kind: str,
    subject: str,
    decision: str,
    *,
    exit_code: Optional[int] = None,
    tags: Optional[List[str]] = None,
    gates: Optional[int] = None,
    finops_floor: str = "",
    rules_fired: Optional[List[str]] = None,
    finding_codes: Optional[List[str]] = None,
    at: str = "",
) -> Dict[str, Any]:
    """Build one ledger row. Raises :class:`LedgerRefused` on an illegal value."""
    if kind not in KINDS:
        raise LedgerRefused("kind %r is not one of %s" % (kind, ", ".join(KINDS)))
    if decision not in DECISIONS:
        raise LedgerRefused(
            "decision %r is not one of %s" % (decision, ", ".join(DECISIONS))
        )
    if not subject:
        raise LedgerRefused("a ledger row must name its subject")
    record: Dict[str, Any] = {
        "at": at or _now(),
        "kind": kind,
        "subject": subject,
        "decision": decision,
    }
    if exit_code is not None:
        record["exit_code"] = int(exit_code)
    if tags:
        record["tags"] = sorted(tags)
    if gates is not None:
        record["gates"] = int(gates)
    if finops_floor:
        record["finops_floor"] = finops_floor
    if rules_fired:
        record["rules_fired"] = sorted(rules_fired)
    if finding_codes:
        record["finding_codes"] = sorted(set(finding_codes))
    return record


def semantic_problems(record: Mapping[str, Any]) -> Tuple[str, ...]:
    """Rules a JSON shape cannot express, checked at the one enforcement point.

    The frozen shape gets most of the way, but ``required`` only proves a KEY is
    present — so an empty ``subject`` satisfies the schema while making the row
    useless ("a ledger row must name its subject"). Rather than rely on every
    caller going through :func:`row`, :func:`append` enforces these too, so a
    hand-built dict cannot slip past the builder.
    """
    problems: List[str] = []
    if not str(record.get("subject", "")).strip():
        problems.append("$.subject: a ledger row must name its subject")
    if str(record.get("kind", "")) not in KINDS:
        problems.append(
            "$.kind: %r is not one of %s" % (record.get("kind"), ", ".join(KINDS))
        )
    if str(record.get("decision", "")) not in DECISIONS:
        problems.append(
            "$.decision: %r is not one of %s"
            % (record.get("decision"), ", ".join(DECISIONS))
        )
    return tuple(problems)


def append(record: Mapping[str, Any], path: Path) -> Path:
    """Validate a row against the frozen shape, then append it. Returns the path."""
    document = dict(record)
    violations = tuple(semantic_problems(document)) + tuple(
        _schema.problems(document, "ledger_row")
    )
    if violations:
        raise LedgerRefused(
            "the ledger row does not satisfy its frozen shape:\n  - %s"
            % "\n  - ".join(violations)
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(document, sort_keys=True, ensure_ascii=False) + "\n")
    return path


def record(
    kind: str,
    subject: str,
    decision: str,
    root: Path | None = None,
    path: Path | None = None,
    **fields: Any,
) -> Optional[Path]:
    """Build, validate and append one row. Never raises on an unwritable ledger.

    A ledger that cannot be written must not take the verb down with it: the
    decision was still made and the caller still needs its answer. The failure is
    reported on stderr and ``None`` is returned, so a caller can tell the two
    apart without a try/except it might get wrong.
    """
    target = path or ledger_path(root)
    try:
        return append(row(kind, subject, decision, **fields), target)
    except (LedgerRefused, OSError) as exc:
        print("tagging-ledger: could not record (%s)" % exc, file=sys.stderr)
        return None


def read(path: Path) -> Tuple[Tuple[Dict[str, Any], ...], Tuple[str, ...]]:
    """Read a ledger. Returns ``(rows, malformed)``, naming each bad line."""
    if not path.is_file():
        return (), ()
    rows: List[Dict[str, Any]] = []
    malformed: List[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except ValueError as exc:
            malformed.append("line %d: not JSON (%s)" % (number, exc))
            continue
        violations = _schema.problems(parsed, "ledger_row")
        if violations:
            malformed.append("line %d: %s" % (number, violations[0]))
            continue
        rows.append(parsed)
    return tuple(rows), tuple(malformed)


def summarise(path: Path) -> Dict[str, Any]:
    """Counts by kind and decision, plus the malformed lines — the live view."""
    rows, malformed = read(path)
    by_kind: Dict[str, int] = {}
    by_decision: Dict[str, int] = {}
    for entry in rows:
        by_kind[str(entry["kind"])] = by_kind.get(str(entry["kind"]), 0) + 1
        by_decision[str(entry["decision"])] = by_decision.get(str(entry["decision"]), 0) + 1
    return {
        "path": str(path),
        "rows": len(rows),
        "by_kind": dict(sorted(by_kind.items())),
        "by_decision": dict(sorted(by_decision.items())),
        "malformed": list(malformed),
        "last": rows[-1] if rows else None,
    }
