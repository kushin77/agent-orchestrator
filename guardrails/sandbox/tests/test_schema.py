"""Contract schema tests: packaged documents validate; bad documents fail.

These prove the JSON Schema contract is real: the packaged profiles.yaml and
categories.yaml validate against their schemas, and documents that violate
each enforced rule (unknown network mode, unknown/extra keys, missing
categories, out-of-range quotas, bad types) genuinely produce errors - the
validator is not a formality.
"""

from __future__ import annotations

import os

from sandbox.catalog import read_yaml_document
from sandbox.schema import (
    is_valid,
    load_schema,
    validate_document,
)

_HERE = os.path.dirname(os.path.abspath(__file__))
_SANDBOX = os.path.dirname(_HERE)
PROFILES_YAML = os.path.join(_SANDBOX, "profiles.yaml")
CATEGORIES_YAML = os.path.join(_SANDBOX, "categories.yaml")


def test_packaged_profiles_document_validates():
    doc = read_yaml_document(PROFILES_YAML)
    assert is_valid(doc, load_schema("profiles"))
    assert is_valid(
        doc["profiles"]["restricted"], load_schema("security-profile")
    )


def test_packaged_categories_document_validates():
    doc = read_yaml_document(CATEGORIES_YAML)
    assert is_valid(doc, load_schema("categories"))


def test_profile_schema_rejects_unknown_network_mode():
    doc = read_yaml_document(PROFILES_YAML)
    doc["profiles"]["restricted"]["networkMode"] = "internet"
    errors = validate_document(doc, load_schema("profiles"))
    assert errors and any("networkMode" in e or "enum" in e for e in errors)


def test_profile_schema_rejects_extra_key():
    doc = read_yaml_document(PROFILES_YAML)
    doc["profiles"]["standard"]["privilegedEscape"] = False
    errors = validate_document(doc, load_schema("profiles"))
    assert errors and any("privilegedEscape" in e for e in errors)


def test_profile_schema_rejects_negative_quota():
    doc = read_yaml_document(PROFILES_YAML)
    doc["profiles"]["standard"]["memoryMb"] = -1
    errors = validate_document(doc, load_schema("profiles"))
    assert errors and any("memoryMb" in e for e in errors)


def test_profile_schema_rejects_non_integer_quota():
    doc = read_yaml_document(PROFILES_YAML)
    doc["profiles"]["standard"]["cpuQuota"] = "lots"
    errors = validate_document(doc, load_schema("profiles"))
    assert errors and any("type" in e or "integer" in e for e in errors)


def test_categories_schema_requires_the_closed_category_set():
    doc = read_yaml_document(CATEGORIES_YAML)
    del doc["categories"]["docker"]
    errors = validate_document(doc, load_schema("categories"))
    assert errors and any("docker" in e for e in errors)


def test_categories_schema_rejects_bad_default():
    doc = read_yaml_document(CATEGORIES_YAML)
    doc["defaultProfile"] = "wide-open"
    errors = validate_document(doc, load_schema("categories"))
    assert errors and any("enum" in e for e in errors)


def test_categories_schema_rejects_profile_value_not_in_enum():
    doc = read_yaml_document(CATEGORIES_YAML)
    doc["categories"]["k8s"] = "root"
    errors = validate_document(doc, load_schema("categories"))
    assert errors and any("root" in e for e in errors)


def test_schema_files_are_valid_json_on_disk():
    for name in (
        "security-profile",
        "profiles",
        "categories",
        "microvm",
    ):
        schema = load_schema(name)
        assert schema["type"] == "object"
        assert "$schema" in schema


def test_type_errors_are_reported():
    schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}
    errors = validate_document({"a": 3}, schema)
    assert errors and any("type" in e for e in errors)
    assert is_valid({"a": "ok"}, schema)
