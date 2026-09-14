"""engine.core.tickets — tenant-facing agentic task manager (issue #634).

Workbook-3 of the Paperclip enterprise-workbook delta (parent #631): the
tenant-facing ticket lifecycle the durable engine core hosts.  It joins two
already-merged primitives and adds nothing that duplicates them:

* :mod:`engine.multiagent.planner` — the injected decomposer (hierarchical
  planner -> bounded fan-out, ``max_escalation_rounds`` so missions always
  terminate);
* :mod:`core` — the durable, tenant-namespaced, event-sourced engine.

Lifecycle: ``created -> decomposed -> dispatched -> executed -> reviewed ->
closed``, where *created* and *closed* are the engine's own
``ENGINEER_TASK_CREATED`` / ``ENGINEER_TASK_CLOSED`` events and the middle
four are the lifecycle events this package appends.  Every event carries the
tenant scope and the ticket state is derived from the log — never stored
beside it — so a restarted process replays the same ticket.

Import as ``core.tickets`` with ``engine/`` on ``sys.path`` (mirrors the
sibling ``core`` package).
"""

from __future__ import annotations

from .handlers import (
    DEFAULT_DECOMPOSE_REF,
    HANDLER_CLOSE,
    HANDLER_DECOMPOSE,
    HANDLER_DISPATCH,
    HANDLER_EXECUTE,
    HANDLER_LIFECYCLE_EVENT,
    HANDLER_REVIEW,
    decomposition_from_report,
    register_decomposer,
    register_ticket_handlers,
    resolve_decomposer,
)
from .model import (
    DecompositionOutcome,
    TicketLifecycleError,
    TicketProjection,
    TicketReview,
    TicketState,
    legal_moves,
    lifecycle_order,
    next_ticket_state,
)
from .runtime import (
    ENGINE_EVENT_TO_TICKET,
    TICKET_EVENT_TO_ENGINE,
    TICKET_LIFECYCLE_EVENTS,
    TicketRuntime,
)
from .workflow import TICKET_WORKFLOW_NAME, ticket_workflow

__all__ = [
    # model
    "TicketState",
    "TicketReview",
    "TicketProjection",
    "DecompositionOutcome",
    "TicketLifecycleError",
    "lifecycle_order",
    "legal_moves",
    "next_ticket_state",
    # handlers
    "DEFAULT_DECOMPOSE_REF",
    "HANDLER_CLOSE",
    "HANDLER_DECOMPOSE",
    "HANDLER_DISPATCH",
    "HANDLER_EXECUTE",
    "HANDLER_LIFECYCLE_EVENT",
    "HANDLER_REVIEW",
    "decomposition_from_report",
    "register_decomposer",
    "register_ticket_handlers",
    "resolve_decomposer",
    # runtime
    "TicketRuntime",
    "ENGINE_EVENT_TO_TICKET",
    "TICKET_EVENT_TO_ENGINE",
    "TICKET_LIFECYCLE_EVENTS",
    # workflow
    "ticket_workflow",
    "TICKET_WORKFLOW_NAME",
]
