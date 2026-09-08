"""Controls-registry tests (issue #26 acceptance #3, AO-GR-6).

Every control is toggleable and ships OFF by default; a control that ships ON
must document on_since_rationale; the registry is validated against
schema/controls.schema.json and rejects unsafe states.
"""

from __future__ import annotations

import pytest

from policy import Control, ControlRegistry
from policy.errors import ControlError


def _valid_controls_doc() -> dict:
    return {
        "version": 1,
        "controls": [
            {
                "id": "sample-control",
                "name": "Sample control",
                "description": "A sample toggleable control.",
                "enabled": False,
                "mode": "block",
                "implemented_by": ["guardrails/policy/bundles/platform/sample.yaml"],
                "since": "issue #26",
            }
        ],
    }


def test_shipped_controls_all_default_off(shipped_controls):
    assert len(shipped_controls) == 3
    assert shipped_controls.active_ids() == ()
    for control in shipped_controls.all():
        assert control.enabled is False  # AO-GR-6: new controls ship OFF
        assert control.id in ("model-call-budget", "tool-use-guard", "data-egress-guard")


def test_registry_loads_from_mapping():
    registry = ControlRegistry.from_mapping(_valid_controls_doc())
    assert "sample-control" in registry
    assert not registry.is_active("sample-control")


def test_control_is_active_only_when_enabled():
    off = Control("c1", "Off", "d", False, "block", (), "t")
    on = Control("c2", "On", "d", True, "block", (), "t", on_since_rationale="prod")
    registry = ControlRegistry([off, on])
    assert registry.is_active("c1") is False
    assert registry.is_active("c2") is True
    assert registry.active_ids() == ("c2",)


def test_enabled_control_without_rationale_is_rejected():
    doc = _valid_controls_doc()
    doc["controls"][0]["enabled"] = True
    with pytest.raises(ControlError) as exc:
        ControlRegistry.from_mapping(doc)
    assert "on_since_rationale" in str(exc.value)


def test_mode_off_requires_disabled():
    doc = _valid_controls_doc()
    doc["controls"][0]["mode"] = "off"
    doc["controls"][0]["enabled"] = True
    with pytest.raises(ControlError) as exc:
        ControlRegistry.from_mapping(doc)
    assert "mode 'off'" in str(exc.value)


def test_duplicate_control_ids_are_rejected():
    doc = _valid_controls_doc()
    doc["controls"].append(dict(doc["controls"][0]))
    with pytest.raises(ControlError) as exc:
        ControlRegistry.from_mapping(doc)
    assert "duplicate" in str(exc.value)


def test_schema_rejects_bad_registry_shapes():
    from policy.schemas import SchemaValidationError, load_schema

    from policy.controls import _schema as controls_schema

    for bad in (
        {},  # missing version + controls
        {"version": 1, "controls": []},  # minItems 1
        {"version": 1, "controls": [{"id": "x"}]},  # missing required fields
        {"version": 1, "controls": [{"id": "X", "name": "n", "description": "d",
                                     "enabled": False, "mode": "block",
                                     "implemented_by": ["a"], "since": "t"}]},  # bad id pattern
        {"version": 1, "mystery": True, "controls": [dict(_valid_controls_doc()["controls"][0])]},
    ):
        with pytest.raises((SchemaValidationError, ControlError)):
            ControlRegistry.from_mapping(bad)


def test_registry_loads_shipped_yaml_from_disk(shipped_controls):
    assert len(shipped_controls.ids()) == 3


def test_unknown_control_id_is_absent():
    registry = ControlRegistry.from_mapping(_valid_controls_doc())
    assert "does-not-exist" not in registry
    assert registry.get("does-not-exist") is None
    assert registry.is_active("does-not-exist") is False
