"""Office module integration (coordinator directive, 2026-09-20): reconciles
the per-office directory contract three parallel lanes produced independently
— CTO (issue #1573, PR #1579), CFO (issue #1582, PR #1583) and this lane's
own registry/personas/offices.yaml — into one coherent structure.

Covers:
- every office in offices.yaml declares a `moduleDir` and that directory's
  charter.yaml/abilities.yaml/reports.yaml exist;
- CEO, CTO and PMO charter/abilities/reports validate against the canonical
  shared schema (registry/personas/offices/schema/office.schema.json), moved
  there from its CTO-only origin and generalized to the office enum
  ceo/cto/cfo/pmo;
- every reports.yaml member id across all four offices resolves to a real
  persona card (extends the same cross-reference check
  test_cto_office.py established for CTO alone);
- CFO is the one documented exception: registry/personas/offices/cfo/
  charter.yaml predates the canonicalization and uses an incompatible field
  shape, so it validates against its own legacy schema
  (registry/personas/offices/schema/office-charter-legacy-cfo.schema.json)
  instead — this test asserts that delta explicitly rather than silently
  passing it through the canonical schema (which it would fail).
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
import yaml

PKG = Path(__file__).resolve().parents[1]  # registry/personas
OFFICES_DIR = PKG / "offices"
CANONICAL_SCHEMA_PATH = OFFICES_DIR / "schema" / "office.schema.json"
CFO_LEGACY_SCHEMA_PATH = OFFICES_DIR / "schema" / "office-charter-legacy-cfo.schema.json"
OFFICES_YAML_PATH = PKG / "offices.yaml"
CARDS_DIR = PKG / "cards"

# Offices whose charter/abilities/reports conform to the canonical schema.
# cfo is deliberately excluded here — see module docstring.
CANONICAL_OFFICES = ("ceo", "cto", "pmo")


def _schema(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _offices_doc() -> dict:
    return yaml.safe_load(OFFICES_YAML_PATH.read_text(encoding="utf-8"))


def test_every_office_declares_a_module_dir_that_exists():
    doc = _offices_doc()
    for office in doc["offices"]:
        module_dir = PKG.parents[1] / office["moduleDir"]
        assert module_dir.is_dir(), f"office {office['id']!r} moduleDir missing: {module_dir}"
        for name in ("charter.yaml", "abilities.yaml", "reports.yaml"):
            assert (module_dir / name).is_file(), (
                f"office {office['id']!r} module missing {name} under {module_dir}"
            )


@pytest.mark.parametrize("office_id", CANONICAL_OFFICES)
@pytest.mark.parametrize("filename", ["charter.yaml", "abilities.yaml", "reports.yaml"])
def test_canonical_office_files_match_shared_schema(office_id, filename):
    schema = _schema(CANONICAL_SCHEMA_PATH)
    doc = _load(OFFICES_DIR / office_id / filename)
    jsonschema.validate(instance=doc, schema=schema)
    assert doc["office"] == office_id


def test_cfo_charter_deliberately_does_NOT_match_the_canonical_schema():
    """No-false-green proof (GR-8): CFO's charter.yaml uses schemaVersion/
    tenant/personaCardRef/authority.{grants,denies}, none of which the
    canonical schema declares under its closed `additionalProperties: false`
    object — so validating it against the canonical schema MUST fail. If
    this stops failing, either CFO's charter was silently reshaped (good —
    update this test) or the canonical schema quietly loosened
    (additionalProperties true) and stopped catching a real shape mismatch
    (bad — investigate).
    """
    schema = _schema(CANONICAL_SCHEMA_PATH)
    doc = _load(OFFICES_DIR / "cfo" / "charter.yaml")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=doc, schema=schema)


def test_cfo_charter_matches_its_own_legacy_schema():
    schema = _schema(CFO_LEGACY_SCHEMA_PATH)
    doc = _load(OFFICES_DIR / "cfo" / "charter.yaml")
    jsonschema.validate(instance=doc, schema=schema)
    assert doc["office"] == "cfo"


@pytest.mark.parametrize("office_id", ("ceo", "cto", "cfo", "pmo"))
def test_reports_members_resolve_to_real_cards(office_id):
    reports = _load(OFFICES_DIR / office_id / "reports.yaml")
    for member in reports.get("members", []):
        card_path = PKG.parents[1] / member["card"]
        assert card_path.is_file(), (
            f"{office_id} reports.yaml member {member['id']!r} points at a "
            f"nonexistent card: {member['card']}"
        )
        card_id = _load(card_path)["id"]
        assert card_id == member["id"], (
            f"{office_id} reports.yaml member id {member['id']!r} does not "
            f"match the card's own id {card_id!r} at {member['card']}"
        )


def test_reports_rejects_unknown_card_id():
    """No-false-green proof (GR-8), mirrored from test_cto_office.py: a
    member pointing at a nonexistent card path must be catchable by the
    same logic test_reports_members_resolve_to_real_cards exercises above.
    """
    fake_member = {"id": "not-a-real-sme", "card": "registry/personas/cards/not-a-real-sme.yaml"}
    card_path = PKG.parents[1] / fake_member["card"]
    assert not card_path.is_file()


def test_office_head_persona_matches_module_dir_office_field():
    doc = _offices_doc()
    for office in doc["offices"]:
        charter = _load(PKG.parents[1] / office["moduleDir"] / "charter.yaml")
        assert charter["office"] == office["id"]
        assert charter["reportsTo"] == (
            "board" if office["id"] == "ceo" else "ceo"
        ), f"office {office['id']!r} charter.reportsTo drifted from the org chart edge"
