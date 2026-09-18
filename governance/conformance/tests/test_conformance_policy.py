"""Policy loading and validation (issue #140)."""

from __future__ import annotations

from pathlib import Path

import pytest

from checker import PolicyUnavailable, load_policy


def test_policy_loads_the_ladder_and_required_sets(policy):
    assert policy.ladder == ("template", "class", "pattern", "enterprise", "faang", "elite")
    assert set(policy.required) == {"class", "type", "priority", "area"}
    assert "infra/" in policy.infra_paths


def test_ladder_ranking_orders_the_rungs(policy):
    assert policy.rank("template") == 0
    assert policy.rank("elite") == len(policy.ladder) - 1
    assert policy.rank("nonsense") == -1
    assert policy.at_least("elite", "enterprise") is True
    assert policy.at_least("template", "enterprise") is False


def test_expectations_are_per_class(policy):
    assert policy.expectations_for("enterprise") == ("gdc",)
    assert policy.expectations_for("elite") == ("gdc", "pillar")
    assert policy.expectations_for("template") == ()


def test_declaring_fields_are_the_policys_whole_vocabulary(policy):
    """The criterion the filing path refuses a `--declare` by (issue #517): class,
    the required companions, every rung's expectations and the `prefixed` set."""
    assert policy.declaring_fields == (
        "class",
        "type",
        "priority",
        "area",
        "gdc",
        "pillar",
        "phase",
        "source",
    )
    assert policy.declares("phase") is True  # `prefixed`, but no rung requires it
    assert policy.declares("priorty") is False  # a typo is not a declaring label


def test_missing_policy_is_cannot_assess(tmp_path: Path):
    with pytest.raises(PolicyUnavailable):
        load_policy(tmp_path / "absent.yaml")


def test_empty_ladder_is_rejected(policy_file: Path):
    policy_file.write_text("ladder: []\n", encoding="utf-8")
    with pytest.raises(PolicyUnavailable) as excinfo:
        load_policy(policy_file)
    assert "empty class ladder" in str(excinfo.value)


def test_expectation_naming_a_non_rung_is_rejected(policy_file: Path):
    """A policy that constrains a rung which does not exist is malformed."""
    policy_file.write_text(
        "ladder: [template, elite]\nexpectations:\n  nonexistent: [gdc]\n",
        encoding="utf-8",
    )
    with pytest.raises(PolicyUnavailable) as excinfo:
        load_policy(policy_file)
    assert "not a rung" in str(excinfo.value)


def test_non_mapping_policy_is_rejected(policy_file: Path):
    policy_file.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(PolicyUnavailable):
        load_policy(policy_file)


def test_malformed_yaml_is_rejected(policy_file: Path):
    policy_file.write_text("ladder: [template\nbroken: : :\n", encoding="utf-8")
    with pytest.raises(PolicyUnavailable):
        load_policy(policy_file)
