"""The validator enforces what it says, and refuses what it cannot (issue #654)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from integrations.erp.finops import schema as schemas
from integrations.erp.finops.model import Refused

TINY = {
    "$schema": schemas.DIALECT,
    "type": "object",
    "required": ["kind"],
    "additionalProperties": False,
    "properties": {
        "kind": {"type": "string", "minLength": 1, "pattern": "^[a-z-]+$"},
        "price": {"type": "number", "minimum": 0},
        "unpriced": {"type": "boolean"},
    },
}


def test_a_valid_instance_reports_no_violations() -> None:
    assert schemas.validate({"kind": "sales-order", "price": 0.5}, TINY) == []


def test_each_violation_names_the_field() -> None:
    violations = schemas.validate({"kind": "", "price": -1, "extra": 1}, TINY)
    joined = " ".join(violations)
    assert "document.kind" in joined
    assert "document.price" in joined
    assert "unexpected property 'extra'" in joined


def test_a_missing_required_property_is_named() -> None:
    assert any("missing required property 'kind'" in item for item in schemas.validate({}, TINY))


def test_a_bool_is_not_a_number() -> None:
    # True is an int subclass; a schema demanding a number is not satisfied by it.
    violations = schemas.validate({"kind": "k", "price": True}, TINY)
    assert any("expected number" in item for item in violations)


def test_an_unsupported_keyword_is_refused_rather_than_ignored() -> None:
    with pytest.raises(Refused) as caught:
        schemas.assert_supported({"type": "object", "oneOf": [{}]}, where="scratch")
    assert caught.value.code == "unsupported-schema-keyword"
    assert "oneOf" in caught.value.detail


def test_the_freeze_walks_nested_schemas() -> None:
    nested = {"type": "object", "properties": {"x": {"type": "string", "format": "date"}}}
    problems = schemas.check_schema(nested, where="scratch")
    assert problems and "properties.x" in problems[0]


def test_every_schema_this_lane_ships_passes_its_own_freeze() -> None:
    directory = Path(__file__).resolve().parents[1] / "schema"
    names = sorted(path.name for path in directory.glob("*.json"))
    assert names, "the lane ships no schemas"
    for name in names:
        schemas.load_and_refuse(directory / name)


def test_a_schema_file_that_is_not_an_object_is_a_hard_error(tmp_path) -> None:
    path = tmp_path / "bad.schema.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON object"):
        schemas.load(path)
