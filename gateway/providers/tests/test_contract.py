"""Interface-contract tests (issue #15, criterion 1).

Asserts the contract-freeze surface: the abstract ``ModelProvider`` shape,
message/usage/result value objects, the adapter registry (every default
provider config maps to a registered adapter whose ``name`` matches), and the
fail-closed construction rules (config/adapter name mismatch, unsupported
model rejected before any transport call).
"""

from __future__ import annotations

import pytest

from providers import PROVIDER_CLASSES
from providers.contract import (
    ChatMessage,
    ChatOptions,
    ChatResult,
    ModelProvider,
    Usage,
    is_valid_role,
)
from providers.errors import ProviderConfigurationError
from providers.transport import FailingTransport

from support import config_for


def test_model_provider_is_abstract() -> None:
    with pytest.raises(TypeError):
        ModelProvider()  # type: ignore[abstract]


def test_chat_message_validates_role() -> None:
    with pytest.raises(ValueError):
        ChatMessage(role="tool", content="nope")
    assert is_valid_role("system")
    assert not is_valid_role("tool")


def test_usage_totals_tokens() -> None:
    usage = Usage(input_tokens=10, output_tokens=5)
    assert usage.tokens == 15
    assert usage.to_dict() == {
        "input_tokens": 10,
        "output_tokens": 5,
        "tokens": 15,
    }


def test_chat_result_to_dict_shape() -> None:
    result = ChatResult(
        provider="deepseek",
        model="deepseek-chat",
        content={"sentiment": "positive"},
        usage=Usage(input_tokens=3, output_tokens=4),
        latency_ms=12.5,
        tenant_id="acme",
        agent_id="agent-1",
        logical_key="LOW",
    )
    data = result.to_dict()
    assert data["content"] == {"sentiment": "positive"}
    assert data["usage"]["tokens"] == 7
    assert data["latency_ms"] == 12.5
    assert data["tenant_id"] == "acme"
    assert data["agent_id"] == "agent-1"
    assert data["logical_key"] == "LOW"


def test_every_default_config_has_a_registered_adapter(configs) -> None:
    assert set(configs) == set(PROVIDER_CLASSES)
    for name in configs:
        assert PROVIDER_CLASSES[name].name == name


@pytest.mark.parametrize("provider_name", ["anthropic", "deepseek", "openai", "gemini", "ollama"])
def test_every_provider_constructs_from_default_config(provider_name) -> None:
    adapter = PROVIDER_CLASSES[provider_name](config_for(provider_name), FailingTransport())
    assert adapter.name == provider_name


def test_adapter_rejects_config_name_mismatch() -> None:
    anthropic = PROVIDER_CLASSES["anthropic"]
    deepseek_cfg = config_for("deepseek")  # wrong config for an anthropic adapter
    with pytest.raises(ProviderConfigurationError):
        anthropic(deepseek_cfg, FailingTransport())


def test_unsupported_model_is_rejected_before_transport() -> None:
    from providers.anthropic import AnthropicProvider

    transport = FailingTransport()
    adapter = AnthropicProvider(config_for("anthropic"), transport)
    messages = [ChatMessage(role="user", content="hi")]
    with pytest.raises(ProviderConfigurationError):
        adapter.chat(
            messages,
            None,
            ChatOptions(model="gpt-4o"),  # not an anthropic model
            None,  # type: ignore[arg-type]
        )
    # No transport request may have been attempted.
    assert transport.attempts == 0
