"""Routing policy for the gateway proxy (issue #16).

The router turns a resolved task + agent into a concrete route decision:

    task-type  ->  capability  ->  FinOps ladder tier  ->  registry tier
                     ->  provider fallback chain (primary -> fallback -> local)

The routing *policy* is declarative and owned by this lane
(``config/routing.yaml``): which task type needs which capability (registry
catalog ids) and which FinOps task class it maps to, how the FinOps ladder
tier maps onto the registry tier vocabulary, and the ordered provider chain
per registry tier (cloud -> local Ollama by default).  The *tier choice* is
delegated to the injected FinOps chooser (issue #17) and the *health* of the
chain is the injected health signal (issue #18) — both consumed, never
redefined.

---knowledge---
module_id: gateway.proxy.router
system: gateway
app: proxy
solution_class: enterprise
patterns: [declarative-policy, delegated-tier-choice, fail-closed]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [Router, RoutingConfig, load_routing_config, TaskRoute, AgentRoute, RoutingGroup]
invariants: "the tier choice is delegated to the injected FinOps chooser and chain health to the injected health signal; the policy itself is declarative"
gotchas: "the ordered provider chain per registry tier defaults to cloud then local Ollama"
related: ["#16"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

try:
    import yaml  # type: ignore
except ImportError as exc:  # pragma: no cover - declared repo dependency
    raise RuntimeError(f"proxy: missing dependency ({exc}); need PyYAML") from exc

from proxy.contract import (
    CapabilityDeniedError,
    NoHealthyRouteError,
    RoutingConfigError,
    UnknownTaskRouteError,
)
from proxy.model import (
    AgentView,
    RouteCandidate,
    RouteDecision,
    TaskRequest,
    TaskView,
    TierChoice,
)
from proxy.resolver import Chooser, HealthSignal, is_healthy

PKG_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PKG_DIR / "config" / "routing.yaml"

#: Canonical ladder tier -> registry profile tier map (fallback defaults).
DEFAULT_TIER_MAP: dict[str, str] = {
    "L0": "LOW",
    "L1": "MED",
    "L2": "HIGH",
}

#: Roles a provider hop can carry.
ROLE_PRIMARY = "primary"
ROLE_FALLBACK = "fallback"
ROLE_LOCAL = "local"

#: The local (keyless) terminal provider of every cloud -> local chain.
LOCAL_PROVIDER = "ollama"


@dataclass(frozen=True)
class TaskRoute:
    """One routing-policy entry: task type -> capability + FinOps task class."""

    capability: str
    task_class: str


@dataclass(frozen=True)
class AgentRoute:
    """One agent's pinned provider route within a routing group."""

    provider: str
    fallbacks: tuple[str, ...] = ()


@dataclass
class RoutingGroup:
    """A named routing group: agent id -> pinned provider + fallback chain."""

    agents: dict[str, AgentRoute]
    retry_max_attempts: int = 2


@dataclass
class RoutingConfig:
    """Validated routing policy (loads ``config/routing.yaml``, fail closed)."""

    schema_version: int = 1
    routes: dict[str, TaskRoute] = field(default_factory=dict)
    tier_map: dict[str, str] = field(default_factory=dict)
    provider_chains: dict[str, tuple[str, ...]] = field(default_factory=dict)
    routing_groups: dict[str, RoutingGroup] = field(default_factory=dict)

    def route_for(self, task_type: str) -> TaskRoute:
        route = self.routes.get(task_type)
        if route is None:
            raise UnknownTaskRouteError(
                f"task type {task_type!r} is not in the proxy routing policy; "
                "a runtime dispatch must reference a routed task type"
            )
        return route

    def map_tier(self, ladder_tier: str) -> str:
        registry = self.tier_map.get(ladder_tier)
        if registry is None:
            raise RoutingConfigError(
                f"ladder tier {ladder_tier!r} is not mapped to a registry tier"
            )
        return registry

    def chain_for(self, registry_tier: str) -> tuple[str, ...]:
        chain = self.provider_chains.get(registry_tier)
        if not chain:
            raise RoutingConfigError(
                f"no provider chain configured for registry tier {registry_tier!r}"
            )
        return chain

    def group_route_for(self, agent_id: str) -> AgentRoute | None:
        """The agent's pinned provider route from any routing group (or None)."""
        for group in self.routing_groups.values():
            route = group.agents.get(agent_id)
            if route is not None:
                return route
        return None


def _validate_config(data: Mapping[str, Any]) -> RoutingConfig:
    if not isinstance(data, Mapping) or not data.get("schemaVersion"):
        raise RoutingConfigError("routing config is missing schemaVersion")
    routes_raw = data.get("routes")
    if not isinstance(routes_raw, Mapping) or not routes_raw:
        raise RoutingConfigError("routing config must declare at least one route")
    routes: dict[str, TaskRoute] = {}
    for task_type, spec in routes_raw.items():
        if not isinstance(spec, Mapping) or not spec.get("capability") or not spec.get("taskClass"):
            raise RoutingConfigError(
                f"route {task_type!r} must declare capability and taskClass"
            )
        routes[str(task_type)] = TaskRoute(
            capability=str(spec["capability"]), task_class=str(spec["taskClass"])
        )
    tier_map: dict[str, str] = {}
    for ladder, registry in (data.get("tierMap") or {}).items():
        tier_map[str(ladder)] = str(registry)
    chains_raw = data.get("providerChains")
    if not isinstance(chains_raw, Mapping) or not chains_raw:
        raise RoutingConfigError("routing config must declare providerChains")
    chains: dict[str, tuple[str, ...]] = {}
    for tier, providers in chains_raw.items():
        if not isinstance(providers, list) or not providers:
            raise RoutingConfigError(f"provider chain for {tier!r} must be a non-empty list")
        chain = tuple(str(p) for p in providers)
        if len(set(chain)) != len(chain):
            raise RoutingConfigError(f"provider chain for {tier!r} contains duplicates")
        chains[str(tier)] = chain
    routing_groups: dict[str, RoutingGroup] = {}
    for group_name, group_spec in (data.get("routingGroups") or {}).items():
        if not isinstance(group_spec, Mapping):
            raise RoutingConfigError(f"routing group {group_name!r} must be a mapping")
        agents_raw = group_spec.get("agents")
        if not isinstance(agents_raw, Mapping) or not agents_raw:
            raise RoutingConfigError(f"routing group {group_name!r} must declare agents")
        agents: dict[str, AgentRoute] = {}
        for agent_id, spec in agents_raw.items():
            if isinstance(spec, str) and spec:
                provider, fallbacks = spec, ()
            elif isinstance(spec, Mapping) and spec.get("provider"):
                provider = str(spec["provider"])
                fb = spec.get("fallbacks")
                fallbacks = tuple(str(f) for f in fb) if isinstance(fb, (list, tuple)) else ()
            else:
                raise RoutingConfigError(
                    f"routing group {group_name!r} agent {agent_id!r} must "
                    "declare a provider"
                )
            if provider in fallbacks:
                raise RoutingConfigError(
                    f"routing group {group_name!r} agent {agent_id!r} fallback "
                    f"chain contains its own primary provider {provider!r}"
                )
            agents[str(agent_id)] = AgentRoute(provider=provider, fallbacks=fallbacks)
        retry_raw = group_spec.get("retryMaxAttempts")
        retry_max = int(retry_raw) if isinstance(retry_raw, int) and retry_raw > 0 else 2
        routing_groups[str(group_name)] = RoutingGroup(
            agents=agents, retry_max_attempts=retry_max
        )
    return RoutingConfig(
        schema_version=int(data.get("schemaVersion")),
        routes=routes,
        tier_map=tier_map or dict(DEFAULT_TIER_MAP),
        provider_chains=chains,
        routing_groups=routing_groups,
    )


def load_routing_config(path: Path = DEFAULT_CONFIG_PATH) -> RoutingConfig:
    """Load and validate the proxy routing policy from YAML (fail closed)."""
    if not Path(path).is_file():
        raise RoutingConfigError(f"routing config not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    try:
        return _validate_config(data)
    except RoutingConfigError:
        raise
    except Exception as exc:  # noqa: BLE001 - wrap any loader failure
        raise RoutingConfigError(f"routing config {path} is invalid: {exc}") from exc


def _role_for(provider: str, index: int, length: int) -> str:
    if index == 0:
        return ROLE_PRIMARY
    if provider == LOCAL_PROVIDER or index == length - 1:
        return ROLE_LOCAL if provider == LOCAL_PROVIDER else ROLE_FALLBACK
    return ROLE_FALLBACK


class Router:
    """Task-type -> capability -> tier -> candidate-chain resolution."""

    def __init__(self, config: RoutingConfig | None = None) -> None:
        self.config = config or load_routing_config()

    @classmethod
    def from_path(cls, path: Path) -> "Router":
        return cls(load_routing_config(path))

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def route(
        self,
        task_view: TaskView,
        agent_view: AgentView,
        request: TaskRequest,
        *,
        chooser: Chooser,
        health: HealthSignal = None,
    ) -> RouteDecision:
        """Resolve the full route for one task + agent.

        Steps: capability boundary (fail closed) -> FinOps tier choice ->
        registry tier + provider chain -> health filter.  Raises
        ``CapabilityDeniedError`` when the agent does not hold the required
        capability and ``NoHealthyRouteError`` when no candidate of the chain
        is healthy (never a silent pass).
        """
        route = self.config.route_for(task_view.task_type)
        if not agent_view.has_capability(route.capability):
            raise CapabilityDeniedError(
                f"agent {agent_view.agent_id!r} (profile {agent_view.profile_id!r}) "
                f"does not hold capability {route.capability!r} required by task "
                f"type {task_view.task_type!r}"
            )
        choice = chooser.choose(
            task_class=route.task_class,
            tenant_id=request.tenant_id,
            agent_id=agent_view.agent_id,
            complexity=request.complexity,
            tokens=request.tokens,
        )
        if not isinstance(choice, TierChoice):
            raise RoutingConfigError(
                f"chooser returned {type(choice).__name__}, expected proxy.TierChoice"
            )
        registry_tier = self.config.map_tier(choice.tier)
        agent_route = self.config.group_route_for(agent_view.agent_id)
        chain = (
            self._agent_chain(agent_route)
            if agent_route is not None
            else self._ordered_chain(registry_tier, choice)
        )
        all_candidates = tuple(
            RouteCandidate(
                provider=provider,
                registry_tier=registry_tier,
                role=_role_for(provider, i, len(chain)),
            )
            for i, provider in enumerate(chain)
        )
        candidates = tuple(
            c for c in all_candidates if is_healthy(health, c.provider)
        )
        if not candidates:
            raise NoHealthyRouteError(
                f"no healthy route for task {task_view.task_type!r} at registry "
                f"tier {registry_tier!r}: {', '.join(c.provider for c in all_candidates)} "
                "all unhealthy (or absent)"
            )
        return RouteDecision(
            task_type=task_view.task_type,
            capability=route.capability,
            task_class=route.task_class,
            ladder_tier=choice.tier,
            registry_tier=registry_tier,
            candidates=candidates,
            all_candidates=all_candidates,
            estimated_cost_usd=choice.estimated_cost_usd,
            budget_action=choice.budget_action,
            reasons=tuple(choice.reasons),
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _ordered_chain(
        self, registry_tier: str, choice: TierChoice
    ) -> tuple[str, ...]:
        """The provider chain for a registry tier, hoisting the chooser's
        preferred provider (when given) to primary."""
        chain = list(self.config.chain_for(registry_tier))
        if choice.provider and choice.provider in chain:
            chain = [choice.provider] + [p for p in chain if p != choice.provider]
        return tuple(chain)

    def _agent_chain(self, agent_route: AgentRoute) -> tuple[str, ...]:
        """The pinned provider chain for a routing-group agent (primary ->
        fallbacks).

        Group pinning is authoritative for the agent: the tier chain is not
        used for a group member, but the FinOps tier choice still sets the
        cost/budget and the model tier within the pinned provider (the backend
        resolves each candidate's model per tier).
        """
        return (agent_route.provider,) + tuple(
            f for f in agent_route.fallbacks if f != agent_route.provider
        )
