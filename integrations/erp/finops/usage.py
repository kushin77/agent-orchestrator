"""The metering sink: one ``UsageRecord`` per metered ERP operation.

The audit sink (:mod:`.ledger`) answers *"what happened"*; this module answers
*"what did it cost"*. An ERP document operation consumes no model tokens, so the
record is not shaped like a model call — it is shaped like the platform's usage
vocabulary, which is the only shape the platform's roll-ups and budget enforcers
understand:

* ``provider`` is ``erp`` and ``model`` is the ERP document kind, so the
  platform's existing per-provider/per-model attribution, chargeback report
  (``telemetry.budgets.chargeback``) and budget enforcer work on ERP usage with
  no change to those lanes. This lane does not publish a second cost model.
* ``input_tokens``/``output_tokens`` are zero, not absent: an ERP operation
  really does consume zero tokens, and the honest figure is zero.
* ``metered`` is the load-bearing flag. ``metered: False`` means *this operation
  is metered and nothing priced it* — ``cost_usd`` stays ``None`` (never ``0``),
  which is exactly the platform's "never silently zero" rule. A roll-up then
  refuses to bill it (:mod:`.rollup`) instead of reporting a free operation.

Consumption is public-API-only: a ``UsageStore`` is passed in and appended to
(``telemetry.metering.store``), and no path under ``telemetry/`` is ever opened
for writing here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from telemetry.metering.model import (
    COST_SOURCE_NONE,
    COST_SOURCE_RATE_CARD,
    UsageRecord,
)
from telemetry.metering.store import UsageStore

from . import schema as schemas
from .model import MeteredEvent, Refused

__all__ = [
    "ERP_PROVIDER",
    "EVENT_SCHEMA",
    "SOURCE_ERP_DOCUMENT",
    "UNMETERED_RATE_UNPUBLISHED",
    "UsageSink",
    "assert_event_shape",
    "assert_event_shape_dict",
]

#: The published shape of a metered event. Checked on every emission, so a field
#: the emitter stops writing is a refusal at the boundary rather than a discovery
#: by a consumer.
EVENT_SCHEMA = Path(__file__).resolve().parent / "schema" / "usage-event.schema.json"

#: The provider dimension every ERP operation is metered under. Kept as a
#: constant so a roll-up, a budget policy and a chargeback line cannot disagree
#: about the string.
ERP_PROVIDER = "erp"

#: The source type of a record this lane produces. The intake vocabulary in
#: ``telemetry.metering.intake`` names gateway/proxy shapes; an ERP document
#: operation is a genuinely new source, so it is named rather than mislabelled
#: as one of them.
SOURCE_ERP_DOCUMENT = "erp_document"

#: The ``unmeteredReason`` recorded when the rate card declares an operation
#: without a price. Distinct from a missing rate, which is refused outright.
UNMETERED_RATE_UNPUBLISHED = "erp-rate-unpublished"


def assert_event_shape_dict(payload: Mapping[str, Any], *, where: str = "event") -> None:
    """Refuse ``usage-event-invalid`` when a payload is not the published shape.

    A raising entry point of its own, because the check ``cli.py`` runs must be
    able to *fail* on a bad payload rather than merely report one: a guard that
    can only be observed is a guard nothing depends on.
    """
    violations = schemas.validate(
        dict(payload), schemas.load_and_refuse(EVENT_SCHEMA), where=where
    )
    if violations:
        raise Refused("usage-event-invalid", "; ".join(violations), where=where)


def assert_event_shape(event: MeteredEvent) -> None:
    """Refuse when a metered event does not match its own published shape."""
    assert_event_shape_dict(event.to_dict(), where=event.source_key())


@dataclass
class UsageSink:
    """The usage sink over one platform usage store."""

    store: UsageStore

    def record(self, event: MeteredEvent, *, cost_usd: Optional[float]) -> UsageRecord:
        """Append one usage record for this event and return it.

        ``cost_usd`` is ``None`` when the operation is declared unpriced; the
        record is then ``metered: False`` and stays billable, so it shows up in
        the roll-up as *unmetered* rather than disappearing.
        """
        record = self.build(event, cost_usd=cost_usd)
        self.store.append(record)
        return record

    def build(self, event: MeteredEvent, *, cost_usd: Optional[float]) -> UsageRecord:
        """Build (without storing) the usage record for one event."""
        assert_event_shape(event)
        metered = cost_usd is not None
        if metered and cost_usd <= 0:  # pragma: no cover - defensive
            raise ValueError(f"a metered ERP operation cannot cost {cost_usd!r}")
        return UsageRecord(
            tenant_id=event.tenant,
            agent_id=event.actor,
            provider=ERP_PROVIDER,
            model=event.kind,
            route=event.operation,
            outcome="ok",
            input_tokens=0,
            output_tokens=0,
            billable=True,
            metered=metered,
            ts=event.at,
            source_type=SOURCE_ERP_DOCUMENT,
            source_key=event.source_key(),
            cost_usd=cost_usd if metered else None,
            cost_source=COST_SOURCE_RATE_CARD if metered else COST_SOURCE_NONE,
            unmetered_reason=None if metered else UNMETERED_RATE_UNPUBLISHED,
        )

    def records(self) -> Any:
        """Every stored record, in append order."""
        return self.store.read()

    def count(self) -> int:
        """How many records the store holds."""
        return self.store.count()

    def seen(self, source_key: str) -> bool:
        """Whether the store already holds this operation's identity."""
        return self.store.seen(source_key)
