"""telemetry/role_health — per-role cadence + declaration consumption (#637).

---knowledge---
module_id: telemetry.role_health_tests.test_declared_inputs
system: telemetry
app: role_health_tests
solution_class: enterprise
patterns: [total-over-registry-vocabulary, pinned-constants]
derives_from: null
owner_sme: qa-sme
tier: L1
interfaces: ["pytest suite over the cadence table and the declared role inputs"]
invariants: "the cadence table must be total over the registry's own cadence vocabulary, not over a local list that could drift"
gotchas: ""
related: ["#637", "#1510"]
do_not_duplicate: null
---knowledge---


The staleness rule is only meaningful if the cadence table and the declared
role inputs are the *declared* ones.  These tests pin both, and prove the table
is total over the registry's own cadence vocabulary rather than over a local
list that could drift from it.
"""

from __future__ import annotations

import pytest

from telemetry.role_health import (
    CADENCE_SECONDS,
    CADENCE_SLACK_FACTOR,
    DEFAULT_WARN_AT_PCT,
    EVENT_CADENCES,
    STATUS_UNKNOWN,
    cadence_seconds,
    cadence_threshold_seconds,
    is_event_cadence,
    load_role_caps,
)

# The workbook-1 cadence vocabulary (registry/personas/registry.py:79).
REGISTRY_CADENCES = {
    "hourly",
    "every-30m",
    "every-15m",
    "daily",
    "event",
    "webhook",
}

REGISTRY_WALL_CLOCK = REGISTRY_CADENCES - EVENT_CADENCES


def test_cadence_table_covers_every_wall_clock_registry_cadence() -> None:
    """The table is total over the registry's wall-clock cadences, no extras."""
    assert set(CADENCE_SECONDS) == REGISTRY_WALL_CLOCK


def test_cadence_seconds_are_pinned() -> None:
    assert CADENCE_SECONDS == {
        "every-15m": 900.0,
        "every-30m": 1800.0,
        "hourly": 3600.0,
        "daily": 86400.0,
    }


def test_threshold_is_cadence_times_the_declared_slack() -> None:
    """The threshold a test pins is the threshold the evaluator used."""
    assert cadence_threshold_seconds("every-15m") == 900.0 * CADENCE_SLACK_FACTOR
    assert cadence_threshold_seconds("every-30m") == 1800.0 * CADENCE_SLACK_FACTOR
    assert cadence_threshold_seconds("hourly") == 3600.0 * CADENCE_SLACK_FACTOR
    assert cadence_threshold_seconds("daily") == 86400.0 * CADENCE_SLACK_FACTOR


def test_no_cadence_reads_as_no_threshold_never_a_default() -> None:
    """An absent/unknown/event cadence has no deadline — never a guessed one."""
    assert cadence_seconds(None) is None
    assert cadence_seconds("every-7m") is None
    assert cadence_seconds("event") is None
    assert cadence_threshold_seconds(None) is None
    assert cadence_threshold_seconds("webhook") is None
    assert is_event_cadence("event") and is_event_cadence("webhook")
    assert not is_event_cadence("hourly")


def test_load_role_caps_consumes_the_live_declaration(caps) -> None:
    """Caps/schedules come from the workbook-1 declaration, not a local list."""
    assert caps.caps == {"ceo": 300.0, "cto": 250.0, "coo": 100.0, "cfo": 50.0, "cmo": 200.0}
    assert caps.schedules == {
        "ceo": "hourly",
        "cto": "every-30m",
        "coo": "every-15m",
        "cfo": "daily",
        "cmo": "hourly",
    }
    assert caps.tiers["ceo"] == "MAX"
    assert caps.tiers["cfo"] == "LOW"


def test_load_role_caps_carries_the_declared_warn_threshold(caps) -> None:
    """The over-cap threshold is consumed from gateway/finops/budgets.yaml."""
    assert caps.warn_at_pct == 100.0
    assert caps.policy == "stop"
    assert "org-chart.yaml" in caps.source
    assert "budgets.yaml" in caps.source


def test_load_role_caps_falls_back_to_the_declared_default(tmp_path) -> None:
    """A missing budgets.yaml degrades to the declared default, not a new number."""
    caps = load_role_caps(budgets_yaml=tmp_path / "absent.yaml")
    assert caps.warn_at_pct == DEFAULT_WARN_AT_PCT == 100.0
    assert caps.role_ids() == ["ceo", "cfo", "cmo", "coo", "cto"]


def test_counts_are_pinned(caps) -> None:
    """Five declared roles, in this order, is the measured fact here."""
    assert caps.role_ids() == ["ceo", "cfo", "cmo", "coo", "cto"]
    assert caps.tenant == "platform"
    assert caps.threshold_seconds("cfo") == 86400.0
    assert caps.schedule("nope") is None
    assert caps.to_dict()["roles"]["cto"]["heartbeatSchedule"] == "every-30m"
