"""Alert severity state machine tests (issue #342).

The property under test is that this is a *state machine*, not an alarm: a
breach escalates a subject to ``ALERT`` **and** a recovery brings it back down,
with both directions recorded. Plus the two honesty states the machinery
exists for: ``NO_DATA`` (nothing measured) and ``PAUSED`` (an operator stop)
are reachable, are never ``OK``, and never hide what they sit on top of.
"""

from __future__ import annotations

import pytest

from telemetry.observability.model import (
    KIND_GUARD,
    KIND_TRACE,
    OUTCOME_FAILED,
    OUTCOME_SUCCESS,
)
from telemetry.observability.severity import (
    REASON_AT_RISK,
    REASON_BREACHED,
    REASON_INITIAL,
    REASON_NO_DATA,
    REASON_NO_SLOS,
    REASON_PAUSED,
    REASON_RECOVERED,
    REASON_RESUMED,
    SEVERITY_CRITICAL,
    SEVERITY_OK,
    SEVERITY_PAUSED,
    STATE_ALERT,
    STATE_NO_DATA,
    STATE_OK,
    STATE_PAUSED,
    STATE_WARNING,
    STATES,
    AlertStateMachine,
    SeverityState,
    classify,
    rank,
    severity_for,
)
from telemetry.observability.slos import (
    KIND_AVAILABILITY,
    VERDICT_AT_RISK,
    VERDICT_BREACHED,
    VERDICT_NO_DATA,
    VERDICT_OK,
    SloDefinition,
    SloEvaluator,
    SloResult,
)
from telemetry.observability.store import TraceStore
from telemetry.observability.tests.conftest import iso_at

TENANT = "acme"
AGENT = "coder-1"


# --------------------------------------------------------------------------- #
# Fixtures / helpers (real evaluations wherever the verdict must be earned)
# --------------------------------------------------------------------------- #
def availability(tenant: str = TENANT, target: float = 0.95) -> SloDefinition:
    return SloDefinition(
        name=f"availability-requests:{tenant}",
        tenant_id=tenant,
        kind=KIND_AVAILABILITY,
        target_ratio=target,
        window_seconds=3600,
    )


def store_with(
    span_factory,
    *,
    tenant: str = TENANT,
    agent: str = AGENT,
    failures: int = 0,
    total: int = 10,
    at: float = 0.0,
) -> TraceStore:
    """A store whose serve attempts are ``failures``/``total`` unsuccessful."""
    spans = [
        span_factory(
            trace_id="tr", span_id="root", kind=KIND_TRACE, parent_span_id=None,
            tenant_id=tenant, agent_id=agent, ts=iso_at(at), name="root",
        )
    ]
    for i in range(total):
        spans.append(
            span_factory(
                trace_id="tr",
                span_id=f"s{i}",
                parent_span_id="root",
                tenant_id=tenant,
                agent_id=agent,
                ts=iso_at(at + 1 + i),
                outcome=OUTCOME_FAILED if i < failures else OUTCOME_SUCCESS,
            )
        )
    return TraceStore(spans=spans)


def evaluated(store: TraceStore, definition: SloDefinition) -> SloResult:
    return SloEvaluator(store).evaluate(definition)


def result(
    verdict: str,
    *,
    tenant: str = TENANT,
    name: str | None = None,
    kind: str = KIND_AVAILABILITY,
    missed: bool = False,
) -> SloResult:
    """A directly-constructed SloResult (for the classification tables)."""
    definition = SloDefinition(
        name=name or f"availability-requests:{tenant}",
        tenant_id=tenant,
        kind=kind,
        target_ratio=0.95,
    )
    return SloResult(
        definition,
        "2026-09-13T00:00:00Z",
        "2026-09-13T01:00:00Z",
        verdict,
        has_window_data=verdict != VERDICT_NO_DATA,
        missed_window=missed,
    )


# --------------------------------------------------------------------------- #
# Classification (pure, total, worst-wins)
# --------------------------------------------------------------------------- #
class TestClassify:
    def test_healthy_results_are_ok(self):
        state, groups = classify([result(VERDICT_OK)])
        assert state == STATE_OK
        assert groups == {"breached": (), "noData": (), "atRisk": ()}

    def test_one_breach_out_of_many_is_alert(self):
        state, groups = classify(
            [result(VERDICT_OK), result(VERDICT_BREACHED), result(VERDICT_OK)]
        )
        assert state == STATE_ALERT
        assert groups["breached"] == ("availability-requests:acme",)

    def test_at_risk_is_warning(self):
        state, _ = classify([result(VERDICT_AT_RISK)])
        assert state == STATE_WARNING

    def test_no_results_is_no_data_never_ok(self):
        state, _ = classify([])
        assert state == STATE_NO_DATA

    def test_no_data_outranks_warning(self):
        state, _ = classify([result(VERDICT_AT_RISK), result(VERDICT_NO_DATA)])
        assert state == STATE_NO_DATA

    def test_breach_outranks_no_data_but_keeps_the_gap_visible(self):
        state, groups = classify(
            [
                result(VERDICT_BREACHED, name="availability-requests:acme"),
                result(VERDICT_NO_DATA, name="latency-p95:acme"),
            ]
        )
        assert state == STATE_ALERT
        # the silent SLO is not swallowed by the louder one
        assert groups["noData"] == ("latency-p95:acme",)

    def test_rank_and_severity_tables_cover_every_state(self):
        assert rank(STATE_OK) < rank(STATE_WARNING) < rank(STATE_NO_DATA)
        assert rank(STATE_NO_DATA) < rank(STATE_ALERT)
        assert rank(STATE_PAUSED) == -1
        for state in STATES:
            assert severity_for(state)
        assert severity_for(STATE_NO_DATA) == SEVERITY_CRITICAL
        with pytest.raises(ValueError):
            severity_for("MELTDOWN")


# --------------------------------------------------------------------------- #
# Escalation
# --------------------------------------------------------------------------- #
class TestEscalation:
    def test_breach_escalates_to_alert(self, span_factory):
        breach = evaluated(store_with(span_factory, failures=6), availability())
        assert breach.verdict == VERDICT_BREACHED

        machine = AlertStateMachine()
        states = [machine.evaluate(TENANT, [breach]) for _ in range(3)]
        assert [state.state for state in states] == [STATE_ALERT] * 3

    def test_first_observation_records_initial_then_escalates(self, span_factory):
        healthy = evaluated(store_with(span_factory, failures=0), availability())
        breach = evaluated(store_with(span_factory, failures=6), availability())

        machine = AlertStateMachine()
        first = machine.evaluate(TENANT, [healthy])
        assert first.state == STATE_OK
        assert [move.reason for move in machine.history(TENANT)] == [REASON_INITIAL]

        second = machine.evaluate(TENANT, [breach])
        assert second.state == STATE_ALERT
        assert second.transitions == 2
        history = machine.history(TENANT)
        assert [(move.from_state, move.to_state) for move in history] == [
            (None, STATE_OK),
            (STATE_OK, STATE_ALERT),
        ]
        assert history[-1].reason == REASON_BREACHED
        assert history[-1].drivers == ("availability-requests:acme",)

    def test_at_risk_escalates_to_warning_then_breach_to_alert(self):
        machine = AlertStateMachine()
        assert machine.evaluate(TENANT, [result(VERDICT_AT_RISK)]).state == STATE_WARNING
        assert machine.evaluate(TENANT, [result(VERDICT_BREACHED)]).state == STATE_ALERT
        assert [move.reason for move in machine.history(TENANT)] == [
            REASON_INITIAL,
            REASON_BREACHED,
        ]

    def test_recovery_confirmations_never_delay_escalation(self):
        machine = AlertStateMachine(recovery_confirmations=3)
        assert machine.evaluate(TENANT, [result(VERDICT_OK)]).state == STATE_OK
        state = machine.evaluate(TENANT, [result(VERDICT_BREACHED)])
        assert state.state == STATE_ALERT
        assert state.pending_recovery == 0


# --------------------------------------------------------------------------- #
# Recovery — the direction that makes it a state machine
# --------------------------------------------------------------------------- #
class TestRecovery:
    def test_recovery_de_escalates_alert_to_ok(self):
        machine = AlertStateMachine()
        assert machine.evaluate(TENANT, [result(VERDICT_BREACHED)]).state == STATE_ALERT

        recovered = machine.evaluate(TENANT, [result(VERDICT_OK)])
        assert recovered.state == STATE_OK
        assert recovered.previous_state == STATE_ALERT
        assert recovered.severity == SEVERITY_OK
        assert recovered.is_ok

        history = machine.history(TENANT)
        assert (history[-1].from_state, history[-1].to_state) == (
            STATE_ALERT,
            STATE_OK,
        )
        assert history[-1].reason == REASON_RECOVERED

    def test_recovery_passes_through_warning_when_still_at_risk(self):
        machine = AlertStateMachine()
        machine.evaluate(TENANT, [result(VERDICT_BREACHED)])
        state = machine.evaluate(TENANT, [result(VERDICT_AT_RISK)])
        assert state.state == STATE_WARNING
        assert [move.to_state for move in machine.history(TENANT)] == [
            STATE_ALERT,
            STATE_WARNING,
        ]

    def test_de_escalation_waits_for_confirmation_but_shows_the_calm(self):
        machine = AlertStateMachine(recovery_confirmations=3)
        machine.evaluate(TENANT, [result(VERDICT_BREACHED)])

        first = machine.evaluate(TENANT, [result(VERDICT_OK)])
        assert first.state == STATE_ALERT          # held on purpose
        assert first.observed_state == STATE_OK    # ... and the calm is visible
        assert first.pending_recovery == 1
        assert first.observed_differs

        second = machine.evaluate(TENANT, [result(VERDICT_OK)])
        assert second.state == STATE_ALERT
        assert second.pending_recovery == 2

        third = machine.evaluate(TENANT, [result(VERDICT_OK)])
        assert third.state == STATE_OK
        assert third.pending_recovery == 0
        assert [move.to_state for move in machine.history(TENANT)] == [
            STATE_ALERT,
            STATE_OK,
        ]

    def test_a_flap_back_resets_the_recovery_countdown(self):
        machine = AlertStateMachine(recovery_confirmations=2)
        machine.evaluate(TENANT, [result(VERDICT_BREACHED)])
        assert machine.evaluate(TENANT, [result(VERDICT_OK)]).pending_recovery == 1
        breached_again = machine.evaluate(TENANT, [result(VERDICT_BREACHED)])
        assert breached_again.state == STATE_ALERT
        assert breached_again.pending_recovery == 0
        # one calm read is not enough again — the countdown restarted
        assert machine.evaluate(TENANT, [result(VERDICT_OK)]).state == STATE_ALERT

    def test_ok_to_no_data_is_an_escalation_not_a_downgrade(self):
        machine = AlertStateMachine()
        machine.evaluate(TENANT, [result(VERDICT_OK)])
        state = machine.evaluate(TENANT, [result(VERDICT_NO_DATA, missed=True)])
        assert state.state == STATE_NO_DATA
        assert not state.is_ok
        assert machine.history(TENANT)[-1].reason == REASON_NO_DATA


# --------------------------------------------------------------------------- #
# NO_DATA is never OK
# --------------------------------------------------------------------------- #
class TestNoData:
    def test_a_subject_with_no_slos_is_no_data(self):
        machine = AlertStateMachine()
        state = machine.evaluate(TENANT, [])
        assert state.state == STATE_NO_DATA
        assert not state.is_ok
        assert machine.history(TENANT)[-1].reason == REASON_NO_SLOS

    def test_no_window_data_stays_no_data_across_reads(self):
        machine = AlertStateMachine()
        first = machine.evaluate(TENANT, [result(VERDICT_NO_DATA, missed=True)])
        assert first.state == STATE_NO_DATA
        history = machine.history(TENANT)
        for _ in range(3):
            state = machine.evaluate(TENANT, [result(VERDICT_NO_DATA, missed=True)])
            assert state.state == STATE_NO_DATA
            assert state.severity == SEVERITY_CRITICAL
        # a steady state records one transition, not one per read
        assert machine.history(TENANT) == history

    def test_guardrail_only_spans_do_not_make_a_subject_ok(self, span_factory):
        """A store with no serve attempts evaluates NO_DATA, never OK."""
        store = TraceStore(
            spans=[
                span_factory(
                    trace_id="tr", span_id="g0", tenant_id=TENANT, agent_id=AGENT,
                    kind=KIND_GUARD, ts=iso_at(0), name="policy.gate",
                )
            ]
        )
        assert evaluated(store, availability()).verdict == VERDICT_NO_DATA
        state = AlertStateMachine().evaluate(
            TENANT, [evaluated(store, availability())]
        )
        assert state.state == STATE_NO_DATA


# --------------------------------------------------------------------------- #
# PAUSED — reachable, honest, and it never hides the state underneath
# --------------------------------------------------------------------------- #
class TestPaused:
    def test_pause_of_a_breaching_subject_keeps_the_breach_visible(self):
        machine = AlertStateMachine()
        machine.evaluate(TENANT, [result(VERDICT_BREACHED)])
        paused = machine.pause(TENANT, reason="maintenance window", actor="ops@x")

        assert paused.state == STATE_PAUSED
        assert paused.is_paused
        assert not paused.is_ok
        assert paused.severity == SEVERITY_PAUSED
        assert paused.previous_state == STATE_ALERT
        assert paused.paused == {
            "reason": "maintenance window",
            "actor": "ops@x",
            "at": paused.since,
        }
        assert machine.history(TENANT)[-1].reason == REASON_PAUSED

        # an evaluation while paused does not relabel, but does keep measuring
        still = machine.evaluate(TENANT, [result(VERDICT_BREACHED)])
        assert still.state == STATE_PAUSED
        assert still.observed_state == STATE_ALERT

    def test_pause_of_an_unobserved_subject_is_no_data_not_ok(self):
        machine = AlertStateMachine()
        paused = machine.pause(TENANT, reason="not yet onboarded")
        assert paused.state == STATE_PAUSED
        assert paused.observed_state == STATE_NO_DATA
        assert paused.observed_severity == SEVERITY_CRITICAL
        assert not paused.is_ok

    def test_a_breach_during_the_pause_surfaces_on_resume(self):
        machine = AlertStateMachine()
        machine.evaluate(TENANT, [result(VERDICT_OK)])
        machine.pause(TENANT, reason="deploy window", actor="ops@x")
        machine.evaluate(TENANT, [result(VERDICT_BREACHED)])  # happens *while* paused
        resumed = machine.resume(TENANT)
        assert resumed.state == STATE_ALERT
        assert resumed.paused is None
        assert machine.history(TENANT)[-1].reason == REASON_RESUMED

    def test_resume_returns_to_the_true_state_when_nothing_changed(self):
        machine = AlertStateMachine()
        machine.evaluate(TENANT, [result(VERDICT_OK)])
        machine.pause(TENANT, reason="deploy window")
        assert machine.resume(TENANT).state == STATE_OK

    def test_pausing_twice_is_idempotent_and_resuming_a_live_subject_is_a_noop(self):
        machine = AlertStateMachine()
        machine.evaluate(TENANT, [result(VERDICT_OK)])
        first = machine.pause(TENANT, reason="a")
        assert machine.pause(TENANT, reason="b") == first
        assert machine.resume(TENANT).state == STATE_OK
        assert machine.resume(TENANT).state == STATE_OK

    def test_resume_of_an_unknown_subject_is_an_error(self):
        with pytest.raises(KeyError):
            AlertStateMachine().resume("nobody")


# --------------------------------------------------------------------------- #
# Subjects, snapshots, validation
# --------------------------------------------------------------------------- #
class TestMachinePlumbing:
    def test_subjects_do_not_interfere(self):
        machine = AlertStateMachine()
        machine.evaluate("acme/coder-1", [result(VERDICT_BREACHED)])
        machine.evaluate("acme/reviewer-1", [result(VERDICT_OK)])
        assert machine.state("acme/coder-1").state == STATE_ALERT
        assert machine.state("acme/reviewer-1").state == STATE_OK
        assert machine.subjects() == ["acme/coder-1", "acme/reviewer-1"]

    def test_snapshot_is_serializable_and_carries_the_evidence(self):
        machine = AlertStateMachine(clock=lambda: "2026-09-13T12:00:00Z")
        machine.evaluate(TENANT, [result(VERDICT_BREACHED)])
        snapshot = machine.snapshot()
        row = snapshot[TENANT]
        assert row["state"] == STATE_ALERT
        assert row["severity"] == SEVERITY_CRITICAL
        assert row["breached"] == ["availability-requests:acme"]
        assert row["since"] == "2026-09-13T12:00:00Z"
        assert row["transitions"] == 1

    def test_recovery_confirmations_must_be_positive(self):
        with pytest.raises(ValueError):
            AlertStateMachine(recovery_confirmations=0)

    def test_state_objects_validate_their_vocabulary(self):
        with pytest.raises(ValueError):
            SeverityState(
                subject=TENANT, state="MELTDOWN", severity="ok",
                since="2026-09-13T00:00:00Z", observed_state=STATE_OK,
                observed_severity=SEVERITY_OK,
            )
        with pytest.raises(ValueError):
            SeverityState(
                subject="", state=STATE_OK, severity=SEVERITY_OK,
                since="2026-09-13T00:00:00Z", observed_state=STATE_OK,
                observed_severity=SEVERITY_OK,
            )

    def test_unknown_subject_state_is_none(self):
        assert AlertStateMachine().state("nobody") is None
        assert AlertStateMachine().history("nobody") == []
