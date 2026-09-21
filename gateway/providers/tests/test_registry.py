"""Provider-registry tests (issue #15, criteria 2, 3, 4, 5).

Covers route resolution (defaults + per-tenant overrides + YAML loading),
fail-closed routing (unknown logical key / unknown provider / unsupported
model), resilient calls through the registry (credentials from factory and
from the vault), graceful degradation to the fallback chain, retry-then-fail,
and the metering/audit hooks firing on every call with the full stamp.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

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


@pytest.mark.parametrize(
    "tier,model",
    [
        ("LOW", "claude-haiku-4-5-20251001"),
        ("MED", "claude-sonnet-5"),
        ("HIGH", "claude-sonnet-5"),
        ("MAX", "claude-opus-5"),
    ],
)
def test_claude_tier_ladder_is_selectable_without_changing_default(tier, model) -> None:
    """claude-anthropic parity: a FinOps chooser can select the ``claude``
    ladder (PROVIDER_TIER_LADDERS); the platform default stays deepseek."""
    registry = _registry(tier_ladder="claude")
    assert registry.resolve_route("acme", tier) == ("anthropic", model)


@pytest.mark.parametrize(
    "tier,model",
    [
        ("LOW", "deepseek-chat"),
        ("MED", "deepseek-chat"),
        ("HIGH", "deepseek-reasoner"),
        ("MAX", "deepseek-reasoner"),
    ],
)
def test_bare_default_tier_ladder_is_unchanged_deepseek(tier, model) -> None:
    """The bare default (no ``tier_ladder`` kwarg) is unchanged: deepseek.
    Asserted against literal model ids (not ``config_for(...).tier_model_for``
    - that would assert the code under test against itself)."""
    registry = _registry()
    assert registry.resolve_route("acme", tier) == ("deepseek", model)


def test_unknown_tier_ladder_is_rejected() -> None:
    with pytest.raises(ProviderConfigurationError):
        _registry(tier_ladder="not-a-ladder")


def test_unknown_logical_key_is_rejected() -> None:
    registry = _registry()
    with pytest.raises(ProviderConfigurationError):
        registry.resolve_route("acme", "NOPE")


def test_tenant_override_provider_only() -> None:
    registry = _registry()
    registry.set_tenant_mapping("acme", {"LOW": "anthropic"})
    assert registry.resolve_route("acme", "LOW") == ("anthropic", "claude-haiku-4-5-20251001")
    # Other tenants keep the platform default.
    assert registry.resolve_route("globex", "LOW") == ("deepseek", "deepseek-chat")


def test_tenant_override_explicit_model() -> None:
    registry = _registry()
    registry.set_tenant_mapping("acme", {"MAX": "anthropic/claude-opus-5"})
    assert registry.resolve_route("acme", "MAX") == ("anthropic", "claude-opus-5")


@pytest.mark.parametrize(
    "legacy_id,current_id",
    [
        ("claude-haiku-4-5", "claude-haiku-4-5-20251001"),
        ("claude-sonnet-4-5", "claude-sonnet-5"),
        ("claude-opus-4-5", "claude-opus-5"),
        ("claude-opus-4-1", "claude-opus-5"),
    ],
)
def test_legacy_model_alias_still_resolves(legacy_id, current_id) -> None:
    """gateway/health + gateway/finops (out of this lane's scope) still pin
    some old Claude ids directly; a tenant/override pinning one must still
    resolve - to the CURRENT id, never the deprecated one - rather than be
    rejected by the fail-closed model check (issue #894 review)."""
    registry = _registry()
    registry.set_tenant_mapping("acme", {"MAX": f"anthropic/{legacy_id}"})
    assert registry.resolve_route("acme", "MAX") == ("anthropic", current_id)


def test_copilot_provider_is_registered_and_routable() -> None:
    """copilot is a first-class provider: config + adapter + tier models.

    The copilot provider id maps onto the existing OpenAI adapter (issue #340);
    a tenant can route to it like any other provider.
    """
    from providers import PROVIDER_CLASSES

    configs = default_provider_configs()
    assert "copilot" in configs
    assert PROVIDER_CLASSES["copilot"].name == "copilot"
    registry = _registry()
    registry.set_tenant_mapping("acme", {"LOW": "copilot"})
    assert registry.resolve_route("acme", "LOW") == ("copilot", "gpt-4o-mini")
    registry.set_tenant_mapping("acme", {"HIGH": "copilot"})
    assert registry.resolve_route("acme", "HIGH") == ("copilot", "gpt-4o")


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
    assert registry.resolve_route("acme", "LOW") == ("anthropic", "claude-haiku-4-5-20251001")
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


def test_allow_fallback_false_skips_the_provider_fallback_chain(sentiment_schema) -> None:
    """``allow_fallback=False`` performs exactly one provider's call: a caller
    that owns its own candidate chain (the gateway proxy) gets the primary's
    failure back instead of a silent degrade to Ollama (issue #334)."""

    def transport_factory(name, cfg):
        if name == "deepseek":
            return FailingTransport()
        return RecordingTransport([ok_response("ollama", "llama3.2", VALID_CONTENT)])

    registry = _registry(
        credentials_factory=lambda tenant, provider: Credentials(api_key=FAKE_KEY),
        transport_factory=transport_factory,
    )
    with pytest.raises(RetryExhaustedError):
        registry.chat(
            make_messages(),
            sentiment_schema,
            tenant_id="acme",
            agent_id="agent-1",
            logical_key="MED",
            allow_fallback=False,
        )


def test_hermes_degrades_to_ollama(sentiment_schema) -> None:
    """Hermes unavailable -> degrade to its local Ollama fallback (frozen map)."""

    def transport_factory(name, cfg):
        if name == "hermes":
            return FailingTransport()
        return RecordingTransport([ok_response("ollama", "llama3.2", VALID_CONTENT)])

    registry = _registry(
        credentials_factory=lambda tenant, provider: Credentials(api_key=FAKE_KEY),
        transport_factory=transport_factory,
        hermes_enabled=True,
    )
    registry.set_tenant_mapping("acme", {"MED": "hermes"})
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
    # hermes is retired by default (enable_hermes off): it is absent from the
    # ACTIVE configs, though its adapter + config remain in the catalog so a
    # reviewed go-live can re-enable it.
    assert names == {"anthropic", "deepseek", "openai", "copilot", "gemini",
                     "ollama", "paperclip", "nous"}
    assert "hermes" not in names
    assert registry.config_for("ollama").requires_key is False
    assert "hermes" in default_provider_configs()


def test_hermes_retired_by_default() -> None:
    """``enable_hermes`` off (default): the registry does not activate hermes.

    The gate is fail-closed at routing time: hermes is absent from the active
    config set, so it cannot be resolved, even though its adapter and config
    remain in the catalog for a reviewed go-live to re-enable.
    """
    registry = _registry()
    assert registry.hermes_enabled is False
    assert "hermes" not in registry.provider_configs()
    with pytest.raises(ProviderConfigurationError):
        registry.config_for("hermes")


def test_hermes_registers_when_enabled() -> None:
    """``enable_hermes`` on: the registry registers (and activates) hermes."""
    registry = _registry(hermes_enabled=True)
    assert registry.hermes_enabled is True
    assert "hermes" in registry.provider_configs()
    assert registry.config_for("hermes").requires_key is False
    # Re-registering a hermes config is accepted when the flag is on.
    registry.register_provider_config(default_provider_configs()["hermes"])


def test_hermes_flag_is_read_fail_closed(tmp_path) -> None:
    """The flag reader reads ``services.hermes`` and fails closed (issue #1518)."""
    from providers.flags import hermes_enabled as flag_enabled

    # A missing/unreadable registry never enables the provider.
    assert flag_enabled(tmp_path / "does-not-exist.yaml") is False
    # An explicit off stays off.
    off = tmp_path / "off.yaml"
    off.write_text("services:\n  hermes:\n    default: off\n", encoding="utf-8")
    assert flag_enabled(off) is False
    # Only an explicit on enables it.
    on = tmp_path / "on.yaml"
    on.write_text("services:\n  hermes:\n    default: on\n", encoding="utf-8")
    assert flag_enabled(on) is True


def test_nous_is_a_keyed_cloud_provider_not_the_local_hermes_hop() -> None:
    """#1559: Nous is the billed cloud target; hermes stays the keyless local hop."""
    registry = _registry()
    nous = registry.config_for("nous")
    assert nous.base_url == "https://inference-api.nousresearch.com/v1"
    assert nous.api_path == "/chat/completions"
    assert nous.requires_key is True
    assert nous.fallback == ("ollama",)
    assert nous.tier_model_for("MAX") == "openai/gpt-6-astra-fast"
    # the local hop is untouched: this provider is additive, not a re-point.
    # hermes is retired-by-default (issue #1518), so check its catalog config
    # rather than the default (flag-off) registry's active set.
    assert default_provider_configs()["hermes"].requires_key is False


def test_the_finops_credit_declaration_prices_exactly_what_nous_routes() -> None:
    """#1559: the credit meter and the tier map must name the same model ids.

    A provider that routes to a model its own FinOps declaration does not price
    would record every call as unmetered - silently. This is the cheap guard
    that keeps the two in-lane declarations from drifting apart.
    """
    import yaml

    root = Path(__file__).resolve().parents[3]
    declaration = yaml.safe_load(
        (root / "gateway" / "finops" / "provider-credits.yaml").read_text(
            encoding="utf-8"
        )
    )
    declared = declaration["providers"]["nous"]["models"]
    routed = set(_registry().config_for("nous").tier_models.values())
    assert routed == set(declared), (
        f"routed {sorted(routed)} != priced {sorted(declared)}"
    )
    assert all(declared[model]["pricePublished"] is True for model in routed)
