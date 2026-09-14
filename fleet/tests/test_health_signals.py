"""The declared machine shape of the fleet-health family (issue #498).

The shape is the contract: a closed label set, a closed value set per label, and
a refusal that is NAMED. These tests prove the declaration is closed (an unknown
state, an extra label or an id-shaped key does not construct), that it is
IMPORTED from the authorities that own each verdict rather than re-typed here, and
that the in-tree JSON schema cannot drift from the module that enforces it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import health_signals as hs
import watchdog
from governance.reconcile.heartbeat import LIVE, ORPHAN, SUSPECT
from governance.reconcile.sweep import (
    FAILED_OUTCOME,
    PARKED,
    RECLAIMED,
    REPORTED,
    SHELVED_OUTCOME,
)

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema" / "health-signal.schema.json"


def test_the_family_declares_exactly_four_metric_kinds():
    """One fact per kind, and the metric name is derived from it, not hand-typed."""
    assert hs.SIGNAL_KINDS == (
        "rung_state",
        "rung_beat_age_seconds",
        "reconcile_outcome",
        "watchdog_verdict",
    )
    assert hs.metric_name(hs.SIGNAL_RUNG_STATE) == "fleet.rung_state"
    with pytest.raises(hs.ShapeRefused):
        hs.metric_name("slo_verdict")


def test_the_label_set_is_closed_at_seven_keys():
    """ADR-0022 D5: a bounded label set, and only these seven keys."""
    assert hs.LABEL_KEYS == frozenset(
        {"service", "signal", "rung", "state", "bucket", "outcome", "verdict"}
    )
    assert hs.LABEL_VALUES[hs.LABEL_SERVICE] == frozenset({hs.SERVICE})


@pytest.mark.parametrize("refused", sorted(hs.REFUSED_LABEL_KEYS))
def test_an_identity_shaped_label_is_refused_by_name(refused: str):
    """A session id is this fleet's `pod_id`: the refusal must be explicit."""
    with pytest.raises(hs.ShapeRefused) as excinfo:
        hs.make(hs.SIGNAL_RUNG_STATE, 1, rung="brain", state="healthy", **{refused: "anything"})
    assert refused in str(excinfo.value)
    assert "refused as identity" in str(excinfo.value)


def test_a_label_key_outside_the_closed_set_is_refused():
    with pytest.raises(hs.ShapeRefused) as excinfo:
        hs.make(hs.SIGNAL_RECONCILE_OUTCOME, 1, severity="high")
    assert "outside the closed label set" in str(excinfo.value)


def test_a_value_outside_its_closed_set_is_refused():
    with pytest.raises(hs.ShapeRefused) as excinfo:
        hs.make(hs.SIGNAL_RUNG_STATE, 1, rung="brain", state="ok")
    assert "outside the closed set for state" in str(excinfo.value)


def test_a_missing_declared_label_is_refused():
    with pytest.raises(hs.ShapeRefused) as excinfo:
        hs.make(hs.SIGNAL_RUNG_STATE, 1, rung="brain")
    assert "state" in str(excinfo.value)


def test_an_extra_label_on_a_declared_kind_is_refused():
    """The shape cannot grow silently: a kind carries exactly its declared labels."""
    with pytest.raises(hs.ShapeRefused) as excinfo:
        hs.make(hs.SIGNAL_RUNG_STATE, 1, rung="brain", state="healthy", outcome="reclaimed")
    assert "do not belong to this signal" in str(excinfo.value)


def test_a_non_finite_or_negative_value_is_refused():
    with pytest.raises(hs.ShapeRefused):
        hs.make(hs.SIGNAL_RUNG_BEAT_AGE, float("nan"), rung="brain", bucket="fresh")
    with pytest.raises(hs.ShapeRefused):
        hs.make(hs.SIGNAL_RUNG_BEAT_AGE, -1, rung="brain", bucket="fresh")


def test_a_unit_that_is_not_the_declared_unit_is_refused():
    with pytest.raises(hs.ShapeRefused) as excinfo:
        hs.Signal(
            kind=hs.SIGNAL_RUNG_BEAT_AGE,
            labels={hs.LABEL_SERVICE: hs.SERVICE, hs.LABEL_SIGNAL: hs.SIGNAL_RUNG_BEAT_AGE,
                    hs.LABEL_RUNG: "brain", hs.LABEL_BUCKET: "fresh"},
            value=1,
            unit="ms",
        )
    assert "declared unit" in str(excinfo.value)
    assert hs.make(hs.SIGNAL_RUNG_BEAT_AGE, 1, rung="brain", bucket="fresh").unit == "s"


def test_the_rung_vocabulary_is_the_watchdogs_own():
    """No second rung list: the rungs and states come from the supervisor itself."""
    assert hs.RUNG_STATES - {hs.NO_DATA} == set(watchdog.RUNG_STATES)
    assert set(hs.RUNG_NAMES) == {name for name, *_ in watchdog.RUNGS} | {watchdog.MONITOR_NAME}


def test_the_reconcile_vocabulary_is_imported_not_redeclared():
    """ADR-0022 D4's no-vocabulary-copy rule, applied to the fleet family."""
    for token in (
        RECLAIMED,
        PARKED,
        SHELVED_OUTCOME,
        REPORTED,
        FAILED_OUTCOME,
        LIVE,
        SUSPECT,
        ORPHAN,
    ):
        assert token in hs.RECONCILE_OUTCOMES
    # The two facets must stay disjoint or one closed label would be ambiguous.
    assert hs.RECONCILE_STATUSES.isdisjoint(hs.RECONCILE_OUTCOMES_DECIDED)


def test_the_watchdog_vocabulary_is_the_channels_own():
    import channel

    for kind in (
        channel.KIND_CURRENT,
        channel.KIND_DOWN,
        channel.KIND_DRIFTED,
        channel.KIND_CAPABILITY_STALE,
        channel.KIND_UNKNOWN,
    ):
        assert kind in hs.WATCHDOG_VERDICTS


def test_no_data_is_a_member_of_every_closed_state_set():
    """Honest emptiness: "could not be established" is always publishable."""
    assert hs.NO_DATA in hs.RUNG_STATES
    assert hs.NO_DATA in hs.RECONCILE_OUTCOMES
    assert hs.NO_DATA in hs.WATCHDOG_VERDICTS


def test_the_in_tree_schema_and_the_module_cannot_drift():
    """The shape is DECLARED in-tree and ENFORCED in code; this pins them together."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    defs = schema["$defs"]
    assert defs["serviceName"]["const"] == hs.SERVICE
    assert set(defs["signalKind"]["enum"]) == set(hs.SIGNAL_KINDS)
    assert set(defs["labelKey"]["enum"]) == set(hs.LABEL_KEYS)
    assert set(defs["rung"]["enum"]) == set(hs.RUNG_NAMES)
    assert set(defs["rungState"]["enum"]) == set(hs.RUNG_STATES)
    assert set(defs["beatAgeBucket"]["enum"]) == set(hs.BEAT_AGE_BUCKETS)
    assert set(defs["reconcileOutcome"]["enum"]) == set(hs.RECONCILE_OUTCOMES)
    assert set(defs["watchdogVerdict"]["enum"]) == set(hs.WATCHDOG_VERDICTS)
    assert set(defs["metric"]["properties"]["name"]["enum"]) == {
        hs.metric_name(kind) for kind in hs.SIGNAL_KINDS
    }
    assert set(defs["metric"]["properties"]["unit"]["enum"]) == set(hs.UNITS.values())
