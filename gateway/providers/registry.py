"""Provider registry + resilient per-tenant client pool (issue #15).

This is the composition root of the adapter layer. It:

1. owns the platform provider configs and the per-tenant model overrides
   (``TenantModelMapping``), resolving any *logical* key (a ``LOW/MED/HIGH/MAX``
   tier from the issue-#9 catalog, or a tenant task key) to an exact
   ``(provider, model)`` route - fail closed on unknown providers/models;
2. builds one resilient ``ProviderClient`` per (tenant, provider) - adapter +
   credentials + circuit breaker + retry policy + metering/audit hooks - and
   reuses it across calls;
3. applies graceful degradation: when the routed provider is unavailable
   (circuit open / retries exhausted / transient error), it walks the
   provider's configured fallback chain (cloud -> local Ollama by default,
   the defragsuite + gov-ai-scout pattern);
4. fires one stamped ``ModelCallEvent`` per provider outcome through the
   ``EventRouter`` (metering + audit hooks) - every call is stamped with
   provider/model/tenant/agent/logical key.

API keys are resolved through the ``ApiKeyVault`` (or an injected
credentials factory); keys never enter chat options, results, events or logs.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, NamedTuple, Sequence

from providers.anthropic import AnthropicProvider
from providers.base import Credentials
from providers.config import (
    ProviderConfig,
    TenantModelMapping,
    TenantOverrides,
    default_provider_configs,
)
from providers.contract import (
    DEFAULT_TIER,
    TIERS,
    CallContext,
    ChatMessage,
    ChatOptions,
    ChatResult,
)
from providers.deepseek import DeepSeekProvider
from providers.errors import (
    CircuitOpenError,
    OutputValidationError,
    ProviderConfigurationError,
    ProviderUnavailableError,
    RetryExhaustedError,
)
from providers.events import (
    STATUS_CIRCUIT_OPEN,
    STATUS_FAILED,
    STATUS_OUTPUT_INVALID,
    STATUS_RETRY_EXHAUSTED,
    STATUS_SUCCESS,
    EventRouter,
    ModelCallEvent,
)
from providers.gemini import GeminiProvider
from providers.ollama import OllamaProvider
from providers.openai import OpenAIProvider
from providers.resilience import (
    CircuitBreakerManager,
    call_with_retries,
)
from providers.transport import HttpTransport, StdlibHttpTransport
from providers.vault import ApiKeyVault

# Adapter registry: canonical provider key -> adapter class.
PROVIDER_CLASSES: dict[str, type] = {
    cls.name: cls
    for cls in (
        AnthropicProvider,
        DeepSeekProvider,
        OpenAIProvider,
        GeminiProvider,
        OllamaProvider,
    )
}

PROVIDER_NAMES: tuple[str, ...] = tuple(sorted(PROVIDER_CLASSES))

# Platform default provider per tier (the issue-#9 ladder). Tenants override
# via TenantModelMapping (see example-tenant-overrides.yaml).
DEFAULT_PROVIDER_BY_TIER: Mapping[str, str] = {
    "LOW": "deepseek",
    "MED": "deepseek",
    "HIGH": "deepseek",
    "MAX": "deepseek",
}


class Route(NamedTuple):
    """A resolved logical key -> (provider, model)."""

    provider: str
    model: str


def _coerce_message(item: ChatMessage | Mapping[str, str]) -> ChatMessage:
    if isinstance(item, ChatMessage):
        return item
    return ChatMessage(role=item["role"], content=item["content"])


def _coerce_messages(messages: Sequence[ChatMessage | Mapping[str, str]]) -> list[ChatMessage]:
    return [_coerce_message(m) for m in messages]


class ProviderClient:
    """One resilient provider adapter bound to a tenant (breaker + retry + hooks).

    Mirrors the ollama ``ResilientOllamaClient`` and defragsuite ``Client``
    architecture: a raw adapter wrapped with retry/backoff + circuit breaker;
    every terminal outcome emits a stamped ``ModelCallEvent``.
    """

    def __init__(
        self,
        provider: Any,
        config: ProviderConfig,
        breaker: Any,
        router: EventRouter,
    ) -> None:
        self._provider = provider
        self._config = config
        self._breaker = breaker
        self._router = router

    @property
    def name(self) -> str:
        return self._provider.name

    def circuit_breaker(self) -> Any:
        return self._breaker

    def chat(
        self,
        messages: list[ChatMessage],
        schema: Mapping[str, Any] | None,
        options: ChatOptions,
        context: CallContext,
    ) -> ChatResult:
        model = options.model or self._config.default_model

        def attempt() -> ChatResult:
            return self._provider.chat(messages, schema, options, context)

        try:
            outcome = call_with_retries(
                attempt, retry=self._config.retry, breaker=self._breaker
            )
        except OutputValidationError as exc:
            self._emit(
                context,
                model,
                STATUS_OUTPUT_INVALID,
                attempts=1,
                error=exc,
            )
            raise
        except CircuitOpenError as exc:
            self._emit(context, model, STATUS_CIRCUIT_OPEN, attempts=0, error=exc)
            raise
        except RetryExhaustedError as exc:
            self._emit(
                context,
                model,
                STATUS_RETRY_EXHAUSTED,
                attempts=self._config.retry.max_attempts,
                error=exc,
            )
            raise
        except ProviderUnavailableError as exc:
            self._emit(context, model, STATUS_FAILED, attempts=1, error=exc)
            raise
        result = outcome.result
        self._emit(
            context,
            result.model,
            STATUS_SUCCESS,
            usage=result.usage,
            latency_ms=outcome.latency_ms,
            attempts=outcome.attempts,
        )
        return result

    def _emit(
        self,
        context: CallContext,
        model: str,
        status: str,
        *,
        usage=None,
        latency_ms: float = 0.0,
        attempts: int = 0,
        error: BaseException | None = None,
    ) -> None:
        event = ModelCallEvent(
            provider=self._provider.name,
            model=model,
            tenant_id=context.tenant_id,
            agent_id=context.agent_id,
            logical_key=context.logical_key,
            status=status,
            usage=usage,
            latency_ms=latency_ms,
            attempts=attempts,
            error_type=type(error).__name__ if error else None,
            error_detail=str(error) if error else None,
        )
        self._router.emit(event)


class ProviderRegistry:
    """Composition root: route resolution, client pool, hooks, fallback chain."""

    def __init__(
        self,
        configs: Mapping[str, ProviderConfig] | None = None,
        *,
        transport_factory: Callable[[str, ProviderConfig], HttpTransport] | None = None,
        credentials_factory: Callable[[str, str], Credentials] | None = None,
        vault: ApiKeyVault | None = None,
        router: EventRouter | None = None,
    ) -> None:
        configs = configs or default_provider_configs()
        self._configs: dict[str, ProviderConfig] = dict(configs)
        self._overrides = TenantOverrides()
        self._router = router or EventRouter()
        self._breakers = CircuitBreakerManager()
        self._clients: dict[tuple[str, str], ProviderClient] = {}
        self._transport_factory = transport_factory or (
            lambda provider, cfg: StdlibHttpTransport()
        )
        self._credentials_factory = credentials_factory
        self._vault = vault

    # -- configuration ------------------------------------------------------ #
    def provider_configs(self) -> dict[str, ProviderConfig]:
        return dict(self._configs)

    def config_for(self, provider: str) -> ProviderConfig:
        config = self._configs.get(provider)
        if config is None:
            raise ProviderConfigurationError(
                f"unknown provider {provider!r}; known: {', '.join(PROVIDER_NAMES)}",
                provider=provider,
            )
        return config

    def register_provider_config(self, config: ProviderConfig) -> None:
        if config.name not in PROVIDER_CLASSES:
            raise ProviderConfigurationError(
                f"no adapter registered for provider {config.name!r}"
            )
        self._configs[config.name] = config

    def set_tenant_mapping(self, tenant_id: str, mappings: Mapping[str, str]) -> None:
        self._overrides.add(
            TenantModelMapping(tenant_id=tenant_id, mappings=dict(mappings))
        )

    def load_tenant_overrides(self, path: str) -> None:
        loaded = TenantOverrides.from_yaml(path)
        for mapping in loaded.all().values():
            self._overrides.add(mapping)

    def add_metering_hook(self, hook) -> None:
        self._router.add_metering_hook(hook)

    def add_audit_hook(self, hook) -> None:
        self._router.add_audit_hook(hook)

    # -- route resolution --------------------------------------------------- #
    def resolve_route(self, tenant_id: str, logical_key: str) -> Route:
        mapping = self._overrides.mapping_for(tenant_id)
        target = mapping.target_for(logical_key) if mapping is not None else None
        if target is None:
            provider = DEFAULT_PROVIDER_BY_TIER.get(logical_key)
            if provider is None:
                raise ProviderConfigurationError(
                    f"no default route for logical key {logical_key!r}; expected a "
                    f"tier ({', '.join(TIERS)}) or a tenant override"
                )
            config = self.config_for(provider)
            return Route(provider=provider, model=config.tier_model_for(logical_key))
        return self._parse_target(target, logical_key)

    def _parse_target(self, target: str, logical_key: str) -> Route:
        if "/" in target:
            provider, model = target.split("/", 1)
        elif target in self._configs:
            provider = target
            config = self._configs[target]
            model = config.tier_model_for(logical_key) if logical_key in TIERS else config.default_model
        else:
            raise ProviderConfigurationError(
                f"override target {target!r} is neither a known provider "
                f"({', '.join(PROVIDER_NAMES)}) nor 'provider/model'"
            )
        config = self.config_for(provider)
        if not config.model_supported(model):
            raise ProviderConfigurationError(
                f"model {model!r} is not supported by provider {provider}",
                provider=provider,
                model=model,
            )
        return Route(provider=provider, model=model)

    # -- calling ------------------------------------------------------------ #
    def chat(
        self,
        messages: Sequence[ChatMessage | Mapping[str, str]],
        schema: Mapping[str, Any] | None = None,
        *,
        tenant_id: str,
        agent_id: str,
        logical_key: str = DEFAULT_TIER,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_ms: int | None = None,
    ) -> ChatResult:
        """Route and perform one resilient, stamped, audited model call."""
        route = self.resolve_route(tenant_id, logical_key)
        route_model = model or route.model
        config = self.config_for(route.provider)
        self._validate_model(config, route_model)
        context = CallContext(
            tenant_id=tenant_id, agent_id=agent_id, logical_key=logical_key
        )
        coerced = _coerce_messages(messages)
        primary_options = ChatOptions(
            model=route_model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_ms=timeout_ms,
        )
        client = self._client_for(tenant_id, route.provider)
        try:
            return client.chat(coerced, schema, primary_options, context)
        except (ProviderUnavailableError, RetryExhaustedError, CircuitOpenError) as primary_error:
            if not config.fallback:
                raise
            # Graceful degradation: walk the provider's fallback chain. Each
            # fallback resolves ITS OWN model for the logical key (a fallback
            # provider does not understand the primary's model id).
            for fallback_name in config.fallback:
                fb_config = self.config_for(fallback_name)
                fb_model = (
                    fb_config.tier_model_for(logical_key)
                    if logical_key in TIERS
                    else fb_config.default_model
                )
                fb_options = ChatOptions(
                    model=fb_model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout_ms=timeout_ms,
                )
                try:
                    fb_client = self._client_for(tenant_id, fallback_name)
                    return fb_client.chat(coerced, schema, fb_options, context)
                except ProviderUnavailableError:
                    continue  # each fallback outcome is already audited
            raise primary_error

    def _validate_model(self, config: ProviderConfig, model: str) -> None:
        if not config.model_supported(model):
            raise ProviderConfigurationError(
                f"model {model!r} is not supported by provider {config.name}",
                provider=config.name,
                model=model,
            )

    # -- internals ---------------------------------------------------------- #
    def _client_for(self, tenant_id: str, provider_name: str) -> ProviderClient:
        key = (tenant_id, provider_name)
        existing = self._clients.get(key)
        if existing is not None:
            return existing
        config = self.config_for(provider_name)
        adapter_class = PROVIDER_CLASSES.get(provider_name)
        if adapter_class is None:
            raise ProviderConfigurationError(
                f"no adapter registered for provider {provider_name!r}"
            )
        transport = self._transport_factory(provider_name, config)
        credentials = self._credentials_for(tenant_id, provider_name, config)
        adapter = adapter_class(config, transport, credentials)
        breaker = self._breakers.get_or_create(
            f"{tenant_id}:{provider_name}", config.breaker
        )
        client = ProviderClient(adapter, config, breaker, self._router)
        self._clients[key] = client
        return client

    def _credentials_for(
        self,
        tenant_id: str,
        provider_name: str,
        config: ProviderConfig,
    ) -> Credentials:
        if self._credentials_factory is not None:
            return self._credentials_factory(tenant_id, provider_name)
        api_key: str | None = None
        if self._vault is not None and self._vault.has_key(tenant_id, provider_name):
            api_key = self._vault.get_key(tenant_id, provider_name)
        if config.requires_key and not api_key:
            raise ProviderConfigurationError(
                f"no API key configured for tenant {tenant_id!r} provider "
                f"{provider_name!r} (set it in the vault); refusing to call "
                f"unauthenticated (fail closed)",
                provider=provider_name,
            )
        return Credentials(api_key=api_key)

    def breaker_snapshots(self) -> dict[str, dict[str, Any]]:
        return self._breakers.snapshots()
