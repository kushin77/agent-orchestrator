"""The C-suite org-chart declaration (issue #632, workbook-1).

Covers acceptance:
- the five C-suite cards match their issue #614 Tab 1 workbook row exactly
  (tier, monthly cap, heartbeat, responsibilities);
- the schema extension is OPTIONAL and backwards-compatible (the pre-existing
  cards stay valid unchanged);
- org-chart.yaml declares the five roles, the edges CTO/COO/CFO/CMO -> CEO and
  CEO -> board, and exactly one root;
- registry.validate_org_chart refuses an UNRESOLVABLE EDGE and a SECOND ROOT;
- the five cards are published in the append-only ledger.
"""

import copy

import pytest

import registry

REAL = registry.PersonaRegistry()
PKG = registry.PKG_DIR

# The workbook rows (issue #614 Tab 1) as the issue's Verify enumerates them:
# tier LOW/MED/HIGH/MAX, cap 300/250/100/50/200, heartbeat
# hourly/every-30m/every-15m/daily (+ the CMO's webhook trigger).
# Card order is the issue's own row order: ceo/cto/coo/cfo/cmo.
WORKBOOK_ROWS = {
    "ceo": {
        "title": "Executive Strategy Director",
        "reportsTo": "board",
        "defaultModelTier": "MAX",
        "monthlyBudgetCapUsd": 300,
        "heartbeatSchedule": "hourly",
    },
    "cto": {
        "title": "System Architect & Dev Lead",
        "reportsTo": "ceo",
        "defaultModelTier": "HIGH",
        "monthlyBudgetCapUsd": 250,
        "heartbeatSchedule": "every-30m",
    },
    "coo": {
        "title": "Operations & Pacing Lead",
        "reportsTo": "ceo",
        "defaultModelTier": "MED",
        "monthlyBudgetCapUsd": 100,
        "heartbeatSchedule": "every-15m",
    },
    "cfo": {
        "title": "Financial & Compute Controller",
        "reportsTo": "ceo",
        "defaultModelTier": "LOW",
        "monthlyBudgetCapUsd": 50,
        "heartbeatSchedule": "daily",
    },
    "cmo": {
        "title": "Marketing & Sales Automation",
        "reportsTo": "ceo",
        "defaultModelTier": "MED",
        "monthlyBudgetCapUsd": 200,
        "heartbeatSchedule": "hourly",
    },
}

CSUITE = tuple(WORKBOOK_ROWS)  # ("ceo", "cto", "coo", "cfo", "cmo")


# --------------------------------------------------------------------------- #
# the five cards match the workbook rows
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("persona", CSUITE)
def test_csuite_card_exists_and_matches_its_workbook_row(persona):
    row = WORKBOOK_ROWS[persona]
    card = registry.card_from_yaml(PKG / "cards" / f"{persona}.yaml")
    assert card["id"] == persona
    assert card["tenant"] == "platform"
    assert card["defaultModelTier"] == row["defaultModelTier"]
    assert card["monthlyBudgetCapUsd"] == row["monthlyBudgetCapUsd"]
    assert card["heartbeatSchedule"] == row["heartbeatSchedule"]
    assert card["reportsTo"] == row["reportsTo"]


@pytest.mark.parametrize("persona", CSUITE)
def test_csuite_card_summary_and_description_carry_the_responsibilities(persona):
    """The workbook 'Core Responsibilities' column lands as summary + description."""
    card = registry.card_from_yaml(PKG / "cards" / f"{persona}.yaml")
    assert card["summary"], f"{persona} needs a summary"
    assert card["description"], f"{persona} needs a longer description"
    # Every card names its reporting line in prose too, so the declaration is
    # legible without the yaml field.
    assert WORKBOOK_ROWS[persona]["reportsTo"] in card["description"].lower()


def test_csuite_tiers_and_caps_are_the_workbook_values():
    """The issue enumerates the sets; this pins the exact pairing, not just membership."""
    cards = {
        p: registry.card_from_yaml(PKG / "cards" / f"{p}.yaml") for p in CSUITE
    }
    assert {c["defaultModelTier"] for c in cards.values()} == {
        "LOW",
        "MED",
        "HIGH",
        "MAX",
    }
    assert {c["monthlyBudgetCapUsd"] for c in cards.values()} == {
        50,
        100,
        200,
        250,
        300,
    }
    assert {c["heartbeatSchedule"] for c in cards.values()} == {
        "hourly",
        "every-30m",
        "every-15m",
        "daily",
    }


def test_csuite_cards_are_published_in_the_ledger():
    for persona in CSUITE:
        assert REAL.lifecycle_status("platform", persona) == "published"
        # resolve() re-verifies the frozen sha256 against disk.
        assert REAL.resolve("platform", persona)["id"] == persona


# --------------------------------------------------------------------------- #
# the schema extension is optional (backwards-compatible)
# --------------------------------------------------------------------------- #


def test_org_chart_fields_are_optional(scratch):
    """A card with none of the three new fields still validates (the 25 old cards)."""
    reg = scratch.write_registry(
        {"alpha.yaml": scratch.card_yaml(id="alpha", name="Alpha")}
    )
    card = reg.get("platform", "alpha")
    assert "reportsTo" not in card
    assert "monthlyBudgetCapUsd" not in card
    assert "heartbeatSchedule" not in card


def test_pre_existing_cards_stay_valid_unchanged():
    """Regression: every non-C-suite seed card validates (none gained a field)."""
    for path in sorted((PKG / "cards").glob("*.yaml")):
        if path.stem in CSUITE:
            continue
        card = registry.card_from_yaml(path)
        assert "reportsTo" not in card
        assert "monthlyBudgetCapUsd" not in card
        assert "heartbeatSchedule" not in card


def test_unknown_heartbeat_schedule_is_refused(scratch):
    reg = scratch.write_registry(
        {"alpha.yaml": scratch.card_yaml(id="alpha", heartbeatSchedule="every-7m")}
    )
    with pytest.raises(registry.InvalidCardError):
        reg.discover()


def test_negative_monthly_budget_cap_is_refused(scratch):
    reg = scratch.write_registry(
        {"alpha.yaml": scratch.card_yaml(id="alpha", monthlyBudgetCapUsd=-1)}
    )
    with pytest.raises(registry.InvalidCardError):
        reg.discover()


# --------------------------------------------------------------------------- #
# the org-chart declaration
# --------------------------------------------------------------------------- #


def test_org_chart_declares_the_five_roles_and_edges():
    chart = registry.load_org_chart()
    assert chart["orgChartSchema"] == "persona-org-chart/v1"
    assert chart["principal"] == "board"
    assert chart["root"] == "ceo"
    assert [n["id"] for n in chart["roles"]] == list(CSUITE)
    assert [n["reportsTo"] for n in chart["roles"]] == [
        "board",
        "ceo",
        "ceo",
        "ceo",
        "ceo",
    ]


def test_org_chart_validates_against_the_live_library():
    chart = registry.load_org_chart()
    assert registry.validate_org_chart(chart) is chart


def test_org_chart_nodes_agree_with_their_cards():
    """The chart is not a second copy: every node's governance equals its card's."""
    chart = registry.load_org_chart()
    for node in chart["roles"]:
        card = registry.card_from_yaml(PKG / "cards" / f"{node['id']}.yaml")
        assert node["defaultModelTier"] == card["defaultModelTier"]
        assert node["monthlyBudgetCapUsd"] == card["monthlyBudgetCapUsd"]
        assert node["heartbeatSchedule"] == card["heartbeatSchedule"]
        assert node["reportsTo"] == card["reportsTo"]


def test_org_chart_has_exactly_one_root():
    chart = registry.load_org_chart()
    roots = [n["id"] for n in chart["roles"] if n["reportsTo"] == chart["principal"]]
    assert roots == ["ceo"]


def test_org_chart_cli_validates_the_real_declaration(capsys):
    assert registry.main(["org-chart"]) == 0
    assert "OK    org chart" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# the refusals (mutation-proven by the two tests below)
# --------------------------------------------------------------------------- #


def test_org_chart_refuses_an_unresolvable_edge():
    """An edge naming a target that is not a resolvable platform persona."""
    chart = copy.deepcopy(registry.load_org_chart())
    chart["roles"][1]["reportsTo"] = "not-a-persona"  # cto -> nowhere
    with pytest.raises(registry.InvalidOrgChartError, match="does not resolve"):
        registry.validate_org_chart(chart)


def test_org_chart_refuses_an_edge_to_a_non_persona_principal():
    """Only the root may report to the principal; a second node must not."""
    chart = copy.deepcopy(registry.load_org_chart())
    chart["roles"][1]["reportsTo"] = "board"  # cto -> board is a second root
    with pytest.raises(registry.InvalidOrgChartError, match="exactly one root"):
        registry.validate_org_chart(chart)


def test_org_chart_refuses_a_second_root():
    """Two roles reporting to the principal (the acceptance-criteria mutation)."""
    chart = copy.deepcopy(registry.load_org_chart())
    chart["roles"][2]["reportsTo"] = "board"  # coo -> board as well as ceo
    with pytest.raises(registry.InvalidOrgChartError, match="exactly one root"):
        registry.validate_org_chart(chart)


def test_org_chart_refuses_zero_roots():
    chart = copy.deepcopy(registry.load_org_chart())
    chart["roles"][0]["reportsTo"] = "cto"  # ceo reports down; nobody roots
    with pytest.raises(registry.InvalidOrgChartError, match="exactly one root"):
        registry.validate_org_chart(chart)


def test_org_chart_refuses_a_root_that_is_not_the_declared_root():
    chart = copy.deepcopy(registry.load_org_chart())
    chart["root"] = "cfo"
    with pytest.raises(registry.InvalidOrgChartError, match="does not report"):
        registry.validate_org_chart(chart)


def test_org_chart_refuses_a_cycle():
    chart = copy.deepcopy(registry.load_org_chart())
    chart["roles"][1]["reportsTo"] = "coo"
    chart["roles"][2]["reportsTo"] = "cto"
    with pytest.raises(registry.InvalidOrgChartError, match="cycle|does not reach"):
        registry.validate_org_chart(chart)


def test_org_chart_refuses_drift_between_chart_and_card():
    """The chart and the card cannot silently disagree on governance."""
    chart = copy.deepcopy(registry.load_org_chart())
    chart["roles"][1]["monthlyBudgetCapUsd"] = 999  # cto cap != its card's 250
    with pytest.raises(registry.InvalidOrgChartError, match="must agree"):
        registry.validate_org_chart(chart)


def test_org_chart_refuses_a_role_whose_card_is_missing():
    chart = copy.deepcopy(registry.load_org_chart())
    chart["roles"].append(
        {"id": "ghost", "title": "Ghost", "reportsTo": "ceo"}
    )
    with pytest.raises(registry.InvalidOrgChartError, match="does not resolve"):
        registry.validate_org_chart(chart)


def test_org_chart_schema_is_closed_on_the_principal_string(tmp_path):
    """The principal is the reserved 'board', not an arbitrary string."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "orgChartSchema: persona-org-chart/v1\n"
        "id: bad\n"
        "tenant: platform\n"
        "version: 1.0.0\n"
        "principal: founder\n"
        "root: ceo\n"
        "roles:\n"
        "  - id: ceo\n"
        "    title: CEO\n"
        "    reportsTo: founder\n",
        encoding="utf-8",
    )
    with pytest.raises(registry.InvalidOrgChartError):
        registry.load_org_chart(bad)


def test_org_chart_status_surfaces_the_declaration(capsys):
    assert registry.main(["status"]) == 0
    import json

    out = capsys.readouterr().out
    payload = json.loads(out[: out.index("}\n") + 1])
    assert payload["orgChart"]["root"] == "ceo"
    assert payload["orgChart"]["roles"] == list(CSUITE)
