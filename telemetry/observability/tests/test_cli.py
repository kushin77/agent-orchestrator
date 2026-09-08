"""CLI tests: honest exit codes (0 OK / 1 non-OK / 2 usage/store error)."""

from __future__ import annotations

import json

import pytest

from telemetry.observability.cli import main
from telemetry.observability.model import (
    KIND_MODEL_CALL,
    KIND_TRACE,
    OUTCOME_FAILED,
    OUTCOME_SUCCESS,
)
from telemetry.observability.store import JsonlSpanSink
from telemetry.observability.tests.conftest import iso_at


def write_store(path, spans):
    with JsonlSpanSink(path) as sink:
        for span in spans:
            sink.write_span(span)


def healthy_spans(span_factory, tenant="acme", total=10):
    spans = [span_factory(trace_id=f"tr-{tenant}", span_id="r",
                          kind=KIND_TRACE, parent_span_id=None,
                          tenant_id=tenant, ts=iso_at(0), name="root")]
    for i in range(total):
        spans.append(span_factory(
            trace_id=f"tr-{tenant}", span_id=f"s{i}", kind=KIND_MODEL_CALL,
            parent_span_id="r", tenant_id=tenant, ts=iso_at(1 + i),
            outcome=OUTCOME_SUCCESS, latency_ms=80.0,
            estimated_cost_usd=0.001,
        ))
    return spans


def breached_spans(span_factory, tenant="acme", total=10, failures=6):
    spans = [span_factory(trace_id=f"tr-{tenant}", span_id="r",
                          kind=KIND_TRACE, parent_span_id=None,
                          tenant_id=tenant, ts=iso_at(0), name="root")]
    for i in range(total):
        outcome = OUTCOME_FAILED if i < failures else OUTCOME_SUCCESS
        spans.append(span_factory(
            trace_id=f"tr-{tenant}", span_id=f"s{i}", kind=KIND_MODEL_CALL,
            parent_span_id="r", tenant_id=tenant, ts=iso_at(1 + i),
            outcome=outcome, latency_ms=80.0, estimated_cost_usd=0.001,
        ))
    return spans


class TestSloEval:
    def test_healthy_store_exits_zero(self, tmp_path, span_factory):
        store_path = str(tmp_path / "healthy.jsonl")
        write_store(store_path, healthy_spans(span_factory))
        rc = main(["slo-eval", "--store", store_path])
        assert rc == 0

    def test_breach_store_exits_one(self, tmp_path, span_factory):
        """A breached SLO must exit non-zero (never a silent pass)."""
        store_path = str(tmp_path / "breach.jsonl")
        write_store(store_path, breached_spans(span_factory))
        rc = main(["slo-eval", "--store", store_path])
        assert rc == 1

    def test_missing_window_tenant_exits_one(self, tmp_path, span_factory):
        """A tenant missing its SLO window exits non-zero (NOT-OK)."""
        store_path = str(tmp_path / "other.jsonl")
        write_store(store_path, healthy_spans(span_factory, tenant="acme"))
        rc = main(["slo-eval", "--store", store_path, "--tenant", "ghost"])
        assert rc == 1

    def test_missing_store_exits_two(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            main(["slo-eval", "--store",
                  str(tmp_path / "missing.jsonl")])
        assert exc.value.code == 2

    def test_bad_usage_exits_two(self, tmp_path, span_factory):
        store_path = str(tmp_path / "healthy.jsonl")
        write_store(store_path, healthy_spans(span_factory))
        with pytest.raises(SystemExit) as exc:
            main(["slo-eval", "--store", store_path, "--bogus-flag"])
        assert exc.value.code == 2


class TestBreach:
    def test_breach_command_exits_one_on_alerts(self, tmp_path, span_factory):
        store_path = str(tmp_path / "breach.jsonl")
        write_store(store_path, breached_spans(span_factory))
        rc = main(["breach", "--store", store_path])
        assert rc == 1

    def test_breach_command_exits_zero_healthy(self, tmp_path, span_factory):
        store_path = str(tmp_path / "healthy.jsonl")
        write_store(store_path, healthy_spans(span_factory))
        rc = main(["breach", "--store", store_path])
        assert rc == 0

    def test_silent_tenant_breach_command_fails(self, tmp_path, span_factory):
        """Healthy tenant data + a silent tenant -> alert -> exit 1."""
        store_path = str(tmp_path / "mixed.jsonl")
        write_store(store_path, healthy_spans(span_factory, tenant="acme"))
        rc = main(["breach", "--store", store_path, "--tenant", "ghost"])
        assert rc == 1


class TestOtherCommands:
    def test_seed_writes_store(self, tmp_path):
        store_path = str(tmp_path / "seed.jsonl")
        rc = main(["seed", "--store", store_path, "--tenants", "acme"])
        assert rc == 0
        assert JsonlSpanSink  # ensure import remains valid
        lines = open(store_path, encoding="utf-8").read().strip().splitlines()
        assert len(lines) > 0

    def test_seed_breach_mode_is_evaluable_red(self, tmp_path):
        store_path = str(tmp_path / "seed-breach.jsonl")
        assert main(["seed", "--store", store_path,
                     "--tenants", "acme", "--mode", "breach"]) == 0
        rc = main(["slo-eval", "--store", store_path])
        assert rc == 1  # seeded breach data fails the gate honestly

    def test_usage_exits_zero_and_prints_json(self, tmp_path, span_factory,
                                              capsys):
        store_path = str(tmp_path / "healthy.jsonl")
        write_store(store_path, healthy_spans(span_factory))
        rc = main(["usage", "--store", store_path])
        out = capsys.readouterr().out
        assert rc == 0
        assert json.loads(out)["dimensions"] == ["tenant"]

    def test_dashboard_writes_files_and_exits_zero(
        self, tmp_path, span_factory
    ):
        store_path = str(tmp_path / "healthy.jsonl")
        write_store(store_path, healthy_spans(span_factory))
        html = str(tmp_path / "dash.html")
        data = str(tmp_path / "dash.json")
        rc = main(["dashboard", "--store", store_path, "--html", html,
                   "--json", data])
        assert rc == 0
        assert open(html, encoding="utf-8").read().startswith("<!doctype html>")
        assert "tenantId" in open(data, encoding="utf-8").read()

    def test_trace_command_exits_zero(self, tmp_path, span_factory):
        store_path = str(tmp_path / "healthy.jsonl")
        write_store(store_path, healthy_spans(span_factory))
        rc = main(["trace", "--store", store_path, "--tenant", "acme"])
        assert rc == 0

    def test_trace_no_match_exits_one(self, tmp_path, span_factory):
        store_path = str(tmp_path / "healthy.jsonl")
        write_store(store_path, healthy_spans(span_factory))
        rc = main(["trace", "--store", store_path, "--trace-id", "nope"])
        assert rc == 1

    def test_report_exits_zero(self, tmp_path, span_factory, capsys):
        store_path = str(tmp_path / "healthy.jsonl")
        write_store(store_path, healthy_spans(span_factory))
        rc = main(["report", "--store", store_path])
        assert rc == 0
        assert "health report" in capsys.readouterr().out
