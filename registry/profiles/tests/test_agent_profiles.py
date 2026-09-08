#!/usr/bin/env python3
"""pytest suite for the AgentProfile validator (issue #9).

Drives registry/profiles/validate.py directly (no network, no external
dependencies beyond PyYAML/jsonschema). Proves, with precise assertions:

  * every published seed is valid (no false red);
  * every broken fixture is rejected for its INTENDED reason (no false green);
  * schema<->catalog parity drift is detected;
  * the published-version ledger refuses mutation (immutability) and
    duplicate (profile, version) entries;
  * a catalog bundle referencing an unknown atomic policy is rejected;
  * seed filename must match declared id/version.

Run:  python3 -m pytest registry/profiles/tests -q
"""

from __future__ import annotations

import importlib.util
import os
import shutil

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILES_DIR = os.path.dirname(HERE)

_SEED_SPEC = importlib.util.spec_from_file_location(
    "agent_profile_validate", os.path.join(PROFILES_DIR, "validate.py"))
V = importlib.util.module_from_spec(_SEED_SPEC)
_SEED_SPEC.loader.exec_module(V)  # type: ignore[union-attr]

SEEDS = os.path.join(PROFILES_DIR, "seeds")
FIXTURES = os.path.join(PROFILES_DIR, "tests", "fixtures")
SCHEMA = os.path.join(PROFILES_DIR, "agent-profile.schema.json")
CATALOG = os.path.join(PROFILES_DIR, "catalog.yaml")
MANIFEST = os.path.join(PROFILES_DIR, "versions", "manifest.yaml")


def _load_yaml(path):
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _dump_yaml(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False)


def _copy_tree(tmp_path, subdir, name):
    src = os.path.join(PROFILES_DIR, subdir, name)
    dst = os.path.join(str(tmp_path), subdir, name)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    return dst


# --------------------------------------------------------------------------
# happy path: every published seed validates
# --------------------------------------------------------------------------

def test_all_published_seeds_are_valid():
    schema = V.load_json(SCHEMA)
    catalog = V.load_yaml(CATALOG)
    seeds = sorted(n for n in os.listdir(SEEDS) if n.endswith(".yaml"))
    assert seeds, "no seed files under seeds/"
    for name in seeds:
        errs = V.validate_seed_file(os.path.join(SEEDS, name), schema, catalog)
        assert errs == [], "%s: %s" % (name, errs)


def test_full_coverage_is_green():
    ok, errors = V.coverage_errors(SEEDS, MANIFEST, SCHEMA, CATALOG)
    assert ok, "coverage should be green: %s" % errors


def test_self_test_rejects_all_fixtures():
    errors = V.self_test_errors(FIXTURES, SCHEMA, CATALOG)
    assert errors == [], "self-test must reject every fixture: %s" % errors


# --------------------------------------------------------------------------
# negative fixtures: each must fail for its INTENDED reason
# --------------------------------------------------------------------------

FIXTURE_EXPECTATIONS = {
    "invalid-unknown-tool.yaml": "mindreader",
    "invalid-unknown-capability.yaml": "time-travel",
    "invalid-unknown-guardrail.yaml": "no-such-policy",
    "invalid-unknown-constraint.yaml": "no-flying",
    "invalid-bad-tier.yaml": "ULTRA",
    "invalid-bad-memory-scope.yaml": "global",
    "invalid-missing-field.yaml": "owner",
    "invalid-bad-version.yaml": "1.0",
    "invalid-bad-id.yaml": "Orchestrator",
    "invalid-duplicate-tools.yaml": "duplicate",
    "invalid-bad-prompt-ref.yaml": "systemPromptRef",
    "invalid-extra-field.yaml": "bogusField",
}


def test_fixture_names_are_covered():
    files = sorted(n for n in os.listdir(FIXTURES)
                   if n.endswith((".yaml", ".yml", ".json")))
    assert set(files) == set(FIXTURE_EXPECTATIONS), (
        "add every new fixture to FIXTURE_EXPECTATIONS")


def _validate_fixture(name):
    schema = V.load_json(SCHEMA)
    catalog = V.load_yaml(CATALOG)
    data = _load_yaml(os.path.join(FIXTURES, name))
    return V.validate_profile_data(data, schema, catalog, label="fixture")


def test_unknown_tool_rejected():
    assert any("mindreader" in e
               for e in _validate_fixture("invalid-unknown-tool.yaml"))


def test_unknown_capability_rejected():
    assert any("time-travel" in e
               for e in _validate_fixture("invalid-unknown-capability.yaml"))


def test_unknown_guardrail_rejected():
    assert any("no-such-policy" in e
               for e in _validate_fixture("invalid-unknown-guardrail.yaml"))


def test_unknown_constraint_rejected():
    assert any("no-flying" in e
               for e in _validate_fixture("invalid-unknown-constraint.yaml"))


def test_bad_tier_rejected():
    assert any("ULTRA" in e
               for e in _validate_fixture("invalid-bad-tier.yaml"))


def test_bad_memory_scope_rejected():
    assert any("global" in e
               for e in _validate_fixture("invalid-bad-memory-scope.yaml"))


def test_missing_required_field_rejected():
    assert any("owner" in e
               for e in _validate_fixture("invalid-missing-field.yaml"))


def test_bad_version_rejected():
    assert any("1.0" in e
               for e in _validate_fixture("invalid-bad-version.yaml"))


def test_bad_id_rejected():
    assert any("Orchestrator" in e
               for e in _validate_fixture("invalid-bad-id.yaml"))


def test_duplicate_tools_rejected():
    assert any("duplicate" in e
               for e in _validate_fixture("invalid-duplicate-tools.yaml"))


def test_bad_prompt_ref_rejected():
    assert any("systemPromptRef" in e
               for e in _validate_fixture("invalid-bad-prompt-ref.yaml"))


def test_extra_field_rejected():
    assert any("bogusField" in e
               for e in _validate_fixture("invalid-extra-field.yaml"))


# --------------------------------------------------------------------------
# versioning / ledger immutability
# --------------------------------------------------------------------------

def test_ledger_refuses_mutation_of_published_seed(tmp_path):
    _copy_tree(tmp_path, "seeds", "coder.1.0.0.yaml")
    _copy_tree(tmp_path, "versions", "manifest.yaml")
    seed = os.path.join(str(tmp_path), "seeds", "coder.1.0.0.yaml")
    data = _load_yaml(seed)
    data["memoryScope"] = ["user"]
    _dump_yaml(seed, data)
    errs = V.ledger_errors(os.path.join(str(tmp_path), "seeds"),
                           os.path.join(str(tmp_path), "versions",
                                        "manifest.yaml"))
    assert any("IMMUTABLE" in e for e in errs), errs


def test_ledger_refuses_duplicate_published_version(tmp_path):
    _copy_tree(tmp_path, "seeds", "orchestrator.1.0.0.yaml")
    _copy_tree(tmp_path, "versions", "manifest.yaml")
    manifest = os.path.join(str(tmp_path), "versions", "manifest.yaml")
    data = _load_yaml(manifest)
    entry = dict(data["published"][0])
    data["published"].append(entry)  # duplicate (profile, version)
    _dump_yaml(manifest, data)
    errs = V.ledger_errors(os.path.join(str(tmp_path), "seeds"), manifest)
    assert any("duplicate published" in e for e in errs), errs


def test_ledger_accepts_unchanged_published_tree(tmp_path):
    for name in sorted(n for n in os.listdir(SEEDS) if n.endswith(".yaml")):
        _copy_tree(tmp_path, "seeds", name)
    _copy_tree(tmp_path, "versions", "manifest.yaml")
    errs = V.ledger_errors(os.path.join(str(tmp_path), "seeds"),
                           os.path.join(str(tmp_path), "versions",
                                        "manifest.yaml"))
    assert errs == [], "unchanged published tree must pass: %s" % errs


# --------------------------------------------------------------------------
# catalog / parity / seed-name consistency
# --------------------------------------------------------------------------

def test_seed_filename_must_match_declared_id(tmp_path):
    src = os.path.join(SEEDS, "coder.1.0.0.yaml")
    dst = os.path.join(str(tmp_path), "coder.1.0.0.yaml")
    shutil.copyfile(src, dst)
    data = _load_yaml(dst)
    data["id"] = "different-id"
    _dump_yaml(dst, data)
    schema = V.load_json(SCHEMA)
    catalog = V.load_yaml(CATALOG)
    errs = V.validate_seed_file(dst, schema, catalog)
    assert any("does not match filename id" in e for e in errs), errs


def test_parity_detects_catalog_only_id(tmp_path):
    schema = V.load_json(SCHEMA)
    catalog = _load_yaml(CATALOG)
    catalog["tools"]["brand_new_tool"] = {"summary": "added later"}
    errs = V.parity_errors(schema, catalog)
    assert any("parity" in e and "brand_new_tool" in e for e in errs), errs


def test_catalog_rejects_bundle_with_unknown_atomic(tmp_path):
    catalog = _load_yaml(CATALOG)
    catalog["guardrailPolicies"]["bundles"]["broken-bundle"] = {
        "policies": ["no-such-atomic-policy"]
    }
    errs = V.catalog_self_errors(catalog)
    assert any("no-such-atomic-policy" in e for e in errs), errs
