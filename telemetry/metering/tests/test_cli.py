"""CLI tests (issue #33): offline evidence surface for the metering engine."""

from __future__ import annotations

import json

from telemetry.metering.cli import main

from conftest import (
    call_record,
    gateway_record,
    metering_record,
    model_call_event,
)


def test_estimate_known_model_exit_zero(capsys):
    code = main(
        [
            "estimate",
            "--provider",
            "gemini",
            "--model",
            "gemini-2.5-pro",
            "--input",
            "250000",
            "--output",
            "5000",
        ]
    )
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["costUsd"] == 0.7
    assert out["longContextApplied"] is True


def test_estimate_unknown_model_exit_unmetered(capsys):
    code = main(
        ["estimate", "--provider", "futureco", "--model", "future-model-x",
         "--input", "100", "--output", "10"]
    )
    assert code == 2  # unmetered — fail closed, never priced as zero
    assert "UNMETERED" in capsys.readouterr().out


def test_cards_lists_all_providers(capsys):
    assert main(["cards"]) == 0
    out = capsys.readouterr().out
    for provider in ("anthropic", "deepseek", "gemini", "ollama", "openai"):
        assert provider in out


def test_ingest_then_report_round_trip(tmp_path, capsys):
    feed = tmp_path / "feed.jsonl"
    store_path = tmp_path / "usage.jsonl"
    with open(feed, "w", encoding="utf-8") as fh:
        for rec in [
            model_call_event(),
            call_record(),
            gateway_record(outcome="blocked"),
            metering_record(outcome="cache_hit", cached=True, zero_cost=True),
        ]:
            fh.write(json.dumps(rec) + "\n")

    assert main(["ingest", "--feed", str(feed), "--store", str(store_path)]) == 0
    out = capsys.readouterr().out
    summary = json.loads(out.splitlines()[0])
    assert summary["ingested"] == 4
    assert summary["duplicates"] == 0

    # replay the identical feed -> nothing new, no double counting
    assert main(["ingest", "--feed", str(feed), "--store", str(store_path)]) == 0
    replay = json.loads(capsys.readouterr().out.splitlines()[0])
    assert replay["ingested"] == 0
    assert replay["duplicates"] == 4

    # report over the store
    assert main(["report", "--store", str(store_path)]) == 0
    report_out = capsys.readouterr().out
    assert "per-tenant" in report_out
    assert "provider mix" in report_out


def test_report_tenant_view(tmp_path, capsys):
    store_path = tmp_path / "usage.jsonl"
    feed = tmp_path / "feed.jsonl"
    with open(feed, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(model_call_event(tenant="acme")) + "\n")
        fh.write(json.dumps(model_call_event(tenant="globex")) + "\n")
    assert main(["ingest", "--feed", str(feed), "--store", str(store_path)]) == 0
    capsys.readouterr()
    assert main(["report", "--store", str(store_path), "--tenant", "acme"]) == 0
    out = capsys.readouterr().out
    assert "== tenant acme ==" in out


def test_demo_walkthrough_runs(capsys):
    assert main(["demo"]) == 0
    out = capsys.readouterr().out
    assert "ingest summary" in out
    assert "UNMETERED" in out or "unmetered" in out
    assert "provider mix" in out


def test_budget_cli_observe_vs_enforce(tmp_path, capsys):
    store_path = tmp_path / "usage.jsonl"
    # globex is an enforce tenant in the default config with a 1M limit; burn 1.5M.
    store_path.touch()
    from telemetry.metering.intake import MeteringIntake
    from telemetry.metering.store import JsonlUsageStore

    store = JsonlUsageStore(store_path)
    intake = MeteringIntake(store=store)
    intake.ingest(model_call_event(tenant="globex", input_tokens=1_500_000,
                                   output_tokens=0))
    assert (
        main(
            [
                "budget",
                "--tenant",
                "globex",
                "--store",
                str(store_path),
                "--day",
                "2026-09-08",
            ]
        )
        == 1
    )
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["decision"] == "block"
    assert verdict["mode"] == "enforce"
