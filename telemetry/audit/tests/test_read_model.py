"""Read-model tests: determinism, the filter vocabulary, stats (issue #347)."""

from __future__ import annotations

import json

import pytest

import read_model
from read_model import FILTER_FIELDS, SEVERITIES, FilterError, severity_of


def test_records_are_sorted_and_deterministic(model):
    rows = model.records()
    assert [row["seq"] for row in rows if row["tenantId"] == "acme"] == [1, 2, 3, 4]
    assert [(row["tenantId"], row["seq"]) for row in rows] == sorted(
        (row["tenantId"], row["seq"]) for row in rows
    )
    assert rows == model.records()


def test_filter_is_deterministic(model):
    first = json.dumps(model.filter(actor="agent:worker-1"), sort_keys=True)
    second = json.dumps(model.filter(actor="agent:worker-1"), sort_keys=True)
    assert first == second


def test_stats_is_deterministic(model):
    assert json.dumps(model.stats(), sort_keys=True) == json.dumps(
        model.stats(), sort_keys=True
    )


def test_filter_by_actor_full_and_bare(model):
    full = model.filter(actor="agent:worker-1")
    bare = model.filter(actor="worker-1")
    assert [row["seq"] for row in full] == [2, 3]
    assert full == bare


def test_filter_by_agent_shorthand_is_actor_kind_agent(model):
    assert [row["seq"] for row in model.filter(agent="worker-1")] == [2, 3]
    # A user id is never matched by the agent shorthand.
    assert model.filter(agent="alice") == []


def test_filter_by_action(model):
    assert [row["seq"] for row in model.filter(action="model.call")] == [1, 1]
    assert len(model.filter(action="policy.deny")) == 1


def test_filter_by_severity_uses_the_derived_projection(model):
    assert [row["seq"] for row in model.filter(severity="info")] == [1, 1]
    assert [row["seq"] for row in model.filter(severity="notice")] == [2, 4]
    assert [row["seq"] for row in model.filter(severity="critical")] == [3]
    assert model.filter(severity="warning") == []


def test_filter_by_since_and_until_is_inclusive_and_windowed(model):
    assert [row["seq"] for row in model.filter(since="2026-09-08T10:01:00Z")] == [2, 3, 4, 1]
    assert [row["seq"] for row in model.filter(until="2026-09-08T10:01:00Z")] == [1, 2]
    window = model.filter(since="2026-09-08T10:01:00Z", until="2026-09-08T10:02:00Z")
    assert [row["seq"] for row in window] == [2, 3]


def test_filter_since_after_until_is_refused(model):
    with pytest.raises(FilterError):
        model.filter(since="2026-09-09T00:00:00Z", until="2026-09-08T00:00:00Z")


def test_filter_by_entity_matches_resource_and_evidence(model):
    assert [row["seq"] for row in model.filter(entity="gateway/proxy")] == [1, 3, 1]
    assert [row["seq"] for row in model.filter(entity="registry-event:aaa")] == [2]
    # A resource prefix matches the entries under it.
    assert [row["seq"] for row in model.filter(entity="registry/agents")] == [2, 4]


def test_filter_by_tenant_scopes_one_chain(model):
    rows = model.filter(tenant="beta")
    assert [row["tenantId"] for row in rows] == ["beta"]


def test_filter_by_unknown_field_is_refused_by_name(model):
    with pytest.raises(FilterError) as excinfo:
        model.filter(bogus="x")
    assert "bogus" in str(excinfo.value)
    assert "actor" in str(excinfo.value)


def test_filter_by_unknown_severity_is_refused(model):
    with pytest.raises(FilterError):
        model.filter(severity="emergency")


def test_filter_field_set_is_closed_and_documented():
    assert FILTER_FIELDS == (
        "actor",
        "agent",
        "action",
        "severity",
        "since",
        "until",
        "entity",
        "tenant",
    )
    assert SEVERITIES == ("info", "notice", "warning", "critical")


def test_severity_projection_is_total():
    assert severity_of("model.call") == "info"
    assert severity_of("policy.deny") == "critical"
    assert severity_of("registry.register") == "notice"
    assert severity_of("ledger.rechain") == "warning"
    assert severity_of("something.unheardof") == "info"
    assert severity_of("noverb") == "info"


def test_stats_counts_and_histograms(model):
    stats = model.stats()
    assert stats["totalRecords"] == 5
    assert stats["tenants"]["acme"]["records"] == 4
    assert stats["tenants"]["acme"]["tailSeq"] == 4
    assert stats["tenants"]["acme"]["firstTs"] == "2026-09-08T10:00:00Z"
    assert stats["tenants"]["acme"]["lastTs"] == "2026-09-09T09:00:00Z"
    assert stats["severityHistogram"] == {
        "info": 2,
        "notice": 2,
        "warning": 0,
        "critical": 1,
    }
    assert stats["actorKindHistogram"] == {"agent": 2, "system": 1, "user": 2}
    assert stats["actionHistogram"]["model.call"] == 2


def test_trusted_tail_reports_the_verified_anchor(model):
    tails = model.trusted_tail()
    assert tails["acme"]["seq"] == 4
    assert len(tails["acme"]["hash"]) == 64
    assert tails["beta"]["seq"] == 1


def test_read_model_scope_can_be_restricted(ledger_dir):
    scoped = read_model.open_read_model(ledger_dir, tenants=["beta"])
    assert scoped.tenants() == ["beta"]
    assert {row["tenantId"] for row in scoped.records()} == {"beta"}
