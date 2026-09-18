"""The gate's exit vocabulary and its bounded retry (issue #840).

The load-bearing properties are that **a genuine check failure is still a failure**,
that the admission codes are consumed from their owner rather than restated, that a
verdict is read from the gate's own last line (**because make masks the exit code**:
measured, ``make verify`` exits 2 for a park, a gate error and a check failure alike),
that a retry is recorded rather than collapsed, and that no unassessed outcome can be
read as a pass.
"""

from __future__ import annotations

from pathlib import Path

import fleet.gatelock as gatelock

from governance.lifecycle import gate  # noqa: E402

import importlib.util as _importlib_util  # noqa: E402

# A bare ``from conftest import ...`` is not safe here: when this suite is
# collected alongside other governance suites, every one of their
# ``tests/conftest.py`` files lands under the same bare module identity
# ``conftest`` in ``sys.modules``, so whichever conftest is imported LAST
# silently wins the name for the rest of collection (issues #699, #702).
# Loading this file's own conftest by absolute path guarantees this module
# always gets ITS directory's conftest regardless of collection order.
_conftest_spec = _importlib_util.spec_from_file_location(
    "governance_lifecycle_tests_conftest", Path(__file__).with_name("conftest.py")
)
_conftest = _importlib_util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
MAKE_FAILURE_EXIT = _conftest.MAKE_FAILURE_EXIT
PARKED_OUTPUT = _conftest.PARKED_OUTPUT
gate_run = _conftest.gate_run
make_run = _conftest.make_run
parked_output = _conftest.parked_output
transcript = _conftest.transcript


def test_the_admission_vocabulary_is_consumed_from_its_owner():
    """The codes are imported from ``fleet/gatelock.py``; a restatement would drift."""
    assert gate.EXIT_REFUSED == gatelock.EXIT_REFUSED
    assert gate.EXIT_PARKED == gatelock.EXIT_PARKED
    assert gate.EXIT_STORE_UNUSABLE == gatelock.EXIT_STORE_UNUSABLE
    assert gate.verdict_of(gatelock.EXIT_REFUSED) == gate.VERDICT_PARKED
    assert gate.verdict_of(gatelock.EXIT_PARKED) == gate.VERDICT_PARKED


def test_the_mapping_is_closed_and_total():
    assert gate.verdict_of(0) == gate.VERDICT_ADMITTED
    assert gate.verdict_of(1) == gate.VERDICT_FAILED
    assert gate.verdict_of(2) == gate.VERDICT_UNASSESSED
    assert gate.verdict_of(10) == gate.VERDICT_PARKED
    assert gate.verdict_of(11) == gate.VERDICT_PARKED
    assert gate.verdict_of(12) == gate.VERDICT_UNASSESSED
    # A signal (128 + N) and a child killed outright are interruptions, not results.
    assert gate.verdict_of(130) == gate.VERDICT_UNASSESSED
    assert gate.verdict_of(143) == gate.VERDICT_UNASSESSED
    assert gate.verdict_of(-9) == gate.VERDICT_UNASSESSED
    # A code outside the gate's own vocabulary is still not a failure.
    assert gate.verdict_of(3) == gate.VERDICT_UNASSESSED


def test_only_a_check_failure_is_a_failure():
    """The fix for #840 must not swallow a genuine failure: rc 1 stays a failure."""
    assert gate.verdict_of(1) == gate.VERDICT_FAILED
    for code in (2, 10, 11, 12, 3, 130, 143, -9):
        assert gate.verdict_of(code) != gate.VERDICT_FAILED


def test_every_outcome_is_named_and_claims_no_more_than_is_knowable():
    assert "a check failed" in gate.reason_of(gate.VERDICT_FAILED, 1, MAKE_FAILURE_EXIT)
    assert "another gate" in gate.reason_of(gate.VERDICT_PARKED, 10, MAKE_FAILURE_EXIT)
    assert "box-wide" in gate.reason_of(gate.VERDICT_PARKED, 11, MAKE_FAILURE_EXIT)
    assert "refused a permit" in gate.reason_of(gate.VERDICT_PARKED, None, 7)
    assert "could not measure" in gate.reason_of(gate.VERDICT_UNASSESSED, 2, 2)
    assert "permit store" in gate.reason_of(gate.VERDICT_UNASSESSED, 12, 2)
    assert "signal" in gate.reason_of(gate.VERDICT_UNASSESSED, None, 143)
    assert "without reporting" in gate.reason_of(gate.VERDICT_UNASSESSED, None, 3)


def test_make_masks_the_gate_code_so_the_verdict_is_read_from_the_transcript():
    """Measured: `make verify` exits 2 for a park, a gate error AND a check failure.

    The exit code therefore cannot say which happened, and a consumer that reads only
    the code mislabels two of the three. This is why the verdict comes from the gate's
    own last line.
    """
    parked = gate.GateAttempt(exit_code=MAKE_FAILURE_EXIT, output=transcript("parked", code=11))
    failed = gate.GateAttempt(exit_code=MAKE_FAILURE_EXIT, output=transcript("failed"))
    unusable = gate.GateAttempt(
        exit_code=MAKE_FAILURE_EXIT,
        output="verify: CANNOT-ASSESS (rc 12, not a pass and not a failure) — the gate permit "
        "store is unusable; nothing was run\nmake: *** [Makefile:146: verify] Error 12\n",
    )
    assert parked.verdict == gate.VERDICT_PARKED
    assert failed.verdict == gate.VERDICT_FAILED
    assert unusable.verdict == gate.VERDICT_UNASSESSED
    # ...and their process exit codes are identical, which is the whole problem.
    assert parked.exit_code == failed.exit_code == unusable.exit_code == MAKE_FAILURE_EXIT
    # The gate's declared code is preserved beside the wrapper's, so neither is lost.
    assert parked.declared_code == 11
    assert parked.codes() == "the gate declared rc 11, the wrapper exited 2"
    assert failed.declared_code == 1


def test_the_declared_code_is_what_distinguishes_a_park_from_a_store_failure():
    assert gate.verdict_of_run(MAKE_FAILURE_EXIT, transcript("parked", code=10)) == gate.VERDICT_PARKED
    assert gate.verdict_of_run(0, transcript("passed")) == gate.VERDICT_ADMITTED
    # No verdict line at all: fall back to the process code, and name it.
    assert gate.verdict_of_run(MAKE_FAILURE_EXIT, "make: *** [Makefile:146: verify] Interrupt\n") \
        == gate.VERDICT_UNASSESSED
    assert gate.verdict_of_run(143, "") == gate.VERDICT_UNASSESSED


def test_a_check_that_prints_a_verdict_cannot_impersonate_the_gate():
    """Checks are teed into the same stream, so the gate's own banner must win.

    A check provokes a refusal to prove the admission control works (and prints one),
    and a check may echo a verdict of its own; the gate's banner is printed last.
    """
    output = (
        "== 3. gate-lock ==\n"
        "verify: PARKED (rc 11, not a pass and not a failure) — provocation, not this run\n"
        "verify: PASS (1 of 1 checks)\n"
        "== 4. docs ==\n"
        "verify: FAIL (1 of 120 checks failed)\n"
        "make: *** [Makefile:146: verify] Error 1\n"
    )
    attempt = gate.GateAttempt(exit_code=MAKE_FAILURE_EXIT, output=output)
    assert attempt.verdict == gate.VERDICT_FAILED
    assert attempt.declared_code == 1
    assert attempt.headline().startswith("verify: FAIL")


def test_a_pass_that_contradicts_the_process_status_is_not_trusted():
    attempt = gate.GateAttempt(exit_code=MAKE_FAILURE_EXIT, output=transcript("passed"))
    assert attempt.verdict == gate.VERDICT_UNASSESSED


def test_the_cap_is_read_from_the_gates_own_message_or_the_environment():
    assert gate.cap_of(PARKED_OUTPUT) == 4
    assert gate.cap_of("verify: PARKED (rc 11)", {"AO_GATE_MAX_CONCURRENT": "7"}) == 7
    # Unknown is reported as unknown, never guessed.
    assert gate.cap_of("verify: PARKED (rc 11)", {}) is None
    assert gate.cap_of("", {"AO_GATE_MAX_CONCURRENT": "not-a-number"}) is None


def test_a_parked_run_is_neither_a_pass_nor_a_failure():
    run = make_run("parked", code=11)
    assert run.parked and run.cannot_assess
    assert not run.admitted and not run.failed
    assert run.verdict == gate.VERDICT_PARKED


def test_an_admitted_run_is_attempted_once():
    run = make_run("passed", retries=3)
    assert run.admitted
    assert len(run.attempts) == 1
    assert run.waited == 0.0


def test_a_failure_is_never_retried():
    """A failure is an answer: re-running it would ask the same question twice."""
    run = make_run("failed", retries=3)
    assert run.failed
    assert len(run.attempts) == 1
    assert run.waited == 0.0


def test_a_park_is_retried_and_every_attempt_is_recorded():
    scripted = [
        (MAKE_FAILURE_EXIT, transcript("parked", code=11)),
        (MAKE_FAILURE_EXIT, transcript("parked", code=11)),
        (0, transcript("passed")),
    ]
    run = gate.run_gate(
        lambda: gate.GateAttempt(*scripted.pop(0)),
        retries=3,
        wait=5.0,
        sleep=lambda _seconds: None,
    )
    assert run.admitted
    assert [attempt.verdict for attempt in run.attempts] == [
        gate.VERDICT_PARKED,
        gate.VERDICT_PARKED,
        gate.VERDICT_ADMITTED,
    ]
    assert [attempt.attempt for attempt in run.attempts] == [1, 2, 3]
    assert run.waited == 10.0


def test_an_admitted_retry_ends_the_run_and_is_still_recorded():
    served = [make_run("parked").final, make_run("passed").final]
    run = gate.run_gate(lambda: served.pop(0), retries=3, wait=1.0, sleep=lambda _seconds: None)
    assert run.admitted
    assert [attempt.verdict for attempt in run.attempts] == [gate.VERDICT_PARKED, gate.VERDICT_ADMITTED]
    assert run.waited == 1.0


def test_an_exhausted_retry_reports_the_retry_it_made():
    """The demanded negative control: exhaustion is CANNOT-ASSESS, retry visible."""
    run = make_run("parked", code=11, retries=1, wait=2.5)
    assert run.cannot_assess
    assert len(run.attempts) == 2
    assert run.waited == 2.5
    assert "2 attempt(s)" in run.detail()
    assert "AO_GATE_MAX_CONCURRENT" in run.detail()


def test_a_disabled_retry_makes_exactly_one_attempt():
    run = make_run("parked", code=11)
    assert run.cannot_assess
    assert len(run.attempts) == 1
    assert "retry disabled" in run.detail()


def test_the_detail_quotes_the_gates_own_sentence_and_names_the_cap():
    detail = make_run("parked", code=11).detail()
    assert "verify: PARKED (rc 11" in detail
    assert "the gate declared rc 11, the wrapper exited 2" in detail
    assert "AO_GATE_MAX_CONCURRENT=4" in detail


def test_the_remediation_for_a_park_says_retry_when_capacity_is_free():
    """The defect's second half: the printed remedy must be reachable."""
    run = make_run("parked", code=10)
    assert "retry when capacity is free" in run.remediation()
    assert "AO_GATE_MAX_CONCURRENT" in run.remediation()
    assert "another gate" in run.final.reason()


def test_an_interrupted_run_says_retry_because_it_measured_nothing():
    run = gate_run([143], output="make: *** [Makefile:146: verify] Interrupt\n")
    assert run.cannot_assess
    assert "signal" in run.remediation()


def test_the_retry_budget_is_bounded_and_overridable():
    assert gate.retry_budget({}) == (gate.DEFAULT_RETRIES, gate.DEFAULT_RETRY_WAIT)
    assert gate.retry_budget({"AO_LIFECYCLE_GATE_RETRIES": "0"})[0] == 0
    assert gate.retry_budget({"AO_LIFECYCLE_GATE_RETRY_WAIT": "0.5"})[1] == 0.5
    assert gate.retry_budget({"AO_LIFECYCLE_GATE_RETRIES": "-1"})[0] == gate.DEFAULT_RETRIES
    assert gate.retry_budget({"AO_LIFECYCLE_GATE_RETRIES": "many"})[0] == gate.DEFAULT_RETRIES


def test_the_run_gate_budget_comes_from_the_environment(monkeypatch):
    """The port passes no budget of its own, so the environment decides."""
    monkeypatch.setenv("AO_LIFECYCLE_GATE_RETRIES", "2")
    monkeypatch.setenv("AO_LIFECYCLE_GATE_RETRY_WAIT", "0")
    codes = [11, 11, 11]
    run = gate.run_gate(
        lambda: gate.GateAttempt(exit_code=codes.pop(0), output=PARKED_OUTPUT),
        sleep=lambda _seconds: None,
    )
    assert run.cannot_assess
    assert len(run.attempts) == 3
