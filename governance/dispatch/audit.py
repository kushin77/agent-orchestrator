"""Dispatch's append-only arbitration audit trail (issue #885).

``arbitrate()`` (claims.py) refuses or grants every dispatch, and until this
module existed that verdict was printed to a caller's terminal and then lost —
nothing durable recorded *why* a unit was refused, or what was granted. This
module is that durable trail, adjacent to (never a replacement for) the claims
ledger `claims.py` already keeps:

* :func:`record_refusal` and :func:`record_grant` each append exactly one JSON
  line for the arbitration outcome they describe.
* :func:`append` writes it and then proves the trail only grew: it re-reads
  the file and requires the bytes read before the write to be an exact prefix
  of what is on disk after — a second writer, a truncating open, or a bug
  never gets to silently replace the evidence.
* Every record validates against ``dispatch.schema.json`` (``$defs.auditRecord``),
  so a consumer can rely on the shape whether the trail was written today or a
  year ago.

Exit-code contract (repository convention, GR-12): a trail that cannot be
appended to safely raises :class:`AuditUnavailable`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platform
    fcntl = None  # type: ignore[assignment]

_PKG_DIR = Path(__file__).resolve().parent
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

from model import Arbitration  # noqa: E402

SCHEMA = "ao.dispatch/audit-v1"

KIND_REFUSAL = "refusal"
KIND_GRANT = "grant"
KINDS = (KIND_REFUSAL, KIND_GRANT)

DEFAULT_AUDIT_PATH = Path(".board/dispatch-audit.jsonl")


class AuditUnavailable(Exception):
    """The audit trail cannot be appended to safely."""


def _record(*, kind: str, issue: int, agent: str, at: str, **extra: Any) -> Dict[str, Any]:
    if kind not in KINDS:
        raise AuditUnavailable(f"record kind {kind!r} is not one of {KINDS}")
    record = {
        "schema": SCHEMA,
        "kind": kind,
        "issue": issue,
        "agent": agent,
        "at": at,
        "reason": None,
        "detail": None,
        "lane": None,
        "epic": None,
        "directive_id": None,
    }
    record.update(extra)
    return record


def record_refusal(*, issue: int, agent: str, at: str, reason: str, detail: str, lane: str = "") -> Dict[str, Any]:
    """One record for a refused arbitration — reason + the evidence quoted in ``detail``."""
    return _record(kind=KIND_REFUSAL, issue=issue, agent=agent, at=at, reason=reason, detail=detail, lane=lane or None)


def record_grant(arbitration: Arbitration, at: str) -> Dict[str, Any]:
    """One record for a granted arbitration."""
    return _record(
        kind=KIND_GRANT,
        issue=arbitration.issue,
        agent=arbitration.agent,
        at=at,
        lane=arbitration.lane or None,
        epic=arbitration.epic,
        directive_id=arbitration.directive_id or None,
    )


def render(record: Dict[str, Any]) -> str:
    """Canonical serialisation: compact JSON, sorted keys, one object per line."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"))


def append(record: Dict[str, Any], path: Path | str = DEFAULT_AUDIT_PATH) -> None:
    """Append one record, proving the trail only grew.

    Reads the file before the write, appends, then re-reads and requires the
    pre-write bytes to be an exact prefix of the post-write bytes — a
    concurrent *truncation or rewrite* is refused rather than silently
    accepted as this write's doing. Concurrent *appends* (two lanes recording
    two different outcomes at once) are legitimate and are serialised with an
    advisory file lock (``fcntl.flock``) rather than mistaken for corruption.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = render(record) + "\n"
    path.touch(exist_ok=True)
    with path.open("r+", encoding="utf-8") as fh:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            before = fh.read()
            fh.seek(0, 2)  # end of file, past whatever concurrent readers saw
            fh.write(line)
            fh.flush()
            fh.seek(0)
            after = fh.read()
        finally:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    if not after.startswith(before) or after != before + line:
        raise AuditUnavailable(
            f"the audit trail {path} was rewritten concurrently — refusing to trust the append"
        )


def read(path: Path | str = DEFAULT_AUDIT_PATH) -> list[Dict[str, Any]]:
    """Read every record from the trail, in file order."""
    path = Path(path)
    if not path.exists():
        return []
    out: list[Dict[str, Any]] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AuditUnavailable(f"{path}:{i}: not valid JSON ({exc})") from exc
        if obj.get("schema") != SCHEMA:
            raise AuditUnavailable(f"{path}:{i}: carries schema {obj.get('schema')!r}, expected {SCHEMA!r}")
        out.append(obj)
    return out
