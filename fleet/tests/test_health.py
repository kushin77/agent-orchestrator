"""Fleet health signal — cmr-style healthy/degraded/failing (issue #163, #277)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import health


def _beat(age_seconds: float = 5.0, commit: str = "head1111") -> dict:
    """A rung heartbeat whose timestamp is `age_seconds` old."""
    stamp = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return {"pid": 111, "state": "idle", "commit": commit, "ts": stamp.strftime("%Y-%m-%dT%H:%M:%SZ")}


def _fleet(
    tmp_path,
    monkeypatch,
    *,
    sister_running: bool = True,
    brain_running: bool = True,
    sister_beat: dict | None = None,
    brain_beat: dict | None = None,
    claims: float | None = None,
) -> None:
    """Point health at a fake fleet: both rungs up, with fresh matching heartbeats."""
    monkeypatch.setattr(health, "loop_running", lambda: sister_running)
    monkeypatch.setattr(health, "brain_running", lambda: brain_running)
    monkeypatch.setattr(health.channel, "head_commit", lambda: "head1111")
    sister_path = tmp_path / "sister.heartbeat.json"
    brain_path = tmp_path / "brain.heartbeat.json"
    sister_path.write_text(json.dumps(sister_beat if sister_beat is not None else _beat()), encoding="utf-8")
    brain_path.write_text(json.dumps(brain_beat if brain_beat is not None else _beat()), encoding="utf-8")
    monkeypatch.setattr(health, "SISTER_HEARTBEAT", sister_path)
    monkeypatch.setattr(health, "BRAIN_HEARTBEAT", brain_path)
    monkeypatch.setattr(health, "stalest_claim_minutes", lambda ledger: claims)


def test_evaluate_is_failing_when_the_sister_is_not_running(tmp_path, monkeypatch):
    _fleet(tmp_path, monkeypatch, sister_running=False)
    level, reasons = health.evaluate(30.0, tmp_path / "claims.jsonl")
    assert level == health.FAILING
    assert any("not running" in r for r in reasons)


def test_evaluate_is_healthy_when_both_rungs_run_current_builds(tmp_path, monkeypatch):
    _fleet(tmp_path, monkeypatch)
    level, reasons = health.evaluate(30.0, tmp_path / "claims.jsonl")
    assert level == health.HEALTHY
    assert reasons == ["sister and brain running current builds with fresh heartbeats, no wedged claims"]


def test_evaluate_is_degraded_when_the_brain_is_dead(tmp_path, monkeypatch):
    """#277: with the brain down the fleet is NOT healthy — nothing can be dispatched."""
    _fleet(tmp_path, monkeypatch, brain_running=False)
    level, reasons = health.evaluate(30.0, tmp_path / "claims.jsonl")
    assert level != health.HEALTHY
    assert level == health.DEGRADED
    assert any("brain" in r and "not running" in r for r in reasons)


def test_evaluate_is_degraded_when_the_brain_has_no_heartbeat(tmp_path, monkeypatch):
    """A live brain on a pre-heartbeat build is not healthy either."""
    _fleet(tmp_path, monkeypatch)
    monkeypatch.setattr(health, "BRAIN_HEARTBEAT", tmp_path / "absent.json")
    level, reasons = health.evaluate(30.0, tmp_path / "claims.jsonl")
    assert level == health.DEGRADED
    assert any("brain" in r and "no heartbeat" in r for r in reasons)


def test_evaluate_is_degraded_when_the_brain_runs_old_code(tmp_path, monkeypatch):
    _fleet(tmp_path, monkeypatch, brain_beat=_beat(commit="old0000"))
    level, reasons = health.evaluate(30.0, tmp_path / "claims.jsonl")
    assert level == health.DEGRADED
    assert any("brain" in r and "old0000" in r for r in reasons)


def test_evaluate_is_degraded_when_a_heartbeat_is_stale(tmp_path, monkeypatch):
    """Staleness comes from the heartbeat age, not the slog mtime."""
    _fleet(tmp_path, monkeypatch, sister_beat=_beat(age_seconds=600.0))
    level, reasons = health.evaluate(30.0, tmp_path / "claims.jsonl")
    assert level == health.DEGRADED
    assert any("sister" in r and "stale" in r for r in reasons)


def test_evaluate_is_healthy_when_idle_and_no_slog_exists(tmp_path, monkeypatch):
    """#277 secondary: an idle-but-healthy fleet must not read `degraded` just
    because `.fleet/slog.jsonl` has not moved."""
    _fleet(tmp_path, monkeypatch)
    assert not (tmp_path / "slog.jsonl").exists()
    level, _reasons = health.evaluate(30.0, tmp_path / "claims.jsonl")
    assert level == health.HEALTHY


def test_evaluate_is_degraded_when_a_claim_is_wedged(tmp_path, monkeypatch):
    _fleet(tmp_path, monkeypatch, claims=45.0)
    level, reasons = health.evaluate(30.0, tmp_path / "claims.jsonl")
    assert level == health.DEGRADED
    assert any("held" in r for r in reasons)


def test_cmd_check_exit_code_matches_the_signal(monkeypatch, capsys):
    monkeypatch.setattr(health, "evaluate", lambda stale, ledger: (health.DEGRADED, ["x"]))
    rc = health.cmd_check(argparse_namespace(stale_minutes=30.0, ledger="x"))
    assert rc == health.DEGRADED
    out = capsys.readouterr().out
    assert '"status": "degraded"' in out


def argparse_namespace(**kwargs):
    import argparse

    return argparse.Namespace(**kwargs)
