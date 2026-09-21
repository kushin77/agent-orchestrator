"""``policy.load`` — schema-validated, semantically-checked policy loading."""

from __future__ import annotations

import json

import pytest

from graph import CannotAssess
import policy as policy_mod


def test_the_committed_policy_loads_and_validates(root):
    # root carries no policy.yaml of its own; load() falls back to the
    # package's committed copy (mirrors graph._load_ticket_builder's fallback).
    pol = policy_mod.load(root)
    assert pol.wave_cap_default >= 1
    assert pol.priority_weights["P0"] > pol.priority_weights["P1"] > pol.priority_weights["P2"]
    assert "sla_breach" in pol.unsourced_terms
    assert pol.unsourced_terms["sla_breach"]["weight"] == 0


def test_priority_weight_reads_the_board_label(root):
    pol = policy_mod.load(root)
    value, level = pol.priority_weight(("priority:P1", "other"))
    assert level == "P1"
    assert value == pol.priority_weights["P1"]
    value, level = pol.priority_weight(())
    assert level == "unset"


def test_lane_for_falls_back_to_default_lane_for_an_unknown_pillar(root):
    pol = policy_mod.load(root)
    lane = pol.lane_for(("pillar:does-not-exist",))
    assert lane.lane == pol.default_lane.lane


def test_lane_for_matches_a_declared_pillar(root):
    pol = policy_mod.load(root)
    lane = pol.lane_for(("pillar:guardrails-security",))
    assert lane.lane == "guardrails"
    assert lane.tier == "L1"


def test_a_malformed_policy_is_cannot_assess(tmp_path, root):
    bad = root / "governance" / "pmo"
    bad.mkdir(parents=True, exist_ok=True)
    (bad / "policy.schema.json").write_text(
        json.dumps(json.loads((policy_mod.Path(__file__).resolve().parents[1] / "policy.schema.json").read_text())),
        encoding="utf-8",
    )
    (bad / "policy.yaml").write_text("version: 1\n", encoding="utf-8")  # missing required keys
    with pytest.raises(CannotAssess):
        policy_mod.load(root)


def test_two_lanes_cannot_declare_the_same_pillar(tmp_path, root):
    schema_src = policy_mod.Path(__file__).resolve().parents[1] / "policy.schema.json"
    good = policy_mod.Path(__file__).resolve().parents[1] / "policy.yaml"
    import yaml

    document = yaml.safe_load(good.read_text(encoding="utf-8"))
    document["lanes"][1]["pillar"] = document["lanes"][0]["pillar"]

    target = root / "governance" / "pmo"
    target.mkdir(parents=True, exist_ok=True)
    (target / "policy.schema.json").write_text(schema_src.read_text(encoding="utf-8"), encoding="utf-8")
    (target / "policy.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(CannotAssess):
        policy_mod.load(root)
