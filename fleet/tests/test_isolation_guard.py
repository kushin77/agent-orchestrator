"""The fleet runtime-isolation guard, as a collected and falsifiable test (#284).

The guard used to live in ``conftest.py``, which pytest never collects as a test
module — so it never ran. Run by nodeid it always FAILED, because the probe
carried a U+2014 that ``json.dumps(ensure_ascii=True)`` escapes (a raw substring
can never match) and the leak check read ``<repo>/fleet/.fleet``, a path no
runtime path uses. A guard that cannot run, cannot pass, and cannot fire is a
formality (GR-12), so this module is the collected replacement — asserted by
effect:

* the write is checked on the **parsed** JSONL record, so JSON escaping cannot
  make the assertion unsatisfiable;
* the leak check reads the **real** repository-root ``.fleet/``;
* two negative controls prove the guard can fail: one leaves a runtime path
  pointing at a live log for real, the other drops a module from the cover.
"""

from __future__ import annotations

import json
from pathlib import Path

import channel

from conftest import RUNTIME_MODULES, RUNTIME_PATHS, live_fleet_dir  # noqa: E402

#: Deliberately non-ASCII: ``json.loads`` decodes the escape that made the old
#: raw-substring probe unsatisfiable, which is exactly the fix under test.
PROBE = "isolation probe \u2014 this must never reach the live slog"


def _bodies(log: Path) -> list[str]:
    """Every record ``body`` in a JSONL slog, parsed rather than substring-matched."""
    if not log.exists():
        return []
    bodies = []
    for line in log.read_text(encoding="utf-8").splitlines():
        if line.strip():
            bodies.append(json.loads(line).get("body") or "")
    return bodies


def leaked_probes(probe: str, fleet_dir: Path) -> list[str]:
    """The live records carrying ``probe`` — non-empty means the cover failed."""
    return [body for body in _bodies(fleet_dir / "slog.jsonl") if probe in body]


def uncovered_paths(cover: dict, tmp_path: Path) -> list[str]:
    """Declared runtime paths that did not move out of the repository."""
    findings: list[str] = []
    for module_name, names in RUNTIME_PATHS.items():
        module = cover.get(module_name)
        if module is None:
            findings.append(f"{module_name}: module was never imported, so its paths are un-redirected")
            continue
        for name in names:
            value = getattr(module, name, None)
            if value is None:
                continue
            if not str(Path(value)).startswith(str(tmp_path)):
                findings.append(f"{module_name}.{name} = {value}")
    return findings


def test_a_test_write_lands_in_tmp_and_never_in_the_live_log(tmp_path):
    """The guard: if the cover regresses this fails, instead of the log lying."""
    channel._slog({"type": "result", "from": "sister", "to": "brain", "body": PROBE})

    written = Path(channel.SLOG)
    assert tmp_path in written.parents, "channel.SLOG was not redirected out of the repo"
    assert PROBE in _bodies(written), "the probe did not reach the redirected log"
    assert leaked_probes(PROBE, live_fleet_dir()) == [], (
        "a test wrote into the live fleet log — the autouse isolation fixture is "
        "not covering this path"
    )


def test_the_cover_redirects_every_declared_path(tmp_path):
    """Importing the whole cover removes the old skip-if-not-yet-imported hole.

    ``monitor`` was the dead entry: no test imported it, the fixture looked it up
    in ``sys.modules``, found ``None`` and skipped it silently.
    """
    assert uncovered_paths(RUNTIME_MODULES, tmp_path) == []


def test_the_cover_check_catches_a_module_dropped_from_the_cover(tmp_path):
    """Negative control: the cover check is not vacuous (the `monitor` failure)."""
    degraded = {name: module for name, module in RUNTIME_MODULES.items() if name != "monitor"}
    findings = uncovered_paths(degraded, tmp_path)
    assert any(finding.startswith("monitor:") for finding in findings), findings


def test_the_guard_catches_a_deliberate_write_into_the_live_log(tmp_path, monkeypatch):
    """Negative control: a real write into a live log must be SEEN.

    The positive test passes on a clean tree, which proves nothing unless the
    detector can fail. This leaves a runtime path pointing at a live log — the
    exact regression the fixture exists to prevent — and requires the guard's own
    leak predicate to fire.
    """
    live = tmp_path / "live-repo" / ".fleet"
    live.mkdir(parents=True)
    monkeypatch.setattr(channel, "SLOG", live / "slog.jsonl")

    channel._slog({"type": "result", "from": "sister", "to": "brain", "body": PROBE})

    assert leaked_probes(PROBE, live) != [], "the guard would NOT catch a live write"
