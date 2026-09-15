"""The audit rail: append-only, hash-chained, and tamper-evident.

Every mutation this lane makes — a document created, a state advanced, a
posting written — lands on one rail, following the ``telemetry/ledger``
discipline the module's gap analysis names as the cannibalization base. Two
properties make it worth more than a log:

* **append-only by construction.** :meth:`Rail.append` returns a *new* rail
  rather than mutating one, so a caller holding a rail cannot be surprised by a
  later write, and there is no in-place edit to make.
* **tamper-evident.** Each entry's digest covers its own fields *and* the
  previous entry's digest, so :meth:`Rail.verify` re-derives the whole chain and
  reports every entry that does not re-derive, every back-link that does not
  match and every sequence number that skips. A rail that has been edited in the
  middle is reported at the edit, not merely at the end.

The action vocabulary is closed (:data:`~.model.ACTIONS`), because the rail is
the record an auditor reads back: an action field of free text would make the
rail unqueryable, so an entry whose action is outside the vocabulary is refused
by name at the append rather than stored and discovered later.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Tuple

from .model import ACTIONS, Finding, Refused

__all__ = ["GENESIS", "Entry", "Rail"]

#: The back-link a rail's first entry carries: no predecessor, but a value, so
#: every entry has a `prev` and no entry's digest is computed against nothing.
GENESIS = "0" * 64


def _canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class Entry:
    """One audited action, and the digest that binds it to its predecessor."""

    seq: int
    at: str
    actor: str
    action: str
    document: str
    detail: str = ""
    prev: str = ""
    digest: str = ""

    def unsigned(self) -> Dict[str, Any]:
        """The fields the digest covers — everything but the digest itself."""
        return {
            "seq": self.seq,
            "at": self.at,
            "actor": self.actor,
            "action": self.action,
            "document": self.document,
            "detail": self.detail,
            "prev": self.prev,
        }

    @classmethod
    def build(
        cls,
        *,
        seq: int,
        at: str,
        actor: str,
        action: str,
        document: str,
        detail: str,
        prev: str,
    ) -> "Entry":
        payload = {
            "seq": seq,
            "at": at,
            "actor": actor,
            "action": action,
            "document": document,
            "detail": detail,
            "prev": prev,
        }
        digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
        return cls(
            seq=seq,
            at=at,
            actor=actor,
            action=action,
            document=document,
            detail=detail,
            prev=prev,
            digest=digest,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "at": self.at,
            "actor": self.actor,
            "action": self.action,
            "document": self.document,
            "detail": self.detail,
            "prev": self.prev,
            "digest": self.digest,
        }


@dataclass(frozen=True)
class Rail:
    """An append-only chain of :class:`Entry` values."""

    entries: Tuple[Entry, ...] = ()

    # --- writing ----------------------------------------------------------

    def append(
        self, *, at: str, actor: str, action: str, document: str, detail: str = ""
    ) -> "Rail":
        """Return a new rail with ``entry`` appended, or refuse the entry."""
        if action not in ACTIONS:
            raise Refused(
                "unknown-action",
                f"{action!r} is not an audit action; the vocabulary is closed so "
                f"the rail stays queryable: {list(ACTIONS)}",
            )
        if not isinstance(document, str) or not document.strip():
            raise Refused("invalid-value", "an audit entry must name a document")
        if not isinstance(at, str) or not at.strip():
            raise Refused(
                "invalid-value",
                f"an audit entry for {document!r} must carry a timestamp",
            )
        if not isinstance(actor, str) or not actor.strip():
            raise Refused("invalid-value", f"an audit entry for {document!r} must name an actor")
        entry = Entry.build(
            seq=len(self.entries),
            at=at,
            actor=actor,
            action=action,
            document=document,
            detail=detail,
            prev=self.head(),
        )
        return Rail(entries=self.entries + (entry,))

    # --- reading ----------------------------------------------------------

    def head(self) -> str:
        """The digest of the last entry, or the genesis value for an empty rail."""
        return self.entries[-1].digest if self.entries else GENESIS

    def verify(self) -> Tuple[Finding, ...]:
        """Every way the chain fails to re-derive; empty means intact."""
        findings: List[Finding] = []
        prev = GENESIS
        for index, entry in enumerate(self.entries):
            if entry.seq != index:
                findings.append(
                    Finding(
                        "audit-broken",
                        f"entry {index} carries seq {entry.seq}",
                        entry.document,
                    )
                )
            if entry.prev != prev:
                findings.append(
                    Finding(
                        "audit-broken",
                        f"entry {index} back-links {entry.prev[:12]} but the "
                        f"previous digest is {prev[:12]}",
                        entry.document,
                    )
                )
            derived = Entry.build(
                seq=entry.seq,
                at=entry.at,
                actor=entry.actor,
                action=entry.action,
                document=entry.document,
                detail=entry.detail,
                prev=entry.prev,
            ).digest
            if entry.digest != derived:
                findings.append(
                    Finding(
                        "audit-broken",
                        f"entry {index} digest does not re-derive (stored "
                        f"{entry.digest[:12]}, derived {derived[:12]})",
                        entry.document,
                    )
                )
            prev = entry.digest
        return tuple(findings)

    def actions(self) -> Tuple[str, ...]:
        return tuple(entry.action for entry in self.entries)

    def for_document(self, document: str) -> Tuple[Entry, ...]:
        return tuple(entry for entry in self.entries if entry.document == document)

    def digest(self) -> str:
        """The rail's identity: the head, so an unchanged rail is unchanged."""
        return self.head()

    def to_data(self) -> List[Dict[str, Any]]:
        return [entry.to_dict() for entry in self.entries]

    def __len__(self) -> int:
        return len(self.entries)
