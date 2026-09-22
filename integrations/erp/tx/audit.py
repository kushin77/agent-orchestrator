"""The append-only audit rail every spine step lands on (ERP-03, issue #648).

Acceptance criterion 1 of #648 asks for a sales cycle that is *posted*: a
transactional spine that changes a stock ledger, a general ledger and a document's
state, and leaves behind a record an auditor can read back. "Audited" is made
mechanical here rather than promised in prose: a rail is a frozen tuple of
entries, each carrying the digest of its predecessor, so

* **append-only is structural.** :meth:`Rail.append` returns a *new* rail; there
  is no mutation and no delete. A caller cannot remove a step it regrets, because
  removing one is not an operation this type has.
* **the chain is tamper-evident.** :meth:`Rail.verify` re-derives every digest
  and every back-link and reports ``audit-broken`` naming the offending sequence
  number when one does not reproduce.
* **the clock is injected.** No entry ever reads the wall clock. ``at`` is a
  parameter, so two runs of the same scenario produce the same chain and the same
  digest — a rail whose value depends on *when* it was built cannot be asserted
  on, and "deterministic and keyless" (acceptance criterion 1) depends on exactly
  that.

The digest covers the entry's canonical JSON *prefixed by its predecessor's
digest*, so reordering two entries breaks the second one's back-link and
rewriting an entry's payload breaks its own digest. The discipline is the one
``telemetry/ledger`` ships and ``integrations/erp/crm/audit.py`` re-expresses for
its lane; this is the same shape for the transactional spine, with the spine's own
action vocabulary (see :data:`~.model.ACTIONS`) as its closed action column.

---knowledge---
module_id: integrations.erp.tx.audit
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [canonical, digest_of, Entry, Rail]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .model import ACTIONS, Finding, Refused

#: Domain separator: a digest from this rail can never collide with one from a
#: differently-shaped chain that happens to hash the same payload.
DOMAIN = "ao-erp-tx-audit-v1"

#: The predecessor digest of the first entry in any rail.
GENESIS = "0" * 64


def canonical(payload: Mapping[str, Any]) -> str:
    """The canonical JSON text a digest is taken over: sorted, tight, no escapes."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_of(entry: Mapping[str, Any], prev: str) -> str:
    """The chained digest of ``entry`` given its predecessor's digest."""
    body = canonical({key: entry[key] for key in sorted(entry) if key != "digest"})
    return hashlib.sha256(f"{DOMAIN}|{prev}|{body}".encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Entry:
    """One recorded step. ``digest`` is derived, never supplied by a caller."""

    seq: int
    at: str
    actor: str
    action: str
    kind: str
    ref: str
    from_state: str
    to_state: str
    note: str = ""
    prev: str = GENESIS
    digest: str = ""

    def payload(self) -> Dict[str, Any]:
        """The digested fields, without ``digest`` itself."""
        return {
            "seq": self.seq,
            "at": self.at,
            "actor": self.actor,
            "action": self.action,
            "kind": self.kind,
            "ref": self.ref,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "note": self.note,
            "prev": self.prev,
        }

    def to_dict(self) -> Dict[str, Any]:
        body = self.payload()
        body["digest"] = self.digest
        return body

    def recompute(self, prev: Optional[str] = None) -> str:
        return digest_of(self.payload(), self.prev if prev is None else prev)


@dataclass(frozen=True)
class Rail:
    """An immutable, hash-chained sequence of audit entries."""

    entries: Tuple[Entry, ...] = field(default_factory=tuple)

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterable[Entry]:
        return iter(self.entries)

    @property
    def head(self) -> str:
        """The chain head: the last digest, or ``GENESIS`` for an empty rail."""
        return self.entries[-1].digest if self.entries else GENESIS

    def append(
        self,
        *,
        at: str,
        actor: str,
        action: str,
        kind: str,
        ref: str,
        from_state: str = "",
        to_state: str = "",
        note: str = "",
    ) -> "Rail":
        """Record one step and return the extended rail.

        The action is checked against the spine's closed vocabulary here, so a
        rail entry can never carry free text in its action column.
        """
        if action not in ACTIONS:
            raise Refused(
                "unknown-action",
                f"audit action {action!r} is not one of {', '.join(ACTIONS)}",
            )
        if not actor:
            raise Refused("invalid-value", "an audit entry requires an actor")
        if not at:
            raise Refused("invalid-value", "an audit entry requires a timestamp")
        if not ref:
            raise Refused("invalid-value", "an audit entry requires the document it records")
        body = {
            "seq": len(self.entries) + 1,
            "at": at,
            "actor": actor,
            "action": action,
            "kind": kind,
            "ref": ref,
            "from_state": from_state,
            "to_state": to_state,
            "note": note,
            "prev": self.head,
        }
        entry = Entry(digest=digest_of(body, self.head), **body)
        return Rail(entries=self.entries + (entry,))

    def for_ref(self, ref: str) -> Tuple[Entry, ...]:
        """Every step recorded against ``ref``, in sequence order."""
        return tuple(entry for entry in self.entries if entry.ref == ref)

    def verify(self) -> List[Finding]:
        """Every way this rail fails to be a well-formed chain (empty is OK).

        Reported rather than raised: a chain is checked as a whole, and a repaired
        mistake must not be able to hide a second one behind it.
        """
        findings: List[Finding] = []
        previous = GENESIS
        for position, entry in enumerate(self.entries, start=1):
            if entry.seq != position:
                findings.append(
                    Finding(
                        "audit-broken",
                        f"entry at position {position} claims seq {entry.seq}",
                        kind=entry.kind,
                        ref=entry.ref,
                    )
                )
            if entry.prev != previous:
                findings.append(
                    Finding(
                        "audit-broken",
                        f"seq {entry.seq} links to {entry.prev[:12]} but the previous "
                        f"digest is {previous[:12]}",
                        kind=entry.kind,
                        ref=entry.ref,
                    )
                )
            expected = entry.recompute(entry.prev)
            if entry.digest != expected:
                findings.append(
                    Finding(
                        "audit-broken",
                        f"seq {entry.seq} digest does not reproduce "
                        f"(recorded {entry.digest[:12]}, recomputed {expected[:12]})",
                        kind=entry.kind,
                        ref=entry.ref,
                    )
                )
            previous = entry.digest
        return findings

    def to_list(self) -> List[Dict[str, Any]]:
        return [entry.to_dict() for entry in self.entries]

    @classmethod
    def from_list(cls, payloads: Iterable[Mapping[str, Any]]) -> "Rail":
        """Rebuild a rail from its serialized form, without re-deriving anything.

        Reconstructing must not *repair*: a caller that loads a broken rail has to
        be able to see that it is broken, which is why nothing here recomputes a
        digest or renumbers a sequence.
        """
        entries: List[Entry] = []
        for payload in payloads:
            entries.append(
                Entry(
                    seq=int(payload.get("seq", 0)),
                    at=str(payload.get("at", "")),
                    actor=str(payload.get("actor", "")),
                    action=str(payload.get("action", "")),
                    kind=str(payload.get("kind", "")),
                    ref=str(payload.get("ref", "")),
                    from_state=str(payload.get("from_state", "")),
                    to_state=str(payload.get("to_state", "")),
                    note=str(payload.get("note", "")),
                    prev=str(payload.get("prev", GENESIS)),
                    digest=str(payload.get("digest", "")),
                )
            )
        return cls(entries=tuple(entries))
