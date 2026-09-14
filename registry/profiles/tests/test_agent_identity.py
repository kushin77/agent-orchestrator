#!/usr/bin/env python3
"""pytest suite for the shared agent-identity schema + parity check (#346).

Drives registry/profiles/parity/{parity.py,cli.py} directly (no network, no
external dependency beyond PyYAML/jsonschema). Proves, with precise assertions:

  * the shared schema's closed vocabularies EQUAL this repo's declared
    vocabularies (agent-profile.schema.json + catalog.yaml) — green;
  * a change on EITHER side is detected (both directions of the drift gate);
  * every published seed projects to an identity view that validates;
  * the projection is the documented one (id -> name, transport -> provider,
    defaultModelTier -> modelTier, capabilitySet -> capabilities, absent status
    -> registered) and NEVER overwrites a declared value;
  * an out-of-vocabulary status and a FREE-TEXT capability are refused;
  * a missing required field, a wrong type, an undeclared field and a bad shape
    are each refused with their own stable code;
  * unreadable input is CANNOT-ASSESS (2), never OK.

Run:  python3 -m pytest registry/profiles/tests -q
"""

from __future__ import annotations

import importlib.util
import os

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILES_DIR = os.path.dirname(HERE)
PARITY_DIR = os.path.join(PROFILES_DIR, "parity")

SCHEMA_PATH = os.path.join(PROFILES_DIR, "agent-identity.schema.json")
PROFILE_SCHEMA_PATH = os.path.join(PROFILES_DIR, "agent-profile.schema.json")
CATALOG_PATH = os.path.join(PROFILES_DIR, "catalog.yaml")
SEEDS_DIR = os.path.join(PROFILES_DIR, "seeds")


def _load(filename, name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(PARITY_DIR, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


P = _load("parity.py", "agent_identity_parity")
CLI = _load("cli.py", "agent_identity_cli")

SCHEMA = P.load_json(SCHEMA_PATH)
PROFILE_SCHEMA = P.load_json(PROFILE_SCHEMA_PATH)
CATALOG = P.load_yaml(CATALOG_PATH)


def _seed(**over):
    """A minimal, valid AgentProfile seed (the projection's input)."""
    base = {
        "id": "paperclip",
        "version": "1.0.0",
        "owner": "platform/quality",
        "systemPromptRef": "paperclip/primary@v1",
        "toolAllowlist": ["file_read"],
        "constraintSet": ["issue-first"],
        "capabilitySet": ["research"],
        "defaultModelTier": "LOW",
        "memoryScope": ["user"],
        "guardrailPolicyRef": "worker-bundle",
    }
    base.update(over)
    return base


def _record(**over):
    """A minimal, valid identity view (the shared schema's input)."""
    base = {
        "id": "paperclip",
        "name": "Paperclip",
        "provider": "anthropic",
        "modelTier": "LOW",
        "status": "active",
        "capabilities": ["research"],
        "version": "1.0.0",
        "owner": "platform/quality",
        "systemPromptRef": "paperclip/primary@v1",
        "toolAllowlist": ["file_read"],
        "constraintSet": ["issue-first"],
        "capabilitySet": ["research"],
        "defaultModelTier": "LOW",
        "memoryScope": ["user"],
        "guardrailPolicyRef": "worker-bundle",
    }
    base.update(over)
    return base


def _codes(findings):
    return [f.split(": ", 1)[1].split(" ", 1)[0] for f in findings]


# --------------------------------------------------------------------------
# the shared schema reconciles with this repo's declared contract
# --------------------------------------------------------------------------

def test_schema_parity_is_green():
    assert P.schema_parity_findings(SCHEMA, PROFILE_SCHEMA, CATALOG) == []


def test_required_is_the_profile_set_plus_the_identity_fields():
    assert set(SCHEMA["required"]) == (
        set(PROFILE_SCHEMA["required"]) | set(P.IDENTITY_REQUIRED))


def test_every_profile_property_is_declared_by_the_shared_schema():
    assert set(PROFILE_SCHEMA["properties"]) <= set(SCHEMA["properties"])
    assert set(P.IDENTITY_REQUIRED) <= set(SCHEMA["properties"])


def test_status_is_the_closed_union():
    status = SCHEMA["definitions"]["statusValue"]["enum"]
    assert set(status) == set(P.STATUS_UNION)
    assert set(P.SHARED_FRONTEND_STATUS) <= set(status)
    assert set(P.AGENT_ORCHESTRATOR_STATUS) <= set(status)


def test_every_pinned_vocabulary_equals_the_catalog():
    vocab = P.catalog_vocab(CATALOG)
    for def_name, section in P.PINNED_DEFINITIONS:
        assert set(SCHEMA["definitions"][def_name]["enum"]) == vocab[section], (
            "%s must equal catalog.%s" % (def_name, section))


def test_closed_vocabularies_cover_every_enum_in_the_schema():
    fields = P.closed_vocab_fields(SCHEMA)
    for name in ("modelTier", "status", "capabilities", "toolAllowlist",
                 "constraintSet", "memoryScope", "guardrailPolicyRef",
                 "capabilitySet", "defaultModelTier"):
        assert name in fields, "%s is not a closed-vocabulary field" % name


# --------------------------------------------------------------------------
# the committed tree is green, and the seeds are the input
# --------------------------------------------------------------------------

def test_evaluate_is_green_on_the_committed_tree():
    status, lines = P.evaluate(SCHEMA_PATH, PROFILE_SCHEMA_PATH,
                               CATALOG_PATH, SEEDS_DIR)
    assert status == P.OK, lines
    assert any("OK" in line for line in lines)


def test_every_seed_validates_with_no_findings():
    names, findings = P.seed_findings(SEEDS_DIR, SCHEMA)
    assert findings == [], findings
    assert len(names) >= 12


def test_cli_exit_codes_are_tri_state():
    assert CLI.main([]) == P.OK
    assert CLI.main(["--schema", os.path.join(SEEDS_DIR, "no-such.json")]) \
        == P.CANNOT_ASSESS


# --------------------------------------------------------------------------
# the documented projection
# --------------------------------------------------------------------------

def test_projection_fills_only_absent_identity_fields():
    view = P.project_identity(_seed())
    assert view["name"] == "paperclip"          # id -> name
    assert view["modelTier"] == "LOW"           # defaultModelTier -> modelTier
    assert view["capabilities"] == ["research"]  # capabilitySet -> capabilities
    assert view["status"] == "registered"       # documented lifecycle default
    assert view["provider"] == "unspecified"    # no transport declared
    assert P.identity_findings(view, SCHEMA, "seed") == []


def test_projection_maps_transport_to_provider():
    assert P.project_identity(_seed(transport="deepseek"))["provider"] \
        == "deepseek"
    assert P.project_identity(_seed(transport="api"))["provider"] == "anthropic"
    assert P.project_identity(_seed(transport="session"))["provider"] \
        == "anthropic"


def test_projection_never_overwrites_a_declared_identity_field():
    view = P.project_identity(_seed(
        name="Paperclip", provider="ollama", modelTier="MAX",
        status="paused", capabilities=["audit"], transport="deepseek"))
    assert view["name"] == "Paperclip"
    assert view["provider"] == "ollama"
    assert view["modelTier"] == "MAX"
    assert view["status"] == "paused"
    assert view["capabilities"] == ["audit"]


def test_a_finops_seed_projects_its_declared_transport():
    """finops-steward declares transport: deepseek (a real seed, not a fixture)."""
    profile = P.load_yaml(os.path.join(SEEDS_DIR, "finops-steward.1.0.0.yaml"))
    assert P.project_identity(profile)["provider"] == "deepseek"


# --------------------------------------------------------------------------
# refusals (each with its own stable code)
# --------------------------------------------------------------------------

def test_out_of_vocabulary_status_is_refused():
    findings = P.identity_findings(_record(status="zombie"), SCHEMA, "rec")
    assert P.F_VOCAB in _codes(findings)
    assert any("field=status" in f and "zombie" in f for f in findings), findings


def test_free_text_capability_is_refused():
    findings = P.identity_findings(
        _record(capabilities=["write whatever i want"]), SCHEMA, "rec")
    assert P.F_VOCAB in _codes(findings)
    assert any("field=capabilities" in f for f in findings), findings


def test_free_text_capability_in_a_seed_is_refused():
    view = P.project_identity(_seed(capabilitySet=["do-anything"]))
    findings = P.identity_findings(view, SCHEMA, "seed")
    assert any("field=capabilities" in f for f in findings), findings


def test_out_of_vocabulary_tier_is_refused():
    findings = P.identity_findings(_record(modelTier="ULTRA"), SCHEMA, "rec")
    assert any("field=modelTier" in f for f in findings), findings


def test_missing_required_field_is_refused():
    record = _record()
    del record["owner"]
    findings = P.identity_findings(record, SCHEMA, "rec")
    assert P.F_MISSING in _codes(findings)
    assert any("field=owner" in f for f in findings), findings


def test_wrong_type_is_refused():
    findings = P.identity_findings(
        _record(capabilities="research"), SCHEMA, "rec")
    assert P.F_TYPE in _codes(findings)
    assert any("field=capabilities" in f for f in findings), findings


def test_undeclared_field_is_refused():
    findings = P.identity_findings(_record(bogusField="x"), SCHEMA, "rec")
    assert P.F_EXTRA in _codes(findings)
    assert any("field=bogusField" in f for f in findings), findings


def test_bad_shape_is_refused():
    findings = P.identity_findings(_record(id="Paperclip"), SCHEMA, "rec")
    assert P.F_SHAPE in _codes(findings)
    assert any("field=id" in f for f in findings), findings


# --------------------------------------------------------------------------
# drift detection, BOTH directions
# --------------------------------------------------------------------------

def test_widening_the_shared_vocabulary_is_drift():
    mutated = P.load_json(SCHEMA_PATH)
    mutated["definitions"]["capabilityId"]["enum"].append("made-up-capability")
    findings = P.schema_parity_findings(mutated, PROFILE_SCHEMA, CATALOG)
    assert any(P.F_DRIFT in f and "field=capabilityId" in f
               and "made-up-capability" in f for f in findings), findings


def test_widening_the_repo_vocabulary_is_drift():
    mutated = P.load_json(PROFILE_SCHEMA_PATH)
    mutated["definitions"]["modelTier"]["enum"].append("ULTRA")
    findings = P.schema_parity_findings(SCHEMA, mutated, CATALOG)
    assert any(P.F_DRIFT in f and "field=modelTier" in f and "ULTRA" in f
               for f in findings), findings


def test_narrowing_the_status_enum_below_the_other_repo_is_drift():
    mutated = P.load_json(SCHEMA_PATH)
    mutated["definitions"]["statusValue"]["enum"] = ["active", "paused"]
    findings = P.schema_parity_findings(mutated, PROFILE_SCHEMA, CATALOG)
    assert any(P.F_DRIFT in f and "field=status" in f for f in findings), findings


def test_a_new_required_field_on_either_side_is_drift():
    mutated = P.load_json(PROFILE_SCHEMA_PATH)
    mutated["required"].append("displayLabel")
    findings = P.schema_parity_findings(SCHEMA, mutated, CATALOG)
    assert any(P.F_DRIFT in f and "field=required" in f
               and "displayLabel" in f for f in findings), findings


def test_a_new_property_on_either_side_is_drift():
    mutated = P.load_json(PROFILE_SCHEMA_PATH)
    mutated["properties"]["displayLabel"] = {"type": "string"}
    findings = P.schema_parity_findings(SCHEMA, mutated, CATALOG)
    assert any(P.F_DRIFT in f and "field=properties" in f
               and "displayLabel" in f for f in findings), findings


def test_a_catalog_id_absent_from_the_shared_vocabulary_is_drift():
    mutated = P.load_yaml(CATALOG_PATH)
    mutated["capabilities"]["teleport"] = {"summary": "invented"}
    findings = P.schema_parity_findings(SCHEMA, PROFILE_SCHEMA, mutated)
    assert any(P.F_DRIFT in f and "field=capabilityId" in f
               for f in findings), findings


# --------------------------------------------------------------------------
# CANNOT-ASSESS is never OK
# --------------------------------------------------------------------------

def test_missing_schema_is_cannot_assess():
    status, lines = P.evaluate(os.path.join(PROFILES_DIR, "nope.json"),
                               PROFILE_SCHEMA_PATH, CATALOG_PATH, SEEDS_DIR)
    assert status == P.CANNOT_ASSESS
    assert any(P.F_CANNOT in line for line in lines)


def test_missing_seeds_dir_is_cannot_assess():
    status, _ = P.evaluate(SCHEMA_PATH, PROFILE_SCHEMA_PATH, CATALOG_PATH,
                           os.path.join(PROFILES_DIR, "no-such-seeds"))
    assert status == P.CANNOT_ASSESS


def test_empty_seeds_dir_is_cannot_assess():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        status, _ = P.evaluate(SCHEMA_PATH, PROFILE_SCHEMA_PATH, CATALOG_PATH,
                               tmp)
    assert status == P.CANNOT_ASSESS


# --------------------------------------------------------------------------
# end-to-end on a scratch seed tree (the gate's mutation shape)
# --------------------------------------------------------------------------

def _write_seed(tmp_path, profile, name="paperclip.1.0.0.yaml"):
    seeds = os.path.join(str(tmp_path), "seeds")
    os.makedirs(seeds, exist_ok=True)
    path = os.path.join(seeds, name)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(profile, fh, sort_keys=False)
    return seeds


def test_scratch_tree_green_then_a_stripped_required_field_is_refused(tmp_path):
    seeds = _write_seed(tmp_path, _seed())
    status, lines = P.evaluate(SCHEMA_PATH, PROFILE_SCHEMA_PATH, CATALOG_PATH,
                               seeds)
    assert status == P.OK, lines

    _write_seed(tmp_path, {k: v for k, v in _seed().items()
                           if k != "owner"})
    status, lines = P.evaluate(SCHEMA_PATH, PROFILE_SCHEMA_PATH, CATALOG_PATH,
                               seeds)
    assert status == P.NOT_OK
    assert any("field=owner" in line for line in lines), lines


def test_scratch_tree_refuses_a_free_text_capability(tmp_path):
    seeds = _write_seed(tmp_path, _seed(capabilitySet=["free-text-capability"]))
    status, lines = P.evaluate(SCHEMA_PATH, PROFILE_SCHEMA_PATH, CATALOG_PATH,
                               seeds)
    assert status == P.NOT_OK
    assert any("field=capabilities" in line and P.F_VOCAB in line
               for line in lines), lines


def test_scratch_tree_refuses_a_declared_out_of_vocabulary_status(tmp_path):
    seeds = _write_seed(tmp_path, _seed(status="zombie"))
    status, lines = P.evaluate(SCHEMA_PATH, PROFILE_SCHEMA_PATH, CATALOG_PATH,
                               seeds)
    assert status == P.NOT_OK
    assert any("field=status" in line and P.F_VOCAB in line
               for line in lines), lines
