"""governance/reconcile's own declared controls (issue #885)."""

from __future__ import annotations

from pathlib import Path

import pytest

from governance.reconcile import policy


def test_default_controls_load():
    controls = policy.load()
    assert controls.max_actions_per_pass >= 0
    assert set(controls.outcome_codes) == policy.OUTCOME_NAMES


def test_code_for_known_outcome():
    controls = policy.load()
    assert controls.code_for("refused") == "reconcile.batch-limit-exceeded"


def test_code_for_unknown_outcome_is_refused():
    controls = policy.load()
    with pytest.raises(policy.ControlsUnavailable):
        controls.code_for("not-a-real-outcome")


def test_missing_schema_tag_is_refused(tmp_path: Path):
    bad = tmp_path / "controls.yaml"
    bad.write_text("schema: not-the-right-schema\n", encoding="utf-8")
    with pytest.raises(policy.ControlsUnavailable):
        policy.load(bad)


def test_negative_limit_is_refused(tmp_path: Path):
    bad = tmp_path / "controls.yaml"
    bad.write_text(
        "schema: ao.reconcile/controls-v1\n"
        "sweep:\n"
        "  max_actions_per_pass: -1\n"
        "  outcome_codes: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(policy.ControlsUnavailable):
        policy.load(bad)


def test_incomplete_outcome_vocabulary_is_refused(tmp_path: Path):
    bad = tmp_path / "controls.yaml"
    bad.write_text(
        "schema: ao.reconcile/controls-v1\n"
        "sweep:\n"
        "  max_actions_per_pass: 5\n"
        "  outcome_codes:\n"
        "    reclaimed: x\n",
        encoding="utf-8",
    )
    with pytest.raises(policy.ControlsUnavailable):
        policy.load(bad)


def test_mutation_zero_limit_changes_behaviour(tmp_path: Path):
    """The mutation test the #885 brief requires: change a control value in a
    temp copy, and behaviour changes (this is proven end to end in
    test_sweep.py::test_batch_limit_refuses_beyond_the_control); here we prove
    the *loader* itself accepts and reports the mutated value distinctly from
    the packaged default."""
    mutated = tmp_path / "controls.yaml"
    mutated.write_text(
        "schema: ao.reconcile/controls-v1\n"
        "sweep:\n"
        "  max_actions_per_pass: 0\n"
        "  outcome_codes:\n"
        "    reclaimed: reconcile.reclaimed\n"
        "    parked: reconcile.parked\n"
        "    shelved: reconcile.shelved\n"
        "    reported: reconcile.reported\n"
        "    failed: reconcile.failed\n"
        "    refused: reconcile.batch-limit-exceeded\n",
        encoding="utf-8",
    )
    default = policy.load()
    zeroed = policy.load(mutated)
    assert zeroed.max_actions_per_pass == 0
    assert zeroed.max_actions_per_pass != default.max_actions_per_pass
