"""Provider-registry tests (issue #15, criteria 2, 3, 4, 5).

Covers route resolution (defaults + per-tenant overrides + YAML loading),
fail-closed routing (unknown logical key / unknown provider / unsupported
model), resilient calls through the registry (credentials from factory and
from the vault), graceful degradation to the fallback chain, retry-then-fail,
and the metering/audit hooks firing on every call with the full stamp.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from providers.base import Credentials
from providers.config import default_provider_configs
from providers.errors import (
    ProviderConfigurationError,
    RetryExhaustedError,
)
from providers.registry import ProviderRegistry
from providers.resilience import RetryPolicy
from providers.transport import FailingTransport, RecordingTransport

from conftest import CONTENT_OBJ, FAKE_KEY, VALID_CONTENT, make_messages
from support import ok_response


def small_configs() -> dict:
    """Platform configs with a small retry budget (fast offline tests)."""
    small = RetryPolicy(max_attempts=2, base_delay_s=0.001, max_delay_s=0.01)
    return {name: replace(cfg, retry=small) for name, cfg in default_provider_configs().items()}


def _registry(**kwargs) -> ProviderRegistry:
    return ProviderRegistry(small_configs(), **kwargs)


# --------------------------------------------------------------------------- #
# Route resolution
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "tier,model",
    [
        ("LOW", "deepseek-chat"),
        ("MED", "deepseek-chat"),
        ("HIGH", "deepseek-reasoner"),
        ("MAX", "deepseek-reasoner"),
    ],
)
def test_default_route_for_each_tier(tier, model) -> None:
    registry = _registry()
    assert registry.resolve_route("acme", tier) == ("deepseek", model)


def test_unknown_logical_key_is_rejected() -> None:
    registry = _registry()
    with pytest.raises(ProviderConfigurationError):
        registry.resolve_route("acme", "NOPE")


def test_tenant_override_provider_only() -> None:
    registry = _registry()
    registry.set_tenant_mapping("acme", {"LOW": "anthropic"})
    assert registry.resolve_route("acme", "LOW") == ("anthropic", "claude-haiku-4-5")
    # Other tenants keep the platform default.
    assert registry.resolve_route("globex", "LOW") == ("deepseek", "deepseek-chat")


def test_tenant_override_explicit_model() -> None:
    registry = _registry()
    registry.set_tenant_mapping("acme", {"MAX": "anthropic/claude-opus-4-5"})
    assert registry.resolve_route("acme", "MAX") == ("anthropic", "claude-opus-4-5")


def test_tenant_override_task_key() -> None:
    registry = _registry()
    registry.set_tenant_mapping("acme", {"summarize": "ollama/llama3.2"})
    assert registry.resolve_route("acme", "summarize") == ("ollama", "llama3.2")


def test_tenant_override_unknown_target_is_rejected() -> None:
    registry = _registry()
    registry.set_tenant_mapping("acme", {"LOW": "bogus"})
    with pytest.raises(ProviderConfigurationError):
        registry.resolve_route("acme", "LOW")


def test_tenant_override_unsupported_model_is_rejected() -> None:
    registry = _registry()
    registry.set_tenant_mapping("acme", {"LOW": "deepseek/gpt-4o"})
    with pytest.raises(ProviderConfigurationError):
        registry.resolve_route("acme", "LOW")


def test_tenant_overrides_load_from_yaml(tmp_path) -> None:
    path = tmp_path / "overrides.yaml"
    path.write_text(
        "tenants:\n"
        "  - tenantId: acme\n"
        "    mappings:\n"
        "      LOW: anthropic\n"
        "      summarize: ollama/llama3.2\n",
        encoding="utf-8",
    )
    registry = _registry()
    registry.load_tenant_overrides(str(path))
    assert registry.resolve_route("acme", "LOW") == ("anthropic", "claude-haiku-4-5")
    assert registry.resolve_route("acme", "summarize") == ("ollama", "llama3.2")


def test_malformed_yaml_is_rejected(tmp_path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("tenants:\n  - mappings: {}\n", encoding="utf-8")
    registry = _registry()
    with pytest.raises(ValueError):
        registry.load_tenant_overrides(str(path))


# --------------------------------------------------------------------------- #
# Calls through the registry
# --------------------------------------------------------------------------- #


def _deepseek_transport() -> RecordingTransport:
    return RecordingTransport(
        [ok_response("deepseek", "deepseek-chat", VALID_CONTENT)]
    )


def test_chat_success_with_credentials_factory(sentiment_schema) -> None:
    registry = _registry(
        credentials_factory=lambda tenant, provider: Credentials(api_key=FAKE_KEY),
        transport_factory=lambda name, cfg: _deepseek_transport(),
    )
    result = registry.chat(
        make_messages(),
        sentiment_schema,
        tenant_id="acme",
        agent_id="agent-1",
        logical_key="MED",
    )
    assert result.content == CONTENT_OBJ
    assert result.provider == "deepseek"
    assert result.model == "deepseek-chat"
    assert result.tenant_id == "acme"
    assert result.agent_id == "agent-1"
    assert result.usage.tokens == 19


def test_chat_sends_authorization_header_from_credentials(sentiment_schema) -> None:
    transport = _deepseek_transport()
    registry = _registry(
        credentials_factory=lambda tenant, provider: Credentials(api_key=FAKE_KEY),
        transport_factory=lambda name, cfg: transport,
    )
    registry.chat(
        make_messages(),
        sentiment_schema,
        tenant_id="acme",
        agent_id="agent-1",
    )
    assert transport.requests[0]["headers"]["authorization"] == f"Bearer {FAKE_KEY}"


def test_chat_fails_closed_without_api_key(sentiment_schema) -> None:
    registry = _registry(
        transport_factory=lambda name, cfg: _deepseek_transport(),
    )
    with pytest.raises(ProviderConfigurationError):
        registry.chat(
            make_messages(),
            sentiment_schema,
            tenant_id="acme",
            agent_id="agent-1",
        )


def test_chat_reads_key_from_vault(sentiment_schema, vault) -> None:
    vault.set_key("acme", "deepseek", FAKE_KEY)
    transport = _deepseek_transport()
    registry = _registry(
        vault=vault,
        transport_factory=lambda name, cfg: transport,
    )
    result = registry.chat(
        make_messages(),
        sentiment_schema,
        tenant_id="acme",
        agent_id="agent-1",
    )
    assert result.content == CONTENT_OBJ
    assert transport.requests[0]["headers"]["authorization"] == f"Bearer {FAKE_KEY}"


def test_chat_honours_explicit_model(sentiment_schema) -> None:
    transport = RecordingTransport(
        [ok_response("deepseek", "deepseek-reasoner", VALID_CONTENT)]
    )
    registry = _registry(
        credentials_factory=lambda tenant, provider: Credentials(api_key=FAKE_KEY),
        transport_factory=lambda name, cfg: transport,
    )
    result = registry.chat(
        make_messages(),
        sentiment_schema,
        tenant_id="acme",
        agent_id="agent-1",
        logical_key="LOW",
        model="deepseek-reasoner",
    )
    assert result.model == "deepseek-reasoner"


def test_chat_rejects_unsupported_explicit_model(sentiment_schema) -> None:
    registry = _registry(
        credentials_factory=lambda tenant, provider: Credentials(api_key=FAKE_KEY),
        transport_factory=lambda name, cfg: _deepseek_transport(),
    )
    with pytest.raises(ProviderConfigurationError):
        registry.chat(
            make_messages(),
            sentiment_schema,
            tenant_id="acme",
            agent_id="agent-1",
            model="gpt-4o",  # not a deepseek model
        )


def test_graceful_degradation_to_ollama(sentiment_schema) -> None:
    """DeepSeek unavailable -> degrade to the local Ollama fallback."""

    def transport_factory(name, cfg):
        if name == "deepseek":
            return FailingTransport()
        return RecordingTransport([ok_response("ollama", "llama3.2", VALID_CONTENT)])

    registry = _registry(
        credentials_factory=lambda tenant, provider: Credentials(api_key=FAKE_KEY),
        transport_factory=transport_factory,
    )
    result = registry.chat(
        make_messages(),
        sentiment_schema,
        tenant_id="acme",
        agent_id="agent-1",
        logical_key="MED",
    )
    assert result.provider == "ollama"
    assert result.model == "llama3.2"
    assert result.content == CONTENT_OBJ


def test_retry_then_fail_after_fallback_exhausted() -> None:
    """Every provider in the chain is down -> the call finally fails."""

    def transport_factory(name, cfg):
        return FailingTransport()

    registry = _registry(
        credentials_factory=lambda tenant, provider: Credentials(api_key=FAKE_KEY),
        transport_factory=transport_factory,
    )
    with pytest.raises(RetryExhaustedError):
        registry.chat(
            make_messages(),
            None,
            tenant_id="acme",
            agent_id="agent-1",
            logical_key="MED",
        )


def test_metering_and_audit_hooks_fire_per_call(sentiment_schema) -> None:
    metering: list = []
    audit: list = []
    registry = _registry(
        credentials_factory=lambda tenant, provider: Credentials(api_key=FAKE_KEY),
        transport_factory=lambda name, cfg: _deepseek_transport(),
    )
    registry.add_metering_hook(metering.append)
    registry.add_audit_hook(audit.append)
    registry.chat(
        make_messages(),
        sentiment_schema,
        tenant_id="acme",
        agent_id="agent-1",
        logical_key="MED",
    )
    assert len(metering) == 1
    assert len(audit) == 1
    event = metering[0]
    assert event.status == "success"
    assert event.provider == "deepseek"
    assert event.model == "deepseek-chat"
    assert event.tenant_id == "acme"
    assert event.agent_id == "agent-1"
    assert event.logical_key == "MED"
    assert event.usage is not None and event.usage.tokens == 19
    assert event.attempts >= 1


def test_provider_configs_are_exposed() -> None:
    registry = _registry()
    names = set(registry.provider_configs())
    assert names == {"anthropic", "deepseek", "openai", "gemini", "ollama"}
    assert registry.config_for("ollama").requires_key is False
