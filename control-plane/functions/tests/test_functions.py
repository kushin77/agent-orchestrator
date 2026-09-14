"""The cockpit function registry's own rules (issue #565, RC-10 of #551).

These exercise the validator's logic and the consumed authorities — the closed
sets, both cross-reference directions, the audit rule and the parameter resolver —
rather than re-asserting the YAML by hand, so a registry edit that breaks a rule
fails here with a named code.

The suite is owned by this lane and is invoked by `scripts/check-control-functions.sh`
(the gate names this directory as a pytest target). It is deliberately NOT added
to `scripts/pytest-suites.txt`: RC-8 (#559) is this EPIC's single writer for the
shared build files, and this lane must not touch them.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
if str(PACKAGE) not in sys.path:
    sys.path.insert(0, str(PACKAGE))

import cockpit_registry as reg  # noqa: E402

REGISTRY = reg.load()
DERIVED = reg.derive()
FUNCTIONS = REGISTRY.document["functions"]


def findings_for(document: dict) -> set[str]:
    """The codes a mutated copy of the committed registry is refused with."""
    return {f.code for f in reg.validate(reg.from_document(copy.deepcopy(document)))}


def only(document: dict, function_id: str, **changes) -> dict:
    """A copy of the registry with one function's fields replaced."""
    mutated = copy.deepcopy(document)
    for entry in mutated["functions"]:
        if entry.get("id") == function_id:
            entry.update(changes)
            break
    else:  # pragma: no cover - a renamed fixture id is a test bug
        raise AssertionError(f"{function_id} is not declared")
    return mutated


# --- the registry itself ----------------------------------------------------

def test_the_whole_registry_validates():
    assert reg.validate(REGISTRY, DERIVED) == []


def test_every_declared_function_is_read_exactly_once():
    ids = [entry["id"] for entry in FUNCTIONS]
    assert len(ids) == len(set(ids))
    assert len(REGISTRY) == len(ids)
    assert set(REGISTRY.functions) == set(ids)


def test_the_reader_ignores_no_function():
    """A function that the loader silently dropped must fail, not vanish."""
    assert len(REGISTRY) == len(FUNCTIONS)


def test_every_function_id_is_an_upper_case_mnemonic():
    for entry in FUNCTIONS:
        assert reg.ID_PATTERN.match(entry["id"]), entry["id"]


def test_the_closed_sets_are_the_schemas_own():
    schema = json.loads(reg.SCHEMA_PATH.read_text(encoding="utf-8"))
    for name in ("kinds", "parameter_types", "roles"):
        assert set(REGISTRY.__dict__[name]) == set(
            schema["properties"][name]["required"]
        ), name
    assert set(reg.closed_parameter_types()) == set(REGISTRY.parameter_types)


def test_the_registry_consumes_the_authorities_it_claims():
    assert REGISTRY.consumes == DERIVED.consumes
    assert REGISTRY.consumes["verbs"] == "control-plane/control/verbs.yaml"
    assert REGISTRY.consumes["transport"] == "portal/server/control_api.py"
    assert REGISTRY.consumes["cockpit"] == "fleet/console.py"


# --- the closed vocabulary the functions claim ------------------------------

def test_every_exposed_verb_is_claimed_by_a_declared_function():
    claimed = {endpoint for f in REGISTRY.ordered for endpoint in f.endpoints}
    exposed = {
        f"{row.family}/{row.action}"
        for row in DERIVED.verbs.values()
        if row.exposed
    }
    assert exposed - claimed == set()
    assert len(exposed) == len(DERIVED.routes)


def test_a_withheld_verb_may_not_be_bound_by_a_function():
    for function in REGISTRY.ordered:
        for endpoint in function.endpoints:
            assert DERIVED.is_routable(endpoint), f"{function.id} -> {endpoint}"


def test_an_endpoint_outside_rc3s_route_set_is_refused():
    codes = findings_for(only(REGISTRY.document, "HEADER", endpoints=["fleet/nonesuch"]))
    assert "ENDPOINT-NOT-DECLARED" in codes


def test_a_withheld_verb_is_refused_by_name():
    codes = findings_for(only(REGISTRY.document, "HEADER", endpoints=["fleet/attach"]))
    assert "ENDPOINT-NOT-EXPOSED" in codes


def test_a_function_that_binds_nothing_is_refused():
    codes = findings_for(only(REGISTRY.document, "HEADER", endpoints=[]))
    assert "NO-BINDING" in codes


# --- RC-2's effect classes and the two-way audit rule -----------------------

def test_an_unknown_effect_class_is_refused():
    codes = findings_for(only(REGISTRY.document, "HEADER", effect_class="suspend"))
    assert "UNKNOWN-EFFECT-CLASS" in codes


def test_an_effect_class_that_contradicts_the_bound_verbs_is_refused():
    codes = findings_for(only(REGISTRY.document, "PAUSE", effect_class="read"))
    assert "EFFECT-CLASS-MISMATCH" in codes


def test_a_read_function_may_not_declare_an_audit_action():
    codes = findings_for(only(REGISTRY.document, "HEADER", audit="fleet.status"))
    assert "AUDIT-FORBIDDEN" in codes


def test_a_hold_function_must_declare_an_audit_action():
    codes = findings_for(only(REGISTRY.document, "PAUSE", audit=None))
    assert "AUDIT-REQUIRED" in codes


def test_a_hold_function_cannot_invent_an_audit_action():
    codes = findings_for(only(REGISTRY.document, "PAUSE", audit="fleet.ghost"))
    assert "AUDIT-UNKNOWN" in codes


# --- parameters -------------------------------------------------------------

def test_the_closed_parameter_types_come_from_the_schema():
    assert set(reg.closed_parameter_types()) == {
        "string", "integer", "number", "boolean", "enum"
    }


def test_a_parameter_with_no_type_is_refused():
    document = copy.deepcopy(REGISTRY.document)
    for entry in document["functions"]:
        if entry["id"] == "LOG":
            del entry["parameters"][0]["type"]
    assert "UNKNOWN-PARAMETER-TYPE" in findings_for(document)


def test_a_parameter_type_outside_the_closed_set_is_refused():
    document = copy.deepcopy(REGISTRY.document)
    for entry in document["functions"]:
        if entry["id"] == "LOG":
            entry["parameters"][0]["type"] = "guid"
    assert "UNKNOWN-PARAMETER-TYPE" in findings_for(document)


def test_a_parameter_the_lever_does_not_accept_is_refused():
    document = copy.deepcopy(REGISTRY.document)
    for entry in document["functions"]:
        if entry["id"] == "LOG":
            entry["parameters"][0]["name"] = "tail-lines"
    assert "UNVERIFIED-PARAMETER" in findings_for(document)


def test_an_enum_without_values_is_refused():
    document = copy.deepcopy(REGISTRY.document)
    for entry in document["functions"]:
        if entry["id"] == "ESCALATE":
            for parameter in entry["parameters"]:
                if parameter["name"] == "severity":
                    del parameter["values"]
    assert "ENUM-VALUES" in findings_for(document)


def test_an_unknown_parameter_is_refused_by_name():
    _, findings = reg.resolve_call(REGISTRY, "LOG", {"tail": 20, "bogus": 1})
    assert [f.code for f in findings] == ["UNKNOWN-PARAMETER"]
    assert "bogus" in str(findings[0])


def test_a_missing_required_parameter_is_refused_by_name():
    _, findings = reg.resolve_call(REGISTRY, "CLAIM", {"issue": 565})
    assert "MISSING-PARAMETER" in {f.code for f in findings}
    assert any("agent" in str(f) for f in findings)


def test_a_parameter_of_the_wrong_type_is_refused_by_name():
    _, findings = reg.resolve_call(REGISTRY, "CLAIM", {"issue": "565", "agent": "a"})
    assert [f.code for f in findings] == ["PARAMETER-TYPE"]
    assert "issue" in str(findings[0])


def test_an_unknown_function_is_refused_by_name():
    call, findings = reg.resolve_call(REGISTRY, "NOPE", {})
    assert call is None
    assert [f.code for f in findings] == ["UNKNOWN-FUNCTION"]


def test_a_resolved_call_names_its_route_argv_and_receipt():
    call, findings = reg.resolve_call(REGISTRY, "LOG", {"tail": 42})
    assert findings == []
    assert call is not None
    assert call.endpoint == "fleet/debug"
    assert call.endpoint_path == f"/api/{DERIVED.route_root}/fleet/debug"
    assert call.arguments == ("--tail", "42")
    assert call.effect_class == "read"
    assert call.audit is None


# --- scope ------------------------------------------------------------------

def test_every_capability_comes_from_rc2s_vocabulary():
    assert {f.scope.capability for f in REGISTRY.ordered} <= DERIVED.capabilities


def test_a_capability_rc2_does_not_declare_is_refused():
    document = copy.deepcopy(REGISTRY.document)
    for entry in document["functions"]:
        if entry["id"] == "LOG":
            entry["scope"]["capability"] = "fleet:superuser"
    assert "CAPABILITY-UNKNOWN" in findings_for(document)


def test_the_tenant_scope_is_the_control_paths_own_org():
    for function in REGISTRY.ordered:
        assert function.scope.tenant == DERIVED.platform_org
        assert function.scope.level in DERIVED.scope_levels


def test_a_tenant_the_control_path_does_not_use_is_refused():
    document = copy.deepcopy(REGISTRY.document)
    for entry in document["functions"]:
        if entry["id"] == "LOG":
            entry["scope"]["tenant"] = "tenant-42"
    assert "TENANT-MISMATCH" in findings_for(document)


def test_role_information_may_not_appear_in_a_scope_block():
    document = copy.deepcopy(REGISTRY.document)
    for entry in document["functions"]:
        if entry["id"] == "LOG":
            entry["scope"]["roles"] = ["CTO"]
    assert "ROLE-AS-PERMISSION" in findings_for(document)


# --- streams, flags, panels -------------------------------------------------

def test_every_stream_is_a_declared_surface():
    for function in REGISTRY.ordered:
        if function.stream:
            assert function.stream in DERIVED.streams


def test_the_fixture_frames_carry_the_servers_own_event_names():
    frames = json.loads((reg.FIXTURES / "streams.json").read_text(encoding="utf-8"))["frames"]
    for key, stream in DERIVED.streams.items():
        assert key in frames, key
        frame = frames[key]["frame"]
        assert frame.startswith(f"event: {stream.event}\n"), f"{key}: {frame!r}"
        assert frames[key]["module"] == stream.module


def test_an_unknown_stream_is_refused():
    codes = findings_for(only(REGISTRY.document, "LOG", stream="telemetry_ghost"))
    assert "UNKNOWN-STREAM" in codes


def test_a_live_function_must_name_the_streams_own_flag():
    codes = findings_for(only(REGISTRY.document, "LOG", flags=["surfaces.remote_control"]))
    assert "MISSING-STREAM-FLAG" in codes


def test_a_control_bound_function_must_name_the_transport_flag():
    codes = findings_for(only(REGISTRY.document, "LOG", flags=["surfaces.fleet_projection"]))
    assert "MISSING-TRANSPORT-FLAG" in codes


def test_a_flag_the_flag_registry_does_not_declare_is_refused():
    codes = findings_for(only(REGISTRY.document, "LOG", flags=["surfaces.not_a_surface"]))
    assert "UNKNOWN-FLAG" in codes


def test_every_named_flag_exists_in_the_flag_registry():
    for function in REGISTRY.ordered:
        assert set(function.flags) <= DERIVED.flags, function.id
    assert DERIVED.transport_flag in DERIVED.flags


def test_every_panel_the_cockpit_renders_is_declared():
    assert set(DERIVED.panels) <= set(REGISTRY.renders)
    assert len(DERIVED.panels) >= 8


def test_an_undeclared_panel_is_refused_naming_the_panel():
    document = copy.deepcopy(REGISTRY.document)
    del document["renders"]["RUNGS"]
    findings = reg.validate(reg.from_document(document))
    assert "UNDECLARED-PANEL" in {f.code for f in findings}
    assert any("RUNGS" in str(f) for f in findings)


def test_a_phantom_panel_is_refused_by_name():
    document = copy.deepcopy(REGISTRY.document)
    document["renders"]["NOTES"] = "LOG"
    codes = findings_for(document)
    assert "PHANTOM-PANEL" in codes or "PANEL-KIND" in codes


def test_a_panel_mapped_to_a_non_panel_function_is_refused():
    document = copy.deepcopy(REGISTRY.document)
    document["renders"]["WATCHDOG"] = "CLOSE"
    assert "PANEL-KIND" in findings_for(document)


def test_a_dropped_function_leaves_its_mnemonic_undeclared():
    document = copy.deepcopy(REGISTRY.document)
    document["functions"] = [e for e in document["functions"] if e["id"] != "CLOSE"]
    findings = reg.validate(reg.from_document(document))
    assert any("UNCLAIMED-MNEMONIC" in f.code and "closure.close" in str(f) for f in findings)


# --- roles: additive filtering only -----------------------------------------

def test_role_suitability_is_additive_only():
    declared = set(REGISTRY.roles)
    for function in REGISTRY.ordered:
        assert set(function.roles) <= declared, function.id
        assert function.roles, function.id
    # A recommendation can only ever NARROW: never a superset, never a grant.
    for role in declared:
        recommended = {f.id for f in reg.recommended(REGISTRY, role)}
        assert recommended <= set(REGISTRY.functions)
        assert recommended == set(reg.render_workspace(REGISTRY, role))


def test_selection_is_not_permission():
    """A function not designed for a role is still resolvable — no role gate."""
    analyst_recommended = {f.id for f in reg.recommended(REGISTRY, "Analyst")}
    assert "CLOSE" not in analyst_recommended
    call, findings = reg.resolve_call(REGISTRY, "CLOSE", {"issue": 565})
    assert findings == []
    assert call is not None and call.effect_class == "irreversible"


def test_an_unknown_role_is_refused():
    with pytest.raises(reg.RegistryError):
        reg.recommended(REGISTRY, "Intern")


def test_every_function_is_designed_for_at_least_one_role():
    for role in REGISTRY.roles:
        assert reg.render_workspace(REGISTRY, role)
