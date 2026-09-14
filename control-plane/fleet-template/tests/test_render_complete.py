"""A pilot renders a COMPLETE fleet: roles, SMEs, routing, run-state and FinOps.

Definition of done, first half: "template renders a working fleet for at least
one pilot repo". Measured on both committed pilots, and cross-checked against
the committed instance file -- a render that only works in memory would not be
a deliverable.
"""

from __future__ import annotations

import os
import re

import render

SHA256 = re.compile(r"^[0-9a-f]{64}$")


def test_every_part_of_the_fleet_is_present(pilot, renderer):
    instance = renderer(pilot)
    definition = instance["definition"]

    assert {role["role"] for role in definition["roles"]} == {
        "commander",
        "general",
        "soldier",
        "auditor",
        "advisor",
    }
    declared_domains = {entry["name"] for entry in instance["params_digest"]["values"]["domains"]}
    assert {sme["domain"] for sme in definition["smes"]} == declared_domains

    assert definition["routing"]["lenses"], "routing.lenses is empty"
    assert set(definition["routing"]["domain_to_role"]) == declared_domains

    finops = definition["finops"]
    assert finops["fleet_total"] > 0
    assert set(finops["per_role"]) == {role["role"] for role in definition["roles"]}
    assert set(finops["per_domain"]) == declared_domains
    assert finops["max_parallelism"] >= 1

    assert definition["parallelism"]["total"] >= 1
    assert set(definition["parallelism"]["per_lens"]) == {
        lens["lens"] for lens in definition["routing"]["lenses"]
    }

    run_state = instance["run_state"]
    assert set(run_state["tri_state"]) == {"required", "parked", "unverified"}
    assert run_state["counts"]["present"] == len(run_state["present"])


def test_every_role_binds_its_model_from_the_model_tiers_parameter(pilot, renderer):
    instance = renderer(pilot)
    tiers = instance["params_digest"]["values"]["model_tiers"]
    for role in instance["definition"]["roles"]:
        assert role["model"] == tiers[role["tier"]]
    for sme in instance["definition"]["smes"]:
        assert sme["model"] == tiers[sme["tier"]]


def test_lens_routing_carries_platoon_and_parallelism(pilot, renderer):
    instance = renderer(pilot)
    for lens in instance["definition"]["routing"]["lenses"]:
        assert lens["lens"].startswith("lens:")
        assert lens["platoon"]
        assert lens["parallelism"] >= 1
        assert lens["domains"]


def test_provenance_digests_are_recorded(pilot, renderer):
    metadata = renderer(pilot)["metadata"]
    assert SHA256.match(metadata["template_sha256"])
    assert SHA256.match(metadata["params_sha256"])
    assert SHA256.match(renderer(pilot)["params_digest"]["sha256"])
    assert metadata["generated_by"] == "control-plane/fleet-template/render.py"


def test_committed_instance_is_exactly_a_fresh_render(pilot, renderer, committed_instance):
    assert committed_instance(pilot) == renderer(pilot)


def test_invariants_are_clean_for_every_pilot(pilot, renderer):
    assert render.check_invariants(renderer(pilot)) == []


def test_lane_check_is_ok(lane_root):
    result = render.check(lane_root)
    assert result.status == render.STATUS_OK, result.lines()
    assert result.exit_code == render.EXIT_OK
    assert len(result.detail["pilots"]) == 2


def test_pilot_files_are_named_after_their_repo(lane_root, pilots):
    for name in pilots:
        assert os.path.isfile(os.path.join(lane_root, render.PILOTS_DIR, f"{name}.params.yaml"))
        assert os.path.isfile(os.path.join(lane_root, render.PILOTS_DIR, f"{name}.fleet.yaml"))
        assert os.path.isfile(os.path.join(lane_root, render.PILOTS_DIR, f"{name}.observations.yaml"))
