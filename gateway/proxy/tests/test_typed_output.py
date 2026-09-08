"""Typed-output validation tests (issue #16, criterion 4).

Typed response must satisfy the prompt module's outputSchema. Invalid output
is retried once (against the next healthy candidate, or the same candidate
when it is the last hop) and a second invalid output is an explicit
CANNOT-ASSESS — never a silent pass.
"""

from __future__ import annotations

from proxy import contract
from proxy.backend import BackendOutputInvalidError
from proxy.model import TaskRequest

from support import (
    INVALID_CLASSIFY_JSON,
    NOT_JSON,
    SINGLE_PROVIDER_HEALTH,
    VALID_CLASSIFY_JSON,
    ScriptedBackend,
    build_gateway,
    make_agent,
    make_task,
)


def _request(**over):
    values = dict(tenant_id="acme", task_type="classify-route",
                  input={"input": "billing outage"})
    values.update(over)
    return TaskRequest(**values)


class TestTypedOutputRetry:
    def test_invalid_then_valid_via_next_candidate_is_success(self):
        backend = ScriptedBackend()
        backend.on("deepseek", lambda c, i: backend.result("deepseek", NOT_JSON))
        backend.on("openai", lambda c, i: backend.result("openai", VALID_CLASSIFY_JSON))
        gateway, *_ = build_gateway(agent=make_agent(), task=make_task(), backend=backend)

        result = gateway.dispatch("orchestrator", _request())
        assert result.served()
        assert result.outcome == contract.OUTCOME_SUCCESS
        assert result.provider == "openai"  # the retry was served by the fallback
        assert [c.provider for c, _ in backend.calls] == ["deepseek", "openai"]

    def test_invalid_twice_is_cannot_assess(self):
        backend = ScriptedBackend()
        backend.on("deepseek", lambda c, i: backend.result("deepseek", NOT_JSON))
        backend.on("openai", lambda c, i: backend.result("openai", NOT_JSON))
        gateway, *_ = build_gateway(agent=make_agent(), task=make_task(), backend=backend)

        result = gateway.dispatch("orchestrator", _request())
        assert result.outcome == contract.OUTCOME_CANNOT_ASSESS
        assert not result.served()
        assert result.content is None
        # initial attempt + ONE retry, then explicit cannot_assess
        assert len(backend.calls) == 2
        assert result.record.outcome == contract.OUTCOME_CANNOT_ASSESS
        assert result.record.error

    def test_single_candidate_invalid_then_valid_retry_same_candidate(self):
        backend = ScriptedBackend()

        def flaky(candidate, invocation):
            n = len(backend.calls)
            raw = NOT_JSON if n == 1 else VALID_CLASSIFY_JSON
            return backend.result("deepseek", raw)

        backend.on("deepseek", flaky)
        gateway, *_ = build_gateway(
            agent=make_agent(), task=make_task(), backend=backend,
            health=SINGLE_PROVIDER_HEALTH,
        )

        result = gateway.dispatch("orchestrator", _request())
        assert result.served()
        assert result.provider == "deepseek"
        assert len(backend.calls) == 2  # initial + one retry

    def test_single_candidate_invalid_twice_is_cannot_assess(self):
        backend = ScriptedBackend().on(
            "deepseek", lambda c, i: backend.result("deepseek", INVALID_CLASSIFY_JSON)
        )
        gateway, *_ = build_gateway(
            agent=make_agent(), task=make_task(), backend=backend,
            health=SINGLE_PROVIDER_HEALTH,
        )

        result = gateway.dispatch("orchestrator", _request())
        assert result.outcome == contract.OUTCOME_CANNOT_ASSESS
        assert len(backend.calls) == 2  # retried the same (only) candidate once
        assert result.record.error is not None

    def test_provider_reported_invalid_output_counts_as_attempt(self):
        backend = ScriptedBackend()
        backend.on("deepseek", lambda c, i: (_ for _ in ()).throw(
            BackendOutputInvalidError("adapter rejected invalid output")))
        backend.on("openai", lambda c, i: backend.result("openai", VALID_CLASSIFY_JSON))
        gateway, *_ = build_gateway(agent=make_agent(), task=make_task(), backend=backend)

        result = gateway.dispatch("orchestrator", _request())
        assert result.served()
        assert result.provider == "openai"

    def test_no_schema_returns_text_content(self):
        backend = ScriptedBackend().on(
            "deepseek", lambda c, i: backend.result("deepseek", "plain text reply")
        )
        gateway, *_ = build_gateway(
            agent=make_agent(), task=make_task(schema=None), backend=backend
        )
        result = gateway.dispatch("orchestrator", _request())
        assert result.served()
        assert result.content == "plain text reply"
