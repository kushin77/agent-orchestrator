"""The schema is a REAL JSON Schema, not a dialect of our own.

`render.py` validates with a stdlib-only subset validator (that is the no-network
constraint on the lane). That leaves an obvious way to fool ourselves: a schema
document that only our validator understands. This module removes that doubt --
it hands the same committed documents to the `jsonschema` library, and if that
library is not installed the cross-check is skipped and reported as skipped
rather than silently passed.

The `$defs` shared by all four documents are resolved against the bundle root
here too, by folding them into the per-document schema before validation.
"""

from __future__ import annotations

import copy
import os

import pytest

import render

jsonschema = pytest.importorskip(
    "jsonschema", reason="cross-check needs the jsonschema library (stdlib-only lane)"
)


def _standalone(bundle, kind):
    schema = copy.deepcopy(bundle[kind])
    schema["$defs"] = copy.deepcopy(bundle["$defs"])
    schema["$schema"] = bundle["$schema"]
    return schema


def _validate_with_jsonschema(document, schema):
    validator = jsonschema.Draft202012Validator(schema)
    return [error.message for error in validator.iter_errors(document)]


@pytest.mark.parametrize("kind", ["template", "params", "run_state_document", "instance"])
def test_the_library_accepts_the_committed_documents(kind, bundle, lane_root, template, committed_instance):
    documents = {"template": template}
    if kind != "template":
        params = render.load_yaml(
            os.path.join(lane_root, render.PILOTS_DIR, "agent-orchestrator.params.yaml")
        )
        if kind == "params":
            documents = {kind: params}
        elif kind == "run_state_document":
            documents = {
                kind: render.load_yaml(os.path.join(lane_root, render.PILOTS_DIR, params["observations"]))
            }
        else:
            documents = {kind: committed_instance("agent-orchestrator")}

    assert _validate_with_jsonschema(documents[kind], _standalone(bundle, kind)) == []


def test_our_validator_and_the_library_agree_on_a_corrupted_instance(bundle, committed_instance):
    instance = committed_instance("agent-orchestrator")
    instance["definition"]["roles"][0]["state"] = "running"

    ours = render.validate(instance, bundle["instance"], bundle)
    theirs = _validate_with_jsonschema(instance, _standalone(bundle, "instance"))
    assert ours, "the lane's own validator accepted a bad run-state value"
    assert theirs, "the library accepted a bad run-state value"


def test_our_validator_and_the_library_agree_on_an_unknown_key(bundle, committed_instance):
    instance = committed_instance("shared-frontend")
    instance["extra_key"] = "nope"

    assert render.validate(instance, bundle["instance"], bundle)
    assert _validate_with_jsonschema(instance, _standalone(bundle, "instance"))


def test_the_schema_declares_the_expected_documents(bundle):
    assert bundle["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    for kind in ("template", "params", "run_state_document", "instance"):
        assert "type" in bundle[kind], f"{kind} is not a schema object"
        assert bundle[kind]["type"] == "object"
