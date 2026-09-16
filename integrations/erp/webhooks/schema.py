"""Schema gate for inbound conversion events (issue #671).

Validation is hand-written against
``schema/conversion-event.schema.json`` rather than run through a generic
JSON-Schema library: this lane has no runtime dependency beyond the stdlib
(the same constraint ``integrations/erp/catalog/validate.py`` documents for its
own stdlib-subset validator), and a conversion event has few enough fields that
a hand check is both auditable and exactly as strict as the schema file it
mirrors. ``tests/test_schema.py`` asserts the two never diverge on the field
set.

Every refusal here is ``schema-violation`` (or ``invalid-body`` for a payload
that is not even a mapping) — the schema gate does not distinguish *why* a
field is wrong beyond naming it, because :mod:`bridge` treats every schema
failure identically: quarantine, never post.
"""

from __future__ import annotations

from typing import Any, Mapping

from .model import KNOWN_HOOKS, SCHEMA_VERSION, ConversionEvent, Refused

REQUIRED_FIELDS = (
    "schemaVersion",
    "eventId",
    "hook",
    "tenant",
    "occurredAt",
    "customerId",
    "customerName",
    "amount",
    "currency",
    "sourceDocument",
)

_OPTIONAL_FIELDS = ("idempotencyKey",)

ALL_FIELDS = REQUIRED_FIELDS + _OPTIONAL_FIELDS


def _fail(detail: str) -> "Refused":
    return Refused("schema-violation", detail)


def parse(payload: Any) -> ConversionEvent:
    """Validate ``payload`` and return the :class:`ConversionEvent` it names.

    Raises :class:`Refused` (``invalid-body`` or ``schema-violation``) on any
    departure from the schema. Never returns a partially-built event: the
    event is constructed only after every field has been checked.
    """
    if not isinstance(payload, Mapping):
        raise Refused("invalid-body", f"payload is a {type(payload).__name__}, not an object")

    unknown = set(payload) - set(ALL_FIELDS)
    if unknown:
        raise _fail(f"unknown field(s): {', '.join(sorted(unknown))}")

    missing = [field for field in REQUIRED_FIELDS if field not in payload]
    if missing:
        raise _fail(f"missing required field(s): {', '.join(missing)}")

    schema_version = payload.get("schemaVersion")
    if schema_version != SCHEMA_VERSION:
        raise _fail(f"schemaVersion must be {SCHEMA_VERSION}, got {schema_version!r}")

    def _nonempty_str(name: str) -> str:
        value = payload.get(name)
        if not isinstance(value, str) or not value.strip():
            raise _fail(f"{name} must be a non-empty string, got {value!r}")
        return value

    event_id = _nonempty_str("eventId")
    hook = _nonempty_str("hook")
    if hook not in KNOWN_HOOKS:
        raise Refused(
            "unknown-hook", f"hook {hook!r} is not one of {', '.join(KNOWN_HOOKS)}"
        )
    tenant = _nonempty_str("tenant")
    occurred_at = _nonempty_str("occurredAt")
    customer_id = _nonempty_str("customerId")
    customer_name = _nonempty_str("customerName")
    source_document = _nonempty_str("sourceDocument")

    amount = payload.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount <= 0:
        raise _fail(f"amount must be a positive number, got {amount!r}")

    currency = payload.get("currency")
    if not isinstance(currency, str) or len(currency) != 3:
        raise _fail(f"currency must be a 3-letter code, got {currency!r}")

    idempotency_key = payload.get("idempotencyKey") or ""
    if not isinstance(idempotency_key, str):
        raise _fail(f"idempotencyKey must be a string, got {idempotency_key!r}")

    return ConversionEvent(
        schema_version=schema_version,
        event_id=event_id,
        hook=hook,
        tenant=tenant,
        occurred_at=occurred_at,
        customer_id=customer_id,
        customer_name=customer_name,
        amount=float(amount),
        currency=currency.upper(),
        source_document=source_document,
        idempotency_key=idempotency_key,
    )
