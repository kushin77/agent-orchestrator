"""The append-only audit rail every CRM-family transition lands on (issue #650).

Acceptance criterion 1 of issue #650 is that lead → opportunity → customer is
"state-machine-driven **and audited**". Audited is made mechanical here rather
than promised in prose: a rail is a frozen tuple of entries, each carrying the
digest of its predecessor, so

* **append-only is structural.** :meth:`Rail.append` returns a *new* rail; there
  is no mutation and no delete. A caller cannot remove a transition it regrets,
  because removing one is not an operation this type has.
* **the chain is tamper-evident.** :meth:`Rail.verify` re-derives every digest
  and every back-link and reports ``audit-broken`` naming the offending
  sequence number when one does not reproduce. This follows
  ``telemetry/ledger``'s hash-chain discipline, which this module cannibalizes.
* **the clock is injected.** No entry ever reads the wall clock. ``at`` is a
  parameter, so two runs of the same scenario produce the same chain and the
  same digest — a rail whose value depends on when it was built cannot be
  asserted on, and `tests/test_audit.py` pins that determinism.

The digest covers the entry's canonical JSON *prefixed by its predecessor's
digest*, so reordering two entries breaks the second one's back-link and
rewriting an entry's payload breaks its own digest.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .model import ACTIONS, Finding, Refused

#: Domain separator: a digest from this rail can never collide with one from a
#: differently-shaped chain that happens to hash the same payload.
DOMAIN = "ao-erp-crm-audit-v1"

#: The predecessor digest of the first entry in any rail.
GENESIS = "0" * 64


def canonical(payload: Mapping[str, Any]) -> str:
    """The canonical JSON text a digest is taken over: sorted, tight, no ASCII escapes."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_of(entry: Mapping[str, Any], prev: str) -> str:
    """The chained digest of ``entry`` given its predecessor's digest."""
    body = canonical({k: entry[k] for k in sorted(entry) if k != "digest"})
    return hashlib.sha256(f"{DOMAIN}|{prev}|{body}".encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Entry:
    """One recorded transition. ``digest`` is derived, never supplied by a caller."""

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
        """Record one transition and return the extended rail.

        The action is checked against the closed vocabulary here, so an audit
        entry can never carry free text in its action field.
        """
        if action not in ACTIONS:
            raise Refused(
                "unknown-action",
                f"audit action {action!r} is not one of {', '.join(ACTIONS)}",
            )
        if not actor:
            raise Refused("invalid-value", "an audit entry requires an actor")
        if not at:
            raise Refused("invalid-timestamp", "an audit entry requires a timestamp")
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
        """Every entry recorded against ``ref``, in sequence order."""
        return tuple(entry for entry in self.entries if entry.ref == ref)

    def verify(self) -> List[Finding]:
        """Every way this rail fails to be a well-formed chain (empty is OK).

        Reported rather than raised: a chain is checked as a whole, and a
        repaired mistake must not be able to hide a second one behind it.
        """
        findings: List[Finding] = []
        previous = GENESIS
        for position, entry in enumerate(self.entries, start=1):
            if entry.seq != position:
                findings.append(
                    Finding(
                        "audit-broken",
                        f"entry at position {position} claims seq {entry.seq}",
                        ref=entry.ref,
                    )
                )
            if entry.prev != previous:
                findings.append(
                    Finding(
                        "audit-broken",
                        f"seq {entry.seq} links to {entry.prev[:12]} but the previous "
                        f"digest is {previous[:12]}",
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

        A rebuild that re-derived the digests would *launder* a tampered chain
        into a valid one, which is the exact failure ``verify`` exists to catch.
        The entries are therefore taken as read, and the caller is expected to
        verify the result.
        """
        entries: List[Entry] = []
        for payload in payloads:
            body = dict(payload)
            digest = body.pop("digest", "")
            try:
                entries.append(Entry(digest=digest, **body))
            except TypeError as exc:
                raise Refused("audit-broken", f"unreadable audit entry: {exc}") from exc
        return cls(entries=tuple(entries))
