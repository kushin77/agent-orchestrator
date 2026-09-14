"""Capability drift — a running rung must report capabilities it does not implement (#319).

The measured gap: after isolation (#263), lifecycle (#269) and reconciliation
(#304) merged, the running sister kept executing pre-merge code and the only
signal was `watchdog decide() == "drifted"` — per rung, about *commits*, silent
about WHICH control was missing. A capability that ships but is not live is a
silently absent control.

These tests drive the repository declaration (`channel.CAPABILITIES`), the rung's
own declaration (its beat) and the comparison, and they PROVE the comparison can
fail: the last test adds a capability the rung does not declare and requires it
to be named.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import channel  # noqa: E402
import watchdog  # noqa: E402


def _git(*args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            ["git", "-C", str(channel.ROOT), *args], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _out(*args: str) -> str:
    result = _git(*args)
    return result.stdout.strip() if result is not None and result.returncode == 0 else ""


HEAD = _out("rev-parse", "--short", "HEAD")
ROOT_COMMIT = _out("rev-list", "--max-parents=0", "HEAD")

pytestmark = pytest.mark.skipif(
    not HEAD or not ROOT_COMMIT, reason="the capability comparison needs the repository's history"
)


def _beat(commit: str, **extra) -> dict:
    entry = {"pid": 111, "state": "idle", "commit": commit, "ts": channel.now_iso()}
    entry.update(extra)
    return entry


def _sister_ids() -> list[str]:
    return [cap.id for cap in channel.capabilities_for_rung("sister")]


# --- the repository's declaration --------------------------------------------


def test_every_declared_capability_is_anchored_in_the_history():
    """`since` must be an ancestor of HEAD and must touch the code it names.

    A capability declared ahead of its implementation is the paper-feature
    class: the declaration promises a control the code cannot have shipped.
    """
    assert channel.CAPABILITIES, "the repository declares no capability at all"
    for cap in channel.CAPABILITIES:
        evidence = channel.ROOT / cap.evidence
        assert evidence.is_file(), f"{cap.id} names {cap.evidence}, which does not exist"
        ancestor = _git("merge-base", "--is-ancestor", cap.since, "HEAD")
        assert ancestor is not None and ancestor.returncode == 0, (
            f"{cap.id} claims since={cap.since}, which is not an ancestor of HEAD"
        )
        touched = _out("diff-tree", "--no-commit-id", "--name-only", "-r", cap.since)
        assert cap.evidence in touched.splitlines(), (
            f"{cap.id} claims {cap.since} provided {cap.evidence}, but that commit does not touch it"
        )


def test_every_capability_is_compared_on_a_supervised_rung():
    """No orphan declaration, no uncompared rung: the gate has no inert entry."""
    for cap in channel.CAPABILITIES:
        assert cap.rungs, f"{cap.id} names no rung, so nothing would ever compare it"
        assert set(cap.rungs) <= set(channel.CAPABILITY_RUNGS), (
            f"{cap.id} names {cap.rungs}, which the watchdog does not supervise"
        )
    for rung in channel.CAPABILITY_RUNGS:
        assert channel.capabilities_for_rung(rung), f"rung {rung} declares no capability"


def test_capability_ids_are_unique():
    ids = [cap.id for cap in channel.CAPABILITIES]
    assert len(ids) == len(set(ids))


# --- the three cases ----------------------------------------------------------


def test_a_legacy_beat_on_head_is_current():
    """A beat with no capability list that beats HEAD's commit is current.

    Every beat written before this shipped looks like that, so the comparison
    must not read "no list" as "no capabilities".
    """
    finding = channel.capability_finding("sister", _beat(HEAD), HEAD)
    assert finding.kind == channel.KIND_CURRENT
    assert finding.missing == ()


def test_a_legacy_beat_on_an_old_commit_names_each_missing_capability():
    finding = channel.capability_finding("sister", _beat(ROOT_COMMIT), HEAD)
    assert finding.kind == channel.KIND_DRIFTED
    assert finding.missing == tuple(_sister_ids())
    line = channel.capability_line(finding)
    assert channel.CASE_LABELS[channel.KIND_DRIFTED] in line
    assert "CAPABILITY STALE" in line
    for cap_id in finding.missing:
        assert cap_id in line, f"the report does not name {cap_id}"


def test_a_current_rung_that_declares_a_subset_is_reported_by_name():
    """The case a commit comparison calls healthy: on HEAD, capability absent."""
    declared = _sister_ids()
    beat = _beat(HEAD, capabilities_version=1, capabilities=[declared[0]])
    finding = channel.capability_finding("sister", beat, HEAD)
    assert finding.kind == channel.KIND_CAPABILITY_STALE
    assert finding.missing == tuple(declared[1:])
    line = channel.capability_line(finding)
    assert channel.CASE_LABELS[channel.KIND_CAPABILITY_STALE] in line
    assert "CAPABILITY STALE" in line
    assert "will NOT fix" in line
    for cap_id in finding.missing:
        assert cap_id in line, f"the report does not name {cap_id}"


def test_a_current_rung_declaring_everything_is_current():
    beat = _beat(HEAD, capabilities_version=1, capabilities=_sister_ids())
    finding = channel.capability_finding("sister", beat, HEAD)
    assert finding.kind == channel.KIND_CURRENT
    assert finding.stale is False


def test_a_rung_declaring_more_than_the_repository_warns_without_failing():
    """The declaration lagging the code is not a missing capability — no false red."""
    beat = _beat(HEAD, capabilities_version=1, capabilities=_sister_ids() + ["unheard-of@1"])
    finding = channel.capability_finding("sister", beat, HEAD)
    assert finding.kind == channel.KIND_CURRENT
    assert finding.undeclared == ("unheard-of@1",)
    assert "unheard-of@1" in channel.capability_line(finding)


def test_a_missing_beat_is_the_down_case():
    finding = channel.capability_finding("sister", None, HEAD)
    assert finding.kind == channel.KIND_DOWN
    assert finding.missing == ()
    line = channel.capability_line(finding)
    assert channel.CASE_LABELS[channel.KIND_DOWN] in line
    assert "bash fleet/run-fleet.sh" in line


def test_a_foreign_vocabulary_is_cannot_assess_rather_than_stale():
    beat = _beat(HEAD, capabilities_version=999, capabilities=[])
    finding = channel.capability_finding("sister", beat, HEAD)
    assert finding.kind == channel.KIND_UNKNOWN
    assert finding.stale is False, "an unreadable declaration must not be reported as a missing capability"


def test_the_three_cases_have_three_distinct_remediations():
    down = channel.capability_finding("sister", None, HEAD)
    drifted = channel.capability_finding("sister", _beat(ROOT_COMMIT), HEAD)
    stale = channel.capability_finding(
        "sister", _beat(HEAD, capabilities_version=1, capabilities=[]), HEAD
    )
    assert len({down.remediation, drifted.remediation, stale.remediation}) == 3
    assert len({down.kind, drifted.kind, stale.kind}) == 3


# --- the comparison bites (the mutation the runbook gate also performs) -------


def test_the_comparison_names_a_capability_the_rung_does_not_declare(monkeypatch):
    """Add a capability the rung does not declare: it MUST be named as missing."""
    declared_by_the_rung = _sister_ids()
    extra = channel.Capability(
        name="capability-drift-report",
        version=1,
        rungs=("sister",),
        since=ROOT_COMMIT,
        evidence="fleet/watchdog.py",
        summary="the watchdog reports the capabilities a rung does not implement",
    )
    monkeypatch.setattr(channel, "CAPABILITIES", channel.CAPABILITIES + (extra,))
    beat = _beat(HEAD, capabilities_version=1, capabilities=declared_by_the_rung)
    finding = channel.capability_finding("sister", beat, HEAD)
    assert finding.kind == channel.KIND_CAPABILITY_STALE
    assert finding.missing == ("capability-drift-report@1",)
    assert "capability-drift-report@1" in channel.capability_line(finding)


# --- the surfaces that report it ----------------------------------------------


def test_status_reports_the_missing_capability_by_name(tmp_path, monkeypatch, capsys):
    """`channel.py status` — the surface the brain reads — names it too."""
    heartbeat = tmp_path / "sister.heartbeat.json"
    heartbeat.write_text(json.dumps(_beat(HEAD, capabilities_version=1, capabilities=[])), encoding="utf-8")
    monkeypatch.setattr(channel, "head_commit", lambda: HEAD)
    ok = channel.report_rung("sister", heartbeat, "fleet/terminal.py", "bash fleet/terminal.sh")
    out = capsys.readouterr().out
    assert ok is False
    assert "CAPABILITY STALE" in out
    assert _sister_ids()[0] in out


def test_watchdog_rung_action_carries_the_capability_line(monkeypatch):
    monkeypatch.setattr(watchdog.channel, "heartbeat_age_seconds", lambda beat: 10)
    monkeypatch.setattr(watchdog, "loop_pid", lambda pattern: 111)
    monkeypatch.setattr(watchdog, "read_beat", lambda path: _beat(HEAD, capabilities_version=1, capabilities=[]))
    monkeypatch.setattr(watchdog, "run_in_flight", lambda: False)
    monkeypatch.setattr(watchdog, "respawn", lambda *a, **k: True)
    line = watchdog.rung_action("sister", "fleet/terminal.py", "fleet/terminal.sh", Path("/tmp/x"), False, HEAD)
    assert "CAPABILITY STALE" in line
    assert _sister_ids()[0] in line


def test_a_capability_stale_pass_exits_non_zero(monkeypatch, capsys):
    """A silently absent control must fail the pass, not be a log line nobody reads."""
    monkeypatch.setattr(
        watchdog, "rung_action", lambda *a, **k: "sister: healthy | sister: rung on CURRENT code MISSING a declared capability — CAPABILITY STALE: missing lane-isolation@1"
    )
    monkeypatch.setattr(watchdog, "monitor_missing", lambda: False)
    assert watchdog.watchdog_once() == 1
    assert "CAPABILITY STALE" in capsys.readouterr().out

    monkeypatch.setattr(watchdog, "rung_action", lambda *a, **k: "sister: healthy | sister: capabilities current — on HEAD, its build is the declared build")
    assert watchdog.watchdog_once() == 0


def test_the_capabilities_cli_provokes_each_case(tmp_path, capsys):
    """The inspector separates the three cases, exit code included."""
    current = tmp_path / "current.json"
    current.write_text(json.dumps(_beat(HEAD)), encoding="utf-8")
    assert watchdog.main(["capabilities", "--rung", "sister", "--beat", str(current), "--commit", HEAD]) == 0
    assert "capabilities current" in capsys.readouterr().out

    drifted = tmp_path / "drifted.json"
    drifted.write_text(json.dumps(_beat(ROOT_COMMIT)), encoding="utf-8")
    assert watchdog.main(["capabilities", "--rung", "sister", "--beat", str(drifted), "--commit", HEAD]) == 1
    out = capsys.readouterr().out
    assert channel.CASE_LABELS[channel.KIND_DRIFTED] in out and "CAPABILITY STALE" in out

    assert watchdog.main(["capabilities", "--rung", "sister", "--beat", str(tmp_path / "absent.json"), "--commit", HEAD]) == 1
    assert channel.CASE_LABELS[channel.KIND_DOWN] in capsys.readouterr().out

    missing = tmp_path / "missing.json"
    missing.write_text(
        json.dumps(_beat(HEAD, capabilities_version=1, capabilities=[])), encoding="utf-8"
    )
    assert watchdog.main(["capabilities", "--rung", "sister", "--beat", str(missing), "--commit", HEAD]) == 1
    out = capsys.readouterr().out
    assert channel.CASE_LABELS[channel.KIND_CAPABILITY_STALE] in out and "CAPABILITY STALE" in out


def test_the_cli_refuses_a_beat_shared_by_two_rungs(tmp_path, capsys):
    assert watchdog.main(["capabilities", "--beat", str(tmp_path / "b.json")]) == 2
    assert "--beat needs exactly one --rung" in capsys.readouterr().err


# --- backward compatibility ----------------------------------------------------


def test_decide_still_classifies_the_historic_states_against_the_remote(monkeypatch):
    """The capability set is added, not swapped — and the baseline is the remote (#739).

    The reason string changed from `HEAD <sha>` to `origin/master <sha>` on purpose:
    the old baseline was the local checkout, which is the defect this test now
    pins shut. The *classes* the function returns are unchanged apart from the
    new CANNOT-ASSESS state.
    """
    monkeypatch.setattr(watchdog.channel, "heartbeat_age_seconds", lambda beat, moment=None: 10)
    assert watchdog.decide(None, None, "base") == ("missing", "no loop process")
    assert watchdog.decide(111, None, "base") == ("stale", "no heartbeat from a live loop")
    assert watchdog.decide(111, _beat("old0000"), "head1111") == (
        "drifted",
        "running old0000, origin/master head1111",
    )
    assert watchdog.decide(111, _beat("head1111"), "head1111") == ("healthy", "")
