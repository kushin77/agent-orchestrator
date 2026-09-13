"""Pytest bootstrap + runtime isolation for the fleet suite (M26).

``fleet/channel.py`` is a standalone script (repo convention: namespace
modules), so the package directory goes to the front of ``sys.path``.

**Isolation is the second job of this file, and it is measured, not assumed.**
On 2026-09-13 running this suite appended fabricated entries to the repo's real
`.fleet/slog.jsonl` — 32 of them over a few runs, including `merged #171` and
`done`, i.e. work that never happened, in the audit log the brain tails and the
health signal reads. The autouse fixture below redirects every runtime path the
fleet writes to a per-test tmp directory, so a new test cannot forget to patch
one; the guard test proves the cover is real.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG_DIR not in sys.path:
    sys.path.insert(0, PKG_DIR)

import channel  # noqa: E402

# module -> the runtime paths it writes (all redirected for every test)
RUNTIME_PATHS = {
    "channel": (
        "INBOX",
        "SENT",
        "OUTBOX",
        "DONE",
        "SLOG",
        "BRAIN_INBOX",
        "BRAIN_SENT",
        "BRAIN_OUTBOX",
        "BRAIN_DONE",
        "HEARTBEAT",
        "BRAIN_HEARTBEAT",
    ),
    "terminal": ("HEARTBEAT", "RUNS", "REPORTED", "PAUSED", "STOPPING", "WORKTREE_ROOT"),
    "brain": ("HEARTBEAT",),
    "health": ("SLOG",),
}


@pytest.fixture(autouse=True)
def isolate_fleet_runtime(tmp_path, monkeypatch):
    """Point every fleet runtime path at a tmp dir for the duration of a test."""
    for module_name, names in RUNTIME_PATHS.items():
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for name in names:
            if hasattr(module, name):
                monkeypatch.setattr(module, name, tmp_path / module_name / name.lower())
    singleton = sys.modules.get("singleton")
    if singleton is not None:
        monkeypatch.setattr(singleton, "FLEET", tmp_path / "singleton")
    return tmp_path


def live_fleet_dir() -> Path:
    """The real `.fleet/` beside the fleet package — what must stay untouched."""
    return Path(__file__).resolve().parents[1] / ".fleet"


def test_a_test_write_lands_in_tmp_and_never_in_the_live_log(tmp_path):
    """The guard: if the cover regresses, this fails instead of the log lying."""
    probe = "isolation probe — this must never reach the live slog"
    channel._slog({"type": "result", "from": "sister", "to": "brain", "body": probe})

    written = Path(channel.SLOG).read_text(encoding="utf-8")
    assert probe in written, "the probe did not go where the fixture points"
    assert str(channel.SLOG).startswith(str(tmp_path)), "SLOG is not redirected"

    live = live_fleet_dir() / "slog.jsonl"
    if live.exists():
        assert probe not in live.read_text(encoding="utf-8"), (
            "a test wrote into the live fleet log — the autouse isolation fixture "
            "is not covering this path"
        )


def test_every_channel_runtime_path_is_redirected(tmp_path):
    """The cover is only real if each path actually moved out of the repo."""
    for name in RUNTIME_PATHS["channel"]:
        value = getattr(channel, name, None)
        if value is None:
            continue
        assert tmp_path in Path(value).parents, f"channel.{name} was not redirected"
