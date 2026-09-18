"""The conversion-event webhook bridge's document model (issue #671, EPIC #665).

This is the model half of the ERP module's **webhook-bridge** lane: the event
envelope a CRM conversion posts, the closed refusal vocabulary, and the value
types the rest of this package is written against. It holds no transport, no
auth and no ledger — an event here is a *value*, produced only by
:func:`schema.parse` from a raw payload, and consumed only by
:class:`bridge.ConversionBridge`.

**Fail-closed by construction.** :class:`Refused` is the only way this package
reports a problem with an inbound event. There is no code path that posts a
partial ledger entry and *then* raises: :mod:`bridge` builds the whole set of
ledger entries in memory, verifies it balances, and only then commits — so a
"half-posted" ledger is not a race this module can lose, it is a shape the
code cannot produce. See ``bridge.ConversionBridge.handle`` for the sequence.

**Self-containment (lane boundary).** This package imports
``integrations.erp.tx.ledger`` (read-only: :class:`~integrations.erp.tx.ledger.Entry`,
:class:`~integrations.erp.tx.ledger.GeneralLedger`,
:class:`~integrations.erp.tx.ledger.PostingPolicy`) to post the derived GL
entries the same way ERP-03 does, and nothing else from a sibling lane. It owns
no file outside ``integrations/erp/webhooks/**``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

#: The envelope version every conversion event carries.
SCHEMA_VERSION = 1

#: The CRM hooks a conversion event may originate from. Closed on purpose: an
#: event naming a hook outside this set cannot be dispositioned by
#: :mod:`hooks`, so it is refused (``unknown-hook``) rather than bridged blind.
HOOK_LEAD_CONVERSION = "erp.crm.lead-conversion"
HOOK_OPPORTUNITY_WIN = "erp.crm.opportunity-win"

KNOWN_HOOKS: Tuple[str, ...] = (HOOK_LEAD_CONVERSION, HOOK_OPPORTUNITY_WIN)

#: The closed refusal vocabulary of this lane. A rail entry / refusal whose code
#: is not one of these is a bug in the refuser: ``negative_control.py`` provokes
#: every one and ``tests/test_negative_control.py`` fails the suite when the
#: provoked set and this set diverge.
REFUSALS: Tuple[str, ...] = (
    "invalid-body",       # the payload is not a mapping at all
    "schema-violation",   # the payload does not validate against the event schema
    "unknown-hook",       # the event names a hook this lane has no disposition for
    "auth-failed",        # the signature is missing, malformed, or does not verify
    "unbalanced-posting",  # the derived ledger entries do not net to zero
    "missing-policy",     # the posting policy declares no account for a required role
    "quarantined",        # already-quarantined event replayed without remediation
)


class Refused(Exception):
    """One refusal, carrying a closed ``code`` and the offending detail."""

    def __init__(self, code: str, detail: str) -> None:
        if code not in REFUSALS:
            raise ValueError(f"{code!r} is not a declared webhook-bridge refusal")
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ConversionEvent:
    """One parsed, schema-valid CRM conversion event.

    Constructed only by :func:`schema.parse` — never by hand in a caller — so
    every event this lane acts on has already cleared the schema gate.
    """

    schema_version: int
    event_id: str
    hook: str
    tenant: str
    occurred_at: str
    customer_id: str
    customer_name: str
    amount: float
    currency: str
    source_document: str
    idempotency_key: str = field(default="")

    def __post_init__(self) -> None:
        if not self.idempotency_key:
            object.__setattr__(self, "idempotency_key", f"{self.tenant}:{self.event_id}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "eventId": self.event_id,
            "hook": self.hook,
            "tenant": self.tenant,
            "occurredAt": self.occurred_at,
            "customerId": self.customer_id,
            "customerName": self.customer_name,
            "amount": self.amount,
            "currency": self.currency,
            "sourceDocument": self.source_document,
            "idempotencyKey": self.idempotency_key,
        }


@dataclass(frozen=True)
class BridgeResult:
    """What handling one event produced: posted, replayed, or quarantined."""

    status: str  # "posted" | "duplicate" | "quarantined"
    event_id: str = ""
    voucher_id: str = ""
    entries: Tuple[Any, ...] = field(default_factory=tuple)
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "eventId": self.event_id,
            "voucherId": self.voucher_id,
            "entryCount": len(self.entries),
            "reason": self.reason,
        }
