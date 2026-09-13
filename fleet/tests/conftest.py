"""Pytest bootstrap + runtime isolation for the fleet suite (M26).

``fleet/channel.py`` is a standalone script (repo convention: namespace
modules), so the package directory goes to the front of ``sys.path``.

**Isolation is the second job of this file, and it is measured, not assumed.**
On 2026-09-13 running this suite appended fabricated entries to the repo's real
`.fleet/slog.jsonl` — 32 of them over a few runs, including `merged #171` and
`done`, i.e. work that never happened, in the audit log the brain tails and the
health signal reads. The autouse fixture below redirects every runtime path the
fleet writes to a per-test tmp directory, so a new test cannot forget to patch
one; the guard itself lives in ``test_isolation_guard.py`` — a collected
module, not this plugin, because a guard test inside ``conftest.py`` is never
collected (issue #284).
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG_DIR not in sys.path:
    sys.path.insert(0, PKG_DIR)

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
    "brain": ("HEARTBEAT", "WAVES"),
    "health": ("SLOG",),
    "telemetry": ("RUNS_LOG",),
    # `watchdog.spawn` OPENS a file named after FLEET_DIR, and `console` reads
    # the same tree — without these three lines a test would create real
    # `.fleet/*.log` files and read the live fleet's state, which is the
    # measured failure this fixture exists to prevent.
    "watchdog": ("FLEET_DIR", "RUNS_DIR"),
    "console": ("FLEET_DIR",),
    "monitor": ("FLEET_DIR", "LOG", "HEARTBEAT", "WAVES_DIR", "SISTER_HEARTBEAT", "BRAIN_HEARTBEAT"),
}


def _import_runtime_modules() -> dict[str, ModuleType]:
    """Import every module in the cover, instead of hoping a test already did.

    The fixture used to look each module up in ``sys.modules`` and skip it when
    absent, so a module no test had imported at fixture time stayed
    un-redirected — ``monitor`` was dead cover for exactly that reason. Importing
    here makes the module set complete by construction, and
    ``test_isolation_guard.py`` fails if a key cannot be covered.
    """
    return {name: importlib.import_module(name) for name in RUNTIME_PATHS}


RUNTIME_MODULES: dict[str, ModuleType] = _import_runtime_modules()


@pytest.fixture(autouse=True)
def isolate_fleet_runtime(tmp_path, monkeypatch):
    """Point every fleet runtime path at a tmp dir for the duration of a test."""
    for module_name, names in RUNTIME_PATHS.items():
        module = RUNTIME_MODULES[module_name]
        for name in names:
            if hasattr(module, name):
                monkeypatch.setattr(module, name, tmp_path / module_name / name.lower())
    singleton = sys.modules.get("singleton")
    if singleton is not None:
        monkeypatch.setattr(singleton, "FLEET", tmp_path / "singleton")
    return tmp_path


def live_fleet_dir() -> Path:
    """The real ``.fleet/`` at the repository root — what must stay untouched.

    ``fleet/tests/conftest.py`` sits two directories below the repository root,
    so ``parents[2]`` is ``<repo>``. The previous ``parents[1]`` resolved to
    ``<repo>/fleet/.fleet``, a path no runtime path uses, so the leak assertion
    could never fire (issue #284).
    """
    return Path(__file__).resolve().parents[2] / ".fleet"
