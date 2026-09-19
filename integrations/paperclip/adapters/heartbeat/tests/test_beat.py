"""Runtime beat contract tests (issue #1271)."""

from __future__ import annotations

import pytest

from integrations.paperclip.adapters.heartbeat import beat

REGISTRY_YAML = """
schema: cmr.runtime-registry/v1
runtimes:
  - id: claude-session
    kind: agent
    transport: cli
    identity: claude
    token_scope: session
  - id: deepseek-sister
    kind: agent
    transport: terminal
    identity: deepseek
    token_scope: fleet-sister
"""


@pytest.fixture
def root(tmp_path):
    (tmp_path / "fleet").mkdir()
    (tmp_path / "fleet" / "runtimes.yaml").write_text(REGISTRY_YAML, encoding="utf-8")
    return tmp_path


def test_load_registry(root):
    registry = beat.load_registry(root)
    assert set(registry) == {"claude-session", "deepseek-sister"}
    assert registry["deepseek-sister"]["kind"] == "agent"


def test_load_registry_missing_file(tmp_path):
    with pytest.raises(beat.BeatRefused):
        beat.load_registry(tmp_path)


def test_load_registry_duplicate_id(tmp_path):
    (tmp_path / "fleet").mkdir()
    (tmp_path / "fleet" / "runtimes.yaml").write_text(
        "runtimes:\n  - id: a\n  - id: a\n", encoding="utf-8"
    )
    with pytest.raises(beat.BeatRefused):
        beat.load_registry(tmp_path)


def test_write_and_read_beat_roundtrip(root):
    record = beat.write_beat("claude-session", "abc123", "running", ts=1000.0, root=root)
    assert record == {
        "runtime": "claude-session",
        "commit": "abc123",
        "state": "running",
        "ts": 1000.0,
    }
    assert beat.read_beat("claude-session", root=root) == record


def test_write_beat_refuses_unregistered_runtime(root):
    with pytest.raises(beat.BeatRefused, match="runtime-unregistered:ghost"):
        beat.write_beat("ghost", "abc123", "running", root=root)


def test_write_beat_refuses_unknown_state(root):
    with pytest.raises(beat.BeatRefused):
        beat.write_beat("claude-session", "abc123", "sleeping", root=root)


def test_write_beat_refuses_empty_commit(root):
    with pytest.raises(beat.BeatRefused):
        beat.write_beat("claude-session", "", "running", root=root)


def test_read_beat_missing_is_none(root):
    assert beat.read_beat("claude-session", root=root) is None


def test_read_beat_torn_file_is_none(root):
    path = beat.beat_path("claude-session", root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert beat.read_beat("claude-session", root=root) is None


def test_read_all_beats(root):
    beat.write_beat("claude-session", "abc123", "running", ts=1.0, root=root)
    beat.write_beat("deepseek-sister", "def456", "idle", ts=2.0, root=root)
    beats = beat.read_all_beats(root)
    assert set(beats) == {"claude-session", "deepseek-sister"}
    assert beats["deepseek-sister"]["state"] == "idle"


def test_read_all_beats_no_dir(root):
    assert beat.read_all_beats(root) == {}


def test_read_all_beats_includes_unregistered_id_on_disk(root):
    # A beat can outlive its registration; the judge (not this module) decides
    # what that means, so read_all_beats must not silently drop it.
    directory = beat.beats_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "ghost.json").write_text(
        '{"runtime": "ghost", "commit": "x", "state": "running", "ts": 1.0}\n',
        encoding="utf-8",
    )
    beats = beat.read_all_beats(root)
    assert "ghost" in beats
