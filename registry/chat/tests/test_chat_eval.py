"""The eval harness: declared expectations that fail by name (issue #509).

Every test here is a *negative control* of the harness: an expectation is
mutated and the harness must report the affected case by name, or a case is made
unrunnable and the harness must report ``CANNOT-ASSESS`` rather than a pass.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

from registry.chat import envelope, labels
from registry.chat.eval import harness, standins
from registry.chat.prompt_modules import ChatPromptRegistry

CASE_IDS = (
    "grounded-question",
    "unanswerable-question",
    "cross-tenant-probe",
    "secret-carrying-prompt",
    "poisoned-retrieved-document",
)

UNRUNNABLE_CASE: Dict[str, Any] = {
    "id": "module-that-does-not-exist",
    "kind": "unanswerable",
    "tenant": "acme",
    "question": "Which module answers this?",
    "context": [],
    "expect": {
        "outcome": "NO_DATA",
        "module": "chat-answer",
        "version": "v9",
        "citations": "empty",
    },
}


def _write(tmp_path: Path, cases_path: Path, mutate) -> Path:
    """A mutated copy of the fixture set, written outside the lane tree."""
    data = yaml.safe_load(cases_path.read_text(encoding="utf-8"))
    mutate(data)
    destination = tmp_path / "cases.yaml"
    destination.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return destination


def _case(data: Dict[str, Any], case_id: str) -> Dict[str, Any]:
    for case in data["cases"]:
        if case["id"] == case_id:
            return case
    raise AssertionError(f"fixture has no case {case_id}")


def test_the_fixture_covers_exactly_the_declared_risk_kinds(cases_path: Path) -> None:
    data = harness.load_cases(cases_path)
    kinds = [case["kind"] for case in data["cases"]]
    ids = [case["id"] for case in data["cases"]]
    assert sorted(kinds) == sorted(standins.CASE_KINDS)
    assert len(set(ids)) == len(ids) == len(standins.CASE_KINDS)
    assert set(ids) == set(CASE_IDS)


def test_every_case_meets_its_declared_expectation(cases_path: Path) -> None:
    report = harness.evaluate(cases_path)
    assert report.failed == (), report.render()
    assert report.unassessable == (), report.render()
    assert report.passed == CASE_IDS
    assert report.exit_code == 0
    rendered = report.render()
    for case_id in CASE_IDS:
        assert f"PASS {case_id}" in rendered
    assert report.fixture_digest == harness.evaluate(cases_path).fixture_digest


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_mutating_one_expectation_fails_that_case_by_name(
    tmp_path: Path, cases_path: Path, case_id: str
) -> None:
    def mutate(data: Dict[str, Any]) -> None:
        expect = _case(data, case_id)["expect"]
        others = [option for option in labels.OUTCOMES if option != expect["outcome"]]
        expect["outcome"] = others[0]

    report = harness.evaluate(_write(tmp_path, cases_path, mutate))
    assert report.failed == (case_id,), report.render()
    assert len(report.passed) == len(CASE_IDS) - 1
    assert report.exit_code == 1
    assert f"FAIL {case_id}" in report.render()
    assert f"NOT-OK — 1/{len(CASE_IDS)} case(s) failed" in report.render()


def test_mutating_the_citation_expectation_fails_the_grounded_case_by_name(
    tmp_path: Path, cases_path: Path
) -> None:
    def mutate(data: Dict[str, Any]) -> None:
        _case(data, "grounded-question")["expect"]["citations"] = "empty"

    report = harness.evaluate(_write(tmp_path, cases_path, mutate))
    assert report.failed == ("grounded-question",), report.render()
    result = report.results[0]
    assert "citations" in result.failed_checks
    assert "empty envelope" in " ".join(
        check.detail for check in result.checks if check.name == "citations"
    )


def test_an_unrunnable_case_is_cannot_assess_and_never_a_pass(
    tmp_path: Path, cases_path: Path
) -> None:
    def mutate(data: Dict[str, Any]) -> None:
        data["cases"].append(UNRUNNABLE_CASE)

    report = harness.evaluate(_write(tmp_path, cases_path, mutate))
    assert report.unassessable == ("module-that-does-not-exist",)
    assert "module-that-does-not-exist" not in report.passed
    assert "module-that-does-not-exist" not in report.failed
    assert report.exit_code == 2
    rendered = report.render()
    assert "CANNOT-ASSESS module-that-does-not-exist" in rendered
    assert "could not be run and no case failed" in rendered
    assert len(report.passed) == len(CASE_IDS)


def test_a_cannot_assess_case_does_not_mask_a_failure(
    tmp_path: Path, cases_path: Path
) -> None:
    def mutate(data: Dict[str, Any]) -> None:
        _case(data, "grounded-question")["expect"]["outcome"] = "NO_DATA"
        data["cases"].append(UNRUNNABLE_CASE)

    report = harness.evaluate(_write(tmp_path, cases_path, mutate))
    assert report.failed == ("grounded-question",)
    assert report.unassessable == ("module-that-does-not-exist",)
    assert report.exit_code == 1
    assert "also CANNOT-ASSESS for module-that-does-not-exist" in report.render()


def test_a_malformed_fragment_is_cannot_assess(tmp_path: Path, cases_path: Path) -> None:
    def mutate(data: Dict[str, Any]) -> None:
        _case(data, "grounded-question")["context"][0]["source_id"] = "kb:doc-1"

    report = harness.evaluate(_write(tmp_path, cases_path, mutate))
    assert report.unassessable == ("grounded-question",)
    assert report.exit_code == 2
    assert "malformed source_id" in report.results[0].detail


def test_an_outcome_outside_the_declared_vocabulary_is_cannot_assess(
    tmp_path: Path, cases_path: Path
) -> None:
    def mutate(data: Dict[str, Any]) -> None:
        _case(data, "unanswerable-question")["expect"]["outcome"] = "MAYBE"

    report = harness.evaluate(_write(tmp_path, cases_path, mutate))
    assert report.unassessable == ("unanswerable-question",)
    assert "not in the declared vocabulary" in report.results[1].detail


def test_a_missing_fixture_set_is_cannot_assess(tmp_path: Path) -> None:
    with pytest.raises(harness.EvalFixtureError):
        harness.evaluate(tmp_path / "absent.yaml")
    assert harness.main(["--cases", str(tmp_path / "absent.yaml")]) == 2


def test_a_fabricated_citation_fails_the_case_by_name(
    monkeypatch: pytest.MonkeyPatch, cases_path: Path
) -> None:
    """The stand-ins cannot fabricate, so drive the check directly."""
    data = harness.load_cases(cases_path)
    case = _case(data, "grounded-question")
    monkeypatch.setattr(
        standins.envelope,
        "envelope",
        lambda fragments: [envelope.Citation(fragment_id="frag-1", source_id="bridge:never-supplied")],
    )
    result = harness.run_case(case, ChatPromptRegistry())
    assert result.status == harness.FAIL
    assert "citations" in result.failed_checks
    assert "fabricated" in result.summary()


def test_the_report_is_deterministic(cases_path: Path) -> None:
    first = harness.evaluate(cases_path)
    second = harness.evaluate(cases_path)
    assert first.render() == second.render()
    assert first.projection() == second.projection()
    assert first.projection()["schema"] == "chat-eval-report/v1"


def test_the_fixture_carries_no_credential_shaped_value(cases_path: Path) -> None:
    raw = cases_path.read_text(encoding="utf-8")
    for shape in (r"AKIA[0-9A-Z]{16}", r"(ghp|gho|ghu|ghs)_[A-Za-z0-9]{36,}",
                  r"(sk|sk-ant|sk-proj)-[A-Za-z0-9_\-]{16,}", r"AIza[0-9A-Za-z_\-]{30,}"):
        assert not re.search(shape, raw), shape
    data = harness.load_cases(cases_path)
    canary = _case(data, "secret-carrying-prompt")["canary"]
    assert "NOT-A-REAL" in canary
