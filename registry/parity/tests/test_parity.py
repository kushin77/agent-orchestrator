"""Behavioral tests for the registry <-> canonical CMR parity gate (issue #145).

These are REAL behavioral tests (they drive the parity library and the
validator code paths), not source-text greps. They prove:

  * default (offline) mode: registry mirrors the FROZEN baseline -> 0; registry
    drift -> 1; an edited / malformed baseline -> 1; a missing baseline -> 2;
  * ``--verify-source`` mode: the freeze matches the live source -> 0; a drifted
    source -> 1; a missing source -> 2 (never 0); the recorded source sha256 is
    actually checked (mutating it refuses);
  * the frozen baseline carries provenance (vendor/repo/path/sha256/refresh) and
    its payload digest is verified, so it cannot be edited silently;
  * a missing/unreadable canonical source is CANNOT-ASSESS (exit 2), never 0;
  * the two registry schemas disagreeing is NOT-OK;
  * a committed asset naming a non-canonical role/lane is NOT-OK;
  * ``weekly_spend_ceiling`` is a first-class non-negative numeric field and a
    negative value is refused (schema AND validator);
  * the backfilled vocabulary still validates every committed card and seed;
  * a mutation/negative control proves the parity comparison is not vacuous.

Run:  python3 -m pytest registry/parity/tests -q
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

import parity

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)                       # registry/parity
REPO = os.path.dirname(os.path.dirname(PKG))      # repo root
PROFILES_DIR = os.path.join(REPO, "registry", "profiles")
PERSONAS_DIR = os.path.join(REPO, "registry", "personas")

FIXTURES = os.path.join(PKG, "fixtures")
CANONICAL = os.path.join(FIXTURES, "canonical")
DRIFT_CANONICAL = os.path.join(FIXTURES, "drift-canonical")
REAL_CMR = os.path.join(REPO, "vendor", "CMR")
BASELINE = os.path.join(PKG, "canonical", "cmr-role-vocabulary.json")
MISSING_BASELINE = os.path.join(PKG, "canonical", "does-not-exist-baseline.json")
GATE = os.path.join(REPO, "scripts", "check-registry-parity.sh")

PROFILE_SCHEMA = os.path.join(PROFILES_DIR, "agent-profile.schema.json")
PERSONA_SCHEMA = os.path.join(PERSONAS_DIR, "persona-card.schema.json")
CATALOG = os.path.join(PROFILES_DIR, "catalog.yaml")
PROFILE_SEEDS = os.path.join(PROFILES_DIR, "seeds")
CARD_SCHEMA = os.path.join(PERSONAS_DIR, "persona-card.schema.json")
CARDS_DIR = os.path.join(PERSONAS_DIR, "cards")
PROFILE_FIXTURES = os.path.join(PROFILES_DIR, "tests", "fixtures")


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# Validator modules under test (the two layers the ceiling is enforced in).
V = _load_module("agent_profile_validate", os.path.join(PROFILES_DIR, "validate.py"))
REG = _load_module("persona_registry", os.path.join(PERSONAS_DIR, "registry.py"))


def _load_yaml(path):
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _make_registry(tmp_path, role_enum=None, persona_role_enum=None, cards=None):
    """Build a scratch registry root holding copies of the two schemas.

    ``role_enum`` / ``persona_role_enum`` override the ``roleId`` enum in the
    profile / persona schema respectively; ``cards`` is a mapping of filename ->
    card dict written under registry/personas/cards/.
    """
    root = tmp_path / "registry-root"
    prof = json.load(open(PROFILE_SCHEMA, encoding="utf-8"))
    pers = json.load(open(PERSONA_SCHEMA, encoding="utf-8"))
    if role_enum is not None:
        prof["definitions"]["roleId"]["enum"] = list(role_enum)
    if persona_role_enum is not None:
        pers["definitions"]["roleId"]["enum"] = list(persona_role_enum)
    prof_dir = root / "registry" / "profiles"
    pers_dir = root / "registry" / "personas"
    prof_dir.mkdir(parents=True)
    pers_dir.mkdir(parents=True)
    (prof_dir / "agent-profile.schema.json").write_text(
        json.dumps(prof, indent=2), encoding="utf-8")
    (pers_dir / "persona-card.schema.json").write_text(
        json.dumps(pers, indent=2), encoding="utf-8")
    if cards:
        cards_dir = pers_dir / "cards"
        cards_dir.mkdir()
        for name, card in cards.items():
            (cards_dir / name).write_text(
                yaml.safe_dump(card, sort_keys=False), encoding="utf-8")
    return str(root)


# --------------------------------------------------------------------------
# tri-state: OK
# --------------------------------------------------------------------------

def test_status_constants_are_tri_state():
    assert (parity.OK, parity.NOT_OK, parity.CANNOT_ASSESS) == (0, 1, 2)


def test_aligned_registry_is_ok_against_canonical_fixture():
    status, report = parity.evaluate(REPO, CANONICAL)
    assert status == parity.OK, report
    assert any("OK" in line for line in report)


def test_self_test_proves_both_drift_directions_fire():
    status, lines = parity.self_test(REPO, CANONICAL)
    assert status == parity.OK, lines
    assert any("caught=True" in line for line in lines)


# --------------------------------------------------------------------------
# tri-state: NOT-OK (drift, both directions, plus internal disagreement)
# --------------------------------------------------------------------------

def test_registry_only_role_is_not_ok(tmp_path):
    """A registry role CMR does not publish is drift (registry forked its taxonomy)."""
    extra = list(parity.canonical_vocab(CANONICAL)["roles"]) + ["registry-invented-role"]
    root = _make_registry(tmp_path, role_enum=extra)
    status, report = parity.evaluate(root, CANONICAL)
    assert status == parity.NOT_OK, report
    assert any("registry-invented-role" in line and "absent from canonical" in line
               for line in report), report


def test_canonical_only_role_is_not_ok():
    """A canonical role the registry never backfilled is drift (missing mirror)."""
    status, report = parity.evaluate(REPO, DRIFT_CANONICAL)
    assert status == parity.NOT_OK, report
    assert any("cmr-new-role" in line and "missing from the registry" in line
               for line in report), report


def test_profile_and_persona_schema_disagreement_is_not_ok(tmp_path):
    """Internal drift: the two registry schemas must declare the same role set."""
    status, report = parity.evaluate(
        _make_registry(tmp_path, persona_role_enum=["owner", "general"]), CANONICAL)
    assert status == parity.NOT_OK, report
    assert any("disagree" in line for line in report), report


def test_committed_asset_with_non_canonical_role_is_not_ok(tmp_path):
    """A card naming a role outside the canonical set fails membership."""
    card = {"id": "bogus", "role": "not-a-cmr-role"}
    root = _make_registry(tmp_path, cards={"bogus.yaml": card})
    status, report = parity.evaluate(root, CANONICAL)
    assert status == parity.NOT_OK, report
    assert any("bogus.yaml" in line and "not-a-cmr-role" in line for line in report), report


# --------------------------------------------------------------------------
# tri-state: CANNOT-ASSESS (never a pass)
# --------------------------------------------------------------------------

def test_missing_canonical_root_is_cannot_assess():
    status, report = parity.evaluate(REPO, os.path.join(REPO, "no-such-cmr-root"))
    assert status == parity.CANNOT_ASSESS, report
    assert "CANNOT-ASSESS" in report[0]


def test_missing_role_schema_is_cannot_assess(tmp_path):
    """A canonical root without the role schema is unreadable -> CANNOT-ASSESS."""
    root = tmp_path / "cmr"
    (root / "catalog").mkdir(parents=True)
    status, report = parity.evaluate(REPO, str(root))
    assert status == parity.CANNOT_ASSESS, report


def test_missing_catalog_dir_is_cannot_assess(tmp_path):
    """The canonical catalog directory is part of the source -> its absence refuses."""
    root = tmp_path / "cmr"
    (root / "onboarding" / "agent-profiles").mkdir(parents=True)
    src = os.path.join(CANONICAL, "onboarding", "agent-profiles", "role.schema.json")
    (root / "onboarding" / "agent-profiles" / "role.schema.json").write_text(
        open(src, encoding="utf-8").read(), encoding="utf-8")
    status, report = parity.evaluate(REPO, str(root))
    assert status == parity.CANNOT_ASSESS, report


def test_real_cmr_baseline_is_ok_when_the_submodule_is_populated():
    """In the main checkout vendor/CMR is populated and the registry mirrors it.

    Skipped in a fresh worktree, where the submodule is empty and the honest
    outcome is CANNOT-ASSESS.
    """
    role_schema = os.path.join(REAL_CMR, "onboarding", "agent-profiles",
                               "role.schema.json")
    if not os.path.isfile(role_schema):
        pytest.skip("vendor/CMR is unpopulated here (fresh worktree)")
    status, report = parity.evaluate(REPO, REAL_CMR)
    assert status == parity.OK, report


# --------------------------------------------------------------------------
# weekly_spend_ceiling: first-class, numeric, non-negative
# --------------------------------------------------------------------------

def _profile_fixture(**overrides):
    data = _load_yaml(os.path.join(PROFILE_SEEDS, "finops-steward.1.0.0.yaml"))
    data.update(overrides)
    return data


def test_weekly_spend_ceiling_is_a_declared_schema_field():
    schema = V.load_json(PROFILE_SCHEMA)
    assert "weekly_spend_ceiling" in schema["properties"]
    assert schema["definitions"]["weeklySpendCeiling"]["minimum"] == 0


def test_seed_carrying_a_ceiling_validates():
    schema = V.load_json(PROFILE_SCHEMA)
    catalog = V.load_yaml(CATALOG)
    errs = V.validate_profile_data(
        _profile_fixture(), schema, catalog, label="finops-steward")
    assert errs == [], errs


@pytest.mark.parametrize("value,fragment", [
    (-5, "non-negative"),
    ("twenty", "must be a number"),
    (True, "must be a number"),
])
def test_bad_ceiling_is_refused_by_the_validator(value, fragment):
    schema = V.load_json(PROFILE_SCHEMA)
    catalog = V.load_yaml(CATALOG)
    errs = V.validate_profile_data(
        _profile_fixture(weekly_spend_ceiling=value), schema, catalog,
        label="finops-steward")
    assert any(fragment in e for e in errs), errs


def test_negative_ceiling_is_refused_by_the_json_schema():
    import jsonschema
    schema = V.load_json(PROFILE_SCHEMA)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft7Validator(schema).validate(
            _profile_fixture(weekly_spend_ceiling=-1))


def test_negative_ceiling_fixture_is_rejected():
    schema = V.load_json(PROFILE_SCHEMA)
    catalog = V.load_yaml(CATALOG)
    data = _load_yaml(os.path.join(PROFILE_FIXTURES,
                                   "invalid-negative-spend-ceiling.yaml"))
    errs = V.validate_profile_data(data, schema, catalog, label="fixture")
    assert any("weekly_spend_ceiling" in e for e in errs), errs


def test_ceiling_is_enforced_on_persona_cards_too():
    schema = V.load_json(CARD_SCHEMA)
    catalog = REG.load_catalog()
    card = REG.card_from_yaml(Path(CARDS_DIR) / "platform-sme.yaml")
    good = dict(card)
    good["weekly_spend_ceiling"] = 42
    REG.validate_card(good, schema, catalog)
    bad = dict(card)
    bad["weekly_spend_ceiling"] = -1
    with pytest.raises(REG.InvalidCardError):
        REG.validate_card(bad, schema, catalog)


# --------------------------------------------------------------------------
# the backfilled vocabulary still validates every committed asset
# --------------------------------------------------------------------------

def test_backfilled_vocabulary_validates_every_committed_card():
    schema = REG.load_card_schema()
    catalog = REG.load_catalog()
    names = sorted(n for n in os.listdir(CARDS_DIR) if n.endswith(".yaml"))
    assert names, "no persona cards found"
    for name in names:
        card = _load_yaml(os.path.join(CARDS_DIR, name))
        REG.validate_card(card, schema, catalog)


def test_backfilled_vocabulary_validates_every_committed_seed():
    schema = V.load_json(PROFILE_SCHEMA)
    catalog = V.load_yaml(CATALOG)
    names = sorted(n for n in os.listdir(PROFILE_SEEDS) if n.endswith(".yaml"))
    assert names, "no profile seeds found"
    for name in names:
        errs = V.validate_seed_file(os.path.join(PROFILE_SEEDS, name), schema, catalog)
        assert errs == [], "%s: %s" % (name, errs)


# --------------------------------------------------------------------------
# mode 1 (default): registry <-> FROZEN baseline (offline, deterministic)
# --------------------------------------------------------------------------

def _scratch_baseline(tmp_path, cmr_root, name="baseline.json"):
    """Freeze a scratch baseline from ``cmr_root`` (a fixture source)."""
    doc = parity.refresh_baseline(cmr_root, extracted="2026-09-14")
    path = tmp_path / name
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return str(path)


def test_frozen_baseline_carries_provenance_and_passes_integrity():
    doc = parity.load_baseline(BASELINE)
    assert parity.check_baseline_integrity(doc) == []
    prov = doc["_provenance"]
    assert prov["vendor_repo"] == "kushin77/CMR"
    assert prov["source_path"] == "onboarding/agent-profiles/role.schema.json"
    assert len(prov["sha256"]) == 64
    assert prov["payload_sha256"] == parity._payload_sha256(parity.baseline_axes(doc))
    # the artifact itself says why it exists and how to refresh it
    assert "unpopulated" in prov["frozen_because"].lower()
    assert "--refresh-baseline" in prov["refresh_command"]


def test_default_mode_is_green_offline_with_no_vendor_source():
    """The whole point: registry vs frozen baseline, rc 0, without vendor/CMR."""
    status, report = parity.evaluate_offline(REPO, BASELINE)
    assert status == parity.OK, report


def test_default_mode_registry_drift_is_not_ok(tmp_path):
    """A registry role absent from the frozen canonical set is drift (rc 1)."""
    frozen_roles = list(parity.baseline_axes(parity.load_baseline(BASELINE))["roles"])
    root = _make_registry(tmp_path, role_enum=frozen_roles + ["registry-invented-role"])
    status, report = parity.evaluate_offline(root, BASELINE)
    assert status == parity.NOT_OK, report
    assert any("registry-invented-role" in line for line in report), report


def test_default_mode_missing_baseline_is_cannot_assess():
    status, report = parity.evaluate_offline(REPO, MISSING_BASELINE)
    assert status == parity.CANNOT_ASSESS, report
    assert status != parity.OK
    assert "CANNOT-ASSESS" in report[0]


def test_default_mode_edited_baseline_payload_is_not_ok(tmp_path):
    """Editing the frozen vocabulary without re-freezing must refuse (rc 1)."""
    doc = parity.load_baseline(BASELINE)
    doc["roles"] = list(doc["roles"]) + ["sneaky-role"]
    edited = tmp_path / "edited-baseline.json"
    edited.write_text(json.dumps(doc), encoding="utf-8")
    status, report = parity.evaluate_offline(REPO, str(edited))
    assert status == parity.NOT_OK, report
    assert any("payload_sha256 mismatch" in line for line in report), report


def test_default_mode_malformed_sha256_is_not_ok(tmp_path):
    """A baseline whose recorded source digest is malformed refuses (rc 1)."""
    doc = parity.load_baseline(BASELINE)
    doc["_provenance"]["sha256"] = "not-a-digest"
    edited = tmp_path / "malformed-sha.json"
    edited.write_text(json.dumps(doc), encoding="utf-8")
    status, report = parity.evaluate_offline(REPO, str(edited))
    assert status == parity.NOT_OK, report
    assert any("64-hex" in line for line in report), report


# --------------------------------------------------------------------------
# mode 2 (--verify-source): frozen baseline <-> live vendor/CMR source
# --------------------------------------------------------------------------

def test_verify_source_matches_the_fixture_source(tmp_path):
    baseline = _scratch_baseline(tmp_path, CANONICAL)
    status, report = parity.verify_source(baseline, CANONICAL)
    assert status == parity.OK, report


def test_verify_source_drifted_source_is_not_ok(tmp_path):
    """A live source the freeze no longer matches is stale -> rc 1."""
    baseline = _scratch_baseline(tmp_path, CANONICAL)
    status, report = parity.verify_source(baseline, DRIFT_CANONICAL)
    assert status == parity.NOT_OK, report
    assert any("stale freeze" in line or "cmr-new-role" in line for line in report), report


def test_verify_source_missing_source_is_cannot_assess_never_ok(tmp_path):
    baseline = _scratch_baseline(tmp_path, CANONICAL)
    status, report = parity.verify_source(
        baseline, os.path.join(REPO, "no-such-cmr-root"))
    assert status == parity.CANNOT_ASSESS, report
    assert status != parity.OK
    assert "CANNOT-ASSESS" in report[0]


def test_verify_source_recorded_sha256_is_actually_checked(tmp_path):
    """Mutating the recorded source sha256 must refuse, not pass silently."""
    baseline = _scratch_baseline(tmp_path, CANONICAL)
    doc = json.load(open(baseline, encoding="utf-8"))
    doc["_provenance"]["sha256"] = "0" * 64
    mutated = tmp_path / "mutated-sha.json"
    mutated.write_text(json.dumps(doc), encoding="utf-8")
    status, report = parity.verify_source(str(mutated), CANONICAL)
    assert status == parity.NOT_OK, report
    assert any("sha256" in line and "stale freeze" in line for line in report), report


def test_verify_source_missing_baseline_is_cannot_assess():
    status, report = parity.verify_source(MISSING_BASELINE, CANONICAL)
    assert status == parity.CANNOT_ASSESS, report


def test_refresh_baseline_round_trips(tmp_path):
    """The documented refresh command produces a baseline that then verifies."""
    path = _scratch_baseline(tmp_path, CANONICAL)
    doc = parity.load_baseline(path)
    assert parity.check_baseline_integrity(doc) == []
    status, report = parity.verify_source(path, CANONICAL)
    assert status == parity.OK, report


def test_frozen_baseline_matches_the_hermetic_canonical_fixture():
    committed = parity.baseline_axes(parity.load_baseline(BASELINE))
    fixture = parity.canonical_vocab(CANONICAL)
    for axis in parity.AXIS_ORDER:
        assert committed[axis] == fixture[axis], axis


def test_frozen_baseline_verifies_against_the_real_cmr_source_when_populated():
    role_schema = os.path.join(REAL_CMR, "onboarding", "agent-profiles",
                               "role.schema.json")
    if not os.path.isfile(role_schema):
        pytest.skip("vendor/CMR is unpopulated here (fresh worktree)")
    status, report = parity.verify_source(BASELINE, REAL_CMR)
    assert status == parity.OK, report


# --------------------------------------------------------------------------
# the shell gate wires those modes
# --------------------------------------------------------------------------

def test_shell_gate_default_mode_is_green_offline():
    proc = subprocess.run(["bash", GATE], cwd=REPO, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_shell_gate_verify_source_refuses_without_the_submodule():
    """In a fresh worktree vendor/CMR is empty -> rc 2, never 0."""
    role_schema = os.path.join(REAL_CMR, "onboarding", "agent-profiles",
                               "role.schema.json")
    if os.path.isfile(role_schema):
        pytest.skip("vendor/CMR is populated here; the refusal path is exercised elsewhere")
    proc = subprocess.run(["bash", GATE, "--verify-source"], cwd=REPO,
                          capture_output=True, text=True)
    assert proc.returncode == 2, proc.stdout + proc.stderr
