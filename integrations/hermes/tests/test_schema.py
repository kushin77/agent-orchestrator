"""Schema tests: capabilities.schema.json is a live, enforced artifact.

The schema is the surface's `schema` evidence (the `faang` rung), so it must be
more than a named file: these tests load it and prove it accepts a valid
capability tier-entry and refuses a forged one.
"""

from __future__ import annotations

import json
from pathlib import Path

from integrations.hermes import mapping as mapping_mod

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "capabilities.schema.json"


def _schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_schema_file_exists_and_is_valid_json():
    assert SCHEMA_PATH.is_file()
    schema = _schema()
    assert schema.get("type") == "object"
    assert schema.get("additionalProperties") is False
    assert "default_tier" in schema.get("required", ())
    assert "max_tier" in schema.get("required", ())


def test_schema_accepts_a_valid_capability_entry():
    schema = _schema()
    assert mapping_mod.validate(
        {"default_tier": "L0", "max_tier": "L1"}, schema
    ) == []


def test_schema_refuses_a_forged_tier():
    schema = _schema()
    findings = mapping_mod.validate(
        {"default_tier": "L9", "max_tier": "L1"}, schema
    )
    assert any("L9" in finding for finding in findings), findings
