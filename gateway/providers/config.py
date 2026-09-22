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
   (``"anthropic"`` or ``"anthropic/claude-opus-5"``). Overrides load from
   YAML (``example-tenant-overrides.yaml``) or the ``TenantOverrides`` API.

---knowledge---
module_id: gateway.providers.config
system: gateway
app: providers
solution_class: enterprise
patterns: [immutable-config, tenant-overrides, consumed-tier-vocabulary, cloud-to-local-fallback]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [ProviderConfig, TenantModelMapping, TenantOverrides, default_provider_configs]
invariants: "the tier to model map consumes the issue-#9 LOW/MED/HIGH/MAX catalog vocabulary rather than redefining it"
gotchas: "the supported-model check is fail-closed, and the fallback chain degrades cloud to local Ollama"
related: ["#15"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any, Mapping

from providers.contract import DEFAULT_TIER
from providers.resilience import CircuitBreakerSettings, RetryPolicy

#: DEPRECATED - pre-parity (issue #894) Claude model ids that other pillars
#: (``gateway/health/health.yaml`` + ``fallback.py`` + ``cli.py``) still
#: reference directly, out of this lane's scope (``gateway/finops/tiers.yaml``
#: migrated off the old ids in issue #971). Rather than edit those remaining
#: out-of-lane files, the old id is accepted here and normalised to its
#: current replacement before the fail-closed ``model_supported`` check and
#: before any request is built, so an old id still resolves to a real,
#: currently-served model. Remove once health/fallback migrate off the old
#: ids.
#:
#: Canonical source (issue #971): the alias table itself lives in
#: ``telemetry/metering/model_aliases.py`` so the gateway's fail-closed model
#: check and the metering rate-card lookup resolve a legacy id through the
#: exact same map. This module re-exports it rather than keeping its own
#: copy that could drift.
_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from telemetry.metering.model_aliases import LEGACY_MODEL_ALIASES  # noqa: E402


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
    #: Generic, adapter-read, platform-config bag for provider-specific
    #: capabilities that are opt-in per provider config - the 2026-09-21
    #: reversal dropped the blanket "ships flag-gated OFF" default (AO-GR-6),
    #: so the bag is empty unless a provider pins a key. E.g.
    #: the anthropic adapter's ``prompt_caching`` / ``thinking_effort``
    #: knobs (issue #255 follow-up, claude-anthropic parity). Empty by
    #: default for every provider; only an adapter that reads a key opts in.
    provider_options: Mapping[str, Any] = field(default_factory=dict)

    def tier_model_for(self, tier: str) -> str:
        """Resolve a logical tier to this provider's model id for that tier."""
        return self.tier_models.get(tier) or self.default_model

    def normalize_model(self, model: str) -> str:
        """Map a deprecated ``LEGACY_MODEL_ALIASES`` id to its current
        replacement; any other id (including one already current) passes
        through unchanged."""
        return LEGACY_MODEL_ALIASES.get(model, model)

    def model_supported(self, model: str) -> bool:
        if not self.supported_models:
            return True  # adapter accepts arbitrary model ids (endpoint passthrough)
        return self.normalize_model(model) in self.supported_models


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
    """Platform-default configurations for the nine shipped providers.

    Model ids are real provider model identifiers (provenance:
    gmail-agent sonnet/opus/haiku tiers, capital-underwriting gemini model
    constants, deepseek chat/reasoner, local Ollama). The paperclip, hermes and
    copilot model ids are default illustrative identifiers for the vendored
    modules (issue #255) and the OpenAI-compatible Copilot hop (issue #340);
    operators override them via per-tenant mapping. The
    default fallback chain for every cloud provider is local Ollama
    (cloud -> local, the defragsuite + gov-ai-scout graceful-degradation
    pattern); hermes is the local hop whose own fallback is also Ollama
    (frozen map ``hermes -> hermes/ollama``, EPIC #253). ``copilot`` reuses the
    existing OpenAI adapter (issue #340 deliverable 4; see
    ``providers/copilot.py``) and is a distinct provider id only so its rate
    card and catalog module are addressable.

    ``nous`` (issue #1559) is the same shape of decision for the opposite kind
    of target: Nous Research's **billed, OpenAI-compatible cloud API** at
    ``https://inference-api.nousresearch.com/v1``, whose tier ids are REAL —
    taken from the endpoint's own live ``GET /v1/models`` catalog (402 models,
    measured 2026-09-20) rather than from the API docs, which advertise three
    ``Hermes-4.x`` ids that the live service has since retired or never served.
    Unlike ``hermes`` — the keyless local Ollama-compatible hop declared by
    ``integrations/hermes/`` (ADR-0012) and priced ``local: true`` by
    ``telemetry/metering/rate_cards/hermes.yaml`` — this provider requires a
    Bearer key. They are separate ids so neither declaration has to become
    false: re-pointing ``hermes`` would silently contradict those files rather
    than red any gate, since none of them derives its hermes facts from this
    module.
    """
    configs: dict[str, ProviderConfig] = {
        "anthropic": _replace(
            _base(
                "anthropic",
                "https://api.anthropic.com",
                "/v1/messages",
                {"LOW": "claude-haiku-4-5-20251001", "MED": "claude-sonnet-5",
                 "HIGH": "claude-sonnet-5", "MAX": "claude-opus-5"},
            ),
            # ``claude-fable-5-1`` is not tier-mapped (it is Anthropic's
            # most-capable/most-expensive model, opt-in via an explicit
            # tenant pin: ``anthropic/claude-fable-5-1``), but it is a
            # supported model id so the fail-closed model check accepts it.
            supported_models=frozenset(
                {"claude-haiku-4-5-20251001", "claude-sonnet-5",
                 "claude-opus-5", "claude-fable-5-1"}
            ),
            # Prompt caching + thinking-effort are Claude-specific
            # capabilities (claude-anthropic module.json features
            # ``prompt-caching`` / ``thinking-effort``); flag-gated OFF by
            # default (GR-28) - empty here means disabled. A tenant/operator
            # enables them by registering a provider config with
            # ``provider_options={"prompt_caching": True, "thinking_effort": "low"}``.
            provider_options={},
        ),
        "deepseek": _base(
            "deepseek",
            "https://api.deepseek.com",
            "/chat/completions",
            {"LOW": "deepseek-chat", "MED": "deepseek-chat",
             "HIGH": "deepseek-reasoner", "MAX": "deepseek-reasoner"},
        ),
        "copilot": _base(
            "copilot",
            "https://api.githubcopilot.com",
            "/chat/completions",
            {"LOW": "gpt-4o-mini", "MED": "gpt-4o-mini",
             "HIGH": "gpt-4o", "MAX": "gpt-4o"},
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
        "paperclip": _base(
            "paperclip",
            "https://api.paperclip.dev/v1",
            "/chat/completions",
            {"LOW": "paperclip-planner", "MED": "paperclip-planner",
             "HIGH": "paperclip-planner", "MAX": "paperclip-planner"},
        ),
        "hermes": _base(
            "hermes",
            "http://localhost:8080",
            "/api/chat",
            {"LOW": "hermes3", "MED": "hermes3",
             "HIGH": "hermes3", "MAX": "hermes3"},
        ),
        # Nous Research (issue #1559): OpenAI-compatible chat/completions behind
        # a REQUIRED Bearer API key, billed against account credits.
        # ``requires_key`` stays at its default of True, so the adapter sends no
        # Authorization header without a key and the call is refused by the
        # provider with an auth error instead of silently succeeding.
        #
        # The tier ids below are VERIFIED-SERVABLE ids, measured 2026-09-20 by
        # probing this endpoint's own chat path. The provider's signals are
        # unambiguous: HTTP 402 (the x402 "payment required" flow) for a model id
        # it ACCEPTS, 400 "Unknown model" for one it does not, and 401 for a bad
        # key. Two traps this avoids, both measured rather than assumed:
        #
        #   1. The ``Hermes-4.x`` ids the API docs advertise are dead -
        #      ``Hermes-4-70B`` and ``Hermes-4-405B`` answer 404 "This model has
        #      been retired", ``Hermes-4.3-36B`` answers 404 "not found", and no
        #      id containing "hermes" is servable at all.
        #   2. ``GET /v1/models`` (HTTP 200, unauthenticated) lists 402 models with
        #      prices, but that catalog is MUCH wider than the chat namespace:
        #      most catalog slugs answer "Unknown model" on /chat/completions and
        #      ``anthropic/claude-sonnet-5`` answers "No pricing available".
        #      Declaring a ladder from the catalog alone would ship dead ids.
        #
        # Each rung is therefore an id that answered 402, ascending by the
        # endpoint's own published per-MTok rates (see
        # ``gateway/finops/provider-credits.yaml``, which prices exactly these).
        "nous": _base(
            "nous",
            "https://inference-api.nousresearch.com/v1",
            "/chat/completions",
            {"LOW": "inclusionai/ling-3.0-flash",
             "MED": "qwen/qwen3.7-flash",
             "HIGH": "anthropic/claude-haiku-4.5",
             "MAX": "openai/gpt-6-astra-fast"},
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
    configs["hermes"] = _replace(configs["hermes"], requires_key=False)
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
        "provider_options": config.provider_options,
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
