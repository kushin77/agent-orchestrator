"""The vocabulary is closed, and an event's identity is derived (issue #654)."""

from __future__ import annotations

import pytest

from integrations.erp.finops.model import (
    EVENT_KIND_FOR_OPERATION,
    OPERATIONS,
    REFUSALS,
    MeteredEvent,
    Refused,
    normalize_event_ts,
)


def test_the_operation_vocabulary_is_closed() -> None:
    assert OPERATIONS == ("create", "transition")
    assert set(EVENT_KIND_FOR_OPERATION) == set(OPERATIONS)


def test_every_refusal_is_declared_once() -> None:
    assert len(REFUSALS) == len(set(REFUSALS))


def test_a_refusal_outside_the_vocabulary_cannot_be_constructed() -> None:
    with pytest.raises(ValueError, match="not in the declared refusal vocabulary"):
        Refused("invented-as-we-go")


def test_an_event_with_an_undeclared_operation_is_refused() -> None:
    with pytest.raises(Refused) as caught:
        MeteredEvent(
            tenant="acme",
            kind="sales-order",
            document_id="SO-1",
            operation="delete",
            actor="agent:x",
            at="2026-09-15T10:00:00Z",
        )
    assert caught.value.code == "unknown-operation"


def test_the_source_key_is_derived_from_the_event_not_from_a_counter() -> None:
    fields = dict(
        tenant="acme",
        kind="sales-order",
        document_id="SO-1",
        operation="create",
        actor="agent:x",
    )
    first = MeteredEvent(**fields, at="2026-09-15T10:00:00Z")
    replay = MeteredEvent(**fields, at="2026-09-15T10:00:00Z")
    later = MeteredEvent(**fields, at="2026-09-15T10:00:01Z")
    assert first.source_key() == replay.source_key()
    assert first.source_key() != later.source_key()


def test_the_event_kind_and_resource_follow_from_the_operation_and_kind() -> None:
    event = MeteredEvent(
        tenant="acme",
        kind="sales-order",
        document_id="SO-1",
        operation="transition",
        actor="agent:x",
        at="2026-09-15T10:00:00Z",
        from_state="draft",
        to_state="submitted",
    )
    assert event.event_kind == "erp.document.transitioned"
    assert event.resource == "erp/documents/sales-order/SO-1"
    payload = event.to_dict()
    assert payload["fromState"] == "draft"
    assert payload["toState"] == "submitted"


def test_a_missing_timestamp_is_refused_rather_than_defaulted_to_now() -> None:
    with pytest.raises(Refused) as caught:
        normalize_event_ts(None)
    assert caught.value.code == "invalid-timestamp"

    with pytest.raises(Refused) as caught:
        normalize_event_ts("half past three")
    assert caught.value.code == "invalid-timestamp"


def test_a_parseable_timestamp_is_normalised_to_the_repo_shape() -> None:
    assert normalize_event_ts("2026-09-15T12:00:00+02:00") == "2026-09-15T10:00:00Z"
