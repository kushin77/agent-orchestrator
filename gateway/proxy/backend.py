"""Model-backend seam for the gateway proxy (issue #16).

The proxy core never talks to a provider directly.  It calls an injected
``ModelBackend`` — one method, ``execute(candidate, invocation)`` — that
performs a single provider call for one routed candidate and returns the raw
output plus the provider's usage/latency stamp.  The real backend (wired in
integration and the CLI) delegates to the merged provider registry
(``providers.registry.ProviderRegistry``, issue #15); unit tests inject a
deterministic double.

The backend reports *output* and *availability* separately so the dispatch
loop owns the two policies the acceptance criteria demand:

- invalid typed output  -> retry once, then CANNOT-ASSESS (never silent pass);
- provider unavailable  -> next candidate in the fallback chain (graceful
  degradation, cloud -> local Ollama).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable

from proxy.model import ChatInvocation, RouteCandidate

from proxy import contract


class BackendError(contract.ProxyError):
    """Base error raised by a model backend."""


class BackendUnavailableError(BackendError):
    """The candidate provider could not serve this call (transient/unavailable).

    The dispatch loop skips to the next candidate in the fallback chain.
    """


class BackendOutputInvalidError(BackendError):
    """The candidate provider reported schema-invalid typed output.

    The dispatch loop counts this as an invalid-output attempt (retry once,
    then CANNOT-ASSESS).
    """


class BackendConfigurationError(BackendError):
    """The candidate provider is misconfigured (hard, non-transient)."""


@dataclass(frozen=True)
class BackendResult:
    """The provider-neutral outcome of one backend call.

    ``raw_text`` is the raw model text the proxy validates against the prompt
    module's output schema; ``content`` is the provider-validated object when
    the provider already parsed it (informational).  Usage/latency/model are
    the provider's own stamp for the gateway call record.
    """

    provider: str
    model: str
    content: Any = None
    raw_text: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0


@runtime_checkable
class ModelBackend(Protocol):
    """One injected provider execution seam.

    Implementations must be offline-testable (no sockets when the transport is
    a double) and must raise ``BackendUnavailableError`` for availability
    failures and ``BackendOutputInvalidError`` when the provider itself
    reports invalid typed output.
    """

    def execute(
        self, candidate: RouteCandidate, invocation: ChatInvocation
    ) -> BackendResult:
        """Perform one provider call for ``candidate`` and return its output."""
        ...


class StaticBackend:
    """Deterministic offline backend double for demos and tests.

    ``handlers`` maps a provider name (or ``None`` for any provider) to a
    callable ``(candidate, invocation) -> BackendResult`` or an exception to
    raise.  A handler may also return a plain dict that is coerced into a
    ``BackendResult``.
    """

    def __init__(
        self,
        handlers: dict[str | None, Callable[..., Any]] | None = None,
        *,
        default_model: str = "model",
    ) -> None:
        self.handlers: dict[str | None, Callable[..., Any]] = dict(handlers or {})
        self.default_model = default_model
        self.calls: list[tuple[RouteCandidate, ChatInvocation]] = []

    def on(self, provider: str | None, handler: Callable[..., Any]) -> "StaticBackend":
        self.handlers[provider] = handler
        return self

    def execute(
        self, candidate: RouteCandidate, invocation: ChatInvocation
    ) -> BackendResult:
        self.calls.append((candidate, invocation))
        handler = self.handlers.get(candidate.provider) or self.handlers.get(None)
        if handler is None:
            raise BackendUnavailableError(
                f"no backend handler for provider {candidate.provider!r}"
            )
        outcome = handler(candidate, invocation)
        if isinstance(outcome, BackendError):
            raise outcome
        if isinstance(outcome, BackendResult):
            return outcome
        if isinstance(outcome, dict):
            return BackendResult(
                provider=candidate.provider,
                model=outcome.get("model", self.default_model),
                content=outcome.get("content"),
                raw_text=outcome.get("raw_text"),
                input_tokens=outcome.get("input_tokens", 0),
                output_tokens=outcome.get("output_tokens", 0),
                latency_ms=outcome.get("latency_ms", 0.0),
            )
        raise BackendError(
            f"backend handler for {candidate.provider!r} returned an invalid "
            f"outcome: {type(outcome).__name__}"
        )
