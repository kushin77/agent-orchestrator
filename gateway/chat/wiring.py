"""gateway.chat.wiring — the composition root (issue #503).

The surface is seam-injected and offline-testable; this module wires the **real**
merged siblings into it, and it is the only place that decides which
implementation each seam gets:

============  ===================================================================
seam          real implementation
============  ===================================================================
gateway       ``proxy.wiring.build_real_gateway``'s handles (the real personas
              resolver, the real FinOps chooser adapter, the real provider
              registry over the offline transport rig, the real limits facade)
              re-mounted on a :class:`ModelGateway` whose two chat-specific
              seams are replaced: the **task resolver** (``registry/chat``'s
              published modules) and the **router** (the proxy policy merged
              with the surface's two declared routes)
credentials   ``identity/chat``: RS256 verify + revocation, both required
budget        ``telemetry/chat``: ``TurnBudgetGuard.from_config()`` over the
              shipped ``telemetry/budgets`` rails (kill switch OFF by default)
attribution   ``telemetry/chat``: ``TurnAttributor`` over the shipped rate
              cards + the caller's ledger/usage store
guardrails    ``guardrails/chat``: the composed ``ChatTurnGuard``
conversation  ``identity/chat`` isolation onto ``engine/memory``'s SESSION scope
models        the module catalog + the proxy routing policy (derived)
============  ===================================================================

Everything stays **offline and deterministic**: provider traffic goes through
the proxy's scriptable transport rig, the router/prompt modules are read from
the committed seeds, and no socket is opened.  Secrets are never defaulted —
the signing key and the ledger's keystore are the caller's (env / GCP Secret
Manager in a deployment); a missing one leaves the corresponding step
*refusing*, never silently permissive.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

from .conversation import ConversationStore
from .models import ModelCatalogue
from .resolver import ChatTaskResolver, chat_routing_config
from .surface import ChatSurface

#: gateway/chat/wiring.py -> gateway/chat -> gateway -> repository root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def build_gateway(
    *,
    health: Any = None,
    limits_engine: Any = None,
    audit_sink: Any = None,
    metering_sink: Any = None,
    routing_path: Optional[Path] = None,
    routes_path: Optional[Path] = None,
    task_resolver: Optional[ChatTaskResolver] = None,
) -> tuple[Any, Any]:
    """The proxy's dispatch core with the surface's two seams installed.

    Returns ``(gateway, wired)`` — the ``ModelGateway`` and the proxy's own
    :class:`~proxy.wiring.WiredProxy` handles (personas, chooser, backend, rig,
    limits), so a caller can reach the real provider rig for a scripted
    response without this module re-exporting the whole proxy surface.
    """
    from proxy.gateway import ModelGateway
    from proxy.router import Router
    from proxy.sinks import ListCallRecordSink
    from proxy.wiring import build_real_gateway

    # NB: the sinks are chosen with explicit ``None`` checks, never ``or`` — an
    # empty ``ListCallRecordSink`` is falsy (it defines ``__len__``), so a
    # truthiness test would silently swap the caller's sink for a fresh one and
    # the evidence would land somewhere nobody reads.  The same care is needed
    # on the way in: the proxy's own ``build_real_gateway`` uses ``or`` for its
    # sinks, so the caller's handles are mounted on the ``ModelGateway``
    # explicitly below instead of being read back off ``wired``.
    audit = audit_sink if audit_sink is not None else ListCallRecordSink()
    metering = metering_sink if metering_sink is not None else ListCallRecordSink()
    wired = build_real_gateway(
        health=health,
        limits_engine=limits_engine,
        audit_sink=audit,
        metering_sink=metering,
    )
    gateway = ModelGateway(
        agent_resolver=wired.agent_resolver,
        task_resolver=task_resolver if task_resolver is not None else ChatTaskResolver(),
        chooser=wired.chooser_adapter,
        backend=wired.backend,
        limits=wired.limits_engine,
        router=Router(
            chat_routing_config(routing_path=routing_path, routes_path=routes_path)
        ),
        health=health,
        audit_sink=audit,
        metering_sink=metering,
    )
    return gateway, wired


def build_surface(
    *,
    registry_path: Optional[Path] = None,
    health: Any = None,
    limits_engine: Any = None,
    audit_sink: Any = None,
    metering_sink: Any = None,
    routing_path: Optional[Path] = None,
    routes_path: Optional[Path] = None,
    catalogue: Optional[ModelCatalogue] = None,
    catalogue_paths: Optional[Mapping[str, Path]] = None,
    signing_key: Optional[bytes] = None,
    revocation_store: Any = None,
    credential_verifier: Any = None,
    memory_store: Any = None,
    conversation: Optional[ConversationStore] = None,
    budget_guard: Any = None,
    attributor: Any = None,
    ledger: Any = None,
    usage_store: Any = None,
    rate_store: Any = None,
    task_resolver: Optional[ChatTaskResolver] = None,
    turn_guard: Any = None,
    clock: Any = None,
) -> ChatSurface:
    """The whole surface, wired over the real merged siblings.

    ``budget_guard`` defaults to the shipped rails (``TurnBudgetGuard.from_config``)
    because a chat turn must meet the kill switch before it meets a model.
    ``attributor`` defaults to a real :class:`TurnAttributor` when a ``ledger``
    is supplied; without one the attribution step is *reported absent* in the
    ``ao`` block rather than faked.
    """
    gateway, _wired = build_gateway(
        health=health,
        limits_engine=limits_engine,
        audit_sink=audit_sink,
        metering_sink=metering_sink,
        routing_path=routing_path,
        routes_path=routes_path,
        task_resolver=task_resolver,
    )
    if budget_guard is None:
        from telemetry.chat.budget_guard import TurnBudgetGuard

        budget_guard = TurnBudgetGuard.from_config()
    if attributor is None and ledger is not None:
        from telemetry.chat.attribution import TurnAttributor

        attributor = TurnAttributor(
            ledger,
            rate_store=rate_store,
            usage_store=usage_store,
        )
    if conversation is None:
        conversation = ConversationStore(memory_store=memory_store)
    return ChatSurface(
        gateway,
        registry_path=registry_path,
        catalogue=catalogue,
        catalogue_paths=catalogue_paths,
        credential_verifier=credential_verifier,
        signing_key=signing_key,
        revocation_store=revocation_store,
        conversation=conversation,
        task_resolver=task_resolver,
        turn_guard=turn_guard,
        budget_guard=budget_guard,
        attributor=attributor,
        clock=clock,
    )


def build_offline_surface(**kwargs: Any) -> ChatSurface:
    """``build_surface`` under its honest name: nothing here opens a socket."""
    return build_surface(**kwargs)
