"""Unit tests for the cockpit function registry (issue #565, RC-10 of EPIC #551).

These exercise the validator's and the renderer's own logic — the closed sets,
the two cross-reference directions, the refusals, and the rule that keeps role
suitability from becoming a permission — rather than re-asserting the YAML by
hand, so a registry edit that breaks a rule fails here with a named reason.

Nothing here touches a live plane: `render()` is a pure function of the registry
document, the fixtures and an optional role, and the suite proves it by rendering
a function that exists only in memory.

The suite is owned by this lane but is NOT declared in `scripts/pytest-suites.txt`:
#559 is this EPIC's single wiring lane for the shared build files. Until then the
gate runs it directly (`scripts/check-control-functions.sh`), so it is not inert.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
CLI_PATH = ROOT / "control-plane" / "functions" / "cli.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cockpit.json"


def _load_cli():
    spec = importlib.util.spec_from_file_location("control_functions_cli", CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cli = _load_cli()
DOCUMENT = cli.load_registry()
FIXTURE_DOC = json.loads(FIXTURES.read_text(encoding="utf-8"))
CLASSES, VERBS = cli.vocabulary_facts(cli.load_vocabulary(ROOT))
ROLES = set(DOCUMENT["roles"])
FUNCTIONS = DOCUMENT["functions"]


def findings_for(mutate):
    """Validate a deep copy of the registry after `mutate(document)`."""
    document = copy.deepcopy(DOCUMENT)
    mutate(document)
    findings, _counts = cli.validate(root=ROOT, document=document)
    return findings


def named(findings, needle):
    return [finding for finding in findings if needle in finding]


# --- the registry itself ----------------------------------------------------
def test_registry_validates_clean():
    findings, counts = cli.validate(root=ROOT, document=DOCUMENT)
    assert findings == [], f"the committed registry does not validate: {findings}"
    assert counts["functions"] == len(FUNCTIONS)


def test_every_function_declares_the_full_field_set():
    required = {
        "id", "title", "kind", "endpoints", "parameters",
        "scope", "effect_class", "audit", "stream", "roles",
    }
    for function in FUNCTIONS:
        missing = required - set(function)
        assert not missing, f"{function.get('id')} is missing {sorted(missing)}"


def test_mnemonics_are_unique_and_uppercase_short():
    seen = set()
    for function in FUNCTIONS:
        fid = function["id"]
        assert re.match(r"^[A-Z][A-Z0-9]{1,7}$", fid), f"{fid} is not a short uppercase mnemonic"
        assert fid not in seen, f"{fid} is declared twice"
        seen.add(fid)


# --- the closed sets are RC-2's and identity/rbac's, never restated ---------
def test_the_effect_class_vocabulary_is_rc2s():
    assert CLASSES == {"read", "hold", "stop", "irreversible"}
    for function in FUNCTIONS:
        assert function["effect_class"] in CLASSES


def test_a_commands_class_and_audit_are_exactly_rc2s():
    for function in FUNCTIONS:
        if function["kind"] != "command":
            continue
        verb = VERBS[function["endpoints"][0]["verb"]]
        assert function["effect_class"] == verb["effect_class"], function["id"]
        assert function["audit"] == verb["audit"], function["id"]


def test_a_commands_capability_is_exactly_rc2s():
    for function in FUNCTIONS:
        if function["kind"] != "command":
            continue
        verb = VERBS[function["endpoints"][0]["verb"]]
        assert function["scope"]["capability"] == verb["capability"], function["id"]


def test_a_panels_capability_is_cited_from_its_owner():
    """A capability the registry invents is refused; a cited one passes."""
    for function in FUNCTIONS:
        if function["kind"] == "command":
            continue
        capability = function["scope"]["capability"]
        if capability is None:
            assert function["scope"].get("why_no_capability"), function["id"]
            continue
        assert capability in cli.declared_permission_sources(ROOT), function["id"]


def test_the_audit_rule_holds_in_both_directions():
    for function in FUNCTIONS:
        if function["effect_class"] == "read":
            assert function["audit"] is None, function["id"]
        else:
            assert function["audit"], function["id"]


# --- coverage, both directions ----------------------------------------------
def test_every_exposed_verb_is_declared_and_every_declared_verb_is_exposed():
    declared = {
        function["endpoints"][0]["verb"]
        for function in FUNCTIONS
        if function["kind"] == "command"
    }
    exposed = {vid for vid, verb in VERBS.items() if verb.get("exposed")}
    assert declared == exposed
    excused = {item["ref"] for item in DOCUMENT["undeclared"] if item["kind"] == "verb"}
    assert not (excused & declared)


def test_every_surface_is_declared_or_excused_by_name():
    declared = {
        endpoint["surface"]
        for function in FUNCTIONS
        for endpoint in function["endpoints"]
        if "surface" in endpoint
    }
    surfaces = set(cli.load_surfaces(ROOT))
    excused = {item["ref"] for item in DOCUMENT["undeclared"] if item["kind"] == "surface"}
    assert declared | excused == surfaces
    assert not (declared & excused)
    for item in DOCUMENT["undeclared"]:
        assert item["reason"].strip(), item


def test_a_surface_route_must_be_the_route_its_declaration_names():
    for function in FUNCTIONS:
        for endpoint in function["endpoints"]:
            if "surface" not in endpoint:
                continue
            declared = cli.surface_routes(cli.load_surfaces(ROOT)[endpoint["surface"]])
            assert endpoint["route"] in declared, f"{function['id']} binds {endpoint['route']}"


def test_a_command_route_must_be_rc3s_route_for_its_verb():
    for function in FUNCTIONS:
        if function["kind"] != "command":
            continue
        endpoint = function["endpoints"][0]
        family, _, action = endpoint["verb"].partition(".")
        assert endpoint["route"] == f"/api/control/{family}/{action}", function["id"]


# --- the refusals, each by name ---------------------------------------------
def test_an_undeclared_exposed_verb_is_refused_by_name():
    def mutate(document):
        document["functions"] = [f for f in document["functions"] if f["id"] != "FST"]

    findings = findings_for(mutate)
    assert named(findings, "MISSING: RC-2 declares the exposed verb 'fleet.status'")


def test_an_undeclared_surface_is_refused_by_name():
    def mutate(document):
        document["functions"] = [f for f in document["functions"] if f["id"] != "OPS"]

    findings = findings_for(mutate)
    assert named(findings, "MISSING: the feature-flag registry declares the surface 'ops_health'")


def test_a_duplicate_id_is_refused_by_name():
    def mutate(document):
        document["functions"].append(copy.deepcopy(document["functions"][0]))

    findings = findings_for(mutate)
    assert named(findings, "duplicate function id(s): ['FST']")


def test_an_unknown_effect_class_is_refused_by_name():
    def mutate(document):
        document["functions"][0]["effect_class"] = "rollback"

    findings = findings_for(mutate)
    assert named(findings, "effect_class 'rollback' is not one of RC-2's closed set")


def test_an_unknown_role_is_refused_by_name():
    def mutate(document):
        document["functions"][0]["roles"] = ["SRE"]

    findings = findings_for(mutate)
    assert named(findings, "role 'SRE' is not one of the closed role set")


def test_a_route_outside_rc3s_route_set_is_refused_by_name():
    def mutate(document):
        document["functions"][0]["endpoints"][0]["route"] = "/api/control/fleet/frobnicate"

    findings = findings_for(mutate)
    assert named(findings, "is not RC-3's declared route for 'fleet.status'")


def test_a_withheld_verb_is_refused_by_name():
    def mutate(document):
        document["functions"][-1]["endpoints"][0] = {
            "route": "/api/control/fleet/live",
            "verb": "fleet.live",
        }
        document["functions"][-1]["kind"] = "command"

    findings = findings_for(mutate)
    assert named(findings, "which RC-2 declares with exposed: false")


def test_an_invented_capability_is_refused_by_name():
    def mutate(document):
        for function in document["functions"]:
            if function["kind"] == "command":
                function["scope"]["capability"] = "fleet:admin"
                return

    findings = findings_for(mutate)
    assert named(findings, "a capability may be cited, never minted")


def test_a_parameter_with_no_type_is_refused_by_name():
    def mutate(document):
        for function in document["functions"]:
            if function["parameters"]:
                del function["parameters"][0]["type"]
                return

    findings = findings_for(mutate)
    assert named(findings, "the parameter declares no type")


def test_a_read_that_claims_an_audit_action_is_refused_by_name():
    def mutate(document):
        document["functions"][0]["audit"] = "fleet.status"

    findings = findings_for(mutate)
    assert named(findings, "a read function must not declare an audit action")


def test_a_mutating_function_without_an_audit_action_is_refused_by_name():
    def mutate(document):
        for function in document["functions"]:
            if function["effect_class"] == "hold":
                function["audit"] = None
                return

    findings = findings_for(mutate)
    assert named(findings, "requires an audit action")


# --- the headless render ----------------------------------------------------
def test_every_declared_function_renders_headlessly():
    rendered = cli.render(DOCUMENT, FIXTURE_DOC)
    assert len(rendered) == len(FUNCTIONS)
    for row in rendered:
        assert set(row) == {
            "id", "title", "kind", "endpoints", "parameters",
            "scope", "effect_class", "audit", "stream", "roles",
        }
        assert row["endpoints"], row["id"]


def test_render_touches_no_plane_and_no_file():
    """A function that exists only in memory renders — so the gate needs no plane."""
    document = {
        "roles": {"Analyst": "reads"},
        "functions": [
            {
                "id": "TST",
                "title": "In-memory only",
                "kind": "panel",
                "endpoints": [{"route": "/api/x", "surface": "s"}],
                "parameters": [
                    {"name": "limit", "type": "integer", "required": False, "default": 3}
                ],
                "scope": {"capability": None, "why_no_capability": "fixture", "tenant_scope": "tenant"},
                "effect_class": "read",
                "audit": None,
                "stream": None,
                "roles": ["Analyst"],
            }
        ],
    }
    rendered = cli.render(document, {"calls": {}})
    assert rendered[0]["parameters"] == {"limit": 3}
    assert cli.render(document, {"calls": {}}) == rendered


def test_an_unknown_function_in_the_fixtures_is_refused_by_name():
    fixtures = copy.deepcopy(FIXTURE_DOC)
    fixtures["calls"]["NOPE"] = {}
    with pytest.raises(cli.Refused) as excinfo:
        cli.render(DOCUMENT, fixtures)
    assert "unknown function 'NOPE'" in str(excinfo.value)


def test_an_unknown_parameter_is_refused_by_name():
    fixtures = copy.deepcopy(FIXTURE_DOC)
    fixtures["calls"]["FST"] = {"wibble": "x"}
    with pytest.raises(cli.Refused) as excinfo:
        cli.render(DOCUMENT, fixtures)
    assert "unknown parameter 'wibble' on function FST" in str(excinfo.value)


def test_a_missing_required_parameter_is_refused_by_name():
    required_id = next(
        f["id"] for f in FUNCTIONS if any(p.get("required") for p in f["parameters"])
    )
    fixtures = copy.deepcopy(FIXTURE_DOC)
    fixtures["calls"][required_id] = {"args": []}
    with pytest.raises(cli.Refused) as excinfo:
        cli.render(DOCUMENT, fixtures)
    assert f"function {required_id}: required parameter 'commandId' was not supplied" in str(
        excinfo.value
    )


def test_a_wrong_typed_parameter_is_refused_by_name():
    fixtures = copy.deepcopy(FIXTURE_DOC)
    fixtures["calls"]["FLT"] = {"limit": "many"}
    with pytest.raises(cli.Refused) as excinfo:
        cli.render(DOCUMENT, fixtures)
    assert "parameter 'limit' expects an integer" in str(excinfo.value)


def test_an_unknown_role_is_refused_by_name_at_render_time():
    fixtures = copy.deepcopy(FIXTURE_DOC)
    with pytest.raises(cli.Refused) as excinfo:
        cli.render(DOCUMENT, fixtures, role="SRE")
    assert "unknown role 'SRE'" in str(excinfo.value)


# --- role suitability is additive filtering only ----------------------------
def test_role_suitability_can_only_narrow_and_never_rewrite_a_scope():
    """The proof that a role is a lens: filtering removes rows, changes nothing.

    If filtering could rewrite a scope, a role would be able to widen one. It
    cannot: for every role, the rendered rows are a strict subset of the
    declared set and every row's capability is byte-identical to the declared
    one, so there is no code path by which a role lens changes what a caller
    would need to hold.
    """
    everything = cli.render(DOCUMENT, FIXTURE_DOC)
    declared = {row["id"]: row for row in everything}
    capabilities = {row["id"]: row["scope"]["capability"] for row in everything}

    for role in sorted(ROLES):
        rendered = cli.render(DOCUMENT, FIXTURE_DOC, role=role)
        assert rendered, role
        assert len(rendered) <= len(everything)
        for row in rendered:
            assert row["id"] in declared
            assert row["scope"] == declared[row["id"]]["scope"], row["id"]
            assert row["audit"] == declared[row["id"]]["audit"], row["id"]
            assert role in row["roles"]
            assert capabilities[row["id"]] == row["scope"]["capability"]

    # the narrowest lens still renders strictly fewer rows than the whole set
    analyst = cli.render(DOCUMENT, FIXTURE_DOC, role="Analyst")
    assert len(analyst) < len(everything)
    assert all(row["effect_class"] == "read" for row in analyst)


def test_the_registry_offers_no_authorisation_surface():
    """The registry cannot permit anything: it holds no permit/deny and no role table.

    Permission is `identity/rbac`'s (`guard`, rechecked per call by RC-3). A name
    that decided access here would be the second permission vocabulary ADR-0025
    D2.3 forbids — and the schema's `additionalProperties: false` is what stops
    one being added silently.
    """
    forbidden = re.compile(r"permit|allow|authoriz|grant|deny|forbid|role_table", re.I)
    callables = {
        name for name in dir(cli) if callable(getattr(cli, name)) and forbidden.search(name)
    }
    assert not callables, f"the registry module offers an authorisation surface: {callables}"

    schema = json.loads(cli.SCHEMA.read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    assert schema["properties"]["functions"]["items"]["additionalProperties"] is False

    # and the authority that really decides is the API's, rechecked per call
    control_api = (ROOT / "portal" / "server" / "control_api.py").read_text(encoding="utf-8")
    assert "guard(" in control_api, "RC-3 must reach identity/rbac's guard"
    assert "row.capability" in control_api, "RC-3 must evaluate the verb's own capability"
