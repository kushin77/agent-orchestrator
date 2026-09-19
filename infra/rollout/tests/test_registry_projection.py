"""Tests for the live-state -> registry-declaration projection (issue #967)."""

from __future__ import annotations

from infra.rollout.registry_projection import (
    plan_surface_names,
    project_all,
    project_surface,
    registry_matches_projection,
)

_PLAN = {
    "phases": {
        "7": {
            "surfaces": [
                {"flag": "surfaces.fleet_projection", "go_live_stage": "full"},
                {"flag": "services.portal", "go_live_stage": "full"},
            ]
        }
    }
}


def test_project_surface_absent_is_never_promoted():
    assert project_surface("fleet_projection", {"flags": {}}) == {
        "stage": "off",
        "promoted": False,
        "live": False,
    }


def test_project_surface_absent_document_is_never_promoted():
    assert project_surface("fleet_projection", None) == {
        "stage": "off",
        "promoted": False,
        "live": False,
    }


def test_project_surface_canary_is_promoted_but_not_live():
    doc = {"flags": {"surfaces.fleet_projection": {"stage": "canary"}}}
    result = project_surface("fleet_projection", doc)
    assert result["promoted"] is True
    assert result["live"] is False


def test_project_surface_full_is_promoted_and_live():
    doc = {"flags": {"surfaces.fleet_projection": {"stage": "full"}}}
    result = project_surface("fleet_projection", doc)
    assert result == {"stage": "full", "promoted": True, "live": True}


def test_project_surface_malformed_entry_fails_closed():
    doc = {"flags": {"surfaces.fleet_projection": "not-a-mapping"}}
    assert project_surface("fleet_projection", doc)["promoted"] is False


def test_project_surface_malformed_document_fails_closed():
    assert project_surface("fleet_projection", "not-a-mapping")["promoted"] is False


def test_plan_surface_names_strips_prefix_and_ignores_services():
    assert plan_surface_names(_PLAN) == {"fleet_projection"}


def test_plan_surface_names_empty_for_unusable_plan():
    assert plan_surface_names(None) == set()
    assert plan_surface_names({}) == set()


def test_project_all_scopes_to_plan_named_surfaces():
    doc = {
        "flags": {
            "surfaces.fleet_projection": {"stage": "full"},
            "surfaces.not_in_plan": {"stage": "full"},
        }
    }
    assert project_all(doc, _PLAN) == {
        "fleet_projection": {"stage": "full", "promoted": True, "live": True}
    }


def test_registry_matches_projection_true_when_never_promoted():
    # Never promoted -> the registry's own value never matters.
    assert registry_matches_projection(None, "fleet_projection", {"promoted": False}) is True


def test_registry_matches_projection_false_when_registry_absent():
    projected = {"stage": "full", "promoted": True, "live": True}
    assert registry_matches_projection(None, "fleet_projection", projected) is False


def test_registry_matches_projection_false_when_registry_lags():
    projected = {"stage": "canary", "promoted": True, "live": False}
    registry_surfaces = {"fleet_projection": {"promoted": False}}
    assert registry_matches_projection(registry_surfaces, "fleet_projection", projected) is False


def test_registry_matches_projection_true_when_registry_caught_up():
    projected = {"stage": "full", "promoted": True, "live": True}
    registry_surfaces = {"fleet_projection": {"promoted": True}}
    assert registry_matches_projection(registry_surfaces, "fleet_projection", projected) is True
