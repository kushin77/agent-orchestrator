"""Pytest bootstrap + a synthetic fleet tree for the heartbeat adapter tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


@pytest.fixture()
def fleet_tree(tmp_path: Path):
    """Build a synthetic fleet tree; return a helper that writes each store."""

    def build(
        *,
        beats: dict[str, dict] | None = None,
        issues: list[dict] | None = None,
        events: list[dict] | None = None,
        inbox: dict[str, list[dict]] | None = None,
    ) -> Path:
        for rung, beat in (beats or {}).items():
            _write(tmp_path / ".fleet" / f"{rung}.heartbeat.json", beat)
        if issues is not None:
            _write(
                tmp_path / ".board" / "snapshot.json",
                {"generated_at": "2026-09-14T00:00:00Z", "source": "test", "issues": issues},
            )
        if events is not None:
            lines = "\n".join(json.dumps(e) for e in events)
            (tmp_path / ".board").mkdir(parents=True, exist_ok=True)
            (tmp_path / ".board" / "claims.jsonl").write_text(lines + "\n", encoding="utf-8")
        for rung, messages in (inbox or {}).items():
            directory = tmp_path / ".fleet" / ("brain/inbox" if rung == "brain" else "inbox")
            for index, message in enumerate(messages):
                _write(directory / f"{index:02d}.json", message)
        return tmp_path

    return build
