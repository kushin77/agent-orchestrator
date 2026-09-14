"""The promotion gate: a changed module version is evaluated before promotion.

Issue #509 acceptance: a prompt-module version change that regresses a case
fails the gate **by name**. These tests build candidate versions in a
disposable copy of the package and assert that each layer of the contract —
schema validation, the module contract, and the fixture's own declared
expectation — bites independently.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import pytest
import yaml

from registry.chat import regression
from registry.chat.eval import harness

PERMISSIVE_SCHEMA: Dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "PermissiveAnswer",
    "type": "object",
    "required": ["answer"],
    "properties": {
        "answer": {"type": "string", "minLength": 1},
        "citations": {"type": "array", "items": {"type": "object"}},
    },
}


def _write_candidate(
    root: Path, version: str, policy: str, output_schema: Optional[str] = None
) -> None:
    source = yaml.safe_load(
        (root / "modules" / "chat-answer.v1.yaml").read_text(encoding="utf-8")
    )
    source["version"] = version
    source["groundingPolicy"] = policy
    source["description"] = f"candidate {version} for the promotion gate"
    if output_schema is not None:
        source["outputSchema"] = output_schema
    (root / "modules" / f"chat-answer.{version}.yaml").write_text(
        yaml.safe_dump(source, sort_keys=True), encoding="utf-8"
    )


def _write_permissive_schema(root: Path) -> str:
    (root / "output-schemas" / "grounded-answer.permissive.schema.json").write_text(
        json.dumps(PERMISSIVE_SCHEMA, indent=2), encoding="utf-8"
    )
    return "../output-schemas/grounded-answer.permissive.schema.json"


def _result(result: regression.PromotionResult, case_id: str) -> harness.CaseResult:
    for entry in result.candidate_results:
        if entry.case_id == case_id:
            return entry
    raise AssertionError(f"no candidate result for {case_id}")


def test_a_candidate_that_keeps_the_contract_regresses_nothing(
    scratch_package: Path, cases_path: Path
) -> None:
    _write_candidate(scratch_package, "v2", "require-citations")
    result = regression.evaluate_promotion(
        "chat-answer@v2", "chat-answer@v1", cases_path, scratch_package
    )
    assert result.regressions == (), result.render()
    assert result.status == regression.OK
    assert result.exit_code == 0
    assert "regresses no fixture case" in result.render()


def test_a_candidate_that_would_answer_without_citations_is_refused_by_name(
    scratch_package: Path, cases_path: Path
) -> None:
    _write_candidate(scratch_package, "v2", "answer-without-citations")
    result = regression.evaluate_promotion(
        "chat-answer@v2", "chat-answer@v1", cases_path, scratch_package
    )
    assert result.status == regression.REGRESSED
    assert result.regressions == ("grounded-question",)
    assert result.exit_code == 1
    rendered = result.render()
    assert "REGRESSED grounded-question" in rendered
    assert "NOT-OK — 1 regression(s): grounded-question" in rendered
    failed = _result(result, "grounded-question").failed_checks
    assert {"schema", "citations", "contract"} <= set(failed)
    assert _result(result, "cross-tenant-probe").status == harness.PASS


def test_a_permissive_schema_is_named_by_the_contract_check(
    scratch_package: Path, cases_path: Path
) -> None:
    output_schema = _write_permissive_schema(scratch_package)
    _write_candidate(scratch_package, "v2", "require-citations", output_schema)
    result = regression.evaluate_promotion(
        "chat-answer@v2", "chat-answer@v1", cases_path, scratch_package
    )
    assert result.regressions == ("grounded-question",)
    failed = _result(result, "grounded-question").failed_checks
    assert "contract" in failed
    assert "citations" not in failed
    assert "schema" not in failed


def test_the_fixture_expectation_bites_independently_of_schema_validation(
    scratch_package: Path, cases_path: Path
) -> None:
    """A permissive schema plus an uncited answer still fails the case by name."""
    output_schema = _write_permissive_schema(scratch_package)
    _write_candidate(scratch_package, "v2", "answer-without-citations", output_schema)
    result = regression.evaluate_promotion(
        "chat-answer@v2", "chat-answer@v1", cases_path, scratch_package
    )
    assert result.regressions == ("grounded-question",)
    failed = _result(result, "grounded-question").failed_checks
    assert "citations" in failed
    assert "schema" not in failed


def test_an_unresolvable_candidate_cannot_be_assessed(
    scratch_package: Path, cases_path: Path
) -> None:
    result = regression.evaluate_promotion(
        "chat-answer@v9", "chat-answer@v1", cases_path, scratch_package
    )
    assert result.status == regression.CANNOT_ASSESS
    assert result.exit_code == 2
    assert "grounded-question" in result.unassessable
    assert "CANNOT-ASSESS" in result.render()


def test_a_malformed_version_reference_is_refused(cases_path: Path) -> None:
    with pytest.raises(regression.PromotionError):
        regression.evaluate_promotion("chat-answer", "chat-answer@v1", cases_path)
    assert regression.main(["--candidate", "chat-answer", "--baseline", "chat-answer@v1"]) == 2


def test_comparing_two_different_task_types_is_refused(cases_path: Path) -> None:
    with pytest.raises(regression.PromotionError):
        regression.evaluate_promotion("chat-answer@v2", "chat-refuse@v1", cases_path)


def test_a_case_failing_on_both_versions_is_not_attributed_to_the_candidate(
    tmp_path: Path, scratch_package: Path, cases_path: Path
) -> None:
    _write_candidate(scratch_package, "v2", "require-citations")
    data = yaml.safe_load(cases_path.read_text(encoding="utf-8"))
    for case in data["cases"]:
        if case["id"] == "grounded-question":
            case["expect"]["citations"] = "empty"
    mutated = tmp_path / "cases.yaml"
    mutated.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    result = regression.evaluate_promotion(
        "chat-answer@v2", "chat-answer@v1", mutated, scratch_package
    )
    assert result.regressions == ()
    assert result.pre_existing == ("grounded-question",)
    assert result.status == regression.OK
    rendered = result.render()
    assert "PRE-EXISTING-FAILURE grounded-question" in rendered
    assert "not this candidate's regression" in rendered


def test_the_promotion_cli_names_the_regression_and_exits_nonzero(
    scratch_package: Path, cases_path: Path, capsys: pytest.CaptureFixture
) -> None:
    _write_candidate(scratch_package, "v2", "answer-without-citations")
    code = regression.main(
        [
            "--candidate",
            "chat-answer@v2",
            "--baseline",
            "chat-answer@v1",
            "--cases",
            str(cases_path),
            "--root",
            str(scratch_package),
        ]
    )
    captured = capsys.readouterr()
    assert code == 1
    assert "REGRESSED grounded-question" in captured.out
