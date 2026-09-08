"""Gateway call-record emission tests (issue #16, criterion 5).

Every dispatch — whatever the outcome — emits ONE full gateway call record to
the audit sink and the metering sink (provider/model/tenant/agent/tokens/
latency/outcome).  Audit/metering records are never silently lost.
"""

from __future__ import annotations

import json

from proxy import contract
from proxy.model import GatewayCallRecord, TaskRequest
from proxy.sinks import JsonlCallRecordSink, ListCallRecordSink

from support import (
    NOT_JSON,
    SINGLE_PROVIDER_HEALTH,
    VALID_CLASSIFY_JSON,
    ScriptedBackend,
    build_gateway,
    make_agent,
    make_long_task,
    make_task,
)


def _request(**over):
    values = dict(tenant_id="acme", task_type="classify-route",
                  input={"input": "billing outage"})
    values.update(over)
    return TaskRequest(**values)


def _gateway(backend, audit=None, metering=None, health=None):
    return build_gateway(
        agent=make_agent(), task=make_task(), backend=backend,
        audit=audit, metering=metering, health=health,
    )[0]


class TestEmissionOnEveryOutcome:
    def test_success_emits_record_to_audit_and_metering(self):
        backend = ScriptedBackend().on(
            "deepseek", lambda c, i: backend.result("deepseek", VALID_CLASSIFY_JSON)
        )
        audit, metering = ListCallRecordSink(), ListCallRecordSink()
        gateway = _gateway(backend, audit=audit, metering=metering)
        result = gateway.dispatch("orchestrator", _request())
        assert len(audit.records) == 1
        assert len(metering.records) == 1
        assert audit.records[0] is metering.records[0]  # same full record
        record = audit.records[0]
        assert isinstance(record, GatewayCallRecord)
        assert record.outcome == contract.OUTCOME_SUCCESS
        assert record.tenant_id == "acme"
        assert record.agent_id == "orchestrator"
        assert record.task_type == "classify-route"
        assert record.capability == "orchestrate"
        assert record.provider == "deepseek"
        assert record.model == "deepseek-model"
        assert record.tier == "LOW"
        assert record.input_tokens == 10
        assert record.output_tokens == 5
        assert record.tokens == 15
        assert record.latency_ms >= 0.0
        assert record.error is None
        assert result.record == record

    def test_blocked_emits_record(self):
        backend = ScriptedBackend().on(
            "deepseek", lambda c, i: backend.result("deepseek", VALID_CLASSIFY_JSON)
        )
        from limits.budget import BudgetController, BudgetMode, BudgetPolicy
        from limits.limiter import LimitsEngine

        engine = LimitsEngine(
            budget=BudgetController(
                default_policy=BudgetPolicy(cap_tokens=50, mode=BudgetMode.ENFORCE)
            )
        )
        audit, metering = ListCallRecordSink(), ListCallRecordSink()
        gateway = build_gateway(
            agent=make_agent(), task=make_long_task(), backend=backend,
            limits=engine, audit=audit, metering=metering,
        )[0]
        result = gateway.dispatch("orchestrator", _request())
        assert result.outcome == contract.OUTCOME_BLOCKED
        assert len(audit.records) == 1
        assert audit.records[0].outcome == contract.OUTCOME_BLOCKED
        assert audit.records[0].error  # explicit, never silent

    def test_cannot_assess_emits_record(self):
        backend = ScriptedBackend().on(
            "deepseek", lambda c, i: backend.result("deepseek", NOT_JSON)
        )
        audit, metering = ListCallRecordSink(), ListCallRecordSink()
        gateway = _gateway(backend, audit=audit, metering=metering,
                           health=SINGLE_PROVIDER_HEALTH)
        result = gateway.dispatch("orchestrator", _request())
        assert result.outcome == contract.OUTCOME_CANNOT_ASSESS
        assert len(audit.records) == 1
        assert audit.records[0].outcome == contract.OUTCOME_CANNOT_ASSESS
        assert audit.records[0].error

    def test_denied_emits_record(self):
        backend = ScriptedBackend()
        audit, metering = ListCallRecordSink(), ListCallRecordSink()
        gateway, *_ = build_gateway(
            agent=make_agent("coder", capabilities=frozenset({"code-author"})),
            task=make_task(),
            backend=backend,
            audit=audit,
            metering=metering,
        )
        result = gateway.dispatch("coder", _request())
        assert result.outcome == contract.OUTCOME_DENIED
        assert len(audit.records) == 1
        assert audit.records[0].outcome == contract.OUTCOME_DENIED

    def test_no_healthy_route_emits_record(self):
        backend = ScriptedBackend()
        audit, metering = ListCallRecordSink(), ListCallRecordSink()
        gateway = _gateway(
            backend,
            audit=audit,
            metering=metering,
            health={"deepseek": False, "openai": False, "ollama": False},
        )
        result = gateway.dispatch("orchestrator", _request())
        assert result.outcome == contract.OUTCOME_NO_HEALTHY_ROUTE
        assert len(audit.records) == 1
        assert audit.records[0].outcome == contract.OUTCOME_NO_HEALTHY_ROUTE


class TestJsonlSink:
    def test_jsonl_append_and_readback(self, tmp_path):
        sink = JsonlCallRecordSink(tmp_path / "audit" / "calls.jsonl")
        backend = ScriptedBackend().on(
            "deepseek", lambda c, i: backend.result("deepseek", VALID_CLASSIFY_JSON)
        )
        audit, metering = JsonlCallRecordSink(sink.path), JsonlCallRecordSink(sink.path)
        gateway = _gateway(backend, audit=audit, metering=metering)
        gateway.dispatch("orchestrator", _request())
        gateway.dispatch("orchestrator", _request())
        rows = sink.read_records()
        assert len(rows) == 4  # 2 dispatches x (audit + metering) both on same file
        for row in rows:
            assert row["outcome"] == contract.OUTCOME_SUCCESS
            assert row["tenantId"] == "acme"
            assert row["provider"] == "deepseek"
            # one JSON object per line, parseable (registry/events style)
        with open(sink.path, "r", encoding="utf-8") as fh:
            lines = [line for line in fh if line.strip()]
        assert all(json.loads(line) for line in lines)
