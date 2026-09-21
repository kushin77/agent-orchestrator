"""Shared exception types for the Agent Identity + Registry service (issue #10).

---knowledge---
module_id: registry.service.errors
system: registry
app: service
solution_class: class
patterns: [exception-taxonomy, single-root]
derives_from: null
owner_sme: platform-sme
tier: L0
interfaces: [RegistryServiceError, UnknownCapabilityError, CrossTenantDenied, InvalidTransitionError, ToolNotAllowedError]
invariants: "every service error derives from RegistryServiceError, so one clause catches the whole surface"
gotchas: ""
related: ["#10"]
do_not_duplicate: null
---knowledge---

All registry-service errors derive from ``RegistryServiceError`` so a caller can
catch the whole service surface with one clause; each subtype names the exact
failure so a gate can distinguish a scope denial from a vocabulary error.
"""


class RegistryServiceError(Exception):
    """Base class for all registry service errors."""


# -- vocabulary / profile (fail-closed references to the #9 contract) ------ #
class VocabularyUnavailableError(RegistryServiceError):
    """The closed platform vocabulary (registry/profiles/catalog.yaml) is missing."""


class UnknownCapabilityError(RegistryServiceError):
    """A capability id is not in the closed platform capability catalog."""


class UnknownToolError(RegistryServiceError):
    """A tool id is not in the closed platform tool catalog."""


class ProfileResolutionError(RegistryServiceError):
    """A profile reference could not be resolved to exactly one immutable seed."""


class UnknownProfileError(ProfileResolutionError):
    """The referenced AgentProfile id/version does not exist in the seed library."""


# -- agent lifecycle ------------------------------------------------------- #
class AgentRegistrationError(RegistryServiceError):
    """Base for errors that refuse an agent registration."""


class InvalidAgentIdError(AgentRegistrationError):
    """The agent id does not match the canonical id pattern."""


class InvalidTenantIdError(AgentRegistrationError):
    """The tenant id does not match the canonical tenant pattern."""


class AgentAlreadyRegisteredError(AgentRegistrationError):
    """An agent with that id is already registered in the same tenant."""


class UnknownAgentError(RegistryServiceError):
    """No such agent in the requested tenant.

    The lookup is strictly tenant-scoped: an agent that exists in a different
    tenant is simply unknown here - there is deliberately no cross-tenant
    fallback.
    """

    def __init__(self, tenant_id: str, agent_id: str) -> None:
        self.tenant_id = tenant_id
        self.agent_id = agent_id
        super().__init__(
            f"unknown agent {agent_id!r} in tenant {tenant_id!r} "
            "(tenant-scoped lookup; no cross-tenant fallback)"
        )


class InvalidTransitionError(RegistryServiceError):
    """The requested lifecycle transition is not allowed from the current state."""


class AgentNotActiveError(RegistryServiceError):
    """The agent is not in the active state (paused or retired) and was refused."""


# -- task routing / capability resolution ---------------------------------- #
class RoutingError(RegistryServiceError):
    """Base for task-route errors."""


class InvalidTaskTypeError(RoutingError):
    """The task type does not match the canonical task-type pattern."""


class UnknownTaskTypeError(RoutingError):
    """No task route exists for this task type in the tenant.

    Resolution fails closed: an unknown task type is never routed anywhere, and
    the router never falls back to another tenant's or another task's agents.
    """

    def __init__(self, tenant_id: str, task_type: str) -> None:
        self.tenant_id = tenant_id
        self.task_type = task_type
        super().__init__(
            f"no task route for task type {task_type!r} in tenant {tenant_id!r} "
            "(fail closed; unknown task types are denied)"
        )


class RouteAgentNotInTenantError(RoutingError):
    """A route references an agent that is not registered in the same tenant."""


class NoRoutableAgentError(RoutingError):
    """No active candidate in the tenant can serve the route's capability set."""


# -- identity issuance / scoped claims ------------------------------------- #
class IdentityError(RegistryServiceError):
    """Base for identity/session errors."""


class CrossTenantDenied(IdentityError):
    """A session's tenant claim does not match the tenant it is being used in.

    This is the no-cross-tenant-fallback denial: a session minted for tenant A
    can never be used to act as tenant B.
    """

    def __init__(self, session_tenant: str, requested_tenant: str) -> None:
        self.session_tenant = session_tenant
        self.requested_tenant = requested_tenant
        super().__init__(
            f"session claims tenant {session_tenant!r} but use in tenant "
            f"{requested_tenant!r} was requested (no cross-tenant fallback)"
        )


class InvalidCredentialError(IdentityError):
    """A credential is malformed or its signature does not verify."""


class SessionExpiredError(InvalidCredentialError):
    """A credential's expiry time has passed."""


class ToolNotAllowedError(IdentityError):
    """The requested tool is not in the session's scoped allowed-tool set."""


# -- actor identity resolution (issue #1275) ------------------------------- #
class ActorResolutionError(IdentityError):
    """Base for actor-string resolution errors."""


class ActorUnresolvedError(ActorResolutionError):
    """An actor string does not match any declared identity record.

    Raised as ``actor-unresolved:<string>`` per the fail-closed doctrine: an
    actor seen in a PR, directive, approval or heartbeat that is not in
    ``registry/service/actors.yaml`` is refused, never guessed at.
    """

    def __init__(self, actor: str) -> None:
        self.actor = actor
        super().__init__(f"actor-unresolved:{actor}")


class DelegationUndeclaredError(ActorResolutionError):
    """An actor delegates to another actor, but the chain is not declared."""

    def __init__(self, actor: str, target: str) -> None:
        self.actor = actor
        self.target = target
        super().__init__(
            f"delegation-undeclared:{actor}->{target}"
        )
