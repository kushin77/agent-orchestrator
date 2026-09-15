"""The audit sink: one hash-chained ledger record per metered ERP operation.

Acceptance criterion 1 of issue #654 is that *every* document create and
transition emits a ledger record, so this module is deliberately thin — it is
the only place in this lane that touches ``telemetry/ledger``, and it touches it
through the **public API only** (``open_ledger`` / ``LedgerStore.append`` /
``verify_ledger``), never through the ledger's files (acceptance criterion 3).

Why the platform ledger rather than a table of this lane's own: the ledger is
already per-tenant, append-only, hash-chained and tamper-evident, and it already
carries the fields an ERP operation needs (actor, action, resource, evidence,
``cost_usd``). A second audit store would be a second thing to verify, and the
one thing it would guarantee is that the two disagree.

The evidence field is the event's :meth:`~.model.MeteredEvent.source_key`, which
is also the metering feed's idempotency key — so the audit chain and the usage
feed point at the *same* identity for one operation, and a replay is detectable
from either side.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from telemetry.ledger import LedgerStore, LedgerVerdict, open_ledger, verify_ledger

from .model import MeteredEvent

__all__ = ["ACTOR_KINDS", "AuditSink", "canonical_actor", "open_sink"]

#: The actor kinds the platform ledger accepts. A bare id is attributed to an
#: agent, which is the honest default for a machine-driven ERP operation.
ACTOR_KINDS = ("user", "agent", "system")


def canonical_actor(value: str) -> str:
    """Canonicalise an actor to the ledger's ``kind:id`` shape.

    The ledger refuses an actor whose kind it does not know, so a lane that
    passed a bare id would get an unhelpful failure at the ledger boundary.
    Normalising here keeps the refusal vocabulary of *this* lane meaningful.
    """
    text = str(value).strip()
    if ":" in text and text.split(":", 1)[0] in ACTOR_KINDS:
        return text
    return f"agent:{text}"


@dataclass
class AuditSink:
    """The tenant-chain audit sink over one open ledger store."""

    store: LedgerStore

    def record(self, event: MeteredEvent) -> Dict[str, Any]:
        """Append this event to its tenant's chain and return the stored record.

        The payload is deliberately ``None``: the event's own fields are the
        record (``action``/``resource``/``evidence``/``ts``), so a record is
        reproducible from the event alone. A producer that considers some of the
        detail sensitive passes it explicitly through :meth:`record_payload`,
        which requires a keystore — the ledger's own fail-closed rule.
        """
        return self.record_payload(event, payload=None)

    def record_payload(self, event: MeteredEvent, *, payload: Any = None) -> Dict[str, Any]:
        """Append the event, optionally carrying an encrypted payload."""
        return self.store.append(
            event.tenant,
            actor=canonical_actor(event.actor),
            action=event.event_kind,
            resource=event.resource,
            evidence=event.source_key(),
            payload=payload,
            ts=event.at,
        )

    # --- reads ------------------------------------------------------------ #

    def records(self, tenant: str) -> List[Dict[str, Any]]:
        """Every stored record for one tenant, in chain order."""
        return self.store.records(tenant)

    def count(self, tenant: str) -> int:
        """How many records one tenant's chain holds."""
        return len(self.store.records(tenant))

    def tenants(self) -> Tuple[str, ...]:
        """Every tenant the ledger holds a chain for."""
        return tuple(sorted(self.store.tenant_ids()))

    def actions(self, tenant: str) -> Tuple[str, ...]:
        """The stored action names, in chain order (what criterion 1 measures)."""
        return tuple(str(record.get("action")) for record in self.store.records(tenant))

    def verify(self, tenant: str) -> LedgerVerdict:
        """The tri-state integrity verdict for one tenant's chain."""
        return verify_ledger(self.store, tenant)

    def tail(self, tenant: str) -> Tuple[int, str]:
        """The verified ``(seq, hash)`` tail of one tenant's chain."""
        return self.store.tail_state(tenant)


def open_sink(directory: Optional[str] = None, keystore: Any = None) -> AuditSink:
    """Open a file-backed (or in-memory) audit sink through the public API."""
    return AuditSink(open_ledger(directory, keystore=keystore))
