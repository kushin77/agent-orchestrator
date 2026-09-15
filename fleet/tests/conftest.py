"""Pytest bootstrap + runtime isolation for the fleet suite (M26).

``fleet/channel.py`` is a standalone script (repo convention: namespace
modules), so the package directory goes to the front of ``sys.path``.

**Isolation is the second job of this file, and it is measured, not assumed.**
On 2026-09-13 running this suite appended fabricated entries to the repo's real
`.fleet/slog.jsonl` — 32 of them over a few runs, including `merged #171` and
`done`, i.e. work that never happened, in the audit log the brain tails and the
health signal reads. The autouse fixture below redirects every runtime path the
fleet writes to a per-test tmp directory, so a new test cannot forget to patch
one.

The cover is PROVED by ``fleet/tests/test_isolation_guard.py``, not promised by
this docstring (issue #284 measured three ways the original guard was inert: its
tests were never collected, its probe was compared against its own JSON escape,
and its "live" path pointed at ``<repo>/fleet/.fleet``, which never exists). This
module therefore exposes the cover as small callables — ``unredirected_paths``,
``leaked_probe``, ``assert_probe_stays_out_of`` — that the guard module drives and
mutates, and ``redirect_runtime_paths`` imports every covered module instead of
skipping the ones no test happened to import.
"""

from __future__ import annotations

import importlib
import json
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
        "LOGS",
        "STEERS",
    ),
    "terminal": ("HEARTBEAT", "RUNS", "REPORTED", "PAUSED", "STOPPING", "RUNNER_HOLD", "WORKTREE_ROOT"),
    "brain": ("HEARTBEAT", "WAVES", "DISPATCH_MARKERS"),
    "health": ("SISTER_HEARTBEAT", "BRAIN_HEARTBEAT"),
    # The dispatch markers' state machine (#796): it reads and writes the marker
    # set, the run registry and the dead-letter store, so a test that reconciles
    # must land in `tmp_path` — the same cover, for the newest runtime tree.
    "markers": ("DISPATCHED", "RUNS", "DEAD_LETTER"),
    "telemetry": ("RUNS_LOG",),
    # `watchdog.spawn` OPENS a file named after FLEET_DIR, and `console` reads
    # the same tree — without these three lines a test would create real
    # `.fleet/*.log` files and read the live fleet's state, which is the
    # measured failure this fixture exists to prevent.
    "watchdog": ("FLEET_DIR", "RUNS_DIR"),
    "console": ("FLEET_DIR",),
    "monitor": ("FLEET_DIR", "LOG", "HEARTBEAT", "WAVES_DIR", "SISTER_HEARTBEAT", "BRAIN_HEARTBEAT"),
}


def redirect_runtime_paths(tmp_path, monkeypatch, coverage=None) -> None:
    """Redirect every covered runtime path; never silently skip a module.

    The previous version looked each module up in ``sys.modules`` and
    ``continue``d when it was absent, so a module no test happened to import
    (measured: ``monitor``) was never redirected at all — the docstring promised
    "a new test cannot forget to patch one" and the promise did not hold.
    Importing each module here means every declared path is covered, a module
    that cannot be imported fails the run loudly, and a constant that was
    renamed fails instead of passing un-covered.
    """
    table = RUNTIME_PATHS if coverage is None else coverage
    for module_name, names in table.items():
        module = importlib.import_module(module_name)
        for name in names:
            if not hasattr(module, name):
                pytest.fail(
                    f"RUNTIME_PATHS declares {module_name}.{name}, which no longer "
                    "exists — the isolation cover cannot be assumed"
                )
            monkeypatch.setattr(module, name, tmp_path / module_name / name.lower())


def unredirected_paths(tmp_path, coverage=None) -> list[str]:
    """Covered paths that still point outside ``tmp_path`` (empty is the pass).

    Shared by the guard (which asserts it is empty) and its negative control
    (which must be able to name an offender): a cover assertion that cannot fail
    is a formality (GR-12).
    """
    table = RUNTIME_PATHS if coverage is None else coverage
    offenders: list[str] = []
    for module_name, names in table.items():
        module = importlib.import_module(module_name)
        for name in names:
            value = getattr(module, name, None)
            if value is None or tmp_path not in Path(value).parents:
                offenders.append(f"{module_name}.{name}")
    return offenders


@pytest.fixture(autouse=True)
def isolate_fleet_runtime(tmp_path, monkeypatch):
    """Point every fleet runtime path at a tmp dir for the duration of a test."""
    redirect_runtime_paths(tmp_path, monkeypatch)
    singleton = importlib.import_module("singleton")
    monkeypatch.setattr(singleton, "FLEET", tmp_path / "singleton")
    return tmp_path


#: The executable the fleet's default runner names (`terminal.DEFAULT_RUNNER`).
DEFAULT_RUNNER_BINARY = "claude"


@pytest.fixture(autouse=True)
def resolvable_default_runner(tmp_path, monkeypatch):
    """Put a stand-in runner on PATH, so the run path resolves deterministically.

    The loop resolves its runner explicitly (#733), so a suite that drives the run
    path must not depend on whether the HOST has the agent CLI installed — the same
    reason this file redirects `.fleet/` instead of promising every test will patch
    it. This is NOT a stub of the resolution: ``shutil.which`` really finds a real
    executable file on a PATH built here. A test that wants an unresolvable runner
    asks for one by name (``resolve_runner("claude-733-absent")``) or empties PATH
    itself, which is how the negative controls are written.
    """
    binary = tmp_path / "runner-bin" / DEFAULT_RUNNER_BINARY
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binary.parent}{os.pathsep}{os.environ.get('PATH', '')}")
    return binary


#: The probe the guard writes and then looks for. It deliberately carries a
#: non-ASCII character: ``channel._slog`` serialises with ``json.dumps``'
#: default ``ensure_ascii=True``, so on disk the probe is escaped and a raw
#: substring test can never be true (#284).
PROBE = "isolation probe — this must never reach the live fleet log"


def live_fleet_dir() -> Path:
    """The REAL `.fleet/` at the repository root — what must stay untouched.

    It is ``<repo>/.fleet``, *beside* ``fleet/``. ``parents[1]`` from this file
    is ``<repo>/fleet``, so the original ``<repo>/fleet/.fleet`` was a directory
    that never exists and the leak assertion below it was unreachable (#284).
    """
    return Path(__file__).resolve().parents[2] / ".fleet"


def records_at(path: Path) -> list[dict]:
    """Parse any JSONL log file; absent, unreadable or torn lines read as none."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError:
        return []
    records: list[dict] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def slog_records(fleet_dir: Path) -> list[dict]:
    """Parse ``<fleet_dir>/slog.jsonl`` — the audit log a ``.fleet/`` holds."""
    return records_at(Path(fleet_dir) / "slog.jsonl")


def leaked_probe(fleet_dir: Path, probe: str = PROBE) -> bool:
    """True when a PARSED record in `fleet_dir`'s audit log carries the probe.

    The comparison is on the parsed record body, never the raw line: the probe's
    em dash is written as ``\\u2014``, which is what made the original guard
    unsatisfiable. The probe is truncated to the 200 characters ``_slog`` keeps,
    so a truncated body is still detected.
    """
    marker = probe[:200]
    return any(marker in str(record.get("body", "")) for record in slog_records(fleet_dir))


def assert_probe_stays_out_of(fleet_dir: Path, probe: str = PROBE) -> None:
    """Fail when a test's write reached `fleet_dir`'s audit log (#215, #284)."""
    if leaked_probe(fleet_dir, probe):
        raise AssertionError(
            f"a test wrote {probe!r} into {Path(fleet_dir) / 'slog.jsonl'} — the autouse "
            "isolation fixture is not covering this path"
        )


def write_slog_probe(probe: str = PROBE) -> None:
    """Emit one real channel record — the write the fixture must intercept."""
    channel._slog({"type": "result", "from": "sister", "to": "brain", "body": probe})
