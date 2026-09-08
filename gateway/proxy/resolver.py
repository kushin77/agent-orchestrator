"""Injected resolver seams for the gateway proxy (issue #16).

The dispatch core is *wired* to the merged sibling contracts through these
duck-typed seams rather than importing them directly, so it is
standalone-testable with deterministic doubles and the real modules plug in
without touching the funnel:

- ``AgentResolver``   — profile/persona resolution (consumes issue #9/#11).
- ``TaskResolver``    — prompt-module resolution + rendering (issue #13).
- ``Chooser``         — FinOps cheapest-capable model-tier chooser (issue #17).
- ``HealthSignal``    — injected model/provider health (gateway/health, issue
  #18, merges in parallel and is consumed as this injected signal only).
- ``LimitsEngineLike``— the cost/capacity guard facade (issue #19).

Every protocol is ``@runtime_checkable`` so fakes and the real modules both
satisfy it structurally.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Optional, Protocol, runtime_checkable

from proxy.model import AgentView, TaskView, TierChoice

# A health signal is absent (everything healthy), a mapping of provider (or
# model) id -> healthy, or a predicate of id -> healthy.  Absent keys in a
# mapping are treated as healthy (an opt-in deny-list).
HealthSignal = Optional[Mapping[str, bool] | Callable[[str], bool]]


def is_healthy(health: HealthSignal, key: str) -> bool:
    """Evaluate the injected health signal for one provider/model id.

    ``None`` means everything is healthy.  A mapping treats a missing key as
    healthy (deny-list semantics).  A callable is invoked with the id and must
    return a bool.
    """
    if health is None:
        return True
    if callable(health):
        return bool(health(key))
    return bool(health.get(key, True))


@runtime_checkable
class AgentResolver(Protocol):
    """Resolves an agent (profile/persona) for a dispatch."""

    def resolve(self, tenant_id: str, agent_id: str) -> AgentView:
        """Return the agent view; raise AgentResolutionError when unknown."""
        ...


@runtime_checkable
class TaskResolver(Protocol):
    """Resolves and renders a prompt module for a task type."""

    def resolve(self, task_type: str, variables: Mapping[str, Any] | None = None) -> TaskView:
        """Return the rendered task view; raise TaskResolutionError when unknown."""
        ...


@runtime_checkable
class Chooser(Protocol):
    """The FinOps cheapest-capable model-tier chooser (duck-typed)."""

    def choose(
        self,
        task_class: str,
        tenant_id: str = "system",
        agent_id: str = "anonymous",
        complexity: float | None = None,
        tokens: int | None = None,
    ) -> TierChoice:
        """Return the routed tier decision; raise on unknown class/budget block."""
        ...


@runtime_checkable
class LimitsEngineLike(Protocol):
    """The cost/capacity guard facade (gateway/limits ``LimitsEngine``).

    Only the two methods the proxy calls are declared; the real
    ``limits.limiter.LimitsEngine`` satisfies this structurally.
    """

    def guard(self, request: Any, requested_tokens: int | None = None) -> Any:
        """Check cache/budget/rate before any provider call.  Returns a decision
        with ``served()`` and ``kind`` (and ``response`` on a cache hit)."""
        ...

    def complete(
        self,
        request: Any,
        raw_response: str,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> Any:
        """Finish an allowed call: throttle output, meter real usage, cache."""
        ...
