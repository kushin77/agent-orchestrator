"""Agent Identity + Registry service (issue #10, work item 06, phase 1).

Self-contained package under ``registry/service/``. Importable as ``service``
when ``registry/`` is on ``sys.path`` (the tests arrange this in
``tests/conftest.py``). The companion audit log lives in ``registry/events``
(the sibling ``events`` package).

Public surface
--------------

- ``AgentRegistry`` - the facade: tenant-scoped register / activate / pause /
  retire / touch, task-route + capability resolution, and scoped-claims session
  issuance. Every mutating call appends to ``registry.events``.
- ``Agent`` / ``AgentSession`` / ``TaskRoute`` / ``RouteResolution`` - the data
  types.
- ``ClosedCatalog`` / ``ResolvedProfile`` + ``load_catalog`` / ``resolve_profile``
  - the #9 closed-vocabulary / profile-seed access (consume, never redefine).
- ``errors`` - every failure type (cross-tenant denial, unknown task type,
  invalid transition, ...) so a gate can distinguish denials from errors.
"""

from service.catalog import (
    ClosedCatalog,
    ResolvedProfile,
    load_catalog,
    require_capability,
    require_tool,
    resolve_profile,
)

from service.errors import (
    AgentAlreadyRegisteredError,
    AgentNotActiveError,
    AgentRegistrationError,
    CrossTenantDenied,
    IdentityError,
    InvalidAgentIdError,
    InvalidCredentialError,
    InvalidTaskTypeError,
    InvalidTenantIdError,
    InvalidTransitionError,
    NoRoutableAgentError,
    ProfileResolutionError,
    RegistryServiceError,
    RouteAgentNotInTenantError,
    RoutingError,
    SessionExpiredError,
    ToolNotAllowedError,
    UnknownAgentError,
    UnknownCapabilityError,
    UnknownProfileError,
    UnknownTaskTypeError,
    UnknownToolError,
    VocabularyUnavailableError,
)

from service.identity import (
    DEFAULT_AUDIENCE,
    DEFAULT_ROLE,
    AgentSession,
    IdentityService,
)

from service.model import (
    STATUS_ACTIVE,
    STATUS_PAUSED,
    STATUS_REGISTERED,
    STATUS_RETIRED,
    STATUSES,
    TERMINAL_STATUSES,
    TRANSITIONS,
    Agent,
    agent_records,
    now_utc,
    require_transition,
    validate_agent_id,
    validate_tenant_id,
)

from service.registry import AgentRegistry

from service.routing import RouteResolution, TaskRoute, TaskRouter

from service.store import RegistryStore

__all__ = [
    "Agent",
    "AgentRegistry",
    "AgentRegistrationError",
    "AgentSession",
    "AgentAlreadyRegisteredError",
    "AgentNotActiveError",
    "ClosedCatalog",
    "CrossTenantDenied",
    "DEFAULT_AUDIENCE",
    "DEFAULT_ROLE",
    "IdentityError",
    "IdentityService",
    "InvalidAgentIdError",
    "InvalidCredentialError",
    "InvalidTaskTypeError",
    "InvalidTenantIdError",
    "InvalidTransitionError",
    "NoRoutableAgentError",
    "ProfileResolutionError",
    "ResolvedProfile",
    "RegistryServiceError",
    "RegistryStore",
    "RouteAgentNotInTenantError",
    "RouteResolution",
    "RoutingError",
    "STATUS_ACTIVE",
    "STATUS_PAUSED",
    "STATUS_REGISTERED",
    "STATUS_RETIRED",
    "STATUSES",
    "TERMINAL_STATUSES",
    "TRANSITIONS",
    "SessionExpiredError",
    "TaskRoute",
    "TaskRouter",
    "ToolNotAllowedError",
    "UnknownAgentError",
    "UnknownCapabilityError",
    "UnknownProfileError",
    "UnknownTaskTypeError",
    "UnknownToolError",
    "VocabularyUnavailableError",
    "agent_records",
    "load_catalog",
    "now_utc",
    "require_capability",
    "require_tool",
    "require_transition",
    "resolve_profile",
    "validate_agent_id",
    "validate_tenant_id",
]
