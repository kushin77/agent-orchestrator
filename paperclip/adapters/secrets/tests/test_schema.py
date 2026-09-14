"""Schema-level tests for the reference/rotation view (issue #417)."""

from __future__ import annotations

import copy
import json

from paperclip.adapters.secrets import vault
from paperclip.adapters.secrets.model import GSM_STORE


def _record(view):
    return copy.deepcopy(view["secrets"][0])


def test_schema_is_json_and_declares_the_store_as_a_constant(schema):
    assert schema["properties"]["store"]["const"] == GSM_STORE
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["store", "secrets"]


def test_record_schema_forbids_a_value_property(schema):
    record = schema["$defs"]["secret_view"]
    assert record["additionalProperties"] is False
    assert "value" not in record["properties"]
    assert record["properties"]["store"]["const"] == GSM_STORE


def test_gsm_path_shape_is_asserted_by_the_schema(schema):
    assert schema["$defs"]["secret_view"]["properties"]["gsm_path"]["pattern"].startswith("^projects/")


def test_a_conforming_view_validates(schema, view):
    assert vault.validate(view, schema) == []


def test_schema_refuses_a_carried_value(schema, view):
    mutant = copy.deepcopy(view)
    mutant["secrets"][0]["value"] = "fake-placeholder-never-a-credential"
    findings = vault.validate(mutant, schema)
    assert any("unexpected field 'value'" in finding for finding in findings)


def test_schema_refuses_a_second_store(schema, view):
    mutant = copy.deepcopy(view)
    mutant["secrets"][0]["store"] = "upstream-paperclip-secret-store"
    findings = vault.validate(mutant, schema)
    assert any("expected the constant 'google-secret-manager'" in finding for finding in findings)


def test_schema_refuses_a_missing_required_record_field(schema, view):
    mutant = copy.deepcopy(view)
    del mutant["secrets"][0]["consumer"]
    findings = vault.validate(mutant, schema)
    assert any("required field 'consumer' is missing" in finding for finding in findings)


def test_schema_admits_a_null_consumer_and_a_null_rotation_time(schema, view):
    mutant = copy.deepcopy(view)
    mutant["secrets"][0]["consumer"] = None
    mutant["secrets"][0]["last_rotated_at"] = None
    mutant["secrets"][0]["rotated"] = False
    assert vault.validate(mutant, schema) == []


def test_schema_refuses_a_non_string_consumer(schema, view):
    mutant = copy.deepcopy(view)
    mutant["secrets"][0]["consumer"] = 7
    findings = vault.validate(mutant, schema)
    assert any("secrets[0].consumer" in finding and "expected" in finding for finding in findings)


def test_schema_refuses_a_non_instant_rotation_time(schema, view):
    mutant = copy.deepcopy(view)
    mutant["secrets"][0]["last_rotated_at"] = "last tuesday"
    findings = vault.validate(mutant, schema)
    assert any("is not an ISO-8601 date-time" in finding for finding in findings)


def test_a_view_that_is_not_an_object_is_refused(schema):
    assert vault.validate_view([], schema) == ["view: expected an object, got list"]


def test_load_schema_reads_the_committed_file(repo_root):
    loaded = vault.load_schema(repo_root)
    on_disk = json.loads((repo_root / vault.SCHEMA_REL).read_text(encoding="utf-8"))
    assert loaded == on_disk
