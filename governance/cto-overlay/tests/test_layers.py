"""Layer behaviour: blocking layers block, warning layers only report (#147)."""

from __future__ import annotations

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


def test_clean_fixture_passes(make_repo, assess):
    root = make_repo("clean")
    report = assess(root)
    assert report.exit_code == engine.EXIT_OK
    assert report.not_ok == []
    assert report.cannot_assess == []


def test_blocking_layer_failure_is_not_ok(make_repo, assess):
    root = make_repo("no-adr", drop=["docs/decision-records/ADR-0001-fixture.md"])
    report = assess(root)
    assert state_of(report, "executive", "adr-check") == engine.STATE_FAIL
    assert summary_of(report, "executive").status == "BLOCKING-FAIL"
    assert report.exit_code == engine.EXIT_NOT_OK


def test_warning_layer_failure_is_reported_but_does_not_block(make_repo, assess):
    root = make_repo("no-telemetry", drop=["telemetry/README.md", "telemetry/collect.py"])
    report = assess(root)
    assert state_of(report, "support", "log-harvest") == engine.STATE_FAIL
    assert summary_of(report, "support").status == "WARNING"
    assert report.exit_code == engine.EXIT_OK


def test_advisory_severity_reports_without_blocking(make_repo, assess):
    root = make_repo("advisory", drop=["docs/decision-records/ADR-0001-fixture.md"])
    report = assess(root, tier="experimental")
    assert state_of(report, "executive", "adr-check") == engine.STATE_FAIL
    assert summary_of(report, "executive").status == "WARNING"
    assert report.exit_code == engine.EXIT_OK


def test_tier_override_promotes_a_warning_layer_to_blocking(make_repo, assess):
    root = make_repo("dockerfile", extra_files={"svc/Dockerfile": "RUN echo hi\n"})
    standard = assess(root, tier="standard")
    assert state_of(standard, "devops", "dockerfile-lint") == engine.STATE_FAIL
    assert summary_of(standard, "devops").status == "WARNING"
    assert standard.exit_code == engine.EXIT_OK

    critical = assess(root, tier="critical")
    assert summary_of(critical, "devops").status == "BLOCKING-FAIL"
    assert critical.exit_code == engine.EXIT_NOT_OK


def test_tier_gated_checks_are_visible_skips(make_repo, assess):
    root = make_repo("tiers")
    standard = assess(root, tier="standard")
    assert state_of(standard, "executive", "security-baseline") == engine.STATE_SKIP
    assert state_of(standard, "engineering", "sast") == engine.STATE_SKIP

    critical = assess(root, tier="critical")
    assert state_of(critical, "executive", "security-baseline") != engine.STATE_SKIP


def test_unselected_layers_are_visible_not_silent(make_repo, assess):
    root = make_repo("selected")
    report = assess(root, layers=["executive"])
    assert state_of(report, "engineering", "syntax") == engine.STATE_SKIP
    assert summary_of(report, "engineering").status == "UNSELECTED"
    assert summary_of(report, "executive").status == "OK"


def test_disabled_layer_is_reported_as_disabled(make_repo, assess):
    def mutate(document):
        document["layers"]["support"]["enabled"] = False

    root = make_repo("disabled", mutate=mutate)
    report = assess(root)
    assert summary_of(report, "support").status == "DISABLED"
    assert state_of(report, "support", "log-harvest") == engine.STATE_SKIP
    states = [verdict.state for verdict in report.tally.verdicts]
    assert engine.STATE_FAIL not in states
    assert report.exit_code == engine.EXIT_OK


def test_real_checks_assert_real_things(make_repo, assess):
    """The checks read the tree, so changing the tree changes the verdict."""
    root = make_repo("coverage")
    assert state_of(assess(root), "engineering", "coverage") == engine.STATE_PASS

    manifest = root / "scripts" / "pytest-suites.txt"
    manifest.write_text("sample\nghost-suite\n", encoding="utf-8")
    report = assess(root)
    assert state_of(report, "engineering", "coverage") == engine.STATE_FAIL
    assert report.exit_code == engine.EXIT_NOT_OK

    manifest.write_text("sample\n", encoding="utf-8")
    assert state_of(assess(root), "engineering", "coverage") == engine.STATE_PASS
