"""The #1721 tally: every layer exactly once, and no state that cannot be a pass."""

from __future__ import annotations

import pytest

import importlib.util as _importlib_util  # noqa: E402
from pathlib import Path as _ConftestPath  # noqa: E402

# A bare ``from conftest import ...`` is not safe here: when this suite is
# collected alongside other governance suites, every one of their
# ``tests/conftest.py`` files lands under the same bare module identity
# ``conftest`` in ``sys.modules``, so whichever conftest is imported LAST
# silently wins the name for the rest of collection (issues #699, #702, #1042).
# Loading this file's own conftest by absolute path guarantees this module
# always gets ITS directory's conftest regardless of collection order.
_conftest_spec = _importlib_util.spec_from_file_location(
    "governance_cto_overlay_tests_conftest", _ConftestPath(__file__).with_name("conftest.py")
)
_conftest = _importlib_util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
engine = _conftest.engine
state_of = _conftest.state_of
summary_of = _conftest.summary_of
verdicts_for = _conftest.verdicts_for


def test_every_declared_check_is_recorded_exactly_once(make_repo, assess):
    root = make_repo("tally")
    config = engine.load_config(root)
    report = assess(root)
    for layer_id in engine.LAYERS:
        recorded = [verdict.check for verdict in verdicts_for(report, layer_id)]
        declared = [check.id for check in config.layers[layer_id].checks]
        assert recorded == declared, f"{layer_id} was not counted exactly once"
    assert report.tally.total() == 12 + len(config.non_negotiable)


def test_layer_summaries_cover_every_layer_exactly_once(make_repo, assess):
    root = make_repo("summaries")
    report = assess(root)
    assert [summary.layer for summary in report.tally.layer_summaries()] == list(
        engine.LAYERS
    ) + [engine.SIGNAL_SCOPE]


def test_duplicate_records_are_refused():
    tally = engine.Tally(expected_scopes=("executive",))
    verdict = engine.Verdict("executive", "adr-check", engine.STATE_PASS, "blocking", "x")
    tally.record(verdict)
    with pytest.raises(engine.EngineError, match="twice"):
        tally.record(verdict)


def test_unknown_state_is_refused_rather_than_counted():
    tally = engine.Tally(expected_scopes=("executive",))
    with pytest.raises(engine.EngineError, match="unknown state"):
        tally.record(engine.Verdict("executive", "adr-check", "MAYBE", "blocking", "x"))


def test_a_check_returning_an_unknown_state_cannot_assess(make_repo, monkeypatch):
    root = make_repo("unknown-state")
    monkeypatch.setitem(engine.CHECKS, "adr-check", lambda ctx: ("MAYBE", "planted"))
    assert engine.main(["run", "--root", str(root)]) == engine.EXIT_CANNOT_ASSESS


def test_blocking_indeterminate_is_cannot_assess(make_repo, monkeypatch, assess):
    root = make_repo("indet")

    def cannot_run(ctx):
        raise engine.Indeterminate("the tool is absent in this image")

    monkeypatch.setitem(engine.CHECKS, "adr-check", cannot_run)
    report = assess(root)
    assert state_of(report, "executive", "adr-check") == engine.STATE_INDET
    assert report.exit_code == engine.EXIT_CANNOT_ASSESS
    assert report.not_ok == []
    assert report.cannot_assess


def test_warning_indeterminate_reports_without_blocking(make_repo, monkeypatch, assess):
    root = make_repo("indet-warning")

    def cannot_run(ctx):
        raise engine.Indeterminate("terraform is absent in this image")

    monkeypatch.setitem(engine.CHECKS, "compose-validate", cannot_run)
    report = assess(root)
    assert state_of(report, "devops", "compose-validate") == engine.STATE_INDET
    assert summary_of(report, "devops").status == "WARNING"
    assert report.exit_code == engine.EXIT_OK


def test_a_definite_failure_outranks_an_incomplete_assessment(
    make_repo, monkeypatch, assess
):
    root = make_repo("mixed")

    def fails(ctx):
        return (engine.STATE_FAIL, "planted failure")

    def cannot_run(ctx):
        raise engine.Indeterminate("planted indeterminacy")

    monkeypatch.setitem(engine.CHECKS, "adr-check", fails)
    monkeypatch.setitem(engine.CHECKS, "syntax", cannot_run)
    report = assess(root)
    assert report.exit_code == engine.EXIT_NOT_OK
    assert report.not_ok and report.cannot_assess


def test_element_that_raises_is_never_a_pass(make_repo, monkeypatch, assess):
    root = make_repo("crash")

    def crashes(ctx):
        raise RuntimeError("planted crash")

    monkeypatch.setitem(engine.CHECKS, "adr-check", crashes)
    report = assess(root)
    assert state_of(report, "executive", "adr-check") == engine.STATE_INDET
    assert report.exit_code == engine.EXIT_CANNOT_ASSESS


def test_skipped_checks_are_visible_not_silent(make_repo, assess):
    root = make_repo("skips")
    report = assess(root, tier="standard")
    counts = report.tally.counts()
    assert counts[engine.STATE_SKIP] == 3
    assert engine.STATE_SKIP in [verdict.state for verdict in report.tally.verdicts]
