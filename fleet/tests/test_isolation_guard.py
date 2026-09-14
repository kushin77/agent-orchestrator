"""Runtime isolation + run-path telemetry, asserted by effect (issues #284, #286).

Why this module exists
----------------------
The isolation guard used to live in ``conftest.py``, and pytest does not collect
``conftest.py`` as a test module. ``python3 -m pytest fleet/tests -q`` reported
"224 passed" while the guard contributed nothing to that number, and it also
always failed when invoked by hand: its probe was compared against its own JSON
escape (``_slog`` writes ``ensure_ascii=True``, so the em dash lands as
``\\u2014``), and ``live_fleet_dir()`` resolved to ``<repo>/fleet/.fleet``, a
directory that never exists — so the one assertion that would detect a real leak
was unreachable. The institutional claim "no test writes into the live audit log"
was checked by nothing.

What is asserted here
---------------------
* the guards live in a COLLECTED module (a ``test_*`` function defined in
  ``conftest.py`` is invisible to ``pytest fleet/tests``);
* every module in the cover is imported and every declared path is redirected —
  ``monitor`` used to be skipped entirely, because no test imported it;
* the probe reaches the tmp log and NOT the real ``<repo>/.fleet/slog.jsonl``,
  compared on the PARSED record;
* the cover and the leak detector can FAIL: a deliberate write into a ``.fleet/``
  audit log is reported by the same helper the guard asserts with, and a covered
  path left pointing at the live tree is named by the cover check;
* the loop's run path really emits BOTH of its records (#286): driving
  ``terminal.loop`` into one dispatch — with every expensive seam stubbed —
  writes the ``started`` record *and* the terminal outcome record that a
  source-text grep used to stand in for, so deleting either emission fails here.
  When this test was first written it could assert only ``started``: the
  ``terminal.loop`` of that day crashed at the verdict with an
  ``UnboundLocalError`` (``loop`` shadowed the module-level ``verdict()``), so
  the outcome was unreachable and a narrow ``except`` absorbed the crash. ``loop``
  no longer shadows ``verdict()``, so both emissions are asserted and that
  ``except`` is gone — it would have hidden exactly the regression this test
  exists to catch.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path

import pytest

import channel
import conftest
import telemetry
import terminal


# --------------------------------------------------------------------------
# #284 — the guard is collected, and the cover it claims is real
# --------------------------------------------------------------------------


def test_the_guards_are_not_defined_in_conftest():
    """A guard test in ``conftest.py`` is never collected — pin its location.

    `pytest fleet/tests` treats ``conftest.py`` as a plugin, not a test module,
    so the guards there were invisible to the suite the repo actually runs.
    Moving one back would silently un-enforce it; this fails instead.
    """
    stray = sorted(name for name in dir(conftest) if name.startswith("test_"))
    assert stray == [], (
        "guard tests defined in conftest.py are not collected by `pytest fleet/tests`; "
        f"they must live in a collected module (found: {stray})"
    )


def test_every_covered_module_is_imported():
    """#284: a module absent from ``sys.modules`` was silently left uncovered."""
    missing = [name for name in conftest.RUNTIME_PATHS if name not in sys.modules]
    assert missing == [], (
        "these modules are in the isolation cover but were never imported, so their "
        f"runtime paths are live: {missing}"
    )


def test_every_covered_runtime_path_is_redirected(tmp_path):
    """The cover is only real if each declared path moved out of the repo."""
    assert conftest.unredirected_paths(tmp_path) == []


def test_the_cover_check_fails_when_a_path_stays_live(tmp_path, monkeypatch):
    """Negative control (#284): point one covered path at the live tree.

    A cover assertion whose pass and fail paths collapse is a formality (GR-12);
    this proves the check the guard relies on can report an offender.
    """
    monkeypatch.setattr(terminal, "WORKTREE_ROOT", conftest.live_fleet_dir())
    offenders = conftest.unredirected_paths(tmp_path)
    assert "terminal.WORKTREE_ROOT" in offenders, (
        "the cover check cannot detect a path that is still live"
    )


def test_the_probe_lands_in_the_tmp_log_as_a_parsed_record(tmp_path):
    """The write the fixture must intercept lands where the fixture points."""
    conftest.write_slog_probe()
    assert str(channel.SLOG).startswith(str(tmp_path)), "SLOG is not redirected"
    bodies = [record.get("body") for record in conftest.records_at(Path(channel.SLOG))]
    assert conftest.PROBE in bodies, "the probe did not go where the fixture points"


def test_the_probe_is_escaped_on_disk_so_the_guard_must_parse_the_record():
    """#284: the raw-line comparison the original guard used is unsatisfiable.

    ``channel._slog`` serialises with ``json.dumps``' default ``ensure_ascii=True``
    (and truncates the body at 200 characters), so the probe is absent from the
    raw line while present in the parsed record. This pins BOTH halves, so
    reverting to ``probe in written`` fails here instead of silently passing.
    """
    conftest.write_slog_probe()
    raw = Path(channel.SLOG).read_text(encoding="utf-8")
    assert "\\u2014" in raw, "expected the em dash escaped on disk"
    assert conftest.PROBE not in raw, "the raw line cannot contain the probe"
    assert any(
        record.get("body") == conftest.PROBE for record in conftest.records_at(Path(channel.SLOG))
    )


def test_no_test_write_reaches_the_real_repo_root_audit_log():
    """The assertion the guard exists for, against the REAL ``<repo>/.fleet/``."""
    conftest.write_slog_probe()
    live = conftest.live_fleet_dir()
    assert live == Path(conftest.__file__).resolve().parents[2] / ".fleet"
    assert live.parent.name != "fleet", (
        "live_fleet_dir() must be <repo>/.fleet, not the <repo>/fleet/.fleet path "
        "that never exists (that was #284's unreachable assertion)"
    )
    assert conftest.leaked_probe(live) is False
    conftest.assert_probe_stays_out_of(live)


def test_the_guard_reads_the_same_root_channel_writes_to():
    """The guard is only meaningful if it watches the log channel actually writes.

    ``channel`` derives every mailbox from ``ROOT``; the guard has to name the
    same directory, or it inspects a path no write can ever reach.
    """
    channel_root = Path(channel.__file__).resolve().parent.parent / ".fleet"
    assert conftest.live_fleet_dir() == channel_root


def test_the_live_log_detector_fails_on_a_deliberate_write(tmp_path, monkeypatch):
    """Negative control (#284): a write that DOES reach a ``.fleet/`` log is caught.

    The deliberate write goes through the production writer (``channel._slog``)
    into a scratch ``.fleet/``, never into the real audit log a running loop is
    tailing — and it must make the guard's own assertion fail, which is the
    direction that was unreachable before the path fix.
    """
    scratch = tmp_path / "scratch-fleet"
    scratch.mkdir()
    monkeypatch.setattr(channel, "SLOG", scratch / "slog.jsonl")
    channel._slog({"type": "result", "from": "sister", "to": "brain", "body": conftest.PROBE})

    with pytest.raises(AssertionError, match="isolation probe"):
        conftest.assert_probe_stays_out_of(scratch)

    # ...and the control is not a tautology: with no write there is no failure.
    empty = tmp_path / "empty-fleet"
    empty.mkdir()
    conftest.assert_probe_stays_out_of(empty)


# --------------------------------------------------------------------------
# #286 — the run path's telemetry is asserted by effect, not by source text
# --------------------------------------------------------------------------


class _Completed:
    """A ``subprocess.run`` result the loop can read."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Beater:
    """Stands in for the heartbeat thread; ``stop`` is all the loop needs."""

    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


DIRECTIVE_ID = "d-iso-284"
DIRECTIVE_ISSUE = 284


def _run_one_cycle(monkeypatch) -> list[list[str]]:
    """Drive ``terminal.loop`` into the run path once, seams stubbed.

    Everything the loop would reach outside its own process — the subagent
    runner, the lane provisioner, the claim ledger, the gates and the GitHub
    board — is replaced with a stub that reports success, so the assertions can
    be about the loop's OWN behaviour (which telemetry it emits). Returns the
    commands the loop issued, so a test can prove the cycle really ran.

    Nothing is caught: ``loop --once`` is driven to completion and any exception
    it raises fails the test. The crash this harness once absorbed narrowly (an
    ``UnboundLocalError: verdict``, from ``loop`` shadowing the module-level
    ``verdict()``) cannot occur at this commit, and an ``except`` that swallows it
    would hide a regression rather than report one (#286).
    """
    calls: list[list[str]] = []
    directive = {
        "id": DIRECTIVE_ID,
        "ts": "2026-09-13T00:00:00Z",
        "type": "directive",
        "from": "brain",
        "to": "sister",
        "correlation_id": "c-iso-284",
        "task": {"kind": "work", "issue": DIRECTIVE_ISSUE, "lane": "fleet"},
    }

    def fake_run(command, **kwargs):  # noqa: ARG001 - the loop passes cwd/text
        calls.append(list(command))
        if command[:1] == ["git"]:
            return _Completed(stdout="abc1234\n")
        if "watch" in command:
            return _Completed(stdout=json.dumps(directive))
        if "held" in command:
            return _Completed(returncode=1, stdout="{}")
        return _Completed()

    monkeypatch.setattr(terminal.singleton, "guard", lambda *a, **k: True)
    monkeypatch.setattr(terminal.subprocess, "run", fake_run)
    monkeypatch.setattr(terminal, "write_heartbeat", lambda *a, **k: None)
    monkeypatch.setattr(terminal, "start_beating", lambda *a, **k: _Beater())
    monkeypatch.setattr(terminal, "claim_issue", lambda *a, **k: (True, "claimed"))
    monkeypatch.setattr(terminal, "provision_worktree", lambda *a, **k: None)
    monkeypatch.setattr(terminal, "run_once", lambda *a, **k: (0, "runner finished"))
    monkeypatch.setattr(terminal, "release_in_flight", lambda *a, **k: None)
    monkeypatch.setattr(terminal, "gate_evidence", lambda *a, **k: (terminal.GATE_OK, "`make verify` rc=0"))
    monkeypatch.setattr(terminal, "landed_evidence", lambda *a, **k: (True, f"#{DIRECTIVE_ISSUE} is closed"))
    monkeypatch.setattr(terminal, "closeout_issue", lambda *a, **k: "OK")

    args = argparse.Namespace(
        runner="true",
        watch_timeout=0.1,
        timeout=1.0,
        idle_sleep=0.0,
        dry_run=False,
        once=True,
    )
    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        returned = terminal.loop(args)
    finally:
        for sig, handler in saved.items():
            signal.signal(sig, handler)
    assert returned == 0, f"`loop --once` returned {returned}; the cycle did not finish"
    return calls


def test_loop_wires_record_run_into_the_run_path(monkeypatch):
    """#286: the run path's telemetry is proved by EFFECT, not by source text.

    The version of this test in ``test_terminal_telemetry.py`` asserted that the
    two ``record_run(...)`` call *strings* appeared in
    ``inspect.getsource(terminal.loop)``. A substring assertion is green while the
    characters are present and the call is dead — commenting the call out while
    keeping its text survived (measured, GR-12) — and red when the call is merely
    reformatted. It proved nothing about the wiring and could not fail on the
    defect it claimed to guard.

    Here one real dispatch is driven end to end with every out-of-process seam
    stubbed, and the records the run path itself wrote are read back from the
    JSONL. Both emissions the old grep named are asserted — the ``started`` record
    and the terminal outcome record — so deleting either one fails.
    """
    calls = _run_one_cycle(monkeypatch)
    assert any("watch" in command for command in calls), "the driver never fed the loop a directive"
    assert any("held" in command for command in calls), (
        "the loop never reached the dispatch stage, so the start record proves nothing"
    )

    records = telemetry.read_records(telemetry.RUNS_LOG)
    statuses = [record["status"] for record in records]
    assert {record["run_id"] for record in records} == {DIRECTIVE_ID}, (
        f"the run path wrote records for other runs: {records}"
    )

    started = [record for record in records if record["status"] == "started"]
    assert len(started) == 1, f"expected exactly one start record, got {statuses}"
    started = started[0]
    assert started["issue"] == str(DIRECTIVE_ISSUE)
    assert started["agent"] == terminal.agent_id_for(DIRECTIVE_ID)
    assert started["started_at"], "the start record must carry the run's start time"
    assert started["finished_at"] is None

    outcome = [record for record in records if record["status"] in {"done", "failed"}]
    assert len(outcome) == 1, (
        f"the run path wrote no terminal record for {DIRECTIVE_ID}: emitted {statuses}"
    )
    outcome = outcome[0]
    assert outcome["issue"] == str(DIRECTIVE_ISSUE)
    assert outcome["agent"] == terminal.agent_id_for(DIRECTIVE_ID)
    assert outcome["started_at"] == started["started_at"], (
        "the outcome record must name the same run as the start record"
    )
    assert outcome["finished_at"], "the outcome record must carry a finish time"
    assert outcome["detail"], "the outcome record must carry what the run decided"
