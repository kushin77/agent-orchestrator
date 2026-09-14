"""Behavioral tests for the registry <-> canonical CMR parity gate (issue #145).

These are REAL behavioral tests (they drive the parity library and the
validator code paths), not source-text greps. They prove:

  * parity is OK on the aligned vocabulary;
  * drift in EITHER direction is NOT-OK (exit 1);
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
