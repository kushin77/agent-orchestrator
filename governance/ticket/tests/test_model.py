"""Model + contract-vocabulary tests (issue #401)."""

from __future__ import annotations

import pytest

from conftest import REPO_ROOT
from model import (
    CannotAssess,
    canonical,
    first_difference,
    is_populated,
    issue_id,
    issue_number,
    load_contract,
    short_ref,
)

EXPECTED_AUTHORITY = {
    "owner": "governance/isolation",
    "status": "governance/dispatch",
    "goal": ".board/snapshot.json",
    "blocked_by": ".board/snapshot.json",
    "facets.lessons": "governance/lessons",
    "facets.raid": "derived",
    "facets.budget": "telemetry/budgets",
}


def test_contract_reads_the_frozen_authority_map():
    contract = load_contract(REPO_ROOT)
    assert contract.authority == EXPECTED_AUTHORITY
    assert contract.tracked == frozenset(EXPECTED_AUTHORITY)
    assert contract.untracked == frozenset({"id", "kind", "evidence"})
    assert contract.facet_names == frozenset(
        {"facets.lessons", "facets.raid", "facets.budget"}
    )


def test_contract_reads_the_closed_enums_from_the_schema():
    contract = load_contract(REPO_ROOT)
    assert contract.enums["status"] == ("in-progress", "blocked", "in-review", "done")
    assert contract.enums["kind"] == (
        "task",
        "incident",
        "rca",
        "corrective-action",
        "lesson",
        "suggestion",
    )
    assert contract.enums["facets.raid.risk"] == ("low", "medium", "high", "critical")
    assert contract.enums["evidence.result"] == ("PASS", "FAIL", "CANNOT-ASSESS")


def test_a_missing_contract_is_cannot_assess(tmp_path):
    with pytest.raises(CannotAssess):
        load_contract(tmp_path)


def test_issue_id_helpers():
    assert issue_id(401) == "kushin77/agent-orchestrator#401"
    assert issue_number("kushin77/agent-orchestrator#401") == 401
    assert issue_number("#401") == 401
    assert issue_number("INC-0001") is None
    assert short_ref("kushin77/agent-orchestrator#401") == "#401"
    assert short_ref("CA-0001") == "CA-0001"


@pytest.mark.parametrize(
    "value, populated",
    [
        (None, False),
        ("", False),
        ([], False),
        ({}, False),
        (0, True),
        (0.0, True),
        (1.23, True),
        ("#1", True),
        (["#1"], True),
        ({"risk": "high"}, True),
        (False, True),
    ],
)
def test_is_populated_distinguishes_empty_from_present(value, populated):
    assert is_populated(value) is populated


def test_canonical_is_byte_stable_across_dict_order():
    left = {"a": 1, "b": {"y": 2, "x": 1}, "c": [3, 1]}
    right = {"c": [3, 1], "b": {"x": 1, "y": 2}, "a": 1}
    assert canonical(left) == canonical(right)
    assert canonical(left).endswith("\n")


def test_first_difference_names_stale_and_missing_keys():
    stale = first_difference({"a": 1, "b": 2}, {"a": 1})
    assert "$.b" in stale and "stale" in stale
    missing = first_difference({"a": 1}, {"a": 1, "b": 2})
    assert "$.b" in missing and "missing" in missing
    assert first_difference({"a": 1}, {"a": 1}) == ""
    changed = first_difference({"a": 1}, {"a": 2})
    assert "$.a" in changed
