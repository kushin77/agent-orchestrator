"""The append-only audit trail of claim resolution (issue #592).

Every composed brief run appends **exactly one record** to the trail: the run's
resolved and unresolved claim counts and the finding lines it produced. The
trail is the surface's answer to "what did the brief actually resolve, and when
did it stop resolving something" — a question the rendered document cannot
answer after the fact.

Three properties, all of them testable and all of them the point:

* **one record per composed brief run** — :func:`composer.compose` calls
  :meth:`Trail.append` once per composition, and never anywhere else;
* **append-only** — :func:`append` opens the trail with ``"a"`` and writes one
  JSON line. Nothing here truncates, rewrites or reorders: a record that has
  been written stays byte-identical, which is what makes the trail evidence
  rather than a cache;
* **deterministic and offline** — the record carries no clock reading and no
  network fact: two runs over one revision append two identical records. The
  trail's order is the run order; its content is the composition's content.

The default trail lives under ``.verify/`` (gitignored runtime state, the
repository's existing home for generated evidence), so composing never dirties
the working tree; a caller may point ``--audit`` anywhere.

---knowledge---
module_id: integrations.paperclip.reporting.audit
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: [record_for, append, read, Trail, trail_path]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from integrations.paperclip.reporting.policy import ClaimPolicy

#: The record schema tag.
TRAIL_SCHEMA = "ao.module-brief-audit/v1"

#: The default trail, repository-relative. `.verify/` is gitignored, so naming a
#: run in the trail never touches the tree's content.
DEFAULT_TRAIL = Path(".verify") / "module-brief-audit.jsonl"


def record_for(composition: Any, policy: ClaimPolicy) -> Dict[str, Any]:
    """The one record a composed brief run appends.

    ``composition`` is the value :func:`integrations.paperclip.reporting.composer.compose`
    returns; this function reads it and never re-composes anything, so the trail
    cannot disagree with the run it records.
    """
    claims = list(composition.claims)
    findings = list(composition.findings)
    unresolved = [f for f in findings if f.code == policy.unresolved_code]
    resolved = len(claims) - len(unresolved)
    hub = (composition.document.get("hub") or {}) if composition.document else {}
    return {
        "schema": TRAIL_SCHEMA,
        "revision": str(hub.get("revision") or ""),
        "claims": len(claims),
        "resolved": resolved,
        "unresolved": len(unresolved),
        "findings": sorted(finding.render() for finding in findings),
    }


def append(path: Path, record: Dict[str, Any]) -> Path:
    """Append one record to the trail. The only write this module performs."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
    return path


def read(path: Path) -> Sequence[Dict[str, Any]]:
    """Every record, in the order it was written. Empty when the trail is absent."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ()
    records = []
    for line in text.splitlines():
        if line.strip():
            records.append(json.loads(line))
    return tuple(records)


@dataclass
class Trail:
    """A destination for the trail, and the rule that one run writes one record."""

    path: Path

    def record(self, composition: Any, policy: ClaimPolicy) -> Dict[str, Any]:
        """Append this run's record and return it."""
        record = record_for(composition, policy)
        append(self.path, record)
        return record

    def records(self) -> Sequence[Dict[str, Any]]:
        return read(self.path)

    def finding_lines(self) -> Sequence[str]:
        """Every finding line the trail has recorded, in run order."""
        out = []
        for record in read(self.path):
            out.extend(str(line) for line in record.get("findings") or [])
        return tuple(out)


def trail_path(repo_root: Path, override: Optional[str] = None) -> Path:
    """Where a run writes: ``--audit`` if given, else the gitignored default."""
    if override:
        return Path(override)
    return Path(repo_root) / DEFAULT_TRAIL


__all__ = [
    "DEFAULT_TRAIL",
    "TRAIL_SCHEMA",
    "Trail",
    "append",
    "read",
    "record_for",
    "trail_path",
]
