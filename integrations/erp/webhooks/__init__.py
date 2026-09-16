"""The CRM→ERPNext conversion-event webhook bridge (ERP-xx, issue #671, EPIC #665).

Connects a CRM conversion event directly to the ERP module's accounting spine:
on a qualifying conversion, this lane derives and posts a balanced GL entry
(receivable/income) so the ledger is written by the event, not by a human
later. See the package README for the full design and
``docs/erp-finops/token-baseline.md`` (PF-2, #667) for the hook inventory this
lane bridges or explicitly dispositions (:mod:`hooks`).

Ships flag-gated **off** (:data:`flags.FLAG_ID`, default off) per the epic's
non-goals. No ERPNext code is vendored, and this lane harvests no ERPNext
doctype: the event envelope in ``schema/conversion-event.schema.json`` is an
original, generic webhook-delivery shape (comparable to how most webhook
senders carry an HMAC-signed envelope), not a pattern read off upstream —
so unlike the module's other lanes this one carries no
``catalog/provenance.json`` (GR-10 harvest record): there is nothing harvested
to record. The GL posting itself is derived through
``integrations.erp.tx.ledger``, which already carries ERP-03's own provenance.
"""

from __future__ import annotations

from . import auth, bridge, flags, hooks, idempotency, model, quarantine, retry, schema

__all__ = [
    "auth",
    "bridge",
    "flags",
    "hooks",
    "idempotency",
    "model",
    "quarantine",
    "retry",
    "schema",
]
