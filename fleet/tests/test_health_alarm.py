"""The runaway + queue-liveness LATCHING alarm (issue #728).

The runaway that motivated the attempt cap (#723) was caught by a human
*noticing* 49 concurrent gates. Detection has to be a signal, and — the epic's
own lesson — an alarm has to be a SIGNAL: a transient excursion that clears
itself before anyone looks is indistinguishable from one that never happened.
These tests pin the three properties that make the difference:

* the signal REPORTS what an operator needs (inbox depth, the oldest pending
  directive's age, the dead-letter count);
* a runaway condition RAISES and then LATCHES — the second measurement, taken
  with the excursion already gone, is still raised, and still names the same
  directive(s) and worktree(s);
* only `ack` clears it, and a clear queue never writes a latch at all.

Everything runs against a scratch fleet directory (`tmp_path`), never the live
`.fleet/` of a running loop: the fixtures are built by writing the same files the
loop writes (an inbox envelope, a claim event, a lane record), so the reads under
test are the real ones.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import health
import pytest
import runaway


def _stamp(minutes_ago: float = 0.0) -> str:
    moment = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _envelope(directive_id: str, *, age_minutes: float = 0.0, issue: int | None = None) -> dict:
    envelope: dict = {
        "id": directive_id,
        "from": "brain",
        "to": "sister",
        "type": "directive",
        "ts": _stamp(age_minutes),
        "nonce": f"nonce-{directive_id}",
    }
    if issue is not None:
        envelope["task"] = {"kind": "work", "issue": issue}
    return envelope


def _fleet_dir(tmp_path) -> Path:
    return tmp_path / "fleet"


def _plant(fleet_dir: Path, envelopes: list[dict]) -> None:
    inbox = runaway.inbox_dir(fleet_dir)
    inbox.mkdir(parents=True, exist_ok=True)
    for envelope in envelopes:
        (inbox / f"{envelope['id']}.json").write_text(json.dumps(envelope), encoding="utf-8")


def _plant_dead_letter(fleet_dir: Path, directive_id: str) -> None:
    directory = runaway.dead_letter_dir(fleet_dir)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{directive_id}.json").write_text(
        json.dumps({"directive_id": directive_id, "state": "dead-letter", "attempts": 5}), encoding="utf-8"
    )


def _claim_event(issue: int, directive_id: str) -> dict:
    return {
        "event": "claim",
        "issue": issue,
        "agent": "ao728-test",
        "at": _stamp(1.0),
        "lane": "fleet-lane",
        "directive_id": directive_id,
        "directive_from": "brain",
    }


def _plant_claim(repo_root: Path, issue: int, directive_id: str) -> Path:
    """One claim event in the directory ledger — the shape `claims.read_ledger` reads."""
    directory = repo_root / ".board" / "claims"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{1:020d}-{issue:05d}-agent-claim.json"
    path.write_text(json.dumps(_claim_event(issue, directive_id)), encoding="utf-8")
    return directory


def _plant_lane(repo_root: Path, issue: int, worktree: str) -> None:
    """One lane record — the shape `governance.isolation.worktree.list_records` reads."""
    directory = repo_root / ".fleet" / "lanes"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"session{issue}.json").write_text(
        json.dumps(
            {
                "session_id": f"session{issue}",
                "issue": issue,
                "agent_id": "ao728-test",
                "lane": "fleet-lane",
                "branch": f"issue-{issue}",
                "worktree": worktree,
                "repo_slug": "kushin77/agent-orchestrator",
            }
        ),
        encoding="utf-8",
    )


# ── the signal: inbox depth, oldest-directive age, dead-letter count ────────


def test_queue_report_counts_depth_age_and_dead_letters(tmp_path):
    fleet = _fleet_dir(tmp_path)
    _plant(fleet, [_envelope("directive-old", age_minutes=90.0), _envelope("directive-new")])
    _plant_dead_letter(fleet, "directive-retired")

    report = health.queue_report(fleet, max_inbox_depth=10, max_oldest_minutes=600.0, max_dead_letters=10)

    assert report.inbox_depth == 2
    assert report.oldest_directive_id == "directive-old"
    assert report.oldest_directive_age_minutes == pytest.approx(90.0, abs=1.0)
    assert report.dead_letters == 1
    assert not report.raised
    assert report.as_dict() == {
        "inbox_depth": 2,
        "oldest_directive": "directive-old",
        "oldest_directive_age_minutes": pytest.approx(90.0, abs=1.0),
        "dead_letters": 1,
    }


def test_queue_report_is_clear_for_an_absent_fleet_dir(tmp_path):
    report = health.queue_report(tmp_path / "never-created")
    assert report.inbox_depth == 0
    assert report.oldest_directive_id is None
    assert report.oldest_directive_age_minutes is None
    assert report.dead_letters == 0
    assert not report.raised


def test_queue_report_raises_on_a_deep_inbox_and_names_every_directive(tmp_path):
    fleet = _fleet_dir(tmp_path)
    _plant(fleet, [_envelope(f"directive-{n:02d}") for n in range(4)])

    report = health.queue_report(fleet, max_inbox_depth=3, max_oldest_minutes=600.0, max_dead_letters=10)

    assert report.raised
    assert report.directives == ("directive-00", "directive-01", "directive-02", "directive-03")
    assert any("4 pending directive(s)" in finding for finding in report.findings)


def test_queue_report_raises_on_an_undrained_directive(tmp_path):
    fleet = _fleet_dir(tmp_path)
    _plant(fleet, [_envelope("directive-stuck", age_minutes=200.0)])

    report = health.queue_report(fleet, max_inbox_depth=10, max_oldest_minutes=120.0, max_dead_letters=10)

    assert report.raised
    assert report.directives == ("directive-stuck",)
    assert any("200" in finding or "199" in finding for finding in report.findings)


def test_queue_report_raises_on_the_dead_letter_cap(tmp_path):
    fleet = _fleet_dir(tmp_path)
    for n in range(3):
        _plant_dead_letter(fleet, f"directive-retired-{n}")

    report = health.queue_report(fleet, max_inbox_depth=10, max_oldest_minutes=600.0, max_dead_letters=3)

    assert report.raised
    assert report.dead_letters == 3
    assert "directive-retired-0" in report.directives


# ── naming the responsible directives and worktrees ────────────────────────


def test_the_alarm_names_the_directive_and_the_worktree_responsible(tmp_path):
    fleet = _fleet_dir(tmp_path)
    repo = tmp_path / "repo"
    _plant(fleet, [_envelope("directive-lane-7", issue=728)])
    _plant_claim(repo, 728, "directive-lane-7")
    _plant_lane(repo, 728, str(tmp_path / "ao-worktrees" / "ao-728-48a78fdf"))
    _plant(fleet, [_envelope("directive-warden", issue=434)])
    _plant_lane(repo, 434, str(tmp_path / "ao-worktrees" / "ao-434-abcdef01"))

    report = health.queue_report(fleet, max_inbox_depth=1, max_oldest_minutes=600.0, max_dead_letters=10)
    worktrees = health.responsible_worktrees(
        report, ledger_path=repo / ".board" / "claims", repo_root=repo
    )

    assert report.raised
    assert "directive-lane-7" in report.directives
    assert str(tmp_path / "ao-worktrees" / "ao-728-48a78fdf") in worktrees
    assert str(tmp_path / "ao-worktrees" / "ao-434-abcdef01") in worktrees


def test_a_worktree_is_named_even_when_no_claim_has_been_taken_yet(tmp_path):
    """The line is blocked *before* the claim: the lane record still names it."""
    fleet = _fleet_dir(tmp_path)
    repo = tmp_path / "repo"
    _plant(fleet, [_envelope("directive-noclaim", issue=728), _envelope("directive-other")])
    _plant_lane(repo, 728, str(tmp_path / "ao-worktrees" / "ao-728-48a78fdf"))

    report = health.queue_report(fleet, max_inbox_depth=1, max_oldest_minutes=600.0, max_dead_letters=10)
    worktrees = health.responsible_worktrees(report, ledger_path=repo / ".board" / "claims", repo_root=repo)

    assert worktrees == (str(tmp_path / "ao-worktrees" / "ao-728-48a78fdf"),)


# ── the latch: raised once, kept raised, cleared only by an ack ────────────


def test_check_does_not_latch_a_latch_line_but_alarm_does(tmp_path, monkeypatch):
    """A read must not write: `check` reports, `alarm` raises."""
    monkeypatch.setenv(health.ENV_MAX_INBOX_DEPTH, "2")
    fleet = _fleet_dir(tmp_path)
    _plant(fleet, [_envelope(f"directive-{n:02d}") for n in range(4)])

    _code, read_only = health.alarm_payload(fleet, ledger_path=tmp_path / "claims")
    assert read_only["alarm"]["measured"] is True
    assert read_only["alarm"]["latched"] is False
    assert not health.latch_path(fleet).exists(), "a read raised the latch"

    code, raised = health.alarm_payload(fleet, ledger_path=tmp_path / "claims", latch=True)
    assert code == health.EXIT_RAISED
    assert raised["alarm"]["latched"] is True
    assert health.latch_path(fleet).exists()


def test_the_alarm_latches_when_the_excursion_is_gone(tmp_path, monkeypatch):
    """The whole point: the SECOND measurement, with the queue drained, is raised."""
    monkeypatch.setenv(health.ENV_MAX_INBOX_DEPTH, "2")
    fleet = _fleet_dir(tmp_path)
    _plant(fleet, [_envelope(f"directive-{n:02d}") for n in range(4)])

    first_code, first = health.alarm_payload(fleet, ledger_path=tmp_path / "claims", latch=True)
    assert first_code == health.EXIT_RAISED
    raised_at = first["alarm"]["raised_at"]
    assert raised_at is not None

    # The excursion clears: the provoking directives are drained.
    for path in runaway.inbox_dir(fleet).glob("*.json"):
        path.unlink()

    second_code, second = health.alarm_payload(fleet, ledger_path=tmp_path / "claims", latch=True)

    assert second_code == health.EXIT_RAISED, "a transient excursion cleared the alarm"
    assert second["alarm"]["measured"] is False
    assert second["alarm"]["latched"] is True
    assert second["alarm"]["raised_at"] == raised_at, "the latch lost the moment the condition began"
    assert second["alarm"]["directives"] == first["alarm"]["directives"]
    assert any("LATCHED" in reason for reason in second["reasons"])


def test_a_latched_alarm_keeps_naming_the_worktree_responsible(tmp_path, monkeypatch):
    monkeypatch.setenv(health.ENV_MAX_INBOX_DEPTH, "1")
    fleet = _fleet_dir(tmp_path)
    repo = tmp_path / "repo"
    _plant(fleet, [_envelope("directive-lane-7", issue=728), _envelope("directive-other")])
    _plant_claim(repo, 728, "directive-lane-7")
    _plant_lane(repo, 728, str(tmp_path / "ao-worktrees" / "ao-728-48a78fdf"))
    ledger = repo / ".board" / "claims"

    _code, first = health.alarm_payload(fleet, ledger_path=ledger, repo_root=repo, latch=True)
    assert first["alarm"]["worktrees"] == [str(tmp_path / "ao-worktrees" / "ao-728-48a78fdf")]

    for path in runaway.inbox_dir(fleet).glob("*.json"):
        path.unlink()

    _code, second = health.alarm_payload(fleet, ledger_path=ledger, repo_root=repo, latch=True)
    assert second["alarm"]["latched"] is True
    assert second["alarm"]["worktrees"] == [str(tmp_path / "ao-worktrees" / "ao-728-48a78fdf")]


def test_acknowledging_the_latch_clears_it(tmp_path, monkeypatch):
    monkeypatch.setenv(health.ENV_MAX_INBOX_DEPTH, "2")
    fleet = _fleet_dir(tmp_path)
    _plant(fleet, [_envelope(f"directive-{n:02d}") for n in range(4)])
    health.alarm_payload(fleet, ledger_path=tmp_path / "claims", latch=True)
    for path in runaway.inbox_dir(fleet).glob("*.json"):
        path.unlink()

    latch = health.acknowledge_latch(fleet, by="akushnir", note="queue drained, cap raised")
    assert latch is not None
    assert latch.latched is False
    assert latch.acknowledged_by == "akushnir"
    assert latch.note == "queue drained, cap raised"
    assert latch.raised_at is not None

    code, payload = health.alarm_payload(fleet, ledger_path=tmp_path / "claims", latch=True)
    assert code == health.EXIT_OK
    assert payload["alarm"]["latched"] is False
    assert payload["alarm"]["raised"] is False


def test_acknowledging_nothing_is_refused(tmp_path):
    fleet = _fleet_dir(tmp_path)
    assert health.acknowledge_latch(fleet) is None
    assert not health.latch_path(fleet).exists()


def test_a_clear_queue_never_writes_a_latch(tmp_path):
    fleet = _fleet_dir(tmp_path)
    _plant(fleet, [_envelope("directive-healthy")])

    code, payload = health.alarm_payload(fleet, ledger_path=tmp_path / "claims", latch=True)

    assert code == health.EXIT_OK
    assert payload["alarm"]["raised"] is False
    assert not health.latch_path(fleet).exists(), "the alarm wrote a latch for a clear queue"


def test_a_recurrence_after_an_ack_is_a_new_alarm(tmp_path, monkeypatch):
    monkeypatch.setenv(health.ENV_MAX_INBOX_DEPTH, "2")
    fleet = _fleet_dir(tmp_path)
    _plant(fleet, [_envelope(f"directive-{n:02d}") for n in range(4)])
    health.alarm_payload(fleet, ledger_path=tmp_path / "claims", latch=True)
    assert health.read_latch(fleet).latched is True
    health.acknowledge_latch(fleet)
    assert health.read_latch(fleet).latched is False
    for path in runaway.inbox_dir(fleet).glob("*.json"):
        path.unlink()

    _plant(fleet, [_envelope(f"directive-{n:02d}") for n in range(4)])
    code, second = health.alarm_payload(fleet, ledger_path=tmp_path / "claims", latch=True)

    # A recurrence after an ack is raised again and is unacknowledged. Compared by
    # acknowledgement state, not by `raised_at`: the stamp is the repo's ISO-second
    # `now_iso`, and two alarms inside the same second are indistinguishable by it.
    assert code == health.EXIT_RAISED
    assert second["alarm"]["latched"] is True
    assert second["alarm"]["acknowledged_at"] is None
    assert health.read_latch(fleet).acknowledged_at is None


# ── configurable thresholds, honestly refused when unreadable ──────────────


def test_thresholds_are_configurable(tmp_path, monkeypatch):
    fleet = _fleet_dir(tmp_path)
    _plant(fleet, [_envelope(f"directive-{n:02d}") for n in range(4)])

    monkeypatch.setenv(health.ENV_MAX_INBOX_DEPTH, "10")
    code, payload = health.alarm_payload(fleet, ledger_path=tmp_path / "claims", latch=True)
    assert code == health.EXIT_OK
    assert payload["alarm"]["raised"] is False

    monkeypatch.setenv(health.ENV_MAX_INBOX_DEPTH, "3")
    code, payload = health.alarm_payload(fleet, ledger_path=tmp_path / "claims", latch=True)
    assert code == health.EXIT_RAISED


def test_an_unreadable_threshold_is_refused(monkeypatch):
    monkeypatch.setenv(health.ENV_MAX_OLDEST_MINUTES, "soon")
    with pytest.raises(health.AlarmConfigError):
        health.thresholds()


def test_a_threshold_of_zero_is_refused(monkeypatch):
    monkeypatch.setenv(health.ENV_MAX_DEAD_LETTERS, "0")
    with pytest.raises(health.AlarmConfigError):
        health.thresholds()


# ── the CLI: emitted, and readable, not only by an ad-hoc command ──────────


def test_cmd_alarm_latches_and_ack_clears_through_the_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(health.ENV_MAX_INBOX_DEPTH, "2")
    fleet = _fleet_dir(tmp_path)
    _plant(fleet, [_envelope(f"directive-{n:02d}") for n in range(4)])
    ledger = str(tmp_path / "claims")

    code = health.main(["alarm", "--fleet-dir", str(fleet), "--ledger", ledger])
    assert code == health.EXIT_RAISED
    assert json.loads(capsys.readouterr().out)["alarm"]["latched"] is True

    for path in runaway.inbox_dir(fleet).glob("*.json"):
        path.unlink()
    assert health.main(["alarm", "--fleet-dir", str(fleet), "--ledger", ledger]) == health.EXIT_RAISED

    assert health.main(["ack", "--fleet-dir", str(fleet), "--by", "tester"]) == health.EXIT_OK
    assert health.main(["alarm", "--fleet-dir", str(fleet), "--ledger", ledger]) == health.EXIT_OK

    assert health.main(["ack", "--fleet-dir", str(fleet)]) == health.EXIT_NOT_OK


def test_cmd_check_reports_the_queue_and_the_latch_read_only(tmp_path, monkeypatch, capsys):
    fleet = _fleet_dir(tmp_path)
    _plant(fleet, [_envelope("directive-stuck", age_minutes=200.0)])
    monkeypatch.setattr(health, "loop_running", lambda: True)
    monkeypatch.setattr(health, "brain_running", lambda: True)
    monkeypatch.setattr(health.channel, "head_commit", lambda: "head1111")
    monkeypatch.setattr(health, "stalest_claim_minutes", lambda ledger: None)
    beat = {"commit": "head1111", "ts": _stamp()}
    (tmp_path / "sister.heartbeat.json").write_text(json.dumps(beat), encoding="utf-8")
    (tmp_path / "brain.heartbeat.json").write_text(json.dumps(beat), encoding="utf-8")
    monkeypatch.setattr(health, "SISTER_HEARTBEAT", tmp_path / "sister.heartbeat.json")
    monkeypatch.setattr(health, "BRAIN_HEARTBEAT", tmp_path / "brain.heartbeat.json")

    code = health.main(["check", "--fleet-dir", str(fleet), "--ledger", str(tmp_path / "claims")])
    payload = json.loads(capsys.readouterr().out)

    assert code == health.DEGRADED
    assert payload["queue"]["inbox_depth"] == 1
    assert payload["queue"]["oldest_directive"] == "directive-stuck"
    assert payload["alarm"]["measured"] is True
    assert payload["alarm"]["latched"] is False
    assert any("runaway" in reason for reason in payload["reasons"])
    assert not health.latch_path(fleet).exists(), "`check` wrote a latch"


def test_cmd_check_is_failing_on_a_latched_alarm(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(health.ENV_MAX_INBOX_DEPTH, "2")
    fleet = _fleet_dir(tmp_path)
    _plant(fleet, [_envelope(f"directive-{n:02d}") for n in range(4)])
    health.alarm_payload(fleet, ledger_path=tmp_path / "claims", latch=True)
    for path in runaway.inbox_dir(fleet).glob("*.json"):
        path.unlink()

    monkeypatch.setattr(health, "evaluate", lambda stale, ledger: (health.HEALTHY, ["rungs fine"]))

    code = health.cmd_check(_namespace(stale_minutes=30.0, ledger=str(tmp_path / "claims"), fleet_dir=str(fleet)))

    assert code == health.FAILING
    assert json.loads(capsys.readouterr().out)["alarm"]["latched"] is True


def _namespace(**kwargs):
    import argparse

    return argparse.Namespace(**kwargs)
