"""Per-provider adapter tests (issue #15, criterion 1).

For each of the five providers, against a scripted ``RecordingTransport``:

- a valid call returns the typed (schema-validated) result with correct
  model_used / usage / latency and the call stamp, and the recorded request
  carries the right endpoint, model and auth header;
- invalid typed output FAILS CLOSED with ``OutputValidationError`` (never a
  silent pass-through);
- system-message handling matches each provider's wire protocol (Anthropic
  ``system`` field, Gemini ``systemInstruction``, inline system role for the
  OpenAI-compatible and Ollama protocols).
"""

from __future__ import annotations

import pytest

from providers import PROVIDER_CLASSES
from providers.base import Credentials
from providers.contract import (
    ASSISTANT,
    CallContext,
    ChatMessage,
    ChatOptions,
)
from providers.errors import OutputValidationError
from providers.transport import RecordingTransport

from conftest import CONTENT_OBJ, FAKE_KEY, VALID_CONTENT, make_messages
from support import auth_header_name, config_for, expected_endpoint, ok_response

PROVIDERS = ["anthropic", "deepseek", "openai", "gemini", "ollama"]


def _credentials(name: str) -> Credentials | None:
    return Credentials(api_key=FAKE_KEY) if auth_header_name(name) else None


def _model_for(name: str) -> str:
    return config_for(name).tier_model_for("MED")


def _ctx() -> CallContext:
    return CallContext(tenant_id="acme", agent_id="agent-1", logical_key="MED")


@pytest.mark.parametrize("name", PROVIDERS)
def test_valid_call_returns_typed_result(name, sentiment_schema) -> None:
    model = _model_for(name)
    transport = RecordingTransport([ok_response(name, model, VALID_CONTENT)])
    adapter = PROVIDER_CLASSES[name](config_for(name), transport, _credentials(name))
    result = adapter.chat(
        make_messages(), sentiment_schema, ChatOptions(model=model), _ctx()
    )
    assert result.content == CONTENT_OBJ
    assert result.provider == name
    assert result.model == model
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 7
    assert result.usage.tokens == 19
    assert result.tenant_id == "acme"
    assert result.agent_id == "agent-1"
    assert result.logical_key == "MED"
    assert result.latency_ms >= 0.0

    req = transport.requests[0]
    assert req["method"] == "POST"
    assert req["url"] == expected_endpoint(name, model)
    expected_auth = auth_header_name(name)
    if expected_auth is None:
        assert "authorization" not in req["headers"]
        assert "x-api-key" not in req["headers"]
        assert "x-goog-api-key" not in req["headers"]
    elif expected_auth == "authorization":
        assert req["headers"]["authorization"] == f"Bearer {FAKE_KEY}"
    else:
        assert req["headers"][expected_auth] == FAKE_KEY


@pytest.mark.parametrize("name", PROVIDERS)
def test_invalid_output_fails_closed(name, sentiment_schema) -> None:
    model = _model_for(name)
    # The provider "answers" with content that does not match the schema.
    transport = RecordingTransport([ok_response(name, model, '{"sentiment": "maybe"}')])
    adapter = PROVIDER_CLASSES[name](config_for(name), transport, _credentials(name))
    with pytest.raises(OutputValidationError):
        adapter.chat(
            make_messages(), sentiment_schema, ChatOptions(model=model), _ctx()
        )
    assert len(transport.requests) == 1


@pytest.mark.parametrize("name", PROVIDERS)
def test_non_json_output_fails_closed(name, sentiment_schema) -> None:
    model = _model_for(name)
    transport = RecordingTransport(
        [ok_response(name, model, "I cannot produce JSON today")]
    )
    adapter = PROVIDER_CLASSES[name](config_for(name), transport, _credentials(name))
    with pytest.raises(OutputValidationError):
        adapter.chat(
            make_messages(), sentiment_schema, ChatOptions(model=model), _ctx()
        )


def test_anthropic_lifts_system_out_of_messages(sentiment_schema) -> None:
    model = _model_for("anthropic")
    transport = RecordingTransport([ok_response("anthropic", model, VALID_CONTENT)])
    adapter = PROVIDER_CLASSES["anthropic"](
        config_for("anthropic"), transport, Credentials(api_key=FAKE_KEY)
    )
    adapter.chat(make_messages(), sentiment_schema, ChatOptions(model=model), _ctx())
    payload = transport.requests[0]["body"]
    assert payload["system"] == "Classify the sentiment as JSON matching the schema."
    roles = [m["role"] for m in payload["messages"]]
    assert "system" not in roles
    assert "anthropic-version" in transport.requests[0]["headers"]


def test_gemini_uses_system_instruction_and_model_role() -> None:
    model = _model_for("gemini")
    messages = make_messages() + [
        ChatMessage(role=ASSISTANT, content="already done")
    ]
    transport = RecordingTransport([ok_response("gemini", model, VALID_CONTENT)])
    adapter = PROVIDER_CLASSES["gemini"](
        config_for("gemini"), transport, Credentials(api_key=FAKE_KEY)
    )
    adapter.chat(messages, None, ChatOptions(model=model), _ctx())
    payload = transport.requests[0]["body"]
    assert payload["systemInstruction"]["parts"][0]["text"] == (
        "Classify the sentiment as JSON matching the schema."
    )
    roles = [c["role"] for c in payload["contents"]]
    assert roles == ["user", "model"]  # assistant -> model, no system in contents
    assert payload["contents"][0]["parts"][0]["text"] == (
        "The deal closed on time and under budget."
    )


def test_openai_compatible_keeps_system_role_inline() -> None:
    model = _model_for("deepseek")
    transport = RecordingTransport([ok_response("deepseek", model, VALID_CONTENT)])
    adapter = PROVIDER_CLASSES["deepseek"](
        config_for("deepseek"), transport, Credentials(api_key=FAKE_KEY)
    )
    adapter.chat(make_messages(), None, ChatOptions(model=model), _ctx())
    payload = transport.requests[0]["body"]
    roles = [m["role"] for m in payload["messages"]]
    assert "system" in roles
