"""identity/cpapi — Control-plane REST API + event/outbox bus (issue #38).

The phase-6 control-plane management surface (issue #38, work item 34;
parent EPIC-00 issue #4). Owner lane: **identity** — this subtree is
``identity/cpapi/**`` only. Doctrine: ``AGENTS.md``,
``docs/EXECUTION-PLAN.md``, ``docs/ARCHITECTURE.md``, ``docs/GOLDEN-RULES.md``.

What this tree ships
--------------------

- a **transport-free REST router** (``router.py``) mapping a declarative table
  of ``(method, path)`` → handler over every pillar entity — tenant, agent,
  profile, persona, promptModule, policy, budget and the pause rail — with
  envelope-standardized responses;
- **authN + authZ** (``access.py``): session verification is injected
  (real adapter = merged ``identity/sso`` / ``registry/service`` session
  verifiers) and every route's ``resource:action`` permission is enforced by
  the injected two-gate guard (real adapter = ``identity/rbac`` ``guard``);
- an **append-only event/outbox bus** (``outbox.py``) with idempotent publish
  and a poll/ack/redeliver consumer contract — the outbox pattern that avoids
  dual-write (issue #38 AC2);
- **audit append on every mutation** (``audit.py``; production maps to the
  merged ``telemetry/ledger``) and **approval-gated destructive operations**
  (``approvals.py``, govctl-style — issue #38 AC3);
- typed request/response models (``model.py``), an **OpenAPI 3.0 spec**
  (``openapi.yaml``) and a thin typed client (``clients/``) — issue #38 AC4.

Everything is offline and testable without a live server: handlers are
invoked through ``ControlPlane.handle(method, path, ...)`` and dependency
seams (``ports.py``) are fakes in tests (``fakes.py``) or thin adapters over
the real merged modules (``wiring.py``).

Public surface (import as ``cpapi`` when ``identity/`` is on ``sys.path``)
----------------------------------------------------------------------------
"""

from . import errors, model, outbox  # noqa: F401
from .access import AuthenticatedPrincipal, authorize_request, verify_token  # noqa: F401
from .approvals import ApprovalGate, ApprovalStore  # noqa: F401
from .audit import AuditStore  # noqa: F401
from .control import ControlPlane  # noqa: F401
from .errors import ApiError  # noqa: F401
from .fakes import build_test_app  # noqa: F401
from .model import (  # noqa: F401
    AgentView,
    ApprovalView,
    AuditRecordView,
    EventView,
    PersonaView,
    PolicyView,
    ProfileView,
    PromptModuleView,
    QuotaView,
    TaskView,
    TenantView,
    UsageView,
)
from .outbox import Outbox  # noqa: F401
from .router import Route, Router  # noqa: F401
from .wiring import RbacAuthorizer, RegistryAgentOps  # noqa: F401

__all__ = [
    "ApiError",
    "ApprovalGate",
    "ApprovalStore",
    "ApprovalView",
    "AgentView",
    "AuditRecordView",
    "AuditStore",
    "AuthenticatedPrincipal",
    "ControlPlane",
    "EventView",
    "Outbox",
    "PersonaView",
    "PolicyView",
    "ProfileView",
    "PromptModuleView",
    "QuotaView",
    "RbacAuthorizer",
    "RegistryAgentOps",
    "Route",
    "Router",
    "TaskView",
    "TenantView",
    "UsageView",
    "authorize_request",
    "build_test_app",
    "verify_token",
]
