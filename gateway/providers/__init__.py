"""Multi-provider client adapters - the model-gateway pillar base (issue #15).

A self-contained package under ``gateway/providers/``. Importable as
``providers`` when ``gateway/`` is on ``sys.path`` (the tests arrange this in
``tests/conftest.py``) and as ``gateway.providers`` once a later gateway-phase
lane adds a ``gateway/__init__.py`` - mirroring the identity/rbac package
convention.

Public surface
--------------

- ``contract``     - the ``ModelProvider`` contract-freeze surface:
  ``chat(messages, schema, options) -> ChatResult`` with
  ``{content, model_used, usage{tokens}, latency_ms}`` plus the call stamp
  (provider/model/tenant/agent/logical key). Tier vocabulary
  ``LOW/MED/HIGH/MAX`` consumed from the issue-#9 AgentProfile catalog.
- adapters         - ``anthropic``, ``deepseek``, ``openai`` (Copilot/GPT),
  ``gemini`` and ``ollama`` (local) providers over an injected transport;
  every adapter validates typed output against the caller's output schema and
  fails closed on invalid output.
- ``registry``     - ``ProviderRegistry`` (route resolution + per-tenant model
  overrides + resilient client pool + graceful degradation + hooks) and
  ``ProviderClient``.
- ``vault``        - ``ApiKeyVault``: per-tenant API keys encrypted at rest
  (Fernet via ``cryptography``, master key from ``AO_VAULT_KEY``), never
  logged. ``new_master_key`` / ``load_master_key`` for operator bootstrap.
- ``resilience``   - ``RetryPolicy`` / ``CircuitBreaker`` /
  ``CircuitBreakerManager`` (CLOSED -> OPEN -> HALF_OPEN).
- ``events``       - ``ModelCallEvent`` + ``EventRouter`` (metering + audit
  hooks consumed by the gateway and phase-5 metering).
- ``config``       - ``ProviderConfig`` + per-tenant ``TenantModelMapping`` /
  ``TenantOverrides`` (+ YAML loader).
- ``transport``    - pluggable HTTP transport: ``StdlibHttpTransport`` (real),
  ``RecordingTransport`` / ``FailingTransport`` (offline tests).

See ``gateway/providers/README.md`` for the full contract and configuration
guide.
"""

from __future__ import annotations

from providers.config import (
    ProviderConfig,
    TenantModelMapping,
    TenantOverrides,
    default_provider_configs,
)
from providers.contract import (
    DEFAULT_TIER,
    TIERS,
    ASSISTANT,
    SYSTEM,
    USER,
    CallContext,
    ChatMessage,
    ChatOptions,
    ChatResult,
    ModelProvider,
    Usage,
)
from providers.errors import (
    CircuitOpenError,
    OutputValidationError,
    ProviderConfigurationError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RetryExhaustedError,
    SchemaDefinitionError,
    VaultError,
    VaultUnavailableError,
)
from providers.events import (
    STATUS_CIRCUIT_OPEN,
    STATUS_CONFIG_ERROR,
    STATUS_FAILED,
    STATUS_OUTPUT_INVALID,
    STATUS_RETRY_EXHAUSTED,
    STATUS_SUCCESS,
    EventRouter,
    ModelCallEvent,
)
from providers.registry import (
    DEFAULT_PROVIDER_BY_TIER,
    PROVIDER_CLASSES,
    PROVIDER_NAMES,
    ProviderClient,
    ProviderRegistry,
)
from providers.resilience import (
    CircuitBreaker,
    CircuitBreakerManager,
    CircuitBreakerSettings,
    RetryPolicy,
)
from providers.transport import (
    FailingTransport,
    HttpResponse,
    RecordingTransport,
    StdlibHttpTransport,
)
from providers.vault import (
    MASTER_KEY_ENV,
    ApiKeyVault,
    load_master_key,
    new_master_key,
)

__all__ = [
    # contract surface
    "TIERS",
    "DEFAULT_TIER",
    "SYSTEM",
    "USER",
    "ASSISTANT",
    "ChatMessage",
    "ChatOptions",
    "ChatResult",
    "CallContext",
    "Usage",
    "ModelProvider",
    # registry / config
    "ProviderRegistry",
    "ProviderClient",
    "ProviderConfig",
    "TenantModelMapping",
    "TenantOverrides",
    "default_provider_configs",
    "PROVIDER_NAMES",
    "PROVIDER_CLASSES",
    "DEFAULT_PROVIDER_BY_TIER",
    # resilience
    "RetryPolicy",
    "CircuitBreaker",
    "CircuitBreakerManager",
    "CircuitBreakerSettings",
    # vault
    "ApiKeyVault",
    "MASTER_KEY_ENV",
    "load_master_key",
    "new_master_key",
    # events
    "EventRouter",
    "ModelCallEvent",
    "STATUS_SUCCESS",
    "STATUS_RETRY_EXHAUSTED",
    "STATUS_CIRCUIT_OPEN",
    "STATUS_OUTPUT_INVALID",
    "STATUS_FAILED",
    "STATUS_CONFIG_ERROR",
    # transport
    "StdlibHttpTransport",
    "RecordingTransport",
    "FailingTransport",
    "HttpResponse",
    # errors
    "ProviderError",
    "ProviderConfigurationError",
    "ProviderUnavailableError",
    "ProviderTimeoutError",
    "CircuitOpenError",
    "RetryExhaustedError",
    "OutputValidationError",
    "SchemaDefinitionError",
    "VaultError",
    "VaultUnavailableError",
]
