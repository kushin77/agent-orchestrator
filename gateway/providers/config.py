"""Provider configuration + per-tenant model mapping (issue #15).

Two configuration layers:

1. ``ProviderConfig`` - the per-provider platform defaults: base URL, the
   canonical tier -> model map (tiers CONSUMED from the issue-#9 catalog:
   ``LOW/MED/HIGH/MAX``), the supported-model set (fail-closed model check),
   timeouts, retry policy, circuit-breaker settings and the graceful-
   degradation fallback chain (cloud -> local Ollama, mirroring the
   defragsuite + gov-ai-scout pattern).

2. Per-tenant overrides - a tenant may remap any *logical* key (a tier such as
   ``LOW`` or a task key such as ``summarize``) to a different provider/model
   (``"anthropic"`` or ``"anthropic/claude-opus-4-5"``). Overrides load from
   YAML (``example-tenant-overrides.yaml``) or the ``TenantOverrides`` API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from providers.contract import DEFAULT_TIER
from providers.resilience import CircuitBreakerSettings, RetryPolicy


@dataclass(frozen=True)
class ProviderConfig:
    """Immutable configuration for one provider adapter."""

    name: str
    base_url: str
    #: Path appended to ``base_url`` for the chat endpoint
    #: (e.g. ``/v1/messages``); adapters that embed the model in the path
    #: (Gemini) override endpoint construction instead.
    api_path: str = "/"
    #: Canonical tier -> actual provider model id (keys from issue-#9 tiers).
    tier_models: Mapping[str, str] = field(default_factory=dict)
    default_model: str = ""
    #: Models this provider will accept; an unknown model is REJECTED
    #: (fail closed) before any request is built.
    supported_models: frozenset[str] = frozenset()
    timeout_ms: int = 30000
    #: Whether an API key is mandatory (Ollama local is keyless).
    requires_key: bool = True
    #: Extra static headers merged into every request.
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    breaker: CircuitBreakerSettings = field(default_factory=CircuitBreakerSettings)
    #: Graceful-degradation chain: provider names tried when this provider is
    #: unavailable (cloud -> local). Empty tuple = no fallback.
    fallback: tuple[str, ...] = ()

    def tier_model_for(self, tier: str) -> str:
        """Resolve a logical tier to this provider's model id for that tier."""
        return self.tier_models.get(tier) or self.default_model

    def model_supported(self, model: str) -> bool:
        if not self.supported_models:
            return True  # adapter accepts arbitrary model ids (endpoint passthrough)
        return model in self.supported_models


def _base(name: str, base_url: str, api_path: str, tier_models: Mapping[str, str]) -> ProviderConfig:
    return ProviderConfig(
        name=name,
        base_url=base_url,
        api_path=api_path,
        tier_models=dict(tier_models),
        default_model=tier_models.get(DEFAULT_TIER, next(iter(tier_models.values()), "")),
        supported_models=frozenset(tier_models.values()),
    )


def default_provider_configs() -> dict[str, ProviderConfig]:
    """Platform-default configurations for the five shipped providers.

    Model ids are real provider model identifiers (provenance:
    gmail-agent sonnet/opus/haiku tiers, capital-underwriting gemini model
    constants, deepseek chat/reasoner, local Ollama). The default fallback
    chain for every cloud provider is local Ollama (cloud -> local, the
    defragsuite + gov-ai-scout graceful-degradation pattern).
    """
    configs: dict[str, ProviderConfig] = {
        "anthropic": _base(
            "anthropic",
            "https://api.anthropic.com",
            "/v1/messages",
            {"LOW": "claude-haiku-4-5", "MED": "claude-sonnet-4-5",
             "HIGH": "claude-sonnet-4-5", "MAX": "claude-opus-4-5"},
        ),
        "deepseek": _base(
            "deepseek",
            "https://api.deepseek.com",
            "/chat/completions",
            {"LOW": "deepseek-chat", "MED": "deepseek-chat",
             "HIGH": "deepseek-reasoner", "MAX": "deepseek-reasoner"},
        ),
        "openai": _base(
            "openai",
            "https://api.openai.com/v1",
            "/chat/completions",
            {"LOW": "gpt-4o-mini", "MED": "gpt-4o-mini",
             "HIGH": "gpt-4o", "MAX": "gpt-4o"},
        ),
        "gemini": _base(
            "gemini",
            "https://generativelanguage.googleapis.com/v1beta",
            "",  # endpoint embeds the model: /models/{model}:generateContent
            {"LOW": "gemini-2.5-flash", "MED": "gemini-2.5-flash",
             "HIGH": "gemini-2.5-pro", "MAX": "gemini-2.5-pro"},
        ),
    }
    for name in configs:
        configs[name] = _with_fallback(configs[name], ("ollama",))
    configs["ollama"] = _base(
        "ollama",
        "http://localhost:11434",
        "/api/chat",
        {"LOW": "llama3.2", "MED": "llama3.2", "HIGH": "qwen2.5", "MAX": "qwen2.5"},
    )
    configs["ollama"] = _replace(configs["ollama"], requires_key=False)
    return configs


def _with_fallback(config: ProviderConfig, fallback: tuple[str, ...]) -> ProviderConfig:
    return _replace(config, fallback=fallback)


def _replace(config: ProviderConfig, **changes: object) -> ProviderConfig:
    values: dict[str, object] = {
        "name": config.name,
        "base_url": config.base_url,
        "api_path": config.api_path,
        "tier_models": config.tier_models,
        "default_model": config.default_model,
        "supported_models": config.supported_models,
        "timeout_ms": config.timeout_ms,
        "requires_key": config.requires_key,
        "extra_headers": config.extra_headers,
        "retry": config.retry,
        "breaker": config.breaker,
        "fallback": config.fallback,
    }
    values.update(changes)
    return ProviderConfig(**values)


# --------------------------------------------------------------------------- #
# Per-tenant overrides
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TenantModelMapping:
    """A tenant's remapping of logical keys to providers/models.

    Each ``mappings`` value is ``"provider"`` (use that provider's default
    model for the tier being resolved) or ``"provider/model"`` (pin an exact
    model). Logical keys are tiers (``LOW/MED/HIGH/MAX``) or any task key the
    tenant defines (e.g. ``summarize``); the registry resolves either.
    """

    tenant_id: str
    mappings: Mapping[str, str] = field(default_factory=dict)

    def target_for(self, logical_key: str) -> str | None:
        return self.mappings.get(logical_key)


class TenantOverrides:
    """Per-tenant logical-key -> provider/model mapping store."""

    def __init__(self) -> None:
        self._by_tenant: dict[str, TenantModelMapping] = {}

    def add(self, mapping: TenantModelMapping) -> None:
        self._by_tenant[mapping.tenant_id] = mapping

    def mapping_for(self, tenant_id: str) -> TenantModelMapping | None:
        return self._by_tenant.get(tenant_id)

    def all(self) -> dict[str, TenantModelMapping]:
        return dict(self._by_tenant)

    @classmethod
    def from_yaml(cls, path: str) -> "TenantOverrides":
        """Load overrides from a YAML document.

        Shape (see ``example-tenant-overrides.yaml``)::

            tenants:
              - tenantId: acme
                mappings:
                  LOW: anthropic
                  summarize: ollama/llama3.2
        """
        import yaml  # local import: PyYAML is a declared repo dependency

        with open(path, "r", encoding="utf-8") as fh:
            document = yaml.safe_load(fh) or {}
        overrides = cls()
        for entry in document.get("tenants", []):
            tenant_id = entry.get("tenantId")
            if not tenant_id:
                raise ValueError(f"{path}: tenant entry missing tenantId")
            mappings = {str(k): str(v) for k, v in (entry.get("mappings") or {}).items()}
            overrides.add(TenantModelMapping(tenant_id=tenant_id, mappings=mappings))
        return overrides
