"""The ``ModelProvider`` contract for the model-gateway pillar (issue #15).

This module is the contract-freeze boundary for phase 2: the gateway proxy
(issue #16), the state-machine engine (phase 3) and the phase-5 metering lanes
consume these names; later lanes do not rename them.

The interface follows the issue acceptance criteria exactly:

    chat(messages, schema, opts) -> typed result
        {content, model_used, usage{tokens}, latency_ms}
        plus the call stamp {provider, tenant_id, agent_id, logical_key}

A concrete provider adapter (``anthropic`` / ``deepseek`` / ``openai`` /
``gemini`` / ``ollama``) speaks one provider wire protocol over an injected
transport, extracts content text + token usage + the model actually used,
then VALIDATES the typed output against the caller's output schema and fails
closed (``OutputValidationError``) when the content does not satisfy the
schema. There is no silent pass-through of invalid output.

Provenance (GR-10 / docs/CANNIBALIZATION.md, adapted, not copied):

- The result shape ``{content, model_used, usage, latency_ms}`` generalizes
  the llm-triage ``Classification`` (model_used / tokens / latency) and the
  gov-ai-scout zod-validated typed-output result into one provider-neutral
  contract.
- The tier vocabulary (``LOW/MED/HIGH/MAX``) is CONSUMED verbatim from the
  issue-#9 AgentProfile catalog (``defaultModelTier``); this lane does not
  redefine it.
- Output schemas are JSON Schema, as declared by the issue-#13 prompt-module
  ``outputSchema`` field; this lane validates against them (schema.py).

Retries, backoff and circuit breaking are NOT the adapter's concern: they are
applied per provider by ``ProviderClient`` (resilience.py + registry.py),
mirroring the harvested ollama resilient client and defragsuite gateway.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Mapping

SYSTEM = "system"
USER = "user"
ASSISTANT = "assistant"
_ROLES = frozenset({SYSTEM, USER, ASSISTANT})

# Logical model tiers consumed from the issue-#9 AgentProfile catalog
# (registry/profiles/catalog.yaml ``tiers``). The phase-2 gateway routes on
# these; this lane resolves each to a provider+model.
TIERS: tuple[str, ...] = ("LOW", "MED", "HIGH", "MAX")
DEFAULT_TIER = "MED"


def is_valid_role(role: str) -> bool:
    """Return whether ``role`` is one of the platform chat roles."""
    return role in _ROLES


@dataclass(frozen=True)
class ChatMessage:
    """One message in a chat conversation (role + content)."""

    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in _ROLES:
            raise ValueError(
                f"invalid role {self.role!r}; expected one of {sorted(_ROLES)}"
            )


@dataclass(frozen=True)
class Usage:
    """Token usage for one model call (contract: ``usage{tokens}``)."""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def tokens(self) -> int:
        """Total tokens consumed by the call."""
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "tokens": self.tokens,
        }


@dataclass(frozen=True)
class ChatOptions:
    """Per-call sampling and transport options (no tenant/agent state here)."""

    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_ms: int | None = None


@dataclass(frozen=True)
class CallContext:
    """The call stamp carried on every call: tenant/agent/logical key."""

    tenant_id: str
    agent_id: str
    logical_key: str = DEFAULT_TIER


@dataclass(frozen=True)
class ChatResult:
    """Typed result of a provider call (contract, plus the call stamp).

    ``content`` is the schema-validated structured object when a schema was
    requested, otherwise the plain text returned by the provider. The result
    is stamped with provider/model/tenant/agent so metering and audit hooks
    (events.py) and downstream lanes never need to re-derive provenance.
    """

    provider: str
    model: str
    content: Any
    usage: Usage
    latency_ms: float
    tenant_id: str
    agent_id: str
    logical_key: str = DEFAULT_TIER
    raw_text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "content": self.content,
            "usage": self.usage.to_dict(),
            "latency_ms": self.latency_ms,
            "tenant_id": self.tenant_id,
            "agent_id": self.agent_id,
            "logical_key": self.logical_key,
        }


class ModelProvider(ABC):
    """Abstract multi-provider client adapter (the contract-freeze surface).

    Implementations are synchronous and offline-testable: they never talk to a
    provider directly - the HTTP transport is injected (transport.py). A
    concrete implementation must:

    1. build the provider-specific request for ``messages``/``options``,
    2. perform ONE HTTP attempt through the injected transport,
    3. parse content text, token usage and the model actually used,
    4. validate the typed output against ``schema`` and fail closed.
    """

    #: Canonical provider key (``anthropic``, ``deepseek``, ``openai``,
    #: ``gemini``, ``ollama``). Concrete adapters fix this; the provider
    #: registry maps provider name -> adapter class.
    name: str = "abstract"

    @abstractmethod
    def chat(
        self,
        messages: list[ChatMessage],
        schema: Mapping[str, Any] | None,
        options: ChatOptions,
        context: CallContext,
    ) -> ChatResult:
        """Return the typed, schema-validated result or raise a ProviderError.

        Raises:
            ProviderUnavailableError: transient transport/availability failure.
            ProviderTimeoutError: the request exceeded its timeout budget.
            OutputValidationError: content does not satisfy ``schema`` (fail
                closed - never silently passed through).
            ProviderConfigurationError: unknown model for this provider.
        """
