"""The turn reaches a provider *through* ``gateway/proxy`` — and it is recorded.

The ADR's single-authority commitment is only real if a turn is one dispatch
through the merged funnel.  These tests hold that: the surface delegates (a spy
proves the call), the dispatch emits its ``GatewayCallRecord`` to the audit and
metering sinks whatever the outcome, the routing stamp is the chooser's, and a
capability boundary refuses at the proxy rather than at the chat surface.
"""

from __future__ import annotations

import pytest

from chat_fixtures import (
    AGENT,
    TENANT,
    grounded_payload,
    token_of,
    with_grounding,
)

from gateway.chat.conversation import ConversationStore
from gateway.chat.errors import ChatSurfaceError
from gateway.chat.resolver import TASK_TYPE_ANSWER, TASK_TYPE_REFUSE
from gateway.chat.surface import ChatSurface
from gateway.chat.wiring import build_gateway


def refusal_of(callable_, *args, **kwargs) -> ChatSurfaceError:
    with pytest.raises(ChatSurfaceError) as raised:
        callable_(*args, **kwargs)
    return raised.value


def test_the_turn_is_one_dispatch_and_one_call_record(
    rigged, body, credential, grounding
):
    rigged.script(grounded_payload())
    response = rigged.surface.completions(
        with_grounding(body, grounding), token=token_of(credential)
    )
    assert len(rigged.records) == 1, "one turn must emit exactly one call record"
    assert len(rigged.metered) == 1, "the metering sink saw a different number"
    record = rigged.records[0]
    assert record.tenant_id == TENANT
    assert record.agent_id == AGENT
    assert record.task_type == TASK_TYPE_ANSWER
    assert record.outcome == "success"
    assert record.provider and record.model
    # the record the caller can see is the record the sinks were given
    assert response["ao"]["dispatch"]["record"]["requestId"] == response["ao"]["turnId"]
    assert response["ao"]["dispatch"]["record"]["provider"] == record.provider


def test_the_surface_delegates_to_the_proxy_gateway(rigged, body, credential, grounding):
    """A spy proves the single model path is the proxy's dispatch, not a fork."""
    calls: list[tuple[str, object]] = []
    inner = rigged.surface.gateway

    class Spy:
        def dispatch(self, agent_id, task_request):
            calls.append((agent_id, task_request))
            return inner.dispatch(agent_id, task_request)

        def dispatch_stream(self, agent_id, task_request):
            return inner.dispatch_stream(agent_id, task_request)

    rigged.surface.gateway = Spy()
    rigged.script(grounded_payload())
    rigged.surface.completions(with_grounding(body, grounding), token=token_of(credential))
    assert len(calls) == 1
    agent_id, task_request = calls[0]
    assert agent_id == AGENT
    assert task_request.task_type == TASK_TYPE_ANSWER
    assert task_request.tenant_id == TENANT
    # the prompt module rendered the grounding the guard admitted, not the raw body
    assert task_request.input["question"] == body["messages"][0]["content"]


def test_a_turn_without_grounding_runs_the_refuse_module(
    rigged, body, credential, grounding
):
    """No admitted fragment -> the published ``chat-refuse`` module, not a guess."""
    from chat_fixtures import refusal_payload

    rigged.script(refusal_payload())
    empty = dict(body)
    empty["grounding"] = {"prefix": "", "fragments": []}
    response = rigged.surface.completions(empty, token=token_of(credential))
    assert rigged.records[0].task_type == TASK_TYPE_REFUSE
    assert response["ao"]["promptModule"] == TASK_TYPE_REFUSE
    assert response["ao"]["grounding"]["state"] == "NO_DATA"
    assert "no-data" in response["choices"][0]["message"]["content"]


def test_the_tier_is_the_choosers_stamp_not_the_clients_claim(
    rigged, body, credential, grounding
):
    """A client asks for a tier; the FinOps chooser's routing stamp decides."""
    rigged.script(grounded_payload())
    response = rigged.surface.completions(
        with_grounding(dict(body, model="HIGH"), grounding), token=token_of(credential)
    )
    tier = response["ao"]["tier"]
    assert tier["requested"] == "HIGH"
    assert tier["resolved"] == "LOW"  # the research class default (tiers.yaml)
    assert tier["claimHonoured"] is False
    assert tier["source"] == "gateway_record"
    assert response["ao"]["degraded"]["degraded"] is True
    assert response["ao"]["degraded"]["toTier"] == "LOW"


def test_a_capability_boundary_refuses_at_the_proxy(rigged, body, grounding, mint):
    """An agent that does not hold the route's capability is denied by the router."""
    credential = mint(agent_id="architecture-sme")
    error = refusal_of(
        rigged.surface.completions,
        with_grounding(body, grounding),
        token=token_of(credential),
    )
    assert error.status == 403
    assert error.to_openai_error()["error"]["code"] == "capability_denied"
    # the denial is still a recorded dispatch: a refused turn is auditable
    assert [record.outcome for record in rigged.records] == ["denied"]
    assert error.extra["record"]["outcome"] == "denied"


def test_an_unreachable_tier_is_a_503_and_not_an_answer(
    registry_on, signing_key, revocation_store, memory_store, budget_guard, mint
):
    """Every candidate unhealthy -> the proxy's ``no_healthy_route``, fail closed."""
    gateway, _wired = build_gateway(
        health={"deepseek": False, "openai": False, "anthropic": False, "ollama": False}
    )
    surface = ChatSurface(
        gateway,
        registry_path=registry_on,
        signing_key=signing_key,
        revocation_store=revocation_store,
        conversation=ConversationStore(memory_store=memory_store),
        budget_guard=budget_guard,
    )
    credential = mint()
    error = refusal_of(
        surface.completions,
        with_grounding(
            {"model": "MED", "messages": [{"role": "user", "content": "status?"}]},
            {"fragments": [], "prefix": ""},
        ),
        token=token_of(credential),
    )
    assert error.status == 503
    assert error.to_openai_error()["error"]["code"] == "no_healthy_route"
    assert gateway.audit_sink.records[-1].outcome == "no_healthy_route"
