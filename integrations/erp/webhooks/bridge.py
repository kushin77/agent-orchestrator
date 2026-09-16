"""The conversion-event bridge itself (issue #671): CRM conversion → GL posting.

:class:`ConversionBridge` is the one seam this lane exposes to a transport
adapter (an HTTP handler, a queue consumer — neither is part of this lane).
:meth:`ConversionBridge.handle` runs the whole fail-closed sequence a webhook
delivery needs, in order, and never partially:

1. **flag gate** — off by default (:mod:`flags`); a disabled bridge refuses
   every delivery with ``quarantined`` rather than silently accepting and
   dropping it, so "the flag was off" is itself an audited outcome.
2. **auth** — :func:`auth.verify` against the raw body, before anything is
   parsed. An unverified payload is never inspected for its contents.
3. **schema** — :func:`schema.parse`. A malformed event is quarantined here,
   named by field.
4. **idempotency** — a replayed ``idempotencyKey`` returns the first recorded
   :class:`~.model.BridgeResult` verbatim; the ledger is never touched twice
   for one event.
5. **derivation + balance proof** — the event is turned into a matched pair of
   GL entries (debit accounts-receivable, credit income) using
   ``integrations.erp.tx.ledger``'s own :class:`~integrations.erp.tx.ledger.Entry`
   and :class:`~integrations.erp.tx.ledger.GeneralLedger`, and
   ``GeneralLedger.verify()`` is called *before* the result is recorded as
   posted. A derivation that does not balance is refused
   (``unbalanced-posting``) rather than posted — this is what makes
   "never half-posted" a property of the code path rather than a hope: the
   ledger object is fully built and checked in memory, and only a ledger that
   passes ``verify()`` with zero findings is ever handed back as ``posted``.

Any step that raises :class:`~.model.Refused` quarantines the event
(:mod:`quarantine`) and returns a ``"quarantined"`` result — the caller (the
transport adapter) decides what that means for the HTTP/queue response; this
lane never itself decides to drop a rejected event without a record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional

from integrations.erp.tx.ledger import Entry, GeneralLedger, PostingPolicy

from . import auth as auth_module
from . import flags as flags_module
from . import schema as schema_module
from .idempotency import IdempotencyStore
from .model import (
    BridgeResult,
    ConversionEvent,
    HOOK_LEAD_CONVERSION,
    HOOK_OPPORTUNITY_WIN,
    KNOWN_HOOKS,
    Refused,
)
from .quarantine import QuarantinedEvent, QuarantineStore

#: The posting roles this bridge needs from the caller's policy. A policy that
#: does not declare both is ``missing-policy`` — the bridge guesses no account,
#: the same discipline ``tx.ledger.PostingPolicy`` documents for ERP-03.
ROLE_RECEIVABLE = "receivable"
ROLE_INCOME = "income"
REQUIRED_ROLES = (ROLE_RECEIVABLE, ROLE_INCOME)

VOUCHER_TYPE = "crm-conversion"


@dataclass
class ConversionBridge:
    """Wires auth, schema, idempotency and GL derivation into one handler."""

    secret: str
    posting_policy: PostingPolicy
    idempotency_store: IdempotencyStore = field(default_factory=IdempotencyStore)
    quarantine_store: QuarantineStore = field(default_factory=QuarantineStore)
    ledger: GeneralLedger = field(default_factory=GeneralLedger)
    flags: Optional[Mapping[str, bool]] = None

    def _quarantine(self, code: str, detail: str, raw_payload: object, at: str) -> BridgeResult:
        self.quarantine_store.add(QuarantinedEvent(code=code, detail=detail, raw_payload=raw_payload, at=at))
        return BridgeResult(status="quarantined", reason=f"{code}: {detail}")

    def handle(
        self,
        raw_body: bytes,
        signature_header: Optional[str],
        payload: object,
        *,
        at: str = "",
    ) -> BridgeResult:
        """Handle one inbound delivery. Never raises: every path returns a
        :class:`~.model.BridgeResult`, so a transport adapter never has to
        guess whether an uncaught exception means "post" or "drop".
        """
        if not flags_module.is_enabled(self.flags):
            return self._quarantine(
                "quarantined", f"{flags_module.FLAG_ID} is off; the bridge refuses all deliveries", payload, at
            )
        try:
            auth_module.verify(self.secret, raw_body, signature_header)
            event = schema_module.parse(payload)
            return self._post(event, payload, at=at)
        except Refused as exc:
            return self._quarantine(exc.code, exc.detail, payload, at)

    def _post(self, event: ConversionEvent, raw_payload: object, *, at: str) -> BridgeResult:
        existing = self.idempotency_store.seen(event.idempotency_key)
        if existing is not None:
            return BridgeResult(
                status="duplicate",
                event_id=event.event_id,
                voucher_id=existing.voucher_id,
                entries=existing.entries,
                reason="idempotency key already recorded",
            )

        missing_roles = [role for role in REQUIRED_ROLES if role not in self.posting_policy.accounts]
        if missing_roles:
            raise Refused(
                "missing-policy",
                f"posting policy declares no account for role(s): {', '.join(missing_roles)}",
            )

        voucher_id = f"{VOUCHER_TYPE}:{event.event_id}"
        receivable_account = self.posting_policy.resolve(ROLE_RECEIVABLE)
        income_account = self.posting_policy.resolve(ROLE_INCOME)

        entries = (
            Entry(
                voucher_type=VOUCHER_TYPE,
                voucher_id=voucher_id,
                account=receivable_account,
                debit=event.amount,
                at=event.occurred_at or at,
            ),
            Entry(
                voucher_type=VOUCHER_TYPE,
                voucher_id=voucher_id,
                account=income_account,
                credit=event.amount,
                at=event.occurred_at or at,
            ),
        )

        candidate_ledger = self.ledger.apply(entries)
        findings = [
            f for f in candidate_ledger.verify() if f.ref == voucher_id or f.ref is None
        ]
        if findings:
            raise Refused(
                "unbalanced-posting",
                f"{voucher_id}: " + "; ".join(f.detail for f in findings),
            )

        self.ledger = candidate_ledger
        result = BridgeResult(status="posted", event_id=event.event_id, voucher_id=voucher_id, entries=entries)
        self.idempotency_store.record(event.idempotency_key, result)
        return result


__all__ = [
    "ConversionBridge",
    "HOOK_LEAD_CONVERSION",
    "HOOK_OPPORTUNITY_WIN",
    "KNOWN_HOOKS",
    "REQUIRED_ROLES",
    "VOUCHER_TYPE",
]
