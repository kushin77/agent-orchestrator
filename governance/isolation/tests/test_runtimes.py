"""The runtime vocabulary a lane record is validated against (#1301)."""

from __future__ import annotations

from pathlib import Path

import pytest

from governance.isolation import runtimes


def test_without_a_registry_the_seven_contract_ids_are_the_vocabulary(tmp_path: Path):
    assert runtimes.runtime_ids(tmp_path) == runtimes.FALLBACK_RUNTIME_IDS
    assert "claude-subagent" in runtimes.runtime_ids(tmp_path)
    assert "copilot-agent" in runtimes.runtime_ids(tmp_path)
    assert "pending #1271" in runtimes.registry_source(tmp_path)


def test_a_registry_file_replaces_the_literal(tmp_path: Path):
    registry = tmp_path / runtimes.REGISTRY_PATH
    registry.parent.mkdir(parents=True)
    registry.write_text(
        "runtimes:\n  - id: claude-session\n    kind: session\n  - id: gemini-agent\n    kind: subagent\n",
        encoding="utf-8",
    )
    assert runtimes.runtime_ids(tmp_path) == ("claude-session", "gemini-agent")
    assert runtimes.unregistered("gemini-agent", tmp_path) == ""
    assert runtimes.unregistered("hermes", tmp_path).startswith("runtime-unregistered:hermes")
    assert runtimes.registry_source(tmp_path) == runtimes.REGISTRY_PATH


def test_an_unregistered_runtime_is_refused_by_name(tmp_path: Path):
    refusal = runtimes.unregistered("gpt-agent", tmp_path)
    assert refusal.startswith("runtime-unregistered:gpt-agent:")
    assert "#1271" in refusal


def test_an_empty_runtime_is_unrecorded_not_unregistered(tmp_path: Path):
    """The dispatchers that mint lanes today pass no runtime; refusing them would
    stop every dispatch. `open` names it runtime-unrecorded instead."""
    assert runtimes.unregistered("", tmp_path) == ""


@pytest.mark.parametrize(
    "body",
    ["runtimes: []\n", "runtimes:\n  - kind: session\n", "runtimes:\n  - id: a\n  - id: a\n", "{not yaml"],
)
def test_an_unreadable_registry_is_refused_not_defaulted(tmp_path: Path, body: str):
    registry = tmp_path / runtimes.REGISTRY_PATH
    registry.parent.mkdir(parents=True)
    registry.write_text(body, encoding="utf-8")
    with pytest.raises(runtimes.RegistryUnreadable):
        runtimes.runtime_ids(tmp_path)
