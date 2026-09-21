"""gateway.chat.resolver — the surface's two seams onto the merged siblings.

This module is where the conversational surface plugs into ``gateway/proxy``
without forking it:

* **the routing policy** — the proxy owns ``config/routing.yaml`` and the
  ``Router`` that reads it.  That policy has no entry for a chat task type, so
  this lane *composes*: it loads the proxy's own validated :class:`RoutingConfig`
  and adds the two routes this surface's prompt modules need, **refusing to
  load at all** if the proxy's policy already declares them.  A merge that could
  silently override the proxy's policy is exactly the second authority
  ADR-0023 §2 forbids; a merge that refuses to override is composition.
* **the task resolver** — the proxy's ``TaskResolver`` seam over
  ``registry/chat`` (issue #509): the published, immutable, digest-pinned chat
  prompt modules.  The surface renders the module's own bodies with the turn's
  own variables and hands the proxy a :class:`TaskView`, so the typed-output
  schema validated at the gateway is the module's schema.

Neither the route table nor the prompt module is re-declared here: the module
ids, their bodies and their output schemas are read from ``registry/chat``, and
the capability/task-class pair each route names is consumed from
``registry/profiles/catalog.yaml`` and ``gateway/finops/tiers.yaml``.

---knowledge---
module_id: gateway.chat.resolver
system: gateway
app: chat
solution_class: enterprise
patterns: [composition-never-fork, refuse-to-override, seam-adapter]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [ChatTaskResolver, ResolvedChatTask, ChatRouteError, load_chat_routes, chat_routing_config, task_type_for]
invariants: "the surface refuses to load at all if the proxy policy already declares its two routes — a silent override is the second authority ADR-0023 forbids"
gotchas: "the task resolver seam reads registry/chat's published, digest-pinned modules rather than carrying its own copy"
related: ["#503", "#509"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

try:  # pragma: no cover - the repo declares PyYAML
    import yaml  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(f"gateway.chat: missing dependency ({exc}); need PyYAML") from exc

from proxy import contract as proxy_contract
from proxy.router import RoutingConfig, TaskRoute, load_routing_config
from proxy.model import Message, TaskView

#: gateway/chat/resolver.py -> gateway/chat -> gateway -> repository root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_ROUTES_PATH = Path(__file__).resolve().parent / "config" / "chat-routes.yaml"
DEFAULT_ROUTING_PATH = REPO_ROOT / "gateway" / "proxy" / "config" / "routing.yaml"

#: The two published chat task types (``registry/chat/manifest.yaml``).
TASK_TYPE_ANSWER = "chat-answer"
TASK_TYPE_REFUSE = "chat-refuse"

#: The order the module's bodies are laid into the invocation.
BODY_ORDER = ("system", "user", "assistant", "examples")


class ChatRouteError(proxy_contract.RoutingConfigError):
    """The chat route declarations could not be composed with the proxy policy."""


def task_type_for(*, admitted_fragments: int) -> str:
    """The module a turn runs under: answer when grounded, refuse when not.

    A turn with no admitted fragment is *not* dropped and *not* answered from
    nothing: it is dispatched under the ``chat-refuse`` module, whose own body
    forbids answering.  The choice is a pure function of how much grounding was
    admitted, so it cannot drift per caller.
    """
    return TASK_TYPE_ANSWER if admitted_fragments > 0 else TASK_TYPE_REFUSE


def load_chat_routes(path: Optional[Path] = None) -> dict[str, TaskRoute]:
    """Load this surface's declared routes (fail closed on anything malformed)."""
    routes_path = Path(path) if path is not None else DEFAULT_ROUTES_PATH
    if not routes_path.is_file():
        raise ChatRouteError(f"chat route declarations are missing: {routes_path}")
    with open(routes_path, "r", encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    if not isinstance(document, Mapping):
        raise ChatRouteError(f"chat route declarations {routes_path} are not a mapping")
    declared = document.get("routes")
    if not isinstance(declared, Mapping) or not declared:
        raise ChatRouteError(
            f"chat route declarations {routes_path} declare no routes"
        )
    routes: dict[str, TaskRoute] = {}
    for task_type, spec in declared.items():
        if not isinstance(spec, Mapping) or not spec.get("capability") or not spec.get("taskClass"):
            raise ChatRouteError(
                f"chat route {task_type!r} must declare capability and taskClass"
            )
        routes[str(task_type)] = TaskRoute(
            capability=str(spec["capability"]), task_class=str(spec["taskClass"])
        )
    return routes


def chat_routing_config(
    *,
    routing_path: Optional[Path] = None,
    routes_path: Optional[Path] = None,
) -> RoutingConfig:
    """The proxy's routing policy, extended with this surface's task types.

    The proxy's document is loaded through the proxy's **own** loader (so its
    validation, tier map, provider chains and routing groups are the ones that
    run), and the surface's two routes are added.  A collision is a refusal,
    never an override: if the proxy's policy ever declares a chat task type, the
    surface stops rather than shadowing it.
    """
    base = load_routing_config(Path(routing_path) if routing_path else DEFAULT_ROUTING_PATH)
    declared = load_chat_routes(routes_path)
    collisions = sorted(set(declared) & set(base.routes))
    if collisions:
        raise ChatRouteError(
            "the proxy routing policy already declares "
            + ", ".join(collisions)
            + "; the chat surface composes routes and must not override them"
        )
    routes = dict(base.routes)
    routes.update(declared)
    return RoutingConfig(
        schema_version=base.schema_version,
        routes=routes,
        tier_map=dict(base.tier_map),
        provider_chains=dict(base.provider_chains),
        routing_groups=dict(base.routing_groups),
    )


@dataclass(frozen=True)
class ResolvedChatTask:
    """What the surface needs to know about the module a turn runs under."""

    task_type: str
    prompt_id: str
    version: str
    model_tier_hint: str
    grounding_policy: str
    output_schema: Mapping[str, Any]
    messages: tuple[Message, ...]

    @property
    def citation_floor(self) -> int:
        """The minimum citations the module's own schema demands."""
        from registry.chat.envelope import citation_floor

        return int(citation_floor(dict(self.output_schema)))


class ChatTaskResolver:
    """The proxy's ``TaskResolver`` seam over ``registry/chat``.

    ``resolve`` is the protocol the proxy calls (``task_type`` + the request's
    variables); :meth:`resolved` is the richer view this surface's own steps
    need (grounding policy, citation floor, prompt id).  Both read the same
    registry and the same published module, so the two can never disagree.
    """

    def __init__(self, registry: Any = None, *, modules_root: Optional[Path] = None) -> None:
        if registry is None:
            from registry.chat.prompt_modules import ChatPromptRegistry

            registry = ChatPromptRegistry(
                Path(modules_root) if modules_root is not None else None
            )
        self.registry = registry
        self._cache: dict[str, ResolvedChatTask] = {}

    # -- protocol ---------------------------------------------------------- #
    def resolve(self, task_type: str, variables: Mapping[str, Any] | None = None) -> TaskView:
        """Render the published module for ``task_type`` (fail closed)."""
        resolved = self.resolved(task_type)
        try:
            rendered = self.registry.render_prompt(task_type, dict(variables or {}))
        except Exception as exc:  # noqa: BLE001 - any render failure is a refusal
            raise proxy_contract.TaskResolutionError(
                f"chat prompt module {task_type!r} could not be rendered: {exc}"
            ) from exc
        bodies = rendered.get("bodies") or {}
        messages = tuple(
            Message(role=role, content=str(bodies[role]))
            for role in BODY_ORDER
            if bodies.get(role)
        )
        return TaskView(
            task_type=task_type,
            version=resolved.version,
            prompt_id=resolved.prompt_id,
            model_tier_hint=resolved.model_tier_hint,
            messages=messages,
            output_schema=resolved.output_schema,
            parameters={},
        )

    # -- richer view ------------------------------------------------------- #
    def resolved(self, task_type: str) -> ResolvedChatTask:
        """The published module behind a task type (cached; fail closed)."""
        cached = self._cache.get(task_type)
        if cached is not None:
            return cached
        try:
            module = self.registry.resolve(task_type)
        except Exception as exc:  # noqa: BLE001 - unknown/unpublished is a refusal
            raise proxy_contract.TaskResolutionError(
                f"chat task type {task_type!r} is not a published registry/chat "
                f"module: {exc}"
            ) from exc
        try:
            schema = json.loads(Path(module.output_schema_path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise proxy_contract.TaskResolutionError(
                f"chat module {task_type!r} output schema is unreadable: {exc}"
            ) from exc
        bodies = tuple(
            Message(role=role, content=module.body_text(role))
            for role in BODY_ORDER
            if role in module.bodies
        )
        # The module declares its hint in the registry's own lower-case
        # vocabulary (`med`); the platform's tier vocabulary is upper-case
        # (`MED`, from gateway/providers/contract.py).  This maps, it does not
        # re-declare: an unknown hint is left exactly as declared.
        hint = module.model_tier_hint
        resolved = ResolvedChatTask(
            task_type=task_type,
            prompt_id=module.prompt_id,
            version=module.version,
            model_tier_hint=hint.upper() if hint else hint,
            grounding_policy=module.grounding_policy,
            output_schema=schema,
            messages=bodies,
        )
        self._cache[task_type] = resolved
        return resolved
