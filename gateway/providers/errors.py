"""Provider-layer exception hierarchy (issue #15).

This module is dependency-free so every other module can import it without
import cycles. The taxonomy mirrors the harvested providers:

- ``ProviderUnavailableError`` and its subclass ``ProviderTimeoutError`` are
  TRANSIENT: they are retried and counted against the circuit breaker.
- Everything else is NOT transient: it is raised immediately and never
  retried (there is no point hammering a provider for a bad key, bad request
  or a caller-supplied schema).

The two fail-closed errors:

- ``OutputValidationError`` - the provider returned content that does not
  satisfy the caller's output schema. The adapter NEVER silently passes
  invalid output through (no-false-green / fail-closed doctrine).
- ``VaultUnavailableError`` - the API-key vault refuses to operate (no master
  key in ``AO_VAULT_KEY``, crypto backend absent). Keys are never stored or
  served in plaintext and never appear in messages.

Adapted from the failure shapes of the cannibalized providers
(GR-10 / docs/CANNIBALIZATION.md): ollama ``exceptions`` tree and the
llm-triage/defragsuite transient-vs-fatal split.
"""

from __future__ import annotations


class ProviderError(Exception):
    """Base class for all model-provider errors."""

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model


class ProviderConfigurationError(ProviderError):
    """Configuration/usage error: unknown provider/model or bad option."""


class ProviderUnavailableError(ProviderError):
    """Transient availability failure (network error, 5xx, connection reset)."""


class ProviderTimeoutError(ProviderUnavailableError):
    """The request exceeded its timeout budget (transient)."""


class CircuitOpenError(ProviderUnavailableError):
    """The circuit breaker is OPEN: fail fast, do not call the provider."""


class RetryExhaustedError(ProviderUnavailableError):
    """Every retry attempt failed; the last underlying error is chained."""


class OutputValidationError(ProviderError):
    """Provider output failed output-schema validation (fail closed)."""


class SchemaDefinitionError(ProviderError):
    """The caller-supplied output schema is unusable (not valid JSON Schema)."""


class VaultError(Exception):
    """Base class for API-key vault errors."""


class VaultUnavailableError(VaultError):
    """The vault cannot operate: master key missing or crypto backend absent."""
