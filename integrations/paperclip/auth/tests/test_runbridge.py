"""The run-correlation bridge: X-Paperclip-Run-Id <-> correlation_id (#412)."""

from __future__ import annotations

from pathlib import Path

import pytest

from integrations.paperclip.auth import runbridge as runbridge_mod
from integrations.paperclip.auth.model import AuthError


def test_bind_and_resolve_both_ways() -> None:
    bridge = runbridge_mod.RunBridge()
    binding = bridge.bind("run-abc", "corr-xyz")
    assert binding.run_id == "run-abc"
    assert binding.correlation_id == "corr-xyz"
    assert bridge.correlation_for("run-abc") == "corr-xyz"
    assert bridge.run_for("corr-xyz") == "run-abc"
    assert bridge.correlation_for("run-unknown") is None


def test_replayed_run_id_refused() -> None:
    bridge = runbridge_mod.RunBridge()
    bridge.bind("run-abc", "corr-1")
    with pytest.raises(AuthError) as exc:
        bridge.bind("run-abc", "corr-2")
    assert exc.value.status == 409
    assert exc.value.code == "replayed_run_id"
    assert "replayed" in exc.value.message


def test_invalid_run_id_refused() -> None:
    bridge = runbridge_mod.RunBridge()
    for bad in ("", "has space", "../etc/passwd", "a\nb"):
        with pytest.raises(AuthError) as exc:
            bridge.bind(bad, "corr-1")
        assert exc.value.status == 400


def test_empty_correlation_refused() -> None:
    bridge = runbridge_mod.RunBridge()
    with pytest.raises(AuthError) as exc:
        bridge.bind("run-abc", "   ")
    assert exc.value.status == 400


def test_bind_from_fleet_is_deterministic_and_catches_replay() -> None:
    bridge = runbridge_mod.RunBridge()
    first = bridge.bind_from_fleet("run-1")
    assert first.correlation_id == "paperclip-run:run-1"
    with pytest.raises(AuthError):
        bridge.bind_from_fleet("run-1")


def test_json_file_ledger_persists(tmp_path: Path) -> None:
    path = tmp_path / "runs.json"
    bridge = runbridge_mod.RunBridge(runbridge_mod.JsonFileRunLedger(path))
    bridge.bind("run-1", "corr-1")
    reopened = runbridge_mod.RunBridge(runbridge_mod.JsonFileRunLedger(path))
    assert reopened.correlation_for("run-1") == "corr-1"
    with pytest.raises(AuthError):
        reopened.bind("run-1", "corr-2")
