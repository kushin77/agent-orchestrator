"""The out-of-epic **pool** — the queue for work the active epic is not driving.

Epic focus (#707) makes the fleet drive exactly one epic. That focus is only
honest if the work it defers is *parked*, not deleted: an out-of-epic issue is
refused `out-of-epic-pooled`, recorded here, and picked up again when the focus
moves on. A refusal that silently drops the issue is indistinguishable from
losing it.

The pool is deliberately **structurally identical to the claim ledger** it
complements — an append-only JSONL rail under ``.board/`` — so it is offline,
auditable and conflict-tolerant by construction. It records *why* an issue was
parked (``reason``) and *when* (``at``), so the pool is a decision log, not just
a set of numbers.

Two properties this module must be able to prove (GR-12 / AO-GR-19):

* a **malformed line is reported** with its line number, never skipped — a reader
  that quietly ignores what it cannot parse turns a corrupt pool into an empty
  one, which reads as "nothing was ever pooled";
* **a drain reports what it drained** — ``drain`` returns the numbers it removed,
  and the convenience ``drain_and_report`` returns a record per drained issue, so
  "the pool drained" is evidence rather than an assertion.

``self_control`` provokes both failures against a temporary pool, so the gate
driving it cannot pass vacuously.

Everything here is stdlib-only and offline.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: The pool rail. A runtime artifact under the board directory, beside
#: ``claims.jsonl``/``claims/`` — same shape, same append-only discipline.
POOL_PATH = Path(".board/pool.jsonl")

#: The only reason the pool is written for today: the issue is outside the
#: active epic. Declared as a closed-ish constant so a reader can tell a
#: deliberate park from a typo.
REASON_OUT_OF_EPIC = "out-of-epic"


class PoolInvalid(ValueError):
    """A pool rail is unreadable or carries a malformed record."""


@dataclass(frozen=True)
class PoolRecord:
    """One line of the append-only pool rail."""

    issue: int
    reason: str
    at: str

    def to_json(self) -> dict[str, object]:
        return {"issue": self.issue, "reason": self.reason, "at": self.at}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_record(obj: object, where: str) -> PoolRecord:
    """Validate one record. Raises ``PoolInvalid`` naming the offending line."""
    if not isinstance(obj, dict):
        raise PoolInvalid(f"{where}: record must be a JSON object, got {type(obj).__name__}")
    issue = obj.get("issue")
    if isinstance(issue, bool) or not isinstance(issue, int) or issue <= 0:
        raise PoolInvalid(f"{where}: field 'issue' must be a positive integer")
    reason = obj.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise PoolInvalid(f"{where}: field 'reason' must be a non-empty string")
    at = obj.get("at")
    if not isinstance(at, str) or not at.strip():
        raise PoolInvalid(f"{where}: field 'at' must be a non-empty string")
    return PoolRecord(issue=issue, reason=reason.strip(), at=at.strip())


def note(issue: int, reason: str, at: str | None = None, path: Path | str = POOL_PATH) -> PoolRecord:
    """Append one park record for ``issue`` and return it.

    Append-only and ``O_APPEND``, so two concurrent lanes interleave whole lines
    rather than corrupting each other's write.
    """
    if isinstance(issue, bool) or not isinstance(issue, int) or issue <= 0:
        raise PoolInvalid(f"pool: issue must be a positive integer, got {issue!r}")
    if not isinstance(reason, str) or not reason.strip():
        raise PoolInvalid("pool: reason must be a non-empty string")
    record = PoolRecord(issue=issue, reason=reason.strip(), at=(at or _now_iso()))
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(record.to_json(), sort_keys=False) + "\n"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    return record


def read(path: Path | str = POOL_PATH) -> list[PoolRecord]:
    """Read every pool record in write order.

    Raises ``PoolInvalid`` naming the line of any malformed record: a reader that
    skipped it would report a shorter pool than the rail records — a silent drop,
    which is exactly the failure this module exists to prevent.
    """
    target = Path(path)
    if not target.exists():
        return []
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise PoolInvalid(f"{target}: unreadable ({exc})") from exc
    records: list[PoolRecord] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PoolInvalid(f"{target}:{lineno}: invalid JSON ({exc.msg})") from exc
        records.append(_parse_record(obj, f"{target}:{lineno}"))
    return records


def numbers(path: Path | str = POOL_PATH) -> list[int]:
    """The pooled issue numbers, de-duplicated, in first-park order."""
    seen: list[int] = []
    for record in read(path):
        if record.issue not in seen:
            seen.append(record.issue)
    return seen


def drain(path: Path | str = POOL_PATH) -> list[int]:
    """Clear the pool and RETURN the numbers it held — a drain reports, never drops.

    The drained set is computed *before* the rail is truncated, so the caller gets
    the evidence of what left the pool. A missing rail drains to an empty list
    (there was never anything to drop).
    """
    drained_numbers = numbers(path)
    target = Path(path)
    if target.exists():
        # Truncate rather than unlink: the rail's path stays stable for the next
        # park, and a concurrently-appending writer keeps writing to the same inode.
        with target.open("w", encoding="utf-8"):
            pass
    return drained_numbers


def drain_and_report(path: Path | str = POOL_PATH, *, epic: int | None = None) -> list[str]:
    """Drain the pool and return one report line per drained issue.

    Called when the resolver returns ``None`` (no focus): the pool empties itself
    and the caller prints what left it. ``epic``, when given, is named in the line
    so the report reads as "the focus left" rather than a bare list of numbers.
    """
    drained = drain(path)
    if not drained:
        return []
    listed = ", ".join(f"#{number}" for number in drained)
    if epic is None:
        return [f"pool: drained {len(drained)} issue(s) — no active epic: {listed}"]
    reason = f"focus left #{epic}"
    return [f"pool: drained {len(drained)} issue(s) — {reason}: {listed}"]


def record_and_raise_message(issue: int, reason: str = REASON_OUT_OF_EPIC) -> str:
    """The one-line summary a caller prints after parking ``issue``."""
    return f"pool: #{issue} parked (reason: {reason})"


# --- the anti-formality self-control ----------------------------------------


def self_control() -> list[str]:
    """Prove the pool can fail (GR-12 / AO-GR-19).

    Returns a problem per failed control; an empty list means every control held.
    The two mutants that matter:

    * a **malformed line must be reported** (a reader that ignores it turns a
      corrupt rail into an empty pool — a silent drop);
    * a **drain that drops without reporting must be caught** — proven by
      comparing the returned set against the rail's own contents before the
      drain, so a ``drain`` that returned ``[]`` while the rail held rows fails
      the control.
    """
    problems: list[str] = []

    def expect(name: str, condition: bool, detail: str) -> None:
        if not condition:
            problems.append(f"pool-self-control['{name}']: {detail}")

    with tempfile.TemporaryDirectory(prefix="ao-pool-selfcontrol.") as work:
        rail = Path(work) / "pool.jsonl"

        # Positive control: a fresh rail is empty, and reading it is not an error.
        expect("empty-rail", read(rail) == [], f"a missing rail must read as empty, got {read(rail)}")
        expect("empty-drain", drain(rail) == [], "draining an absent rail must report nothing drained")

        note(101, REASON_OUT_OF_EPIC, at="2026-01-01T00:00:00Z", path=rail)
        note(102, REASON_OUT_OF_EPIC, at="2026-01-01T00:00:01Z", path=rail)
        note(101, REASON_OUT_OF_EPIC, at="2026-01-01T00:00:02Z", path=rail)

        recorded = read(rail)
        expect(
            "append-preserves-every-record",
            [r.issue for r in recorded] == [101, 102, 101],
            f"expected [101, 102, 101], got {[r.issue for r in recorded]}",
        )
        expect("numbers-dedupe-in-order", numbers(rail) == [101, 102],
               f"expected [101, 102], got {numbers(rail)}")
        expect(
            "record-carries-reason-and-time",
            all(r.reason == REASON_OUT_OF_EPIC and r.at for r in recorded),
            "every record must carry a reason and a timestamp",
        )

        # Mutant 1: a malformed line must be REPORTED, never skipped.
        broken = Path(work) / "broken.jsonl"
        broken.write_text(
            json.dumps({"issue": 101, "reason": REASON_OUT_OF_EPIC, "at": "2026-01-01T00:00:00Z"})
            + "\nnot json\n",
            encoding="utf-8",
        )
        try:
            read(broken)
        except PoolInvalid as exc:
            expect("malformed-line-is-reported", ":2" in str(exc), f"the line number must be named, got: {exc}")
        else:
            problems.append("pool-self-control['malformed-line-is-reported']: a malformed line was silently skipped")

        # A record missing a field is malformed too (schema, not just JSON).
        for name, mutant in (
            ("missing-reason", {"issue": 101}),
            ("non-integer-issue", {"issue": "101", "reason": REASON_OUT_OF_EPIC, "at": "t"}),
            ("empty-reason", {"issue": 101, "reason": "", "at": "t"}),
            ("bool-issue", {"issue": True, "reason": REASON_OUT_OF_EPIC, "at": "t"}),
            ("missing-at", {"issue": 101, "reason": REASON_OUT_OF_EPIC}),
        ):
            bad = Path(work) / f"{name}.jsonl"
            bad.write_text(json.dumps(mutant) + "\n", encoding="utf-8")
            try:
                read(bad)
            except PoolInvalid:
                pass
            else:
                problems.append(f"pool-self-control['schema-rejects-{name}']: a malformed record was accepted")

        # Mutant 2: a drain that drops without reporting must be CAUGHT.
        before = numbers(rail)
        drained = drain(rail)
        expect(
            "drain-reports-what-it-drained",
            drained == before and drained == [101, 102],
            f"drain must return the pre-drain set {before}, got {drained}",
        )
        expect("drain-clears-the-rail", numbers(rail) == [], f"the rail must be clear after a drain, got {numbers(rail)}")
        expect("drain-is-idempotent", drain(rail) == [], "a second drain must report nothing")

        # The failure the control exists to catch: a drop that reports nothing.
        note(103, REASON_OUT_OF_EPIC, at="2026-01-01T00:00:03Z", path=rail)
        dropped_silently = numbers(rail)
        truncate_only(rail)
        expect(
            "a-silent-drop-is-detectable",
            bool(dropped_silently) and numbers(rail) == [],
            "the control's own premise failed: the rail did not hold a row to drop",
        )
        expect(
            "the-silent-drop-differs-from-drain",
            drained != dropped_silently,
            "a drop that reports nothing is distinguishable from a drain — the property under test",
        )

        for name, bad_issue in (("zero", 0), ("negative", -1), ("bool", True)):
            try:
                note(bad_issue, REASON_OUT_OF_EPIC, path=rail)
            except PoolInvalid:
                pass
            else:
                problems.append(f"pool-self-control['note-rejects-{name}-issue']: a bad issue number was accepted")
        try:
            note(104, "   ", path=rail)
        except PoolInvalid:
            pass
        else:
            problems.append("pool-self-control['note-rejects-blank-reason']: a blank reason was accepted")

    return problems


def truncate_only(path: Path | str) -> None:
    """Clear a rail WITHOUT reporting what it held — the silent-drop mutant's half.

    Never called by production code; it exists so ``self_control`` can construct
    the failure mode it is asserting is detectable.
    """
    target = Path(path)
    if target.exists():
        with target.open("w", encoding="utf-8"):
            pass
