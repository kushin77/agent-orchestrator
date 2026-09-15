"""`sent` is not `done`: the dispatch-marker state machine and its reconciliation (#796).

The defect these tests pin was measured, not imagined: the brain reported idle
for 1535s with 63 open issues, and three issues its own idle path had found ready
(``#132``/``#133``/``#786``) were suppressed for ever by
``.fleet/brain/dispatched/advance-*.json``. ``dispatch()`` refused on marker
*existence* and the marker only ever recorded that the send happened — so a
directive that completed and one that died were indistinguishable.

Two halves, and both must stay: a marker whose directive died is re-armed
(COUNTED, bounded by #723's harvested budget and backoff, then parked with the
issue named), and a marker with live evidence stays suppressed. The second half
is what makes the first meaningful — a "reconciler" that simply ignored the
marker set would pass a re-arm-only test.

The runtime redirect for the new module's own directories lives here, not in
``conftest.py``: ``conftest.RUNTIME_PATHS["brain"]`` predates the marker
directory, and a test that wrote markers must never touch the live ``.fleet/``
tree — which is the whole reason that fixture exists.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

import brain
import markers


@pytest.fixture(autouse=True)
def _redirect_marker_runtime(tmp_path, monkeypatch):
    """Point every directory the reconciler reads or writes at a tmp tree."""
    monkeypatch.setattr(brain, "DISPATCH_MARKERS", tmp_path / "brain" / "dispatched")
    monkeypatch.setattr(markers, "DISPATCHED", tmp_path / "brain" / "dispatched")
    monkeypatch.setattr(markers, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(markers, "DEAD_LETTER", tmp_path / "dead-letter")
    monkeypatch.setattr(markers, "INBOX", tmp_path / "inbox")
    monkeypatch.setattr(markers, "BRAIN_INBOX", tmp_path / "brain" / "inbox")
    return tmp_path


class Probe:
    """A literal answer to the four questions — no board, no fleet, no network."""

    def __init__(
        self,
        *,
        closed: bool | None = False,
        claim: bool | None = False,
        run: bool | None = False,
        inflight: bool | None = False,
        dead: str | None = None,
    ) -> None:
        self._closed = closed
        self._claim = claim
        self._run = run
        self._inflight = inflight
        self._dead = dead

    def issue_closed(self, issue: int) -> bool | None:
        return self._closed

    def live_claim(self, issue: int) -> bool | None:
        return self._claim

    def live_run(self, issue: int) -> bool | None:
        return self._run

    def in_flight(self, reference: str) -> bool | None:
        return self._inflight

    def dead_lettered(self, reference: str) -> str | None:
        return self._dead


#: The marker the brain wrote last night and never looked at again: `sent`, and
#: old enough that "it is probably just starting" cannot be the excuse.
OLD = 3600.0


def stale_marker(directory: Path, reference: str = "advance-132", issue: int = 132, **fields):
    path = markers.path_for(reference, directory)
    markers.write(
        path,
        markers.Marker(
            reference=reference,
            issue=issue,
            state=fields.pop("state", markers.SENT),
            ts=fields.pop("ts", markers.now_iso(time.time() - OLD)),
            **fields,
        ),
    )
    return path


def channel_stub(tmp_path, rc: int = 0) -> tuple[Path, Path]:
    """A channel stand-in that COUNTS its invocations (the at-most-once evidence)."""
    log = tmp_path / "sends.txt"
    script = tmp_path / "counting-channel.py"
    script.write_text(
        "import pathlib, sys\n"
        f"pathlib.Path({str(log)!r}).open('a', encoding='utf-8').write('send\\n')\n"
        f"sys.exit({rc})\n",
        encoding="utf-8",
    )
    return script, log


def sends(log: Path) -> int:
    return len(log.read_text(encoding="utf-8").split()) if log.exists() else 0


# --- the three states, separable from the marker set alone -------------------


def test_the_marker_states_survive_a_read_and_stay_distinguishable(tmp_path):
    """Criterion 2: `sent` / `in-flight` / `terminal` are separable on disk."""
    for state in markers.STATES:
        path = stale_marker(tmp_path, reference=f"advance-{state}", state=state)
        assert markers.read(path).state == state
    assert markers.read(stale_marker(tmp_path, state=markers.COMPLETED)).terminal
    assert markers.read(stale_marker(tmp_path, state=markers.DEAD)).terminal
    assert not markers.read(stale_marker(tmp_path, state=markers.SENT)).terminal


def test_a_terminal_marker_is_left_alone_and_reported_as_terminal(tmp_path):
    marker = markers.read(stale_marker(tmp_path, state=markers.COMPLETED))
    verdict = markers.judge(marker, Probe())
    assert verdict.verdict == markers.TERMINAL
    assert verdict.marker is None and verdict.finding is False


# --- half one: a dead directive's marker is re-armed, counted, then parked ---


def test_a_sent_marker_with_no_live_evidence_is_stale_and_re_armed(tmp_path):
    """Criterion 1: open issue, no live claim, no live run, no queued directive."""
    marker = markers.read(stale_marker(tmp_path))
    verdict = markers.judge(marker, Probe(), cap=5, base=30)
    assert verdict.verdict == markers.RE_ARMED
    assert verdict.finding is True, "a re-arm must be a printed finding, never a silent continue"
    assert verdict.marker.attempts == 1
    assert verdict.marker.armed and verdict.marker.rearm["by"] == "reconcile"
    assert verdict.marker.next_attempt_at is not None
    assert verdict.marker.evidence["live_run"] is False
    # The found evidence travels with the marker: the operator can see WHY.
    assert json.loads(json.dumps(verdict.marker.payload()))["evidence"]["issue_closed"] is False


def test_the_re_arm_is_bounded_by_the_harvested_budget_and_then_parks_with_the_issue_named(tmp_path):
    """Criterion 1 (bound) + criterion 3 (a park is a finding naming the issue)."""
    path = stale_marker(tmp_path)
    moment = time.time()
    verdicts, granted = [], 0
    for _ in range(12):
        verdict = markers.judge(markers.read(path), Probe(), moment=moment, cap=3, base=30)
        verdicts.append(verdict.verdict)
        if verdict.marker is not None:
            markers.write(path, verdict.marker)
        if verdict.verdict == markers.RE_ARMED:
            granted += 1
            current = markers.read(path)
            # The send consumes the token and stamps `sent_at` (brain.write_marker).
            markers.write(
                path,
                markers.Marker(
                    **{
                        **current.payload(),
                        "state": markers.SENT,
                        "rearm": None,
                        "sent_at": markers.now_iso(moment),
                        "ts": markers.now_iso(moment),
                    }
                ),
            )
        moment += 400  # past the grace AND any backoff of the harvested formula
    assert granted == 3, f"the budget is K re-arms, not an unbounded retry ({verdicts})"
    assert markers.read(path).state == markers.DEAD
    assert verdicts[-1] == markers.TERMINAL
    parked = [
        markers.judge(markers.read(path), Probe(), moment=moment, cap=3)
        for _ in range(2)
    ]
    assert [verdict.verdict for verdict in parked] == [markers.TERMINAL] * 2


def test_the_backoff_holds_a_stale_marker_and_announces_the_hold_once(tmp_path):
    path = stale_marker(tmp_path, attempts=1, next_attempt_at=markers.now_iso(time.time() + 120))
    first = markers.judge(markers.read(path), Probe(), cap=5)
    assert first.verdict == markers.HELD and first.finding is True
    markers.write(path, first.marker)
    second = markers.judge(markers.read(path), Probe(), cap=5)
    assert second.verdict == markers.HELD
    assert second.finding is False, "a hold must not re-print on every idle tick"


def test_a_marker_inside_the_grace_is_presumed_in_flight_not_stale(tmp_path):
    fresh = markers.read(stale_marker(tmp_path, ts=markers.now_iso()))
    verdict = markers.judge(fresh, Probe(), cap=5)
    assert verdict.verdict == markers.RECENT
    assert verdict.marker is None or not verdict.marker.armed


def test_a_retired_directive_parks_the_marker_by_correlation_not_by_issue(tmp_path):
    marker = markers.read(stale_marker(tmp_path))
    parked = markers.judge(marker, Probe(dead="claim refused: stale — snapshot is 36.6m old"))
    assert parked.verdict == markers.PARKED
    assert parked.marker.state == markers.DEAD
    assert "#132" in markers.Finding("advance-132", 132, parked.verdict, parked.detail, parked.marker.state).render()
    assert "markers.py rearm" in parked.detail, "a park must name the verb that lifts it"


def test_an_unreadable_store_is_cannot_assess_and_never_re_arms(tmp_path):
    """An unknown answer fails towards the delivery guarantee, not away from it."""
    marker = markers.read(stale_marker(tmp_path))
    verdict = markers.judge(marker, Probe(closed=None), cap=5)
    assert verdict.verdict == markers.CANNOT_ASSESS
    assert verdict.marker is None or not verdict.marker.armed, "never re-arm on an unknown"


def test_a_marker_with_no_issue_cannot_be_reconciled_against_anything(tmp_path):
    marker = markers.Marker(reference="11f1b132", state=markers.SENT, ts=markers.now_iso(time.time() - OLD))
    verdict = markers.judge(marker, Probe(), cap=5)
    assert verdict.verdict == markers.CANNOT_ASSESS and verdict.finding is True


def test_reconcile_reports_every_marker_it_looked_at_and_only_the_references_it_was_given(tmp_path):
    stale_marker(tmp_path, reference="advance-132", issue=132)
    stale_marker(tmp_path, reference="advance-133", issue=133)
    findings = markers.reconcile(Probe(), directory=tmp_path, references={"advance-132"}, cap=5)
    assert [finding.reference for finding in findings] == ["advance-132"]


# --- half two: live evidence keeps the suppression ---------------------------


@pytest.mark.parametrize(
    "evidence",
    [
        {"claim": True},
        {"run": True},
        {"inflight": True},
    ],
    ids=["live-claim", "live-run", "queued-directive"],
)
def test_live_evidence_marks_the_order_in_flight_and_grants_no_re_arm(tmp_path, evidence):
    """Criterion 4: the reconciler must not be a marker-ignoring re-dispatcher."""
    path = stale_marker(tmp_path)
    verdict = markers.judge(markers.read(path), Probe(**evidence), cap=5)
    assert verdict.verdict == markers.LIVE
    assert verdict.marker.state == markers.IN_FLIGHT
    assert verdict.marker.armed is False, "a live directive must never be re-armed"
    markers.write(path, verdict.marker)
    assert markers.judge(markers.read(path), Probe(**evidence), cap=5).verdict == markers.LIVE


def test_a_live_run_is_a_live_child_or_a_fresh_beat_never_the_loops_own_pid(tmp_path):
    """#366's lesson, pinned: a leftover marker whose loop is alive is not a run."""
    alive_loop = markers.RUNS / "brain-directive-abc.json"
    alive_loop.parent.mkdir(parents=True, exist_ok=True)
    alive_loop.write_text(
        json.dumps(
            {
                "issue": 786,
                "pid": os.getpid(),  # the loop's own pid: alive, and meaningless
                "child_pid": None,
                "ts": markers.now_iso(time.time() - 3600),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    probe = markers.FleetProbe()
    assert probe.live_run(786) is False, "a live loop pid is not a live run"
    alive_loop.write_text(
        json.dumps({"issue": 786, "pid": os.getpid(), "child_pid": os.getpid(), "ts": markers.now_iso()}) + "\n",
        encoding="utf-8",
    )
    assert markers.FleetProbe().live_run(786) is True


def test_the_dead_letter_probe_correlates_on_the_order_reference_only(tmp_path):
    """A fresh order for an issue with a retired directive must not be parked."""
    entry = markers.DEAD_LETTER / "brain-directive-dead.json"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(
        json.dumps({"reason": "run did not land", "envelope": {"correlation_id": "advance-133", "task": {"issue": 132}}}) + "\n",
        encoding="utf-8",
    )
    probe = markers.FleetProbe()
    assert probe.dead_lettered("advance-133") == "run did not land"
    assert probe.dead_lettered("advance-132") is None


def test_rearm_by_name_resets_the_budget_and_grants_exactly_one_send(tmp_path, monkeypatch):
    """A park is reversible by name — it is not a weld."""
    script, log = channel_stub(tmp_path)
    monkeypatch.setattr(brain, "CHANNEL", str(script))
    monkeypatch.setattr(brain, "board_issue_state", lambda number: ("open", f"#{number} is open (stubbed)"))
    order = {"id": "advance-132", "task": {"issue": 132, "lane": "", "title": "t"}, "body": "b"}
    path = stale_marker(brain.DISPATCH_MARKERS, attempts=5, state=markers.DEAD, reason="the re-arm budget is exhausted")
    assert brain.dispatch(order)[0] is False and sends(log) == 0, "a park must suppress"

    updated = markers.rearm("advance-132", directory=brain.DISPATCH_MARKERS, reason="the snapshot was refreshed")

    assert (updated.state, updated.attempts, updated.armed) == (markers.SENT, 0, True)
    # The operator's token — not the reconciler's — is what lifts the park, and it
    # grants exactly one send; the budget starts again rather than instantly
    # re-parking on the next idle tick.
    assert brain.dispatch(order)[0] is True and sends(log) == 1
    marker = markers.read(path)
    assert marker.attempts == 0 and marker.armed is False
    assert brain.dispatch(order)[0] is False and sends(log) == 1


# --- the delivery guarantee, through the real dispatch path ------------------


def test_dispatch_sends_once_and_every_later_attempt_is_suppressed(tmp_path, monkeypatch):
    """Criterion 4, end to end: the counting stub proves the directive went out once."""
    script, log = channel_stub(tmp_path)
    monkeypatch.setattr(brain, "CHANNEL", str(script))
    monkeypatch.setattr(brain, "board_issue_state", lambda number: ("open", f"#{number} is open (stubbed)"))
    order = {"id": "advance-132", "task": {"issue": 132, "lane": "", "title": "t"}, "body": "b"}

    first = brain.dispatch(order)
    second = brain.dispatch(order)

    assert sends(log) == 1, "a restart re-reading the same order dispatched it twice"
    assert first[0] is True
    assert second[0] is False and brain.DUPLICATE_SUPPRESSED in second[1]
    assert "state=sent" in second[1], "the refusal must name the state it read"
    assert markers.read(brain.order_marker(order)).state == markers.SENT


def test_dispatch_proceeds_only_with_the_reconcilers_token_and_consumes_it(tmp_path, monkeypatch):
    """The re-arm is the ONE way past a marker, and it is one-shot."""
    script, log = channel_stub(tmp_path)
    monkeypatch.setattr(brain, "CHANNEL", str(script))
    monkeypatch.setattr(brain, "board_issue_state", lambda number: ("open", f"#{number} is open (stubbed)"))
    order = {"id": "advance-132", "task": {"issue": 132, "lane": "", "title": "t"}, "body": "b"}
    stale_marker(brain.DISPATCH_MARKERS)

    suppressed = brain.dispatch(order)
    assert suppressed[0] is False and sends(log) == 0, "a marker suppressed without a token"

    findings = markers.reconcile(Probe(), directory=brain.DISPATCH_MARKERS, cap=5)
    assert [finding.verdict for finding in findings] == [markers.RE_ARMED]

    re_sent = brain.dispatch(order)
    assert re_sent[0] is True and sends(log) == 1
    marker = markers.read(brain.order_marker(order))
    assert marker.armed is False, "the send must consume the token"
    assert marker.attempts == 1, "the re-arm count must survive the send"

    again = brain.dispatch(order)
    assert again[0] is False and sends(log) == 1, "the token must authorise exactly one send"


def test_a_terminal_marker_refusal_names_the_state_the_issue_and_the_verb(tmp_path, monkeypatch):
    script, log = channel_stub(tmp_path)
    monkeypatch.setattr(brain, "CHANNEL", str(script))
    monkeypatch.setattr(brain, "board_issue_state", lambda number: ("open", f"#{number} is open (stubbed)"))
    order = {"id": "advance-132", "task": {"issue": 132, "lane": "", "title": "t"}, "body": "b"}
    stale_marker(brain.DISPATCH_MARKERS, state=markers.DEAD, reason="the re-arm budget is exhausted")

    ok, message = brain.dispatch(order)

    assert ok is False and sends(log) == 0
    assert message.startswith(brain.PARKED_SUPPRESSED)
    assert "state=dead" in message and "rearm --reference advance-132" in message
    assert brain.suppressed(message), "handle_order must read this as a suppression, not a failure"


def test_a_refused_re_send_keeps_the_re_arm_budget(tmp_path, monkeypatch):
    """A refused re-send must not reset the bound — that is how a bound goes decorative."""
    script, log = channel_stub(tmp_path, rc=1)
    monkeypatch.setattr(brain, "CHANNEL", str(script))
    monkeypatch.setattr(brain, "board_issue_state", lambda number: ("open", f"#{number} is open (stubbed)"))
    order = {"id": "advance-132", "task": {"issue": 132, "lane": "", "title": "t"}, "body": "b"}
    stale_marker(brain.DISPATCH_MARKERS)
    markers.reconcile(Probe(), directory=brain.DISPATCH_MARKERS, cap=5)

    ok, _ = brain.dispatch(order)

    marker = markers.read(brain.order_marker(order))
    assert ok is False and sends(log) == 1
    assert marker is not None, "the marker carrying the budget was unlinked"
    assert marker.attempts == 1 and marker.armed is False


# --- the idle path, end to end ----------------------------------------------


class _Issue:
    def __init__(self, number: int, title: str = "an issue"):
        self.number = number
        self.title = title


class _Board:
    """The two methods `advance_ready` asks its board for."""

    def __init__(self, closed: frozenset[int] = frozenset()):
        self._closed = closed

    def get(self, number: int):
        return type("Record", (), {"state": "closed" if number in self._closed else "open"})()


def _drive_advance(monkeypatch, tmp_path, board, ready: list[_Issue]):
    script, log = channel_stub(tmp_path)
    monkeypatch.setattr(brain, "CHANNEL", str(script))
    monkeypatch.setattr(brain, "board_issue_state", lambda number: ("open", f"#{number} is open (stubbed)"))
    monkeypatch.setattr(brain.snapshot_mod, "github_records", lambda repo: [])
    monkeypatch.setattr(brain.snapshot_mod, "build_snapshot", lambda records, source: board)
    monkeypatch.setattr(brain.order, "advance_candidates", lambda snapshot, claimed: list(ready))
    return log


def test_the_idle_advance_re_arms_a_stale_marker_and_dispatches_its_issue(tmp_path, monkeypatch):
    """Criterion 1, measured through the idle path the defect was measured on."""
    log = _drive_advance(monkeypatch, tmp_path, _Board(), [_Issue(132)])
    stale_marker(brain.DISPATCH_MARKERS, reference="advance-132", issue=132)

    advanced = brain.advance_ready()

    assert advanced == [132], "the ready issue stayed suppressed by a dead directive's marker"
    assert sends(log) == 1
    marker = markers.read(markers.path_for("advance-132", brain.DISPATCH_MARKERS))
    assert marker.attempts == 1 and marker.state == markers.SENT and marker.armed is False


def test_the_idle_advance_still_suppresses_an_issue_with_a_live_run(tmp_path, monkeypatch):
    """The second half, on the same path: a live directive is NOT re-dispatched."""
    log = _drive_advance(monkeypatch, tmp_path, _Board(), [_Issue(786)])
    stale_marker(brain.DISPATCH_MARKERS, reference="advance-786", issue=786)
    run = markers.RUNS / "brain-directive-38136664.json"
    run.parent.mkdir(parents=True, exist_ok=True)
    run.write_text(json.dumps({"issue": 786, "pid": os.getpid(), "child_pid": None, "ts": markers.now_iso()}) + "\n", encoding="utf-8")

    advanced = brain.advance_ready()

    assert advanced == [], "a live run must keep the marker suppressing the issue"
    assert sends(log) == 0
    marker = markers.read(markers.path_for("advance-786", brain.DISPATCH_MARKERS))
    assert marker.state == markers.IN_FLIGHT and marker.armed is False
