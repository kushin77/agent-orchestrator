"""Wave sync CLI tri-state exit codes (issue #181)."""

from __future__ import annotations

import cli


def test_bootstrap_offline_with_missing_pins_is_cannot_assess(tmp_path, capsys):
    rc = cli.main(
        [
            "bootstrap", "--since", "x", "--out", str(tmp_path / "r.md"),
            "--pins", str(tmp_path / "nope.json"),
        ]
    )
    assert rc == 2
    assert "CANNOT-ASSESS" in capsys.readouterr().err


def test_ledger_append_then_query_round_trips(tmp_path, capsys):
    ledger_path = tmp_path / "ledger.jsonl"
    rc = cli.main(
        [
            "ledger-append",
            "--wave-id", "w1",
            "--started-at", "2026-09-13T10:00:00Z",
            "--finished-at", "2026-09-13T10:00:10Z",
            "--issues", "10",
            "--verify-failures", "1",
            "--pin", "deepseek=abc",
            "--direction", "kushin77/code-indexing#200",
            "--ledger", str(ledger_path),
        ]
    )
    assert rc == 0
    capsys.readouterr()
    rc = cli.main(["ledger-query", "--ledger", str(ledger_path)])
    assert rc == 0


def test_ledger_query_with_no_matching_wave_is_not_ok(tmp_path, capsys):
    ledger_path = tmp_path / "ledger.jsonl"
    cli.main(
        [
            "ledger-append", "--wave-id", "w1",
            "--issues", "1", "--ledger", str(ledger_path),
        ]
    )
    capsys.readouterr()
    rc = cli.main(["ledger-query", "--wave-id", "w9", "--ledger", str(ledger_path)])
    assert rc == 1


def test_ledger_query_with_missing_ledger_is_cannot_assess(tmp_path, capsys):
    rc = cli.main(["ledger-query", "--ledger", str(tmp_path / "nope.jsonl")])
    assert rc == 2
    assert "CANNOT-ASSESS" in capsys.readouterr().err


def test_ledger_append_rejects_negative_counts(tmp_path, capsys):
    rc = cli.main(
        ["ledger-append", "--wave-id", "w1", "--issues", "-3", "--ledger", str(tmp_path / "l.jsonl")]
    )
    assert rc == 1
    assert "NOT-OK" in capsys.readouterr().err


def test_ledger_append_rejects_an_unknown_pin_module(tmp_path):
    import pytest

    with pytest.raises(SystemExit) as excinfo:
        cli.main(
            [
                "ledger-append", "--wave-id", "w1",
                "--pin", "other=abc", "--ledger", str(tmp_path / "l.jsonl"),
            ]
        )
    assert excinfo.value.code == 2
