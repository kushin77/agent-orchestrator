"""Schema-subset validator tests: required fields + closed vocab (issue #428)."""

from __future__ import annotations

from pathlib import Path

from integrations.paperclip import mapping as mapping_mod

ROOT = Path(__file__).resolve().parents[3]


def test_valid_record_passes():
    ticket = {
        "id": "kushin77/agent-orchestrator#428",
        "owner": "orchestrator",
        "status": "in-progress",
        "blocked_by": [],
        "goal": "EPIC-00",
        "evidence": ["kushin77/agent-orchestrator#428"],
    }
    schema = mapping_mod.load_schema(ROOT, "ticket")
    assert mapping_mod.validate(ticket, schema) == []


def test_dropped_required_field_is_refused_by_name():
    ticket = {
        "id": "x",
        "status": "in-progress",
        "blocked_by": [],
        "goal": "g",
        "evidence": ["e"],
    }
    schema = mapping_mod.load_schema(ROOT, "ticket")
    findings = mapping_mod.validate(ticket, schema)
    assert any("required field 'owner' is missing" in f for f in findings), findings


def test_closed_vocabulary_value_is_refused_by_name():
    ticket = {
        "id": "x",
        "owner": "o",
        "status": "started",
        "blocked_by": [],
        "goal": "g",
        "evidence": ["e"],
    }
    schema = mapping_mod.load_schema(ROOT, "ticket")
    findings = mapping_mod.validate(ticket, schema)
    assert any("closed vocabulary" in f for f in findings), findings


def test_unexpected_field_is_refused():
    heartbeat = {
        "agent_id": "monitor",
        "session_id": "s",
        "tick": 0,
        "cadence_seconds": 20,
        "wake": {"cause": "scheduled", "delta": {}},
        "outcome": {"status": "progress", "owner": "fleet/platform"},
        "ts": "2026-09-14T00:00:00Z",
        "extra": True,
    }
    schema = mapping_mod.load_schema(ROOT, "heartbeat")
    findings = mapping_mod.validate(heartbeat, schema)
    assert any("unexpected field 'extra'" in f for f in findings), findings


def test_pattern_and_datetime_and_bounds():
    budget = {
        "scope": {"level": "team", "id": "acme"},
        "period": "month",
        "cap": 10.0,
        "spent": -1.0,
        "currency": "usd",
        "hard_stop": True,
        "burn_rate_alert_pct": 150.0,
        "receipt_ref": "r",
    }
    schema = mapping_mod.load_schema(ROOT, "budget")
    findings = mapping_mod.validate(budget, schema)
    joined = "\n".join(findings)
    assert "below minimum" in joined
    assert "above maximum" in joined
    assert "does not match pattern" in joined


def test_real_schemas_load():
    schemas = mapping_mod.load_schemas(ROOT)
    assert set(schemas) == {"heartbeat", "ticket", "budget"}
    for schema in schemas.values():
        assert schema["type"] == "object"
        assert isinstance(schema["required"], list)
