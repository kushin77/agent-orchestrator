"""Four-provider gateway funnel tests (issue #334).

Proves the full offline funnel for the four providers of the gateway path —
paperclip / hermes / deepseek / ollama — through the REAL gateway proxy +
providers registry + FinOps chooser + limits facade, driven by the scriptable
transport rig (no sockets, no network, no real keys):

- request/response mapping (each provider's wire shape parsed by its adapter),
- tier selection (task class -> FinOps ladder tier -> registry tier),
- the ordered fallback chain (primary -> ... -> local Ollama) at runtime,
- usage accounting (input/output tokens on the gateway call record),
- a failure path (every candidate unavailable -> explicit failure).

The four providers are the routing.yaml chain members plus the two team-local
hops (paperclip/hermes).  The team agents that hold a routable capability
(deepseek/paperclip -> research) are dispatched directly to prove routing-group
pinning; the hermes/ollama PROVIDERS are proven through the orchestrator with
the real health signal, exactly as the golden-path conformance stage does.
"""

from __future__ import annotations

import json

import pytest

from e2e.wiring import (
    PROVIDER_COPILOT,
    PROVIDER_DEEPSEEK,
    PROVIDER_HERMES,
    PROVIDER_OLLAMA,
    PROVIDER_PAPERCLIP,
    build_team_gateway,
)
from proxy import contract
from proxy.model import TaskRequest
from providers.errors import ProviderUnavailableError

CLASSIFY_OK = json.dumps(
    {"route": "support", "priority": "high", "confidence": 0.92,
     "reasoning": "Customer reported an outage on the billing API."}
)
SUMMARIZE_OK = json.dumps(
    {"summary": "The team agreed to ship the current milestone.",
     "keyPoints": ["Ship the current milestone", "Follow up on the flaky test"],
     "actionItems": ["alice: merge the release PR"],
     "wordCount": 42}
)


def _dispatch(wired, agent_id, task_type, **input_vars):
    return wired.gateway.dispatch(
        agent_id,
        TaskRequest(tenant_id="acme", task_type=task_type, input=input_vars),
    )


def _classify(wired, **input_vars):
    return _dispatch(wired, "orchestrator", "classify-route", input="billing outage", **input_vars)


# --------------------------------------------------------------------------- #
# request/response + tier + usage, one test per provider
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "provider,model,health",
    [
        (PROVIDER_DEEPSEEK, "deepseek-chat", {}),
        (PROVIDER_OLLAMA, "llama3.2", {"deepseek": False, "openai": False}),
        (PROVIDER_PAPERCLIP, "paperclip-1",
         {"deepseek": False, "openai": False, "ollama": False}),
        (PROVIDER_HERMES, "hermes-1",
         {"deepseek": False, "openai": False, "ollama": False, "paperclip": False}),
        (PROVIDER_COPILOT, "gpt-4o-mini",
         {"deepseek": False, "openai": False, "ollama": False, "paperclip": False,
          "hermes": False}),
    ],
)
def test_provider_serves_with_tier_and_usage(provider, model, health):
    """Each provider serves the LOW chain with the right model, tier and usage."""
    wired = build_team_gateway(health=health)
    wired.rig.script_success(provider, CLASSIFY_OK)
    result = _classify(wired)

    assert result.outcome == contract.OUTCOME_SUCCESS
    assert result.served()
    assert result.provider == provider
    assert result.model == model
    assert result.tier == "LOW"
    # usage accounting: the rig's canned 120-in / 60-out tokens reach the record
    assert result.input_tokens == 120
    assert result.output_tokens == 60
    rec = result.record
    assert rec.provider == provider
    assert rec.model == model
    assert rec.input_tokens == 120
    assert rec.output_tokens == 60
    assert rec.tokens == 180
    assert rec.capability == "orchestrate"


def test_team_gateway_uses_real_paperclip_and_hermes_adapters():
    """The real adapters (issue #255) own paperclip/hermes: the funnel ASSERTS
    they are registered instead of installing any superseded fallback stub."""
    from providers import PROVIDER_CLASSES
    from providers.hermes import HermesProvider as RealHermesProvider
    from providers.paperclip import PaperclipProvider as RealPaperclipProvider

    from e2e.wiring import assert_team_providers_registered

    assert_team_providers_registered()
    assert PROVIDER_CLASSES["paperclip"] is RealPaperclipProvider
    assert PROVIDER_CLASSES["hermes"] is RealHermesProvider


# --------------------------------------------------------------------------- #
# routing-group pinning through the real team personas
# --------------------------------------------------------------------------- #
def test_team_agent_deepseek_routes_to_its_pinned_provider():
    """The deepseek persona is pinned to provider deepseek by routing.yaml."""
    wired = build_team_gateway()
    wired.rig.script_success(PROVIDER_DEEPSEEK, SUMMARIZE_OK)
    result = _dispatch(wired, "deepseek", "summarize", thread="ship the milestone")
    assert result.outcome == contract.OUTCOME_SUCCESS
    assert result.provider == PROVIDER_DEEPSEEK
    assert result.model == "deepseek-chat"
    assert result.record.agent_id == "deepseek"


def test_team_agent_paperclip_routes_to_its_pinned_provider():
    """The paperclip persona is pinned to provider paperclip by routing.yaml."""
    wired = build_team_gateway()
    wired.rig.script_success(PROVIDER_PAPERCLIP, SUMMARIZE_OK)
    result = _dispatch(wired, "paperclip", "summarize", thread="ship the milestone")
    assert result.outcome == contract.OUTCOME_SUCCESS
    assert result.provider == PROVIDER_PAPERCLIP
    assert result.model == "paperclip-1"
    assert result.record.agent_id == "paperclip"


# --------------------------------------------------------------------------- #
# fallback chain at runtime (primary unavailable -> local ollama)
# --------------------------------------------------------------------------- #
def test_primary_unavailable_falls_back_to_ollama_at_runtime():
    """A runtime primary failure degrades to the terminal local Ollama hop."""
    wired = build_team_gateway()
    wired.rig.fail(PROVIDER_PAPERCLIP, ProviderUnavailableError("paperclip down"))
    wired.rig.script_success(PROVIDER_OLLAMA, SUMMARIZE_OK)
    result = _dispatch(wired, "paperclip", "summarize", thread="ship the milestone")

    assert result.outcome == contract.OUTCOME_SUCCESS
    assert result.provider == PROVIDER_OLLAMA
    assert result.model == "llama3.2"


# --------------------------------------------------------------------------- #
# failure path
# --------------------------------------------------------------------------- #
def test_all_candidates_unavailable_is_explicit_failure():
    """Every candidate down -> explicit failed, never a silent pass."""
    wired = build_team_gateway()
    for provider in (PROVIDER_DEEPSEEK, "openai", PROVIDER_OLLAMA,
                     PROVIDER_PAPERCLIP, PROVIDER_HERMES, PROVIDER_COPILOT):
        wired.rig.fail(provider, ProviderUnavailableError(f"{provider} down"))
    result = _classify(wired)
    assert result.outcome == contract.OUTCOME_FAILED
    assert not result.served()
    assert result.content is None


# --------------------------------------------------------------------------- #
# regression: the proxy chain order is authoritative (issue #334)
# --------------------------------------------------------------------------- #
def test_proxy_chain_order_not_shadowed_by_registry_fallback():
    """deepseek down -> OPENAI serves, not ollama (registry fallback must not
    shadow routing.yaml's LOW chain: deepseek -> openai -> ollama)."""
    wired = build_team_gateway()
    wired.rig.fail(PROVIDER_DEEPSEEK, ProviderUnavailableError("deepseek down"))
    wired.rig.script_success("openai", CLASSIFY_OK)
    wired.rig.script_success(PROVIDER_OLLAMA, CLASSIFY_OK)
    result = _classify(wired)

    assert result.outcome == contract.OUTCOME_SUCCESS
    assert result.provider == "openai"
    assert result.model == "gpt-4o-mini"
