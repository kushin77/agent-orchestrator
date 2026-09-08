"""Typed-output validation tests - fail closed (issue #15, criterion 1).

Every provider adapter must validate the model's typed output against the
caller's output schema. Invalid output is NEVER silently passed through:
non-JSON content, missing required fields, wrong enum values and undeclared
properties all raise ``OutputValidationError``. A schema that is not usable
JSON Schema raises ``SchemaDefinitionError``. Schemas load from a dict or
from a ``.json`` schema file (the issue-#13 prompt-module ``outputSchema``
reference shape).
"""

from __future__ import annotations

import pytest

from providers.errors import OutputValidationError, SchemaDefinitionError
from providers.schema import (
    OutputSchemaValidator,
    load_schema,
    parse_and_validate,
)

VALID = '{"sentiment": "positive", "confidence": 0.92}'
VALID_OBJ = {"sentiment": "positive", "confidence": 0.92}


def test_valid_payload_passes(sentiment_schema) -> None:
    assert parse_and_validate(VALID, sentiment_schema) == VALID_OBJ


def test_schema_none_returns_raw_text() -> None:
    assert parse_and_validate("plain text reply", None) == "plain text reply"


def test_fenced_json_passes(sentiment_schema) -> None:
    fenced = '```json\n' + VALID + '\n```'
    assert parse_and_validate(fenced, sentiment_schema) == VALID_OBJ


def test_prose_wrapped_json_passes(sentiment_schema) -> None:
    prose = 'Here is the verdict:\n' + VALID
    assert parse_and_validate(prose, sentiment_schema) == VALID_OBJ


def test_non_json_content_fails_closed(sentiment_schema) -> None:
    with pytest.raises(OutputValidationError):
        parse_and_validate("I am sorry, I cannot do that", sentiment_schema)


def test_wrong_enum_fails_closed(sentiment_schema) -> None:
    with pytest.raises(OutputValidationError):
        parse_and_validate('{"sentiment": "maybe", "confidence": 0.5}', sentiment_schema)


def test_missing_required_field_fails_closed(sentiment_schema) -> None:
    with pytest.raises(OutputValidationError):
        parse_and_validate('{"sentiment": "positive"}', sentiment_schema)


def test_undeclared_property_fails_closed(sentiment_schema) -> None:
    with pytest.raises(OutputValidationError):
        parse_and_validate(
            '{"sentiment": "positive", "confidence": 0.5, "evil": true}',
            sentiment_schema,
        )


def test_confidence_out_of_range_fails_closed(sentiment_schema) -> None:
    with pytest.raises(OutputValidationError):
        parse_and_validate('{"sentiment": "positive", "confidence": 9.0}', sentiment_schema)


def test_file_backed_schema_loads_and_validates(sentiment_schema, sentiment_schema_file) -> None:
    schema = load_schema(sentiment_schema_file)
    assert schema == sentiment_schema
    assert parse_and_validate(VALID, schema) == VALID_OBJ


def test_validator_rejects_malformed_schema() -> None:
    with pytest.raises(SchemaDefinitionError):
        OutputSchemaValidator({"type": "object", "properties": "not-a-map"})


def test_parse_and_validate_rejects_non_dict_schema_source() -> None:
    with pytest.raises(SchemaDefinitionError):
        parse_and_validate(VALID, "not a schema")  # type: ignore[arg-type]
