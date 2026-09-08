"""Outbox bus tests: idempotent publish, poll/dispatch, redelivery, dead-letter.

Issue #38 AC2 — the event/outbox bus for async effects with redelivery and the
outbox pattern to avoid dual-write. Direct bus tests plus endpoint-level
consumer contract tests.
"""

import itertools
import json

import pytest

from cpapi import errors as err
from cpapi.fakes import FakeClock, build_test_app
from cpapi.outbox import Outbox, open_outbox


def _unique_rng():
    counter = itertools.count(1)
    return lambda: f"{next(counter):x}".ljust(20, "0")


@pytest.fixture()
def clock():
    return FakeClock("2026-09-08T00:00:00Z")


@pytest.fixture()
def box(clock):
    return Outbox(clock=clock, lease_seconds=300, rng=_unique_rng())


@pytest.fixture()
def rig():
    return build_test_app(tenant_id="acme", admin_subject="u_admin")


# --- append-only + idempotent publish -------------------------------------------


def test_publish_appends_with_monotonic_seq(box):
    first = box.publish("agent.registered", "acme", aggregate_id="worker-1")
    second = box.publish("agent.activated", "acme", aggregate_id="worker-1")
    assert (first.seq, second.seq) == (1, 2)
    assert first.eventId != second.eventId
    assert [r.seq for r in box.records] == [1, 2]


def test_publish_unknown_event_type_refused(box):
    with pytest.raises(err.ApiError) as exc:
        box.publish("mystery.event", "acme")
    assert exc.value.status == 422
    assert exc.value.code == "refused"


def test_publish_idempotent_by_key_returns_existing(box):
    one = box.publish(
        "agent.registered", "acme", aggregate_id="worker-1",
        idempotency_key="register:acme:worker-1",
    )
    two = box.publish(
        "agent.registered", "acme", aggregate_id="worker-1",
        idempotency_key="register:acme:worker-1",
    )
    # No duplicate append; the same record is returned.
    assert one.eventId == two.eventId
    assert len(box) == 1
    assert box.state() == {"pending": 1}


def test_duplicate_event_id_is_conflict(clock):
    box = Outbox(clock=clock, rng=lambda: "same-same-same")
    box.publish("agent.registered", "acme")
    with pytest.raises(err.ApiError) as exc:
        box.publish("agent.registered", "acme")  # identical generated id
    assert exc.value.code == "duplicate_event"


# --- consumer dispatch contract ---------------------------------------------------


def test_poll_claims_oldest_first_and_marks_dispatched(box):
    e1 = box.publish("agent.registered", "acme", aggregate_id="worker-1")
    e2 = box.publish("agent.registered", "acme", aggregate_id="worker-2")
    claimed = box.poll(limit=10, consumer="user:worker-1")
    assert [e.seq for e in claimed] == [1, 2]
    assert all(e.state == "dispatched" for e in claimed)
    assert box.state() == {"dispatched": 2}
    # Polling again yields nothing (nothing pending).
    assert box.poll(consumer="user:worker-1") == []


def test_ack_marks_delivered_and_is_idempotent(box):
    event = box.publish("agent.registered", "acme", aggregate_id="worker-1")
    box.poll(consumer="consumer-1")
    delivered = box.ack(event.eventId, consumer="consumer-1")
    assert delivered.state == "delivered"
    assert box.state() == {"delivered": 1}
    # Re-acking a delivered event is a no-op, not an error.
    again = box.ack(event.eventId, consumer="consumer-1")
    assert again.state == "delivered"


def test_ack_wrong_consumer_is_refused(box):
    event = box.publish("agent.registered", "acme")
    box.poll(consumer="consumer-1")
    with pytest.raises(err.ApiError) as exc:
        box.ack(event.eventId, consumer="consumer-2")
    assert exc.value.status == 422


def test_fail_redelivers_then_dead_letters(box):
    event = box.publish("task.completed", "acme", aggregate_id="task-1")
    # max_attempts=5 default: four failures redeliver, the fifth kills it.
    for attempt in range(4):
        box.poll(consumer="consumer-1")
        box.fail(event.eventId, consumer="consumer-1", error=f"attempt {attempt}")
        assert box.get(event.eventId).state == "pending"
    box.poll(consumer="consumer-1")
    dead = box.fail(event.eventId, consumer="consumer-1", error="attempt 5")
    assert dead.state == "dead"
    assert dead.attempts == 5
    # Dead events are replayable.
    replayed = box.replay([event.eventId])
    assert replayed[0].state == "pending"
    assert replayed[0].attempts == 0


def test_lease_expiry_redelivers_orphaned_events(clock):
    box = Outbox(clock=clock, lease_seconds=10)
    event = box.publish("control.pause", "acme")
    box.poll(consumer="orphan")
    assert box.get(event.eventId).state == "dispatched"
    # The lease lapses (consumer crashed without ack)...
    clock.advance(11)
    reclaimed = box.redeliver_due()
    assert [e.eventId for e in reclaimed] == [event.eventId]
    assert box.get(event.eventId).state == "pending"
    # ... so a healthy consumer can claim it again.
    clock.advance(1)
    claimed = box.poll(consumer="healthy")
    assert [e.eventId for e in claimed] == [event.eventId]


def test_lease_not_expired_not_reclaimed(clock):
    box = Outbox(clock=clock, lease_seconds=60)
    event = box.publish("agent.registered", "acme")
    box.poll(consumer="active")
    clock.advance(30)
    assert box.redeliver_due() == []
    assert box.get(event.eventId).state == "dispatched"


def test_records_are_never_rewritten(box):
    event = box.publish("agent.registered", "acme", aggregate_id="worker-1")
    original = box.get(event.eventId)
    box.poll(consumer="c")
    after = box.get(event.eventId)
    # seq/type/tenant/aggregate are immutable across delivery state changes.
    assert (after.seq, after.type, after.tenantId, after.aggregateId) == (
        original.seq, original.type, original.tenantId, original.aggregateId,
    )
    assert len(box.records) == 1


# --- file-backed outbox ------------------------------------------------------------


def test_open_outbox_roundtrip(clock, tmp_path):
    path = str(tmp_path / "outbox.jsonl")
    box = open_outbox(path, clock=clock)
    box.publish("agent.registered", "acme", aggregate_id="worker-1", idempotency_key="k1")
    box.publish("control.pause", "acme", aggregate_id="acme")
    reopened = open_outbox(path, clock=clock)
    assert len(reopened) == 2
    assert [r.seq for r in reopened.records] == [1, 2]
    # Idempotency is preserved across reopen (dedupe by persisted key).
    dup = reopened.publish("agent.registered", "acme", aggregate_id="worker-1", idempotency_key="k1")
    assert dup.seq == 1
    assert len(reopened) == 2


def test_open_outbox_refuses_gapped_file(clock, tmp_path):
    path = str(tmp_path / "bad.jsonl")
    box = open_outbox(path, clock=clock)
    box.publish("agent.registered", "acme")
    # Corrupt: rewrite the file with a non-contiguous seq.
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"seq": 5, "eventId": "evt_x", "type": "agent.registered",
                                 "tenant_id": "acme", "aggregate_id": None, "payload": {},
                                 "actor": "system", "created_at": clock.now_utc(),
                                 "state": "pending", "attempts": 0, "consumer": None,
                                 "lease_until": None, "last_error": None,
                                 "idempotency_key": None}) + "\n")
    with pytest.raises(ValueError):
        open_outbox(path, clock=clock)


# --- endpoint-level consumer contract ------------------------------------------------


def test_api_register_then_poll_ack(rig):
    admin = rig.principal("acme", "u_admin")
    rig.app.handle("POST", "/v1/agents", principal=admin, body={"agentId": "worker-1", "profileRef": "coder"})

    # Consumer lists pending domain events for its tenant.
    events = rig.app.handle("GET", "/v1/outbox/events", principal=admin)
    assert events["status"] == 200
    assert events["data"]["count"] == 2  # agent.registered + agent.provision

    polled = rig.app.handle(
        "POST", "/v1/outbox/poll", principal=admin,
        body={"consumer": "user:worker-1", "limit": 10},
    )
    assert polled["status"] == 200
    assert polled["data"]["count"] == 2
    event_id = polled["data"]["items"][0]["eventId"]

    acked = rig.app.handle("POST", f"/v1/outbox/{event_id}/ack", principal=admin, body={"consumer": "user:worker-1"})
    assert acked["status"] == 200
    assert acked["data"]["state"] == "delivered"

    failed = rig.app.handle("GET", "/v1/outbox/events", principal=admin)
    delivered = [e for e in failed["data"]["items"] if e["state"] == "delivered"]
    assert len(delivered) == 1


def test_api_poll_is_tenant_scoped(rig):
    admin = rig.principal("acme", "u_admin")
    rig.app.handle("POST", "/v1/agents", principal=admin, body={"agentId": "worker-1", "profileRef": "coder"})
    # A globex admin sees no acme events (no cross-tenant leakage).
    rig.authorizer.add_admin("g_admin", "globex")
    globex_admin = rig.principal("globex", "g_admin")
    events = rig.app.handle("GET", "/v1/outbox/events", principal=globex_admin)
    assert events["status"] == 200
    assert events["data"]["count"] == 0
