"""Fleet health signal — cmr-style healthy/degraded/failing (issue #163)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import health


def test_evaluate_is_failing_when_the_loop_is_not_running(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "loop_running", lambda: False)
    level, reasons = health.evaluate(30.0, tmp_path / "claims.jsonl")
    assert level == health.FAILING
    assert any("not running" in r for r in reasons)


def test_evaluate_is_healthy_when_loop_runs_and_slog_is_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "loop_running", lambda: True)
    slog = tmp_path / "slog.jsonl"
    slog.write_text("{}\n")
    monkeypatch.setattr(health, "SLOG", slog)
    monkeypatch.setattr(health, "stalest_claim_minutes", lambda ledger: None)
    level, reasons = health.evaluate(30.0, tmp_path / "claims.jsonl")
    assert level == health.HEALTHY
    assert reasons == ["loop running, slog fresh, no wedged claims"]


def test_evaluate_is_degraded_when_slog_is_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "loop_running", lambda: True)
    slog = tmp_path / "slog.jsonl"
    slog.write_text("{}\n")
    old = (datetime.now(timezone.utc) - timedelta(minutes=90)).timestamp()
    import os

    os.utime(slog, (old, old))
    monkeypatch.setattr(health, "SLOG", slog)
    monkeypatch.setattr(health, "stalest_claim_minutes", lambda ledger: None)
    level, reasons = health.evaluate(30.0, tmp_path / "claims.jsonl")
    assert level == health.DEGRADED
    assert any("stale" in r for r in reasons)


def test_evaluate_is_degraded_when_a_claim_is_wedged(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "loop_running", lambda: True)
    slog = tmp_path / "slog.jsonl"
    slog.write_text("{}\n")
    monkeypatch.setattr(health, "SLOG", slog)
    monkeypatch.setattr(health, "stalest_claim_minutes", lambda ledger: 45.0)
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
