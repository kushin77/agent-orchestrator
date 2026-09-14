"""Parameterization: changing a parameter changes the output.

The acceptance criterion is "parameterized (repo id, domains, model tiers,
budget), not hand-edited per instance". The only way to measure that is to
change one parameter and observe a different fleet, which is what every test
here does. A hardcoded blob would pass "it renders" and fail all of these.
"""

from __future__ import annotations

import pytest

import render


def _domains(names):
    library = {
        "testing": dict(
            name="testing",
            sme="test-quality",
            labels=["domain:testing", "testing"],
            prompt="test-quality.txt",
            platoon="qa",
            parallelism=2,
        ),
        "docs": dict(
            name="docs",
            sme="sniper-generic",
            labels=["domain:docs", "docs"],
            prompt="sniper-generic.txt",
            platoon="docs",
            parallelism=1,
        ),
    }
    return [dict(library[name], name=name) for name in names]


def _budget(**overrides):
    budget = {
        "currency": "USD",
        "fleet_total": 400,
        "per_role": {"commander": 50, "general": 30, "soldier": 120, "auditor": 50, "advisor": 20},
        "per_domain": {"testing": 40, "docs": 20},
        "max_parallelism": 8,
    }
    budget.update(overrides)
    return budget


def _surrogate(repo, domains, model_tiers=None, budget=None):
    return {
        "repo": repo,
        "domains": _domains(domains),
        "model_tiers": model_tiers
        or {
            "brain": "claude-sonnet-5",
            "volume": "deepseek-v4-pro",
            "verify": "deepseek-v4-pro",
            "escalate": "claude-opus-4-8",
        },
        "budget": budget or _budget(),
    }


def test_changing_the_repo_changes_every_identity(renderer, template):
    first = renderer(params_doc={"apiVersion": "ao.fleet-template/v1", "kind": "FleetParams", "repo": "repo-one", "values": _surrogate("repo-one", ["testing", "docs"])}, observations=None)
    second = renderer(params_doc={"apiVersion": "ao.fleet-template/v1", "kind": "FleetParams", "repo": "repo-two", "values": _surrogate("repo-two", ["testing", "docs"])}, observations=None)

    assert first["isolation"]["fleet_id"] != second["isolation"]["fleet_id"]
    assert first["isolation"]["state_prefix"] != second["isolation"]["state_prefix"]
    assert first["isolation"]["worktree_prefix"] != second["isolation"]["worktree_prefix"]
    assert set(render.member_ids(first)).isdisjoint(render.member_ids(second))
    assert all(member.startswith("repo-one:") for member in render.member_ids(first))
    assert all(member.startswith("repo-two:") for member in render.member_ids(second))


def test_adding_a_domain_adds_an_sme_a_lens_and_a_routing_edge(renderer):
    small = renderer(params_doc={"apiVersion": "ao.fleet-template/v1", "kind": "FleetParams", "repo": "probe", "values": _surrogate("probe", ["testing"])}, observations=None)
    large = renderer(params_doc={"apiVersion": "ao.fleet-template/v1", "kind": "FleetParams", "repo": "probe", "values": _surrogate("probe", ["testing", "docs"])}, observations=None)

    assert len(small["definition"]["smes"]) == 1
    assert len(large["definition"]["smes"]) == 2
    assert len(small["definition"]["routing"]["lenses"]) == 1
    assert len(large["definition"]["routing"]["lenses"]) == 2
    assert set(large["definition"]["routing"]["domain_to_role"]) == {"testing", "docs"}
    assert set(large["definition"]["finops"]["per_domain"]) == {"testing", "docs"}


def test_changing_a_model_tier_changes_every_role_on_that_tier(renderer):
    baseline = renderer(params_doc={"apiVersion": "ao.fleet-template/v1", "kind": "FleetParams", "repo": "probe", "values": _surrogate("probe", ["testing"])}, observations=None)
    escalated = renderer(
        params_doc={
            "apiVersion": "ao.fleet-template/v1",
            "kind": "FleetParams",
            "repo": "probe",
            "values": _surrogate(
                "probe",
                ["testing"],
                model_tiers={
                    "brain": "claude-sonnet-5",
                    "volume": "claude-opus-4-8",
                    "verify": "deepseek-v4-pro",
                    "escalate": "claude-opus-4-8",
                },
            ),
        },
        observations=None,
    )

    baseline_volume = {
        member["id"]: member["model"]
        for member in baseline["definition"]["roles"] + baseline["definition"]["smes"]
        if member["tier"] == "volume"
    }
    escalated_volume = {
        member["id"]: member["model"]
        for member in escalated["definition"]["roles"] + escalated["definition"]["smes"]
        if member["tier"] == "volume"
    }
    assert baseline_volume and escalated_volume
    assert all(model == "deepseek-v4-pro" for model in baseline_volume.values())
    assert all(model == "claude-opus-4-8" for model in escalated_volume.values())

    untouched = [
        member["model"]
        for member in escalated["definition"]["roles"]
        if member["tier"] != "volume"
    ]
    baseline_untouched = [
        member["model"] for member in baseline["definition"]["roles"] if member["tier"] != "volume"
    ]
    assert untouched == baseline_untouched


def test_changing_the_budget_changes_the_finops_ceilings(renderer):
    baseline = renderer(params_doc={"apiVersion": "ao.fleet-template/v1", "kind": "FleetParams", "repo": "probe", "values": _surrogate("probe", ["testing"])}, observations=None)
    cheaper = renderer(
        params_doc={
            "apiVersion": "ao.fleet-template/v1",
            "kind": "FleetParams",
            "repo": "probe",
            "values": _surrogate("probe", ["testing"], budget=_budget(fleet_total=250)),
        },
        observations=None,
    )
    assert baseline["definition"]["finops"]["fleet_total"] == 400
    assert cheaper["definition"]["finops"]["fleet_total"] == 250
    assert baseline["params_digest"]["sha256"] != cheaper["params_digest"]["sha256"]


def test_a_budget_that_breaks_its_own_ceiling_is_a_finding(renderer):
    instance = renderer(
        params_doc={
            "apiVersion": "ao.fleet-template/v1",
            "kind": "FleetParams",
            "repo": "probe",
            "values": _surrogate(
                "probe",
                ["testing"],
                budget=_budget(fleet_total=100, per_role={"commander": 90, "general": 60, "soldier": 10, "auditor": 10, "advisor": 10}),
            ),
        },
        observations=None,
    )
    codes = {finding.code for finding in render.check_invariants(instance)}
    assert "FINOPS_CEILING_EXCEEDED" in codes


def test_the_template_default_is_applied_when_the_value_is_omitted(renderer):
    values = _surrogate("probe", ["testing"])
    defaulted = renderer(
        params_doc={"apiVersion": "ao.fleet-template/v1", "kind": "FleetParams", "repo": "probe", "values": values},
        observations=None,
    )
    supplied = renderer(
        params_doc={
            "apiVersion": "ao.fleet-template/v1",
            "kind": "FleetParams",
            "repo": "probe",
            "values": dict(values, display_name="probe squads"),
        },
        observations=None,
    )
    assert defaulted["metadata"]["display_name"] == "probe agent fleet"
    assert supplied["metadata"]["display_name"] == "probe squads"


def test_a_placeholder_resolving_to_a_missing_parameter_is_a_hard_failure(renderer, template):
    mutated = render.dump_yaml(template).replace(
        ".fleet/${repo}/memory/auditor.md", ".fleet/${no_such_param}/memory/auditor.md"
    )
    assert "no_such_param" in mutated, "the fixture replacement did not apply"
    with pytest.raises(render.InputError):
        renderer(
            params_doc={
                "apiVersion": "ao.fleet-template/v1",
                "kind": "FleetParams",
                "repo": "probe",
                "values": _surrogate("probe", ["testing"]),
            },
            observations=None,
            template_doc=render.yaml.safe_load(mutated),
        )
