"""The purebliss single-tenant org: CEO/CTO/CFO/PMO offices over the tenant
org chart (owner directive: purebliss.app is the only tenant; its
organization has CEO/CTO/CFO/PMO offices with agents, personas, prompts,
budgets and fleets configured declaratively).

Covers:
- registry/personas/org-charts/purebliss.yaml validates against the shared
  org-chart schema/validator, resolving against the purebliss card library
  with platform fallback (tenant-first shadowing);
- the new `pmo` persona/role resolves and matches the org-chart node exactly
  (tier, cap, heartbeat) the same way ceo/cto/cfo already did upstream;
- registry/personas/offices.yaml is schema-valid and every office's
  monthlyBudgetUsd equals its head persona's org-chart cap (never a second,
  drifting literal);
- every office's headPersona/memberPersonas/fleetPack/routingGroup reference
  something real: a resolvable persona, the purebliss-team pack version, and
  the routing group declared in gateway/proxy/config/routing.yaml.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
import yaml

import registry

PKG = registry.PKG_DIR
REPO_ROOT = PKG.parents[1]

PUREBLISS_ORG_CHART = PKG / "org-charts" / "purebliss.yaml"
OFFICES_PATH = PKG / "offices.yaml"
OFFICES_SCHEMA_PATH = PKG / "persona-offices.schema.json"


def _purebliss_registry() -> registry.PersonaRegistry:
    return registry.PersonaRegistry(
        cards_dir=PKG / "cards" / "purebliss",
        platform_dir=PKG / "cards",
    )


def test_purebliss_org_chart_validates():
    chart = registry.load_org_chart(PUREBLISS_ORG_CHART)
    assert chart["tenant"] == "purebliss"
    assert chart["root"] == "ceo"
    cards = _purebliss_registry().discover()
    validated = registry.validate_org_chart(chart, cards=cards)
    role_ids = {node["id"] for node in validated["roles"]}
    assert role_ids == {"ceo", "cto", "cfo", "pmo"}


def test_purebliss_cards_shadow_platform_where_present():
    cards = _purebliss_registry().discover()
    for role_id in ("ceo", "cto", "cfo"):
        assert cards[("purebliss", role_id)]["tenant"] == "purebliss"
    # pmo is NEW: no platform pmo card exists, only pmo-sme (a distinct,
    # narrower specialist that reports into the pmo office as a member).
    assert ("purebliss", "pmo") in cards
    assert ("platform", "pmo") not in registry.PersonaRegistry().discover()


def test_purebliss_org_chart_agrees_with_cards():
    chart = registry.load_org_chart(PUREBLISS_ORG_CHART)
    cards = _purebliss_registry().discover()
    for node in chart["roles"]:
        card = cards[("purebliss", node["id"])]
        assert card["defaultModelTier"] == node["defaultModelTier"]
        assert card["monthlyBudgetCapUsd"] == node["monthlyBudgetCapUsd"]
        assert card["heartbeatSchedule"] == node["heartbeatSchedule"]
        assert card["reportsTo"] == node["reportsTo"]


def _load_offices():
    schema = json.loads(OFFICES_SCHEMA_PATH.read_text(encoding="utf-8"))
    doc = yaml.safe_load(OFFICES_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(instance=doc, schema=schema)
    return doc


def test_offices_yaml_is_schema_valid():
    doc = _load_offices()
    assert doc["tenant"] == "purebliss"
    assert {o["id"] for o in doc["offices"]} == {"ceo", "cto", "cfo", "pmo"}


def test_office_budgets_match_org_chart_caps():
    """An office's monthlyBudgetUsd must equal its head role's org-chart cap:
    the office layer restates the cap for display, it never redefines it.
    """
    chart = registry.load_org_chart(PUREBLISS_ORG_CHART)
    caps = {node["id"]: node["monthlyBudgetCapUsd"] for node in chart["roles"]}
    doc = _load_offices()
    for office in doc["offices"]:
        assert office["monthlyBudgetUsd"] == caps[office["headPersona"]], (
            f"office {office['id']!r} monthlyBudgetUsd drifted from the "
            f"org-chart cap for head persona {office['headPersona']!r}"
        )


def test_office_head_personas_resolve():
    cards = _purebliss_registry().discover()
    doc = _load_offices()
    for office in doc["offices"]:
        assert ("purebliss", office["headPersona"]) in cards


def test_office_member_personas_resolve_platform_or_purebliss():
    cards = _purebliss_registry().discover()
    doc = _load_offices()
    for office in doc["offices"]:
        for member in office["memberPersonas"]:
            assert ("purebliss", member) in cards or (
                "platform",
                member,
            ) in cards, f"office {office['id']!r} member {member!r} does not resolve"


def test_office_fleet_pack_and_routing_group_are_purebliss_team():
    doc = _load_offices()
    for office in doc["offices"]:
        assert office["fleetPack"] == "purebliss-team@1.0.0"
        assert office["routingGroup"] == "purebliss-team"
    routing_path = REPO_ROOT / "gateway" / "proxy" / "config" / "routing.yaml"
    routing = yaml.safe_load(routing_path.read_text(encoding="utf-8"))
    assert "purebliss-team" in routing["routingGroups"]


def test_office_agent_profiles_are_in_purebliss_team_pack():
    pack_path = (
        REPO_ROOT / "registry" / "packs" / "releases" / "purebliss-team.1.0.0.yaml"
    )
    pack = yaml.safe_load(pack_path.read_text(encoding="utf-8"))
    pack_profile_ids = {entry["ref"].split("@")[0] for entry in pack["contents"]["profile"]}
    doc = _load_offices()
    for office in doc["offices"]:
        for profile_id in office["agentProfiles"]:
            assert profile_id in pack_profile_ids
