"""The shared seam, from the hermes side: the drift the extraction closed (#1208).

`integrations/hermes/` was written as a systematic copy of
`integrations/paperclip/`, and the copy had **already drifted**: paperclip's
validator enforced ``format: date-time``, ``minimum`` and ``maximum``, and this
adapter's did not. The extraction unifies *up* — this adapter now enforces the
superset — and the assertions below are the provocation that the drift is closed
rather than merely moved:

* `mapping.validate` is the seam's object (identity), and the helper bodies are
  gone from this module, so a re-forked copy fails here;
* each of the three keywords this adapter's own copy ignored is provoked;
* the transport is the seam's, over this adapter's boundary — and its boundary is
  the keyless one: no path prefix, no auth header.
"""

from __future__ import annotations

from pathlib import Path

from integrations import _seam
from integrations.hermes import client as client_mod
from integrations.hermes import mapping as mapping_mod
from integrations.hermes import model as model_mod

ROOT = Path(__file__).resolve().parents[3]

#: The helper bodies that used to be defined twice, once per adapter.
SHARED_HELPERS = (
    "_strip_comment",
    "_tokenize",
    "_split_kv",
    "_split_flow",
    "_parse_flow",
    "_scalar",
    "_parse_map",
    "_parse_seq",
    "_parse_block",
    "_type_ok",
    "_validate",
)


def test_the_shared_tools_are_the_seams_objects():
    assert mapping_mod.load_yaml is _seam.load_yaml
    assert mapping_mod.load_yaml_file is _seam.load_yaml_file
    assert mapping_mod.validate is _seam.validate
    assert model_mod.Response is _seam.Response
    assert client_mod.Transport is _seam.Transport


def test_the_adapter_no_longer_defines_the_shared_helpers():
    """A re-forked helper would shadow the seam's and re-open the drift."""
    for name in SHARED_HELPERS:
        assert not hasattr(mapping_mod, name), name
    assert not hasattr(client_mod, "_decode")


def test_the_validator_now_enforces_the_keywords_this_copy_had_drifted_below():
    """The three rules hermes's own copy ignored, one provocation each."""
    provocations = (
        ({"type": "string", "format": "date-time"}, "yesterday", "ISO-8601"),
        ({"type": "integer", "minimum": 10}, 3, "below minimum"),
        ({"type": "integer", "maximum": 100}, 101, "above maximum"),
    )
    for schema, value, expected in provocations:
        findings = mapping_mod.validate(value, schema)
        assert any(expected in finding for finding in findings), (schema, value, findings)


def test_the_validator_still_accepts_what_it_should():
    schema = {
        "type": "object",
        "required": ["default_tier", "max_tier"],
        "additionalProperties": False,
        "properties": {
            "default_tier": {"type": "string", "enum": ["L0", "L1", "L2"]},
            "max_tier": {"type": "string", "enum": ["L0", "L1", "L2"]},
        },
    }
    assert mapping_mod.validate({"default_tier": "L0", "max_tier": "L1"}, schema) == []
    findings = mapping_mod.validate({"default_tier": "L0", "max_tier": "L1", "x": 1}, schema)
    assert any("unexpected field 'x'" in finding for finding in findings), findings


def test_the_shared_loader_reads_the_flow_rows_tiers_yaml_writes():
    """``gateway/finops/tiers.yaml`` writes its rows as inline flow mappings."""
    data = mapping_mod.load_yaml(
        "tiers:\n"
        "  - { id: deepseek-v4-flash, provider: deepseek, costPerMTok: 0.21 }\n"
    )
    assert data == {
        "tiers": [{"id": "deepseek-v4-flash", "provider": "deepseek", "costPerMTok": 0.21}]
    }
    assert mapping_mod.read_tiers(ROOT), "the real tiers source still parses"


def test_the_fixture_transport_is_the_seams_over_a_keyless_boundary():
    assert issubclass(client_mod.FixtureTransport, _seam.FixtureTransport)
    assert client_mod.FixtureTransport is not _seam.FixtureTransport, (
        "this adapter binds the seam to its own error vocabulary"
    )
    transport = client_mod.FixtureTransport(
        {"responses": [{"method": "GET", "path": "/health", "status": 200, "body": {"status": "ok"}}]}
    )
    assert transport.prefix == "", "the keyless service's /health is root-level"
    assert transport.boundary.label == "hermes"
    assert client_mod.HermesClient(transport=transport).health().body == {"status": "ok"}


def test_error_for_status_renders_through_the_shared_boundary():
    unavailable = model_mod.error_for_status(503, "/health", "not deployed")
    assert isinstance(unavailable, model_mod.UnavailableError)
    assert str(unavailable) == "hermes /health: HTTP 503 — not deployed"
    assert model_mod.error_for_status(200, "/health").args[0] == "hermes /health: HTTP 200"
