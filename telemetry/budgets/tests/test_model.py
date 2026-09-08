"""telemetry/budgets — model vocabulary tests (issue #34).

The decision ladder / outcome mapping / resource vocabulary this lane owns
and the metering mapping consumed from issue #33.
"""

from __future__ import annotations

import pytest

from telemetry.budgets.model import (
    ALLOWED_DECISIONS,
    BLOCKING_DECISIONS,
    DECISION_ALLOW,
    DECISION_BLOCK,
    DECISION_WARN,
    EnforcerDecision,
    KIND_BUDGET,
    KIND_KILL_SWITCH,
    KIND_QUOTA,
)


def test_decision_ladder_disjoint():
    # No overlap: a decision is either allowed or blocking, never both.
    assert not (ALLOWED_DECISIONS & BLOCKING_DECISIONS)


def test_blocking_is_not_allowed():
    d = EnforcerDecision(tenant_id="acme", kind=KIND_BUDGET,
                         decision=DECISION_BLOCK, reason="x", code="x")
    assert not d.allowed
    assert d.auditable


def test_warn_is_allowed_but_auditable():
    d = EnforcerDecision(tenant_id="acme", kind=KIND_BUDGET,
                         decision=DECISION_WARN, reason="x", code="x")
    assert d.allowed
    assert d.auditable


def test_plain_allow_is_not_auditable():
    d = EnforcerDecision(tenant_id="acme", kind=KIND_BUDGET,
                         decision=DECISION_ALLOW, reason="x", code="x")
    assert d.allowed
    assert not d.auditable


def test_outcome_mapping_on_block():
    d = EnforcerDecision(tenant_id="acme", kind=KIND_QUOTA,
                         decision=DECISION_BLOCK, reason="x",
                         code="quota.hard.requests.exceeded", outcome="blocked")
    assert d.to_dict()["outcome"] == "blocked"


def test_unknown_decision_rejected():
    with pytest.raises(ValueError):
        EnforcerDecision(tenant_id="acme", kind=KIND_BUDGET,
                         decision="maybe", reason="x", code="x")


def test_unknown_kind_rejected():
    with pytest.raises(ValueError):
        EnforcerDecision(tenant_id="acme", kind="nonsense",
                         decision=DECISION_ALLOW, reason="x", code="x")
