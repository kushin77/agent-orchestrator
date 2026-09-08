"""Metering/audit event-router tests (issue #15, criterion 4).

The ``EventRouter`` fans every stamped ``ModelCallEvent`` out to the metering
and audit hook sets. A hook that raises propagates (fail closed - audit and
metering records are never silently lost). Events are stamped with
provider/model/tenant/agent/logical key and never carry key material.
"""

from __future__ import annotations

import pytest

from providers.contract import Usage
from providers.events import (
    STATUS_FAILED,
    STATUS_SUCCESS,
    EventRouter,
    ModelCallEvent,
)


def _event(**overrides) -> ModelCallEvent:
    fields = dict(
        provider="deepseek",
        model="deepseek-chat",
        tenant_id="acme",
        agent_id="agent-1",
        logical_key="MED",
    )
    fields.update(overrides)
    return ModelCallEvent(**fields)


def test_event_serializes_with_full_stamp() -> None:
    event = _event(
        usage=Usage(input_tokens=10, output_tokens=5),
        latency_ms=4.2,
        attempts=2,
        status=STATUS_SUCCESS,
    )
    data = event.to_dict()
    assert data["provider"] == "deepseek"
    assert data["model"] == "deepseek-chat"
    assert data["tenant_id"] == "acme"
    assert data["agent_id"] == "agent-1"
    assert data["logical_key"] == "MED"
    assert data["usage"]["tokens"] == 15
    assert data["latency_ms"] == 4.2
    assert data["attempts"] == 2
    assert data["status"] == STATUS_SUCCESS
    assert "ts" in data


def test_router_dispatches_to_metering_and_audit() -> None:
    metering: list = []
    audit: list = []
    router = EventRouter(metering_hooks=[metering.append], audit_hooks=[audit.append])
    event = _event()
    router.emit(event)
    assert metering == [event]
    assert audit == [event]


def test_router_add_hooks() -> None:
    router = EventRouter()
    seen: list = []
    router.add_metering_hook(seen.append)
    router.add_audit_hook(seen.append)
    router.emit(_event(status=STATUS_SUCCESS))
    router.emit(_event(status=STATUS_FAILED))
    assert len(seen) == 4  # metering + audit for each of the two events


def test_hook_exception_propagates_fail_closed() -> None:
    router = EventRouter()

    def boom(_event: ModelCallEvent) -> None:
        raise RuntimeError("audit sink unavailable")

    router.add_audit_hook(boom)
    with pytest.raises(RuntimeError):
        router.emit(_event())


def test_events_do_not_carry_key_material() -> None:
    # Keys live only in the vault/request headers; an event carries no secret.
    event = _event(status=STATUS_SUCCESS)
    blob = str(event.to_dict())
    assert "api-key" not in blob.lower()
    assert "authorization" not in blob
    assert "x-api-key" not in blob
