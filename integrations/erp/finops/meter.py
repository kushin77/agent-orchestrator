"""The meter: the one path by which an ERP document operation is metered.

This module is where acceptance criterion 1 ("every document create/transition
emits a ledger record") and criterion 2 ("a tenant at budget gets a
deterministic hard-stop") meet, and the order of its steps is the reason both
hold:

1. **the model owns legality.** The kind must exist in the core document model
   (ERP-02, ``integrations/erp/core``) or the operation is refused
   ``unknown-document-kind``; a transition resolves through the core workflow
   (``next_state``), so an illegal move is refused by the lane that owns the
   state machine rather than by a second copy here.
2. **the event is built and checked before anything is written.** Tenant,
   document id, actor and timestamp are required, the timestamp is normalized,
   and a replay (same :meth:`~.model.MeteredEvent.source_key`) or an
   out-of-order event (an earlier ``at`` than this tenant's last) is refused.
   Nothing has been written at this point, so a refused event leaves no trace.
3. **the price is resolved, then the budget is checked.** ``rate-missing``
   precedes ``budget-exhausted``: an operation this lane cannot price is refused
   before a tenant is blamed for a budget.
4. **only then are the sinks written** — the audit chain first, then the usage
   feed. A stop at step 3 therefore leaves *nothing*: no ledger record, no usage
   record, no cost. That is what makes the hard-stop a stop rather than a flag.

The meter keeps two pieces of state, both of them *derived* and both rebuildable
from the sinks: the set of emitted source keys (so a replay is refused here as
well as by the metering store) and each tenant's last metered timestamp (so the
roll-up has a total order to rely on). Neither is a source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from integrations.erp.core.validators import DocumentModel

from .budget import ErpBudgetGuard
from .ledger import AuditSink
from .model import (
    Finding,
    MeteredEvent,
    OP_CREATE,
    OP_TRANSITION,
    Refused,
    normalize_event_ts,
    now_utc_iso,
)
from .rates import RateCard
from .usage import UsageSink

__all__ = ["ErpMeter"]


@dataclass
class ErpMeter:
    """Meters ERP document operations onto the audit chain and the usage feed."""

    model: DocumentModel
    rates: RateCard
    audit: AuditSink
    usage: UsageSink
    budget: Optional[ErpBudgetGuard] = None
    emitted: Set[str] = field(default_factory=set, init=False)
    last_ts: Dict[str, str] = field(default_factory=dict, init=False)
    events: List[MeteredEvent] = field(default_factory=list, init=False)

    # --- the declared surface -------------------------------------------- #

    def kinds(self) -> Tuple[str, ...]:
        """Every document kind the core model declares (the metered surface)."""
        return tuple(self.model.document_kinds())

    def lifecycle_kinds(self) -> Tuple[str, ...]:
        """The kinds that declare a lifecycle, and so can be transitioned."""
        return tuple(self.model.lifecycle_kinds())

    def coverage(self) -> List[Finding]:
        """Every way the rate card fails to cover the live core model."""
        return self.rates.coverage(self.kinds(), self.lifecycle_kinds())

    # --- the operations --------------------------------------------------- #

    def create(
        self,
        kind: str,
        *,
        tenant: str,
        document_id: str,
        actor: str,
        at: Optional[str] = None,
        document: Optional[Mapping[str, Any]] = None,
    ) -> MeteredEvent:
        """Meter the creation of one ERP document.

        ``document`` is optional and, when supplied, is validated by the core
        model (its schema and its family rules) and cross-checked against the
        metered tenant — a document belonging to another tenant is refused
        ``tenant-mismatch`` rather than metered against the caller's.
        """
        self._require_kind(kind)
        if document is not None:
            self._cross_check(kind, tenant, document)
        event = MeteredEvent(
            tenant=tenant,
            kind=kind,
            document_id=document_id,
            operation=OP_CREATE,
            actor=actor,
            at=normalize_event_ts(now_utc_iso() if at is None else at),
        )
        return self._emit(event)

    def transition(
        self,
        kind: str,
        *,
        tenant: str,
        document_id: str,
        actor: str,
        from_state: str,
        action: str,
        at: Optional[str] = None,
        target: Optional[str] = None,
        document: Optional[Mapping[str, Any]] = None,
    ) -> MeteredEvent:
        """Meter one transition of an ERP document.

        The destination state is resolved by the core workflow
        (``next_state``), so an undeclared state, an action that does not exist
        from ``from_state``, and a move that is not a declared transition are
        all refused by the model that owns those facts.
        """
        self._require_kind(kind)
        if document is not None:
            self._cross_check(kind, tenant, document)
        workflow = self.model.workflow_for(kind)
        to_state = workflow.next_state(from_state, action, target=target)
        event = MeteredEvent(
            tenant=tenant,
            kind=kind,
            document_id=document_id,
            operation=OP_TRANSITION,
            actor=actor,
            at=normalize_event_ts(now_utc_iso() if at is None else at),
            from_state=from_state,
            to_state=to_state,
        )
        return self._emit(event)

    # --- internals -------------------------------------------------------- #

    def _require_kind(self, kind: Any) -> str:
        if not isinstance(kind, str) or not kind.strip():
            raise Refused("unknown-document-kind", f"{kind!r} is not a document kind")
        declared = self.kinds()
        if kind not in declared:
            raise Refused(
                "unknown-document-kind",
                f"{kind!r} is not declared by the core document model "
                f"(declared: {', '.join(declared)})",
            )
        return kind

    def _cross_check(self, kind: str, tenant: str, document: Mapping[str, Any]) -> None:
        """Refuse a document that is not this kind, or not this tenant's."""
        document_tenant = document.get("tenant")
        if document_tenant is not None and str(document_tenant) != tenant:
            raise Refused(
                "tenant-mismatch",
                f"the document belongs to {document_tenant!r}, not {tenant!r}",
                where=f"erp/{kind}",
            )
        self.model.validate_document(kind, dict(document))

    def _emit(self, event: MeteredEvent) -> MeteredEvent:
        """Validate, guard, then write the event to both sinks."""
        self._require_text(event.tenant, "missing-tenant", "an event must name its tenant")
        self._require_text(
            event.document_id, "missing-document-id", "an event must name its document"
        )
        self._require_text(event.actor, "missing-actor", "an event must name its actor")

        key = event.source_key()
        if key in self.emitted:
            raise Refused(
                "duplicate-event",
                f"{event.operation} of {event.kind}/{event.document_id} at {event.at} "
                f"has already been metered",
                where=key,
            )
        previous = self.last_ts.get(event.tenant)
        if previous is not None and event.at < previous:
            raise Refused(
                "clock-regression",
                f"{event.at} is earlier than {event.tenant}'s last metered event {previous}",
                where=key,
            )

        cost = self.rates.price_for(event.kind, event.operation)
        if self.budget is not None:
            self.budget.guard(
                event.tenant,
                requested_cost_usd=float(cost or 0.0),
                month=event.at[:7],
                where=key,
            )

        self.audit.record(event)
        self.usage.record(event, cost_usd=cost)
        self.emitted.add(key)
        self.last_ts[event.tenant] = event.at
        self.events.append(event)
        return event

    @staticmethod
    def _require_text(value: Any, code: str, detail: str) -> None:
        if value is None or not str(value).strip():
            raise Refused(code, detail)


def event_sequence(events: Sequence[MeteredEvent]) -> Tuple[str, ...]:
    """The ledger action names for a sequence of metered events.

    The coverage check compares this against what the ledger actually holds, so
    a meter that emitted a record for only one of the two operations fails on
    the *chain*, not on an in-memory list.
    """
    return tuple(event.event_kind for event in events)
