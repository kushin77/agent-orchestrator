"""gateway/proxy — model gateway proxy (issue #16).

The central route -> dispatch -> log funnel of the Model Gateways pillar: a
task for an agent is resolved (profile/persona -> promptModule), routed to a
provider/model tier with a fallback chain driven by injected health, guarded
by the cost/capacity facade, dispatched through an injected model backend,
validated as TYPED output against the prompt module's output schema (retry
once, then CANNOT-ASSESS), and logged as one full gateway call record to the
audit + metering sinks.

Importable as ``proxy`` when ``gateway/`` is on ``sys.path`` (the tests
arrange this in ``tests/conftest.py``; consumers of the merged contract do the
same) and as ``gateway.proxy`` once a later gateway-phase lane adds a
``gateway/__init__.py`` — mirroring the ``providers``/``limits`` package
conventions.

Consumed contracts (never redefined): issue #9 AgentProfile + catalog
(``registry/profiles``), issue #11 personas (``registry/personas``), issue #13
prompt modules (``registry/prompts``), issue #15 providers
(``gateway/providers``), issue #17 FinOps chooser (``gateway/finops``), issue
#19 cost/capacity facade (``gateway/limits``), issue #18 health (injected).

Public surface
--------------

- ``gateway.ModelGateway``  - the dispatch core: ``dispatch(agent_id, request)``
  and ``dispatch_stream(...)``.
- ``handler.GatewayHandler`` - the thin ``POST /v1/agents/{agentId}/tasks``
  (+ streaming) handler the phase-7 REST surface mounts.
- ``router.Router``/``load_routing_config`` - the routing policy
  (``config/routing.yaml``).
- ``model``/``contract``  - value objects, closed outcome vocabulary, errors.
- ``wiring.build_real_gateway`` - compose the real merged sibling modules into
  a fully offline ``ModelGateway`` (demos + integration tests).
- ``cli``  - offline demo/evidence CLI.

See ``gateway/proxy/README.md`` for the full contract.
"""

from __future__ import annotations

from proxy import contract, model, schema, sinks  # noqa: F401  (re-exported)
from proxy.contract import (  # noqa: F401
    OUTCOME_BLOCKED,
    OUTCOME_CACHE_HIT,
    OUTCOME_CANNOT_ASSESS,
    OUTCOME_DENIED,
    OUTCOME_FAILED,
    OUTCOME_NO_HEALTHY_ROUTE,
    OUTCOME_RATE_LIMITED,
    OUTCOME_REFUSED,
    OUTCOME_SUCCESS,
    OUTCOMES,
    AgentResolutionError,
    BudgetBlockedError,
    CapabilityDeniedError,
    NoHealthyRouteError,
    ProxyError,
    RoutingConfigError,
    TaskResolutionError,
    UnknownTaskRouteError,
)
from proxy.gateway import ModelGateway  # noqa: F401
from proxy.handler import (  # noqa: F401
    OUTCOME_STATUS,
    GatewayHandler,
    outcome_status,
    parse_task_request,
)
from proxy.model import (  # noqa: F401
    STAGE_AGENT_RESOLVED,
    STAGE_ATTEMPT,
    STAGE_COMPLETED,
    STAGE_GUARD,
    STAGE_RECEIVED,
    STAGE_ROUTE_SELECTED,
    STAGE_TASK_RESOLVED,
    AgentView,
    ChatInvocation,
    DispatchEvent,
    GatewayCallRecord,
    Message,
    RouteCandidate,
    RouteDecision,
    TaskRequest,
    TaskResult,
    TaskView,
    TierChoice,
)
from proxy.router import Router, load_routing_config  # noqa: F401
from proxy.sinks import (  # noqa: F401
    CallRecordSink,
    JsonlCallRecordSink,
    ListCallRecordSink,
    NoopCallRecordSink,
)
from proxy.wiring import build_real_gateway  # noqa: F401

__all__ = [
    "AgentResolutionError",
    "AgentView",
    "BudgetBlockedError",
    "CallRecordSink",
    "CapabilityDeniedError",
    "ChatInvocation",
    "DispatchEvent",
    "GatewayCallRecord",
    "GatewayHandler",
    "JsonlCallRecordSink",
    "ListCallRecordSink",
    "Message",
    "ModelGateway",
    "NoHealthyRouteError",
    "NoopCallRecordSink",
    "OUTCOME_BLOCKED",
    "OUTCOME_CACHE_HIT",
    "OUTCOME_CANNOT_ASSESS",
    "OUTCOME_DENIED",
    "OUTCOME_FAILED",
    "OUTCOME_NO_HEALTHY_ROUTE",
    "OUTCOME_RATE_LIMITED",
    "OUTCOME_REFUSED",
    "OUTCOME_STATUS",
    "OUTCOME_SUCCESS",
    "OUTCOMES",
    "ProxyError",
    "RouteCandidate",
    "RouteDecision",
    "Router",
    "RoutingConfigError",
    "TaskRequest",
    "TaskResolutionError",
    "TaskResult",
    "TaskView",
    "TierChoice",
    "UnknownTaskRouteError",
    "build_real_gateway",
    "load_routing_config",
    "outcome_status",
    "parse_task_request",
]
