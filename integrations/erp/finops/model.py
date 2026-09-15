"""The FinOps vocabulary of the ERP module — one place, closed (issue #654).

Issue #654 requires three things of this package, and each one is a
*vocabulary* before it is code:

1. **every document create/transition emits a ledger record** — so the set of
   operations that can be metered is closed (:data:`OPERATIONS`), each operation
   has exactly one ledger action name (:data:`EVENT_KIND_FOR_OPERATION`), and a
   metered event is a value object (:class:`MeteredEvent`) whose identity is
   derived, never hand-written (:func:`MeteredEvent.source_key`).
2. **a tenant at budget gets a deterministic hard stop** — so the refusals this
   package raises are a closed set (:data:`REFUSALS`) and a refusal that is not
   in it cannot even be constructed (:class:`Refused`). A vocabulary that grows
   by accident is a vocabulary nothing can prove covered.
3. **no telemetry file is edited** — so nothing here opens a telemetry path for
   writing. The sinks take telemetry *objects* (:mod:`.ledger`, :mod:`.usage`),
   never paths into ``telemetry/``.

The refusal codes are declared with their *reason*, because a code without a
stated invariant is a string: ``unknown-document-kind`` means the core document
model (``integrations/erp/core``, ERP-02) does not declare the kind at all, and
that is a different failure from ``rate-missing`` (the kind exists but this lane
publishes no price for the operation). A caller branches on the name; the
negative control provokes the name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

#: The telemetry/metering bucket helpers are reused verbatim rather than
#: re-derived: a second definition of "which UTC day does this timestamp fall
#: in" is how a rollup silently disagrees with the feed it reads.
from telemetry.metering.model import (  # noqa: F401
    day_bucket,
    month_bucket,
    now_utc_iso,
    parse_ts,
)

__all__ = [
    "DAY_BUCKET_KIND",
    "EVENT_KIND_FOR_OPERATION",
    "Finding",
    "MeteredEvent",
    "OP_CREATE",
    "OP_TRANSITION",
    "OPERATIONS",
    "REFUSALS",
    "Refused",
    "SCHEMA_VERSION",
    "day_bucket",
    "month_bucket",
    "normalize_event_ts",
    "now_utc_iso",
    "parse_ts",
]

#: The version this package writes on the records it emits. A record carrying
#: another version is refused rather than best-effort parsed.
SCHEMA_VERSION = "ao.erp.finops/v1"

OP_CREATE = "create"
OP_TRANSITION = "transition"

#: The closed set of ERP document operations that are metered. There is no
#: third operation: a delete or an update is not part of the ERP-02 document
#: surface, so it cannot be metered by accident.
OPERATIONS: Tuple[str, ...] = (OP_CREATE, OP_TRANSITION)

#: One ledger action per operation. The names are namespaced ``erp.document.*``
#: so an ERP-document record is distinguishable from a control-plane or model
#: action on the same chain - the chain is shared, the namespace is not.
EVENT_KIND_FOR_OPERATION: Dict[str, str] = {
    OP_CREATE: "erp.document.created",
    OP_TRANSITION: "erp.document.transitioned",
}

#: The day bucket of a metered event, as recorded on the usage row.
DAY_BUCKET_KIND = "utc-day"

#: The closed refusal vocabulary of this lane. Every code below has at least
#: one provocation in ``negative_control.py``; a code added here without one
#: fails that driver, which is the point.
REFUSALS: Tuple[str, ...] = (
    "unknown-operation",        # the operation is not in OPERATIONS
    "unknown-document-kind",    # the core document model declares no such kind
    "missing-tenant",           # an event with no tenant to meter against
    "tenant-mismatch",          # the document's tenant is not the metered tenant
    "missing-document-id",      # an event that cannot name the document it meters
    "missing-actor",            # an event with nobody to attribute it to
    "invalid-timestamp",        # an unparseable event timestamp
    "clock-regression",         # an event older than the tenant's last metered one
    "duplicate-event",          # the same event (same source key) emitted twice
    "rate-missing",             # the kind/operation has no declared rate entry
    "rate-invalid",             # a declared rate that is not a positive amount
    "unknown-currency",         # a currency the rate card does not declare
    "rate-card-invalid",        # a rate card that does not satisfy its schema
    "unsupported-schema-keyword",  # a schema promising more than the validator enforces
    "budget-policy-invalid",    # a declared budget that cannot be enforced
    "budget-unknown-tenant",    # no policy for a tenant: billable, so fail closed
    "budget-exhausted",         # the deterministic hard stop
    "ledger-unverified",        # the audit chain does not verify; no cost is published
    "unmetered-usage",          # a cost was demanded for usage nothing priced
    "usage-event-invalid",      # an emitted event that does not match its published shape
    "provenance-code-copied",   # GR-10: the harvest record claims copied code
    "provenance-empty",         # GR-10: a harvest record with no harvests
    "provenance-invalid",       # GR-10: a harvest record that does not satisfy its schema
)

_REFUSALS = frozenset(REFUSALS)


class Refused(Exception):
    """One refusal from this lane: a declared code, a detail, and where.

    The constructor refuses a code outside :data:`REFUSALS`, so a typo cannot
    invent a refusal the negative control has never heard of — the vocabulary
    is closed at the point of use, not by review.
    """

    def __init__(self, code: str, detail: str = "", *, where: Optional[str] = None) -> None:
        if code not in _REFUSALS:
            raise ValueError(
                f"{code!r} is not in the declared refusal vocabulary "
                f"({', '.join(REFUSALS)})"
            )
        self.code = code
        self.detail = detail
        self.where = where
        super().__init__(f"{code}: {detail}" if detail else code)

    def to_dict(self) -> Dict[str, Any]:
        """The machine-readable shape (what a refusal log records)."""
        payload: Dict[str, Any] = {"code": self.code, "detail": self.detail}
        if self.where is not None:
            payload["where"] = self.where
        return payload


def normalize_event_ts(value: Any) -> str:
    """Normalize an event timestamp, refusing anything unparseable.

    ``telemetry.metering.model.parse_ts`` is the repo-wide normalizer and it
    accepts ``None`` as "now", which is right for an intake adapter and wrong
    here: this lane records what *happened*, so a missing timestamp is a
    malformed event (``invalid-timestamp``) rather than a silent
    ``datetime.now()`` that makes a replay non-reproducible.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        raise Refused("invalid-timestamp", "a metered event must carry its timestamp")
    try:
        return parse_ts(value)
    except (ValueError, TypeError) as exc:
        raise Refused("invalid-timestamp", f"unparseable timestamp: {value!r}") from exc


@dataclass(frozen=True)
class Finding:
    """One measured problem, reported rather than raised.

    A whole-set check (does every declared kind have a rate?) inspects a set
    instead of raising on the first offence, so it reports findings. The code
    is drawn from the same closed vocabulary, so a finding and a refusal are
    the same kind of fact.
    """

    code: str
    detail: str = ""
    where: Optional[str] = None

    def __post_init__(self) -> None:
        if self.code not in _REFUSALS:
            raise ValueError(f"{self.code!r} is not in the declared refusal vocabulary")

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"code": self.code, "detail": self.detail}
        if self.where is not None:
            payload["where"] = self.where
        return payload


@dataclass(frozen=True)
class MeteredEvent:
    """One ERP document operation, as it is metered.

    Frozen because a metered event is a *record of what happened*: the ledger
    already holds it, and a mutable in-memory copy that disagrees with the
    chain is worse than no copy. The two sinks both derive their key from
    :meth:`source_key`, so a replay of the same event is refused by either sink
    for the same reason — the metering feed's own idempotency rule, applied
    before the feed is touched.
    """

    tenant: str
    kind: str
    document_id: str
    operation: str
    actor: str
    at: str
    from_state: Optional[str] = None
    to_state: Optional[str] = None

    def __post_init__(self) -> None:
        if self.operation not in OPERATIONS:
            raise Refused(
                "unknown-operation",
                f"{self.operation!r} is not one of {', '.join(OPERATIONS)}",
            )

    @property
    def event_kind(self) -> str:
        """The ledger action name for this event."""
        return EVENT_KIND_FOR_OPERATION[self.operation]

    @property
    def resource(self) -> str:
        """The ledger resource the event is about (queryable, per-tenant)."""
        return f"erp/documents/{self.kind}/{self.document_id}"

    def source_key(self) -> str:
        """The stable identity of this event across both sinks.

        Deliberately derived from the event's own facts and its timestamp: the
        same operation on the same document at the same instant is the *same*
        event (a replay), while the same operation at a later instant is a new
        one. A counter would make a replay depend on when it was replayed.
        """
        return (
            f"erp:{self.tenant}:{self.kind}:{self.document_id}:"
            f"{self.operation}:{self.at}"
        )

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "schemaVersion": SCHEMA_VERSION,
            "tenant": self.tenant,
            "kind": self.kind,
            "documentId": self.document_id,
            "operation": self.operation,
            "eventKind": self.event_kind,
            "actor": self.actor,
            "at": self.at,
            "sourceKey": self.source_key(),
            "resource": self.resource,
        }
        if self.from_state is not None:
            payload["fromState"] = self.from_state
        if self.to_state is not None:
            payload["toState"] = self.to_state
        return payload
