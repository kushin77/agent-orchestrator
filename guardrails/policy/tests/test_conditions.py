"""Condition evaluator tests: operators, trees, paths, and fail-closed errors."""

from __future__ import annotations

import pytest

from policy import ConditionError
from policy.conditions import evaluate_condition, validate_condition

CTX = {
    "tenant": {"id": "acme", "plan": "free", "grants": {"shell": True}},
    "budget": {"utilization_ratio": 0.9},
    "tool": {"name": "shell"},
    "request": {"tags": ["a", "b"], "tokens": 1200},
    "egress": {"classification": "public", "destination": "https://x.example"},
}


def leaf(**kwargs) -> dict:
    return {"path": kwargs["path"], "op": kwargs["op"], "value": kwargs["value"]}


# ---------------------------------------------------------------------------
# leaf operators (positive)
# ---------------------------------------------------------------------------


def test_equality_and_inequality_operators():
    assert evaluate_condition({"path": "tool.name", "op": "eq", "value": "shell"}, CTX)
    assert not evaluate_condition({"path": "tool.name", "op": "eq", "value": "search"}, CTX)
    assert evaluate_condition({"path": "tool.name", "op": "ne", "value": "search"}, CTX)


def test_bool_never_equals_numeric():
    # Python True == 1; the DSL must treat them as distinct.
    assert not evaluate_condition({"path": "tenant.grants.shell", "op": "eq", "value": 1}, CTX)
    assert evaluate_condition({"path": "tenant.grants.shell", "op": "eq", "value": True}, CTX)
    assert evaluate_condition({"path": "tenant.grants.shell", "op": "ne", "value": False}, CTX)


def test_numeric_comparison_operators():
    assert evaluate_condition({"path": "budget.utilization_ratio", "op": "gte", "value": 0.9}, CTX)
    assert evaluate_condition({"path": "budget.utilization_ratio", "op": "gt", "value": 0.5}, CTX)
    assert not evaluate_condition({"path": "budget.utilization_ratio", "op": "gt", "value": 0.9}, CTX)
    assert evaluate_condition({"path": "budget.utilization_ratio", "op": "lt", "value": 1.0}, CTX)
    assert evaluate_condition({"path": "budget.utilization_ratio", "op": "lte", "value": 0.9}, CTX)


def test_in_and_not_in_operators():
    assert evaluate_condition({"path": "tool.name", "op": "in", "value": ["shell", "bash"]}, CTX)
    assert not evaluate_condition({"path": "tool.name", "op": "in", "value": ["search"]}, CTX)
    assert evaluate_condition({"path": "tool.name", "op": "not_in", "value": ["search"]}, CTX)


def test_glob_and_regex_operators():
    assert evaluate_condition(
        {"path": "egress.destination", "op": "glob", "value": "https://*"}, CTX
    )
    assert not evaluate_condition(
        {"path": "egress.destination", "op": "glob", "value": "http://*"}, CTX
    )
    assert evaluate_condition(
        {"path": "tool.name", "op": "regex", "value": "^s"}, CTX
    )
    assert not evaluate_condition(
        {"path": "tool.name", "op": "regex", "value": "^e"}, CTX
    )


def test_exists_and_missing_operators():
    assert evaluate_condition({"path": "tenant.grants.shell", "op": "exists"}, CTX)
    assert not evaluate_condition({"path": "tenant.grants.shell", "op": "missing"}, CTX)
    assert evaluate_condition({"path": "tenant.missing_attr", "op": "missing"}, CTX)
    assert not evaluate_condition({"path": "tenant.missing_attr", "op": "exists"}, CTX)


def test_dotted_and_indexed_paths():
    assert evaluate_condition({"path": "request.tags[0]", "op": "eq", "value": "a"}, CTX)
    assert evaluate_condition({"path": "request.tags[1]", "op": "eq", "value": "b"}, CTX)
    # an out-of-range index makes the path absent -> fail closed (ConditionError)
    with pytest.raises(ConditionError):
        evaluate_condition({"path": "request.tags[5]", "op": "eq", "value": "a"}, CTX)


# ---------------------------------------------------------------------------
# condition trees
# ---------------------------------------------------------------------------


def test_all_requires_every_child():
    cond = {
        "all": [
            {"path": "tool.name", "op": "eq", "value": "shell"},
            {"path": "tenant.grants.shell", "op": "eq", "value": True},
        ]
    }
    assert evaluate_condition(cond, CTX)
    bad = {
        "all": [
            {"path": "tool.name", "op": "eq", "value": "shell"},
            {"path": "tenant.grants.shell", "op": "eq", "value": False},
        ]
    }
    assert not evaluate_condition(bad, CTX)


def test_any_requires_one_child():
    cond = {
        "any": [
            {"path": "tool.name", "op": "eq", "value": "search"},
            {"path": "tool.name", "op": "eq", "value": "shell"},
        ]
    }
    assert evaluate_condition(cond, CTX)
    miss = {
        "any": [
            {"path": "tool.name", "op": "eq", "value": "search"},
            {"path": "tool.name", "op": "eq", "value": "exfil.copy"},
        ]
    }
    assert not evaluate_condition(miss, CTX)


def test_not_inverts_a_child():
    cond = {"not": {"path": "tool.name", "op": "eq", "value": "search"}}
    assert evaluate_condition(cond, CTX)
    assert not evaluate_condition(
        {"not": {"path": "tool.name", "op": "eq", "value": "shell"}}, CTX
    )


def test_nested_tree():
    cond = {
        "all": [
            {"path": "tenant.plan", "op": "eq", "value": "free"},
            {
                "any": [
                    {"path": "tool.name", "op": "eq", "value": "shell"},
                    {"path": "tool.name", "op": "eq", "value": "bash"},
                ]
            },
        ]
    }
    assert evaluate_condition(cond, CTX)


def test_all_short_circuits_on_first_false_child():
    # The second leaf would raise on a missing path, but the first leaf is
    # already False so evaluation stops without consulting it.
    cond = {
        "all": [
            {"path": "tool.name", "op": "eq", "value": "search"},
            {"path": "tenant.absent_attr", "op": "gt", "value": 1},
        ]
    }
    assert not evaluate_condition(cond, CTX)


# ---------------------------------------------------------------------------
# fail-closed errors
# ---------------------------------------------------------------------------


def test_unknown_operator_raises():
    with pytest.raises(ConditionError):
        evaluate_condition({"path": "tool.name", "op": "explodes", "value": 1}, CTX)


def test_missing_required_path_on_comparison_raises():
    with pytest.raises(ConditionError):
        evaluate_condition({"path": "tenant.absent", "op": "gt", "value": 1}, CTX)


def test_missing_required_path_on_eq_raises():
    with pytest.raises(ConditionError):
        evaluate_condition({"path": "tenant.absent", "op": "eq", "value": 1}, CTX)


def test_non_numeric_comparison_raises():
    with pytest.raises(ConditionError):
        evaluate_condition({"path": "tool.name", "op": "gt", "value": 1}, CTX)


def test_in_with_non_list_value_raises():
    with pytest.raises(ConditionError):
        evaluate_condition({"path": "tool.name", "op": "in", "value": "shell"}, CTX)


def test_invalid_regex_raises_at_evaluation():
    with pytest.raises(ConditionError):
        evaluate_condition({"path": "tool.name", "op": "regex", "value": "(["}, CTX)


def test_condition_must_be_a_mapping():
    with pytest.raises(ConditionError):
        evaluate_condition("not-a-mapping", CTX)


# ---------------------------------------------------------------------------
# startup structural validation (no runtime context needed)
# ---------------------------------------------------------------------------


def test_validate_condition_flags_structural_problems():
    assert validate_condition({"path": "x", "op": "bogus", "value": 1})
    assert validate_condition({"path": "x", "op": "gt"})  # missing value
    assert validate_condition({"path": "x", "op": "in", "value": "not-a-list"})
    assert validate_condition({"path": "x", "op": "regex", "value": "(["})
    assert validate_condition({"path": "x", "op": "eq", "value": 1, "all": []})  # mixed
    assert validate_condition({"path": "x", "op": "exists", "value": True})  # value on exists
    assert validate_condition({"surprise": True})  # unknown key
    assert validate_condition({"all": []})  # empty all


def test_validate_condition_accepts_wellformed_trees():
    cond = {
        "all": [
            {"path": "tool.name", "op": "eq", "value": "shell"},
            {
                "any": [
                    {"path": "a", "op": "exists"},
                    {"path": "b", "op": "regex", "value": "^ok"},
                ]
            },
            {"not": {"path": "c", "op": "missing"}},
        ]
    }
    assert validate_condition(cond) == []
