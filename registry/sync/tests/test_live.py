"""registry/sync live hermes head-agent registration (issue #889)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from sync.live import HeadRegistrationError, register_head_agent

REGISTRY_ROOT = Path(__file__).resolve().parents[2]


def _copy_registry_tree(tmp_path: Path) -> Path:
    """A real, self-contained copy of registry/{profiles,personas,events} —
    offline, no network, but genuine schema/catalog files, not fabricated
    fixtures."""
    dest = tmp_path / "registry"
    for sub in ("profiles", "personas", "events"):
        shutil.copytree(REGISTRY_ROOT / sub, dest / sub)
    return dest


def test_register_head_agent_against_the_real_committed_hermes_files():
    """No copy, no fixture: the actual seed profile + persona card must validate."""
    doc = register_head_agent(REGISTRY_ROOT)
    assert doc["registered"] is True
    assert doc["agent_id"] == "hermes"


def test_register_head_agent_appends_a_register_event(tmp_path):
    from events.event_log import EventLog  # registry/events/event_log.py

    registry_root = _copy_registry_tree(tmp_path)
    log = EventLog(path=str(tmp_path / "events.jsonl"))
    doc = register_head_agent(registry_root, event_log=log)
    assert doc["event"]["event"] == "register"
    assert doc["event"]["agentId"] == "hermes"
    assert len(log) == 1


def test_missing_head_identity_field_is_refused_by_name(tmp_path):
    """Negative control: a persona card with no 'name' refuses registration,
    named by field — it is never silently registered."""
    registry_root = _copy_registry_tree(tmp_path)
    card_path = registry_root / "personas" / "cards" / "hermes.yaml"
    lines = card_path.read_text(encoding="utf-8").splitlines()
    stripped = [ln for ln in lines if not ln.startswith("name:")]
    card_path.write_text("\n".join(stripped) + "\n", encoding="utf-8")

    with pytest.raises(HeadRegistrationError) as excinfo:
        register_head_agent(registry_root)
    assert "name" in str(excinfo.value)


def test_missing_seed_profile_is_refused(tmp_path):
    registry_root = _copy_registry_tree(tmp_path)
    os.remove(registry_root / "profiles" / "seeds" / "hermes.1.0.0.yaml")

    with pytest.raises(HeadRegistrationError) as excinfo:
        register_head_agent(registry_root)
    assert "hermes.1.0.0.yaml" in str(excinfo.value)


def test_invalid_persona_card_is_refused_with_the_real_validator_error(tmp_path):
    """Corrupt a real field (unknown capability) and confirm the live
    validator — not a stub — is the one refusing it."""
    registry_root = _copy_registry_tree(tmp_path)
    card_path = registry_root / "personas" / "cards" / "hermes.yaml"
    text = card_path.read_text(encoding="utf-8")
    text = text.replace("  - code-author", "  - not-a-real-capability")
    card_path.write_text(text, encoding="utf-8")

    with pytest.raises(HeadRegistrationError) as excinfo:
        register_head_agent(registry_root)
    assert "not-a-real-capability" in str(excinfo.value)
