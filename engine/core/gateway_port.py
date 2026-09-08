"""Provider-agnostic model-gateway port.

Issue #21 acceptance #4: *the engine is provider-agnostic — it calls the
model gateway, never a provider directly.*  The engine core therefore depends
only on the duck-typed :class:`ModelGateway` protocol declared here and never
imports a provider package (``gateway/providers/*``), a vendor SDK, or a
provider name.

The protocol mirrors the merged model-gateway proxy contract
(``gateway/proxy``, issue #16): a ``dispatch`` call for an agent + task
returns a typed result carrying an outcome from a closed vocabulary, content,
the provider/model actually used, and a per-call cost/usage record.  The
runtime injects whatever object satisfies the protocol — in production the
real gateway proxy, in the offline suite a fake — so the engine stays
deterministic and standalone-testable.  See ``engine/core/README.md`` for the
exact mapping table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class GatewayRequest:
    """A model-gateway dispatch request (mirrors the proxy TaskRequest)."""

    tenant_id: str
    agent_id: str
    task_type: str
    input_: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GatewayResult:
    """Typed result of one model-gateway dispatch."""

    outcome: str  # closed vocabulary: success (the proxy never stays silent)
    content: Any = None
    provider: str = ""
    model: str = ""
    cost: float = 0.0
    usage: Mapping[str, int] = field(default_factory=dict)


class ModelGateway(Protocol):
    """What the engine requires of a model gateway.

    Engine code calls ``gateway.dispatch(request)`` and reads
    ``result.outcome`` / ``result.content`` / ``result.cost``.  It never
    inspects provider internals, so any gateway (real proxy or fake) can be
    injected.
    """

    def dispatch(self, request: GatewayRequest) -> GatewayResult:
        """Dispatch one task to the gateway and return a typed result."""
        ...


class NullGateway:
    """A gateway that refuses every call.

    Used when an engine is built without a gateway: a ``task`` step fails
    closed with a clear message instead of silently passing.
    """

    def dispatch(self, request: GatewayRequest) -> GatewayResult:
        raise RuntimeError(
            "no model gateway configured on this engine; a task step cannot run"
        )
