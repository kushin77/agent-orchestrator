"""End-to-end integration tests (issue #16) over the REAL sibling modules.

Uses ``proxy.wiring.build_real_gateway`` which composes the merged contracts
read-only: personas + profile mapping (issue #9/#11) for the agent resolver,
the prompt library (issue #13) for the task resolver, the FinOps chooser over
the real ``tiers.yaml`` (issue #17) for the tier decision, the provider
registry (issue #15) over an offline scriptable transport rig, and the limits
facade (issue #19).  Every provider call is a canned response — no sockets.
"""

from __future__ import annotations

import json

import pytest

from proxy import contract
from proxy.model import TaskRequest
from proxy.sinks import JsonlCallRecordSink
from proxy.wiring import build_real_gateway

CLASSIFY_OK = json.dumps(
    {"route": "support", "priority": "high", "confidence": 0.92,
     "reasoning": "Customer reported an outage on the billing API."}
)
CODE_REVIEW_OK = json.dumps(
    {"verdict": "approve", "blockingFindings": [],
     "summary": "The diff is small, focused and correct."}
)
SUMMARIZE_OK = json.dumps(
    {"summary": "The team agreed to ship the current milestone.",
     "keyPoints": ["Ship the current milestone", "Follow up on the flaky test"],
     "actionItems": ["alice: merge the release PR"],
     "wordCount": 42}
)


@pytest.fixture()
def wired():
    """A fresh real-module gateway per test (isolated transport scripts)."""
    return build_real_gateway()


def _req(task_type: str, **input_vars):
    return TaskRequest(tenant_id="acme", task_type=task_type, input=input_vars)


class TestRealDispatchSuccess:
    def test_classify_route_by_orchestrator(self, wired):
        wired.rig.script_success("deepseek", CLASSIFY_OK)
        result = wired.gateway.dispatch("orchestrator",
                                        _req("classify-route", input="billing outage on api"))
        assert result.outcome == contract.OUTCOME_SUCCESS
        assert result.served()
        assert result.provider == "deepseek"
        assert result.model == "deepseek-chat"
        assert result.tier == "LOW"
        assert result.content["route"] == "support"
        # routing stamp on the call record
        assert result.record.capability == "orchestrate"
        assert result.record.task_class == "classify-route"

    def test_code_review_verdict_by_reviewer(self, wired):
        wired.rig.script_success("deepseek", CODE_REVIEW_OK)
        result = wired.gateway.dispatch(
            "reviewer",
            _req("code-review-verdict", diff="+raise_on_invalid()", context="small PR"),
        )
        assert result.outcome == contract.OUTCOME_SUCCESS
        assert result.content["verdict"] == "approve"
        assert result.record.capability == "code-review"
        assert result.record.task_class == "code-review"
        assert result.tier in ("LOW", "MED")

    def test_summarize_by_researcher(self, wired):
        wired.rig.script_success("deepseek", SUMMARIZE_OK)
        result = wired.gateway.dispatch(
            "researcher", _req("summarize", thread="long planning thread text")
        )
        assert result.outcome == contract.OUTCOME_SUCCESS
        assert result.content["wordCount"] == 42
        assert result.record.capability == "research"


class TestRealFallback:
    def test_primary_unhealthy_falls_to_cloud_fallback(self, wired):
        wired = build_real_gateway(health={"deepseek": False})
        wired.rig.script_success("openai", CLASSIFY_OK)
        result = wired.gateway.dispatch("orchestrator",
                                        _req("classify-route", input="billing outage"))
        assert result.outcome == contract.OUTCOME_SUCCESS
        assert result.provider == "openai"
        assert result.model == "gpt-4o-mini"

    def test_all_unhealthy_is_explicit_failure(self):
        wired = build_real_gateway(
            health={"deepseek": False, "openai": False, "ollama": False,
                    "anthropic": False}
        )
        result = wired.gateway.dispatch("orchestrator",
                                        _req("classify-route", input="billing outage"))
        assert result.outcome == contract.OUTCOME_NO_HEALTHY_ROUTE
        assert not result.served()
        assert result.content is None


class TestRealBoundaries:
    def test_capability_denied_real_personas(self, wired):
        # real persona 'coder' does not hold the 'orchestrate' capability
        result = wired.gateway.dispatch("coder",
                                        _req("classify-route", input="billing outage"))
        assert result.outcome == contract.OUTCOME_DENIED
        assert result.content is None

    def test_budget_exhausted_blocks_real_limits(self):
        from limits.budget import BudgetController, BudgetMode, BudgetPolicy
        from limits.limiter import LimitsEngine

        enforce = LimitsEngine(
            budget=BudgetController(
                default_policy=BudgetPolicy(cap_tokens=5, mode=BudgetMode.ENFORCE)
            )
        )
        wired = build_real_gateway(limits_engine=enforce)
        wired.rig.script_success("deepseek", CLASSIFY_OK)
        result = wired.gateway.dispatch("orchestrator",
                                        _req("classify-route", input="billing outage"))
        assert result.outcome == contract.OUTCOME_BLOCKED
        assert not result.served()
        assert result.content is None

    def test_invalid_output_cannot_assess_real_modules(self, wired):
        wired.rig.set_default("definitely not schema-shaped json {{{")
        result = wired.gateway.dispatch("orchestrator",
                                        _req("classify-route", input="billing outage"))
        assert result.outcome == contract.OUTCOME_CANNOT_ASSESS
        assert not result.served()
        assert result.content is None


class TestRealObservability:
    def test_finops_cost_attribution_emitted(self, wired):
        wired.rig.script_success("deepseek", CLASSIFY_OK)
        before = len(wired.finops_sink.records)
        result = wired.gateway.dispatch("orchestrator",
                                        _req("classify-route", input="billing outage"))
        assert result.outcome == contract.OUTCOME_SUCCESS
        # the FinOps chooser emitted its own cost-attribution CallRecord
        assert len(wired.finops_sink.records) == before + 1
        record = wired.finops_sink.records[-1]
        assert record.tenant_id == "acme"
        assert record.task_class == "classify-route"

    def test_gateway_call_record_emitted_to_audit_and_metering(self, wired):
        wired.rig.script_success("deepseek", CLASSIFY_OK)
        assert len(wired.audit_sink.records) == 0
        result = wired.gateway.dispatch("orchestrator",
                                        _req("classify-route", input="billing outage"))
        assert result.outcome == contract.OUTCOME_SUCCESS
        assert len(wired.audit_sink.records) == 1
        assert len(wired.metering_sink.records) == 1
        rec = wired.audit_sink.records[0].to_dict()
        assert rec["outcome"] == "success"
        assert rec["provider"] == "deepseek"
        assert rec["model"] == "deepseek-chat"
        assert rec["tokens"] > 0
        assert rec["tenantId"] == "acme"
        assert rec["agentId"] == "orchestrator"

    def test_jsonl_audit_integration(self, tmp_path):
        sink = JsonlCallRecordSink(tmp_path / "audit.jsonl")
        wired = build_real_gateway(audit_sink=sink, metering_sink=sink)
        wired.rig.script_success("deepseek", CLASSIFY_OK)
        wired.gateway.dispatch("orchestrator",
                               _req("classify-route", input="billing outage"))
        rows = sink.read_records()
        assert len(rows) == 2  # audit + metering share the append-only JSONL
        assert {row["outcome"] for row in rows} == {"success"}


class TestRealStreaming:
    def test_dispatch_stream_incremental_events(self, wired):
        wired.rig.script_success("deepseek", CLASSIFY_OK)
        items = list(
            wired.gateway.dispatch_stream(
                "orchestrator", _req("classify-route", input="billing outage")
            )
        )
        stages = [item.stage for item in items if hasattr(item, "stage")]
        assert stages[0] == "received"
        assert "route_selected" in stages
        assert "attempt" in stages
        assert stages[-1] == "completed"
        terminal = items[-1]
        assert terminal.outcome == contract.OUTCOME_SUCCESS
