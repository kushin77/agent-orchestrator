"""Shared test doubles + fixtures for the gateway proxy tests (issue #16).

Pure offline helpers: fake resolvers/chooser/limits/backend plus canned typed
outputs.  Tests exercise the dispatch core with deterministic doubles here,
and the real sibling modules in ``test_integration.py`` via
``proxy.wiring.build_real_gateway``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from proxy import contract
from proxy.backend import BackendResult
from proxy.model import (
    AgentView,
    Message,
    RouteCandidate,
    TaskView,
    TierChoice,
)

# --- Canned JSON Schema + valid/invalid outputs ---------------------------- #
CLASSIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["route", "priority", "confidence", "reasoning"],
    "properties": {
        "route": {"type": "string", "enum": ["billing", "support", "sales", "security", "noise"]},
        "priority": {"type": "string", "enum": ["critical", "high", "normal", "low"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reasoning": {"type": "string", "maxLength": 500},
    },
}

VALID_CLASSIFY_JSON = json.dumps(
    {"route": "support", "priority": "high", "confidence": 0.9,
     "reasoning": "Billing outage reported."}
)
INVALID_CLASSIFY_JSON = json.dumps(
    {"route": "not-a-route", "priority": "high", "confidence": 0.9,
     "reasoning": "bad enum value"}
)
NOT_JSON = "this is not json {{{"

# --- Fake seams ------------------------------------------------------------- #
def make_agent(
    agent_id: str = "orchestrator",
    tenant_id: str = "acme",
    *,
    profile_id: str = "orchestrator",
    capabilities: frozenset[str] = frozenset({"orchestrate", "research"}),
    tier: str = "MED",
) -> AgentView:
    return AgentView(
        agent_id=agent_id,
        tenant_id=tenant_id,
        profile_id=profile_id,
        persona_id=agent_id,
        capability_set=frozenset(capabilities),
        tool_allowlist=frozenset({"file_read", "shell_exec"}),
        constraint_set=frozenset({"issue-first", "no-secrets"}),
        default_model_tier=tier,
        guardrail_policy_ref="worker-bundle",
    )


def make_task(
    task_type: str = "classify-route",
    *,
    schema: Mapping[str, Any] | None = CLASSIFY_SCHEMA,
    messages: tuple[Message, ...] = (Message("system", "sys"), Message("user", "usr")),
    hint: str = "low",
) -> TaskView:
    return TaskView(
        task_type=task_type,
        version="v1",
        prompt_id=f"{task_type}@v1",
        model_tier_hint=hint,
        messages=messages,
        output_schema=dict(schema) if schema is not None else None,
    )


def make_long_task(task_type: str = "classify-route") -> TaskView:
    """A task with a long prompt so token-budget estimates exceed small caps."""
    return make_task(
        task_type,
        messages=(Message("system", "sys"), Message("user", "y" * 800)),
    )


#: Restrict the routed chain to a single (deepseek) provider for single-candidate
#: tests: every other provider of the LOW chain is marked unhealthy.
SINGLE_PROVIDER_HEALTH = {"openai": False, "ollama": False}


class FakeAgentResolver:
    """Deterministic agent resolver over a fixed view map."""

    def __init__(self, views: Mapping[str, AgentView] | None = None) -> None:
        self.views: dict[str, AgentView] = dict(views or {})
        self.calls: list[tuple[str, str]] = []

    def with_agent(self, view: AgentView) -> "FakeAgentResolver":
        self.views[(view.tenant_id, view.agent_id)] = view
        return self

    def resolve(self, tenant_id: str, agent_id: str) -> AgentView:
        self.calls.append((tenant_id, agent_id))
        view = self.views.get((tenant_id, agent_id))
        if view is None:
            raise contract.AgentResolutionError(
                f"unknown agent {agent_id!r} for tenant {tenant_id!r}"
            )
        return view


class FakeTaskResolver:
    """Deterministic task resolver over a fixed task-view map."""

    def __init__(self, views: Mapping[str, TaskView] | None = None) -> None:
        self.views: dict[str, TaskView] = dict(views or {})

    def with_task(self, view: TaskView) -> "FakeTaskResolver":
        self.views[view.task_type] = view
        return self

    def resolve(self, task_type: str, variables: Mapping[str, Any] | None = None) -> TaskView:
        view = self.views.get(task_type)
        if view is None:
            raise contract.TaskResolutionError(
                f"unknown task type {task_type!r} (not a published prompt module)"
            )
        return view


@dataclass
class FakeChooser:
    """Deterministic chooser seam returning configured TierChoices."""

    default_tier: str = "L0"
    default_provider: str | None = None
    choice: TierChoice | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def choose(
        self,
        task_class: str,
        tenant_id: str = "system",
        agent_id: str = "anonymous",
        complexity: float | None = None,
        tokens: int | None = None,
    ) -> TierChoice:
        self.calls.append(
            {
                "task_class": task_class,
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "complexity": complexity,
                "tokens": tokens,
            }
        )
        if self.choice is not None:
            return self.choice
        return TierChoice(
            task_class=task_class,
            tier=self.default_tier,
            provider=self.default_provider,
        )


class ScriptedBackend:
    """Deterministic backend seam: one handler per provider (+ default).

    ``handlers`` maps provider name -> callable(candidate, invocation) that
    returns a ``BackendResult`` or raises a ``BackendError``.  Records every
    call so tests can assert on the exact attempt order.
    """

    def __init__(
        self,
        handlers: dict[str, Callable[..., Any]] | None = None,
        *,
        default: Callable[..., Any] | None = None,
    ) -> None:
        self.handlers: dict[str, Callable[..., Any]] = dict(handlers or {})
        self.default = default
        self.calls: list[tuple[RouteCandidate, Any]] = []

    def on(self, provider: str, handler: Callable[..., Any]) -> "ScriptedBackend":
        self.handlers[provider] = handler
        return self

    def result(self, provider: str, raw_text: str, **over: Any) -> BackendResult:
        return BackendResult(
            provider=provider,
            model=over.pop("model", f"{provider}-model"),
            raw_text=raw_text,
            content=over.pop("content", None),
            input_tokens=over.pop("input_tokens", 10),
            output_tokens=over.pop("output_tokens", 5),
            latency_ms=over.pop("latency_ms", 1.0),
            **over,
        )

    def execute(self, candidate: RouteCandidate, invocation: Any) -> BackendResult:
        self.calls.append((candidate, invocation))
        handler = self.handlers.get(candidate.provider) or self.default
        if handler is None:
            raise contract.ProxyError(
                f"no handler for provider {candidate.provider!r}"
            )
        return handler(candidate, invocation)


class ServingLimits:
    """Limits-facade double: always allows (used to isolate dispatch logic)."""

    def __init__(self) -> None:
        self.guards: list[Any] = []
        self.completes: list[tuple[Any, str]] = []

    def guard(self, request: Any, requested_tokens: int | None = None) -> Any:
        self.guards.append(request)
        return _AllowDecision(request)

    def complete(self, request: Any, raw_response: str, **kwargs: Any) -> Any:
        self.completes.append((request, raw_response))
        return _CompleteResult()


class _AllowDecision:
    """Guard decision double: always allowed (kind=allow)."""

    kind = "allow"
    response = None

    def __init__(self, request=None) -> None:
        self.request = request

    def served(self) -> bool:
        return True


class _CompleteResult:
    refused = False
    trimmed = False
    response = None
    metering = None


def build_gateway(
    *,
    agent: AgentView | None = None,
    task: TaskView | None = None,
    backend: ScriptedBackend | None = None,
    chooser: FakeChooser | None = None,
    limits=None,
    health=None,
    audit=None,
    metering=None,
    validator=None,
):
    """Assemble a ModelGateway over deterministic doubles (unit tests)."""
    from proxy.gateway import ModelGateway
    from proxy.router import Router
    from proxy.sinks import ListCallRecordSink

    agent_resolver = FakeAgentResolver()
    if agent is not None:
        agent_resolver.with_agent(agent)
    task_resolver = FakeTaskResolver()
    if task is not None:
        task_resolver.with_task(task)
    gateway = ModelGateway(
        agent_resolver=agent_resolver,
        task_resolver=task_resolver,
        chooser=chooser if chooser is not None else FakeChooser(),
        backend=backend if backend is not None else ScriptedBackend(),
        limits=limits if limits is not None else ServingLimits(),
        router=Router(),
        health=health,
        validator=validator,
        audit_sink=audit if audit is not None else ListCallRecordSink(),
        metering_sink=metering if metering is not None else ListCallRecordSink(),
    )
    return gateway, agent_resolver, task_resolver
