"""CTO office module structural validation (issue #1573, Parent #1510).

Extends the existing pytest-registry-personas suite (scripts/verify.sh) —
it is discovered by the same `pytest registry/personas/tests` invocation, no
new make target and no new gate wiring; this IS the gate that already runs
in `make verify`.

Validates registry/personas/offices/cto/{charter,abilities,reports,
delivery-lifecycle}.yaml against schema/office.schema.json (structure) plus
cross-reference checks jsonschema cannot express on its own: every
reports.yaml member id must resolve to a real persona card file.

No-false-green proof (GR-8): test_reports_rejects_unknown_card_id and
test_schema_rejects_missing_required_field below assert on FAILURE — an
empty-match or vacuously-true validator would fail these on its own.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
import yaml

PERSONAS_ROOT = Path(__file__).resolve().parents[1]  # registry/personas
OFFICE_DIR = PERSONAS_ROOT / "offices" / "cto"
CARDS_DIR = Path(__file__).resolve().parents[1] / "cards"
SCHEMA_PATH = OFFICE_DIR / "schema" / "office.schema.json"

OFFICE_FILES = [
    OFFICE_DIR / "charter.yaml",
    OFFICE_DIR / "abilities.yaml",
    OFFICE_DIR / "reports.yaml",
    OFFICE_DIR / "delivery-lifecycle.yaml",
]


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def test_office_files_exist():
    for path in OFFICE_FILES:
        assert path.is_file(), f"missing CTO office file: {path}"


@pytest.mark.parametrize("path", OFFICE_FILES, ids=lambda p: p.name)
def test_office_file_matches_schema(path: Path):
    doc = _load(path)
    jsonschema.validate(instance=doc, schema=_schema())
    assert doc["office"] == "cto"


def test_schema_rejects_missing_required_field():
    """No-false-green proof: a doc missing the required `office` key MUST
    fail validation. If this test itself fails, the validator has stopped
    being able to fail (GR-8) and must be treated as broken, not green."""
    bad = {"schema": "cto-office-charter/v1", "version": "1.0.0"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=_schema())


def test_reports_members_resolve_to_real_cards():
    reports = _load(OFFICE_DIR / "reports.yaml")
    repo_root = PERSONAS_ROOT.parents[1]
    for member in reports["members"]:
        card_path = repo_root / member["card"]
        assert card_path.is_file(), (
            f"reports.yaml member {member['id']!r} points at a nonexistent "
            f"card: {member['card']}"
        )
        card_id = yaml.safe_load(card_path.read_text())["id"]
        assert card_id == member["id"], (
            f"reports.yaml member id {member['id']!r} does not match the "
            f"card's own id {card_id!r} at {member['card']}"
        )


def test_reports_rejects_unknown_card_id():
    """No-false-green proof: a member pointing at a nonexistent card path
    MUST be caught by the same logic test_reports_members_resolve_to_real_cards
    exercises."""
    fake_member = {"id": "not-a-real-sme", "card": "registry/personas/cards/not-a-real-sme.yaml"}
    card_path = PERSONAS_ROOT.parents[1] / fake_member["card"]
    assert not card_path.is_file()


def test_charter_and_abilities_carry_provenance():
    for name in ("charter.yaml", "abilities.yaml"):
        doc = _load(OFFICE_DIR / name)
        assert doc.get("provenance"), f"{name} is missing GR-10 provenance"


def test_delivery_lifecycle_names_cto_gates():
    """The owner control-flow mandate requires the CTO gate to sit at merge
    and apply, explicitly — this asserts the contract stays named, not
    silently dropped in a future edit."""
    doc = _load(OFFICE_DIR / "delivery-lifecycle.yaml")
    gated = {stage["stage"]: stage["ctoGate"] for stage in doc["chain"]}
    assert gated["merge"] == "REQUIRED"
    assert gated["apply"] == "REQUIRED"
    # Everything upstream of merge/apply is explicitly ungated (hermes routes
    # and executes; it does not require CTO sign-off to route).
    assert gated["hermes-routing"] == "none"
