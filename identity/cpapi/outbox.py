"""Append-only event/outbox bus with idempotent publish + poll/dispatch.

The control plane's async-effects rail (issue #38 AC2): a durable, append-only
outbox that records domain events exactly once and exposes a consumer
poll/dispatch contract with redelivery. The outbox pattern exists to **avoid
dual-write**: a mutation handler persists its business change and appends its
domain event in the same control-plane request, so the event stream is the
single source of truth for downstream async effects (provision, degrade,
budget-exceeded, kill, ...) — never a second write that can drift.

Record lifecycle::

    publish (pending) --poll--> dispatched (lease) --ack--> delivered
                                  |
                                  +--fail--> pending (redelivery, attempts+1)
                                  +--lease expiry--> pending (redelivery, via
                                                          redeliver_due)
    fail when attempts >= max_attempts --> dead (needs replay)

Guarantees (each negative-tested):

- **Append-only**: records are never rewritten, deleted or reordered; ``seq``
  is monotonic with no gaps or duplicates and ``eventId`` is unique.
- **Idempotent publish**: re-publishing under the same ``idempotency_key``
  returns the existing record instead of appending a duplicate — a redelivered
  request never emits its effect twice at the bus layer.
- **Closed event vocabulary**: ``publish`` refuses an unknown event type (fail
  closed); the merged pillars' vocabularies appear as dotted types.
- **Redelivery**: a dispatched event whose consumer never acked within its
  lease is reclaimed by ``redeliver_due``; a failed event is redelivered up to
  ``max_attempts``, then dead (replayable).

In-memory by default (offline tests); ``Outbox`` also supports an append-only
JSON Lines file backing via ``open_outbox`` for durable single-node use.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .errors import conflict, refused, validation_error
from .model import EventView

# Closed domain-event vocabulary (dotted types over the merged vocabularies).
EVENT_TYPES = frozenset(
    {
        # agent lifecycle / provisioning effects (registry + state machine)
        "agent.registered",
        "agent.activated",
        "agent.paused",
        "agent.retired",
        "agent.provision",
        "agent.degrade",
        # task/async effects
        "task.dispatched",
        "task.completed",
        # budget / safety rails (telemetry/budgets)
        "budget.exceeded",
        "control.pause",
        "control.resume",
        # governance / approvals
        "approval.required",
        "approval.approved",
        "approval.denied",
    }
)

# Delivery states.
STATE_PENDING = "pending"
STATE_DISPATCHED = "dispatched"
STATE_DELIVERED = "delivered"
STATE_DEAD = "dead"

DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_LEASE_SECONDS = 300


@dataclass(frozen=True)
class OutboxRecord:
    """One append-only outbox row."""

    seq: int
    event_id: str
    type: str
    tenant_id: str
    aggregate_id: Optional[str]
    payload: Dict[str, Any]
    actor: str
    created_at: str
    state: str = STATE_PENDING
    attempts: int = 0
    consumer: Optional[str] = None
    lease_until: Optional[str] = None
    last_error: Optional[str] = None
    idempotency_key: Optional[str] = None


def _now_ms(clock: Any) -> int:
    """RFC-3339 ts -> epoch ms for lease math (kept next to the clock seam)."""
    import datetime as _dt

    text = clock.now_utc()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError(f"clock returned a non-RFC-3339 timestamp: {text!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return int(parsed.timestamp() * 1000)


class Outbox:
    """Append-only outbox with idempotent publish + a poll/dispatch contract.

    ``clock`` supplies RFC-3339 UTC timestamps and ``lease_seconds`` the
    consumer dispatch lease. ``now_ms`` is derived from the clock so lease
    comparisons stay deterministic under an injected fake clock.
    """

    def __init__(
        self,
        *,
        clock: Any,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        rng: Any = None,
    ) -> None:
        import uuid as _uuid

        self._clock = clock
        self._max_attempts = max_attempts
        self._lease_seconds = lease_seconds
        self._rng = rng or (lambda: _uuid.uuid4().hex)
        self._records: List[OutboxRecord] = []
        self._by_id: Dict[str, OutboxRecord] = {}
        self._by_key: Dict[str, OutboxRecord] = {}

    # --- storage ------------------------------------------------------------------

    @property
    def records(self) -> List[OutboxRecord]:
        return list(self._records)

    def __len__(self) -> int:
        return len(self._records)

    # --- publish ------------------------------------------------------------------

    def publish(
        self,
        event_type: str,
        tenant_id: str,
        *,
        aggregate_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        actor: str = "system:control-plane",
        idempotency_key: Optional[str] = None,
    ) -> EventView:
        """Append one domain event, idempotently.

        Publishing the same ``idempotency_key`` again returns the existing
        record (no duplicate append). A generated key means every call appends
        (the caller owns dedupe, e.g. by ``aggregate_id`` + state). An unknown
        event type is refused (closed vocabulary).
        """
        if event_type not in EVENT_TYPES:
            raise refused(f"unknown event type {event_type!r} (closed vocabulary)")
        if idempotency_key is not None:
            existing = self._by_key.get(idempotency_key)
            if existing is not None:
                return self._to_view(existing)
        seq = len(self._records) + 1
        event_id = f"evt_{self._rng()[:20]}"
        record = OutboxRecord(
            seq=seq,
            event_id=event_id,
            type=event_type,
            tenant_id=tenant_id,
            aggregate_id=aggregate_id,
            payload=dict(payload or {}),
            actor=actor,
            created_at=self._clock.now_utc(),
            idempotency_key=idempotency_key,
        )
        if event_id in self._by_id:
            raise conflict("duplicate event id", code="duplicate_event")
        self._records.append(record)
        self._by_id[event_id] = record
        if idempotency_key is not None:
            self._by_key[idempotency_key] = record
        return self._to_view(record)

    def get(self, event_id: str) -> EventView:
        record = self._by_id.get(event_id)
        if record is None:
            raise refused(f"unknown event {event_id!r}")
        return self._to_view(record)

    # --- consumer dispatch contract --------------------------------------------------

    def poll(self, *, limit: int = 10, consumer: str = "consumer") -> List[EventView]:
        """Dispatch contract: claim the oldest ``pending`` events for a consumer.

        Claimed events move to ``dispatched`` with a lease; the consumer must
        ``ack`` within the lease or the event is redelivered by
        ``redeliver_due``. ``attempts`` counts *failures* (incremented on
        ``fail``, not on claim) so a consumer that crashes without acking is
        reclaimed by lease expiry without spending the dead-letter budget.
        """
        if limit < 1:
            raise validation_error("limit must be >= 1", field="limit")
        now = _now_ms(self._clock)
        claimed: List[EventView] = []
        for record in self._records:
            if len(claimed) >= limit:
                break
            if record.state != STATE_PENDING:
                continue
            updated = self._claim(record, consumer, now)
            claimed.append(self._to_view(updated))
        return claimed

    def events_for(self, tenant_id: str, *, limit: int = 100) -> List[EventView]:
        """Return the tenant's events (newest last) as typed views for listing."""
        if limit < 1:
            raise validation_error("limit must be >= 1", field="limit")
        matches = [r for r in self._records if r.tenant_id == tenant_id]
        return [self._to_view(r) for r in matches[-limit:]]

    def ack(self, event_id: str, *, consumer: str = "consumer") -> EventView:
        """Mark a dispatched event delivered (final state)."""
        record = self._require(event_id)
        if record.state != STATE_DISPATCHED:
            if record.state == STATE_DELIVERED:
                return self._to_view(record)  # idempotent ack
            raise conflict(
                f"event {event_id} is {record.state!r}, not dispatched",
                code="not_dispatched",
            )
        if record.consumer != consumer:
            raise refused(f"event {event_id} is leased to {record.consumer!r}")
        updated = OutboxRecord(**{**record.__dict__, "state": STATE_DELIVERED})
        self._replace(updated)
        return self._to_view(updated)

    def fail(
        self, event_id: str, *, consumer: str = "consumer", error: str = "processing failed"
    ) -> EventView:
        """Report a processing failure: redeliver or dead-letter.

        A failed event returns to ``pending`` for redelivery until
        ``max_attempts`` is spent, then lands in ``dead`` (replayable).
        """
        record = self._require(event_id)
        if record.state != STATE_DISPATCHED:
            raise conflict(
                f"event {event_id} is {record.state!r}, not dispatched",
                code="not_dispatched",
            )
        if record.consumer != consumer:
            raise refused(f"event {event_id} is leased to {record.consumer!r}")
        attempts = record.attempts + 1
        state = STATE_DEAD if attempts >= self._max_attempts else STATE_PENDING
        updated = OutboxRecord(
            **{
                **record.__dict__,
                "state": state,
                "attempts": attempts,
                "consumer": None,
                "lease_until": None,
                "last_error": error,
            }
        )
        self._replace(updated)
        return self._to_view(updated)

    def redeliver_due(self) -> List[EventView]:
        """Reclaim dispatched events whose lease expired (orphaned consumer).

        A consumer that crashes without acking never loses the event: after
        the lease lapses the event returns to ``pending`` and is polled again
        (at-least-once with redelivery).
        """
        now = _now_ms(self._clock)
        reclaimed: List[EventView] = []
        for record in self._records:
            if record.state != STATE_DISPATCHED or not record.lease_until:
                continue
            if int(record.lease_until) > now:
                continue
            updated = OutboxRecord(
                **{
                    **record.__dict__,
                    "state": STATE_PENDING,
                    "consumer": None,
                    "lease_until": None,
                }
            )
            self._replace(updated)
            reclaimed.append(self._to_view(updated))
        return reclaimed

    def replay(self, event_ids: List[str]) -> List[EventView]:
        """Return dead events to ``pending`` (operator replay of the DLQ)."""
        replayed: List[EventView] = []
        for event_id in event_ids:
            record = self._require(event_id)
            if record.state != STATE_DEAD:
                raise conflict(f"event {event_id} is {record.state!r}, not dead")
            updated = OutboxRecord(
                **{**record.__dict__, "state": STATE_PENDING, "attempts": 0, "last_error": None}
            )
            self._replace(updated)
            replayed.append(self._to_view(updated))
        return replayed

    def state(self) -> Dict[str, int]:
        """Delivery counters (used by status endpoints and tests)."""
        counts: Dict[str, int] = {}
        for record in self._records:
            counts[record.state] = counts.get(record.state, 0) + 1
        return counts

    # --- internals ------------------------------------------------------------------

    def _claim(self, record: OutboxRecord, consumer: str, now_ms: int) -> OutboxRecord:
        lease_until = str(now_ms + self._lease_seconds * 1000)
        updated = OutboxRecord(
            **{
                **record.__dict__,
                "state": STATE_DISPATCHED,
                "consumer": consumer,
                "lease_until": lease_until,
            }
        )
        self._replace(updated)
        return updated

    def _require(self, event_id: str) -> OutboxRecord:
        record = self._by_id.get(event_id)
        if record is None:
            raise refused(f"unknown event {event_id!r}")
        return record

    def _replace(self, updated: OutboxRecord) -> None:
        # Append-only rule: seq/eventId/type/tenant never change on replace.
        old = self._by_id[updated.event_id]
        if (old.seq, old.event_id, old.type, old.tenant_id) != (
            updated.seq,
            updated.event_id,
            updated.type,
            updated.tenant_id,
        ):
            raise refused("outbox records are append-only (seq/type/tenant immutable)")
        self._records[updated.seq - 1] = updated
        self._by_id[updated.event_id] = updated

    def _to_view(self, record: OutboxRecord) -> EventView:
        return EventView(
            seq=record.seq,
            eventId=record.event_id,
            type=record.type,
            tenantId=record.tenant_id,
            aggregateId=record.aggregate_id,
            createdAt=record.created_at,
            state=record.state,
            attempts=record.attempts,
            payload=dict(record.payload),
        )


def _default_rng() -> str:
    import uuid as _uuid

    return _uuid.uuid4().hex


def open_outbox(path: str, *, clock: Any, **kwargs: Any) -> Outbox:
    """Open an outbox backed by an append-only JSON Lines file.

    The file is read on open and every publish is appended; this gives durable
    single-node behavior while keeping the same in-process contract. Rows with
    duplicate or non-contiguous sequence numbers are refused on load
    (append-only integrity).
    """
    box = Outbox(clock=clock, rng=_default_rng, **kwargs)
    seen: List[int] = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                seq = data["seq"]
                if seq != len(box._records) + 1:
                    raise ValueError(
                        "outbox file is not append-only (seq gap/reorder)"
                    )
                seen.append(seq)
                record = OutboxRecord(**data)
                box._records.append(record)
                box._by_id[record.event_id] = record
                if record.idempotency_key is not None:
                    box._by_key[record.idempotency_key] = record
    if seen and seen != list(range(1, max(seen) + 1)):
        raise ValueError("outbox file has duplicate or gapped sequence numbers")

    original_publish = box.publish

    def _publish_and_persist(*args: Any, **kw: Any) -> EventView:
        view = original_publish(*args, **kw)
        record = box._by_id[view.eventId]
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.__dict__, sort_keys=True) + "\n")
        return view

    box.publish = _publish_and_persist  # type: ignore[method-assign]
    return box
