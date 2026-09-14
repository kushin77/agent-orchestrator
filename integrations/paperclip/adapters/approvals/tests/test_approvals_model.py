"""The kind -> authority map and its refusals (issue #416)."""

from __future__ import annotations

import pytest

from integrations.paperclip.adapters.approvals.model import (
    AUTHORITIES,
    KIND_HIRE,
    KIND_OVERRIDE,
    KIND_TOP_UP,
    KINDS,
    ROLES,
    STATE_DENIED,
    STATE_GRANTED,
    STATE_PENDING,
    STATES,
    ApprovalRefused,
    authority_for,
)


def test_every_kind_names_exactly_one_authority() -> None:
    assert set(AUTHORITIES) == set(KINDS)
    for kind in KINDS:
        authority = authority_for(kind)
        assert authority.surface
        assert authority.store
        assert authority.deciders


def test_the_authorities_are_the_fleet_surfaces() -> None:
    assert authority_for(KIND_HIRE).surface == "governance/dispatch"
    assert authority_for(KIND_TOP_UP).surface == ".fleet/sent"
    assert authority_for(KIND_OVERRIDE).surface == "fleet/control.py"


def test_a_kind_with_no_authority_is_refused_by_name() -> None:
    with pytest.raises(ApprovalRefused) as excinfo:
        authority_for("transfer")
    assert excinfo.value.reason == "no-authority"
    assert "transfer" in excinfo.value.detail


def test_deciders_are_fleet_roles() -> None:
    for authority in AUTHORITIES.values():
        for role in authority.deciders:
            assert role in ROLES


def test_the_three_states_and_three_kinds_are_closed() -> None:
    assert STATES == (STATE_PENDING, STATE_GRANTED, STATE_DENIED)
    assert KINDS == (KIND_HIRE, KIND_TOP_UP, KIND_OVERRIDE)
