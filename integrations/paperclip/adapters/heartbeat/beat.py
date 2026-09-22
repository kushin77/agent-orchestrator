"""Runtime beat records (issue #1271) — every runtime posts `{runtime, commit,
state, ts}`, keyed by the id `fleet/runtimes.yaml` declares.

This is deliberately a SEPARATE, smaller contract from `adapter.derive_heartbeat`
above it: that function derives the rich `wake`/`delta`/`outcome` shape for the
three FLEET rungs (brain/sister/monitor) from their own stores. `#1271` needs a
liveness beat any runtime can post — including ones this repo does not run a
loop for (`copilot-agent`, `hermes`, `paperclip` itself) — so this module is a
pure, minimal writer/reader over one flat record, checked against the registry
rather than the fleet's internal rung vocabulary.

Storage: `.fleet/runtime-beats/<id>.json`, one file per runtime, written
atomically (tmp + rename) so a torn write can never corrupt a beat a liveness
check is reading concurrently.

Fail-closed: a beat for an id `fleet/runtimes.yaml` does not list is REFUSED at
write time (`BeatRefused`), never silently accepted — an unregistered runtime is
a finding (`runtime-unregistered:<id>`), not a beat.

---knowledge---
module_id: integrations.paperclip.adapters.heartbeat.beat
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: [BeatRefused, registry_path, load_registry, beats_dir, beat_path, write_beat, read_beat, read_all_beats]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[4]
REGISTRY_PATH = ROOT / "fleet" / "runtimes.yaml"
BEATS_DIR_NAME = "runtime-beats"

#: The closed state vocabulary a beat may report. Anything else is refused.
BEAT_STATES = ("running", "idle", "paused", "stopped")


class BeatRefused(Exception):
    """A beat could not be written/read as posted — never fabricated."""


def registry_path(root: Path | None = None) -> Path:
    return (root / "fleet" / "runtimes.yaml") if root is not None else REGISTRY_PATH


def load_registry(root: Path | None = None) -> dict[str, dict]:
    """The declared runtimes, keyed by id. Refuses a missing/unreadable/duplicate registry."""
    path = registry_path(root)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BeatRefused(f"registry unreadable at {path}: {exc}") from None
    try:
        doc = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise BeatRefused(f"registry is not valid YAML: {exc}") from None
    entries = doc.get("runtimes") if isinstance(doc, dict) else None
    if not isinstance(entries, list):
        raise BeatRefused("registry has no 'runtimes' list")
    registered: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict) or "id" not in entry:
            raise BeatRefused(f"registry entry missing 'id': {entry!r}")
        rid = entry["id"]
        if rid in registered:
            raise BeatRefused(f"registry declares '{rid}' more than once")
        registered[rid] = entry
    return registered


def beats_dir(root: Path | None = None) -> Path:
    base = root if root is not None else ROOT
    return base / ".fleet" / BEATS_DIR_NAME


def beat_path(runtime_id: str, root: Path | None = None) -> Path:
    return beats_dir(root) / f"{runtime_id}.json"


def write_beat(
    runtime_id: str,
    commit: str,
    state: str,
    *,
    ts: float | None = None,
    root: Path | None = None,
) -> dict:
    """Write one runtime's beat. Refuses an unregistered id or an unknown state."""
    registry = load_registry(root)
    if runtime_id not in registry:
        raise BeatRefused(f"runtime-unregistered:{runtime_id}")
    if state not in BEAT_STATES:
        raise BeatRefused(f"'{state}' is not a declared beat state ({', '.join(BEAT_STATES)})")
    if not commit:
        raise BeatRefused("commit must be non-empty")
    record = {
        "runtime": runtime_id,
        "commit": commit,
        "state": state,
        "ts": float(ts) if ts is not None else time.time(),
    }
    path = beat_path(runtime_id, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return record


def read_beat(runtime_id: str, root: Path | None = None) -> dict | None:
    """One runtime's last beat, or None. A torn/unreadable file reads as absent."""
    path = beat_path(runtime_id, root)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return record if isinstance(record, dict) else None


def read_all_beats(root: Path | None = None) -> dict[str, dict]:
    """Every beat currently on disk, keyed by the FILE's stem — including ids the
    registry no longer declares, so a caller (the liveness judge) can still
    report `runtime-unregistered` for a beat that outlived its registration."""
    directory = beats_dir(root)
    if not directory.is_dir():
        return {}
    beats: dict[str, dict] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(record, dict):
            beats[path.stem] = record
    return beats
