"""Engine core error taxonomy.

Every failure mode of the durable orchestration engine core maps to one
typed error here so callers can distinguish a missing namespace from a
corrupt event log from an illegal state transition without string matching.
The root is :class:`EngineError`; handlers raise :class:`StepFailure` for a
step/compensation that should be treated as failed (its message is recorded
in the event log), not as a programming bug.
"""

from __future__ import annotations


class EngineError(Exception):
    """Base class for every engine-core failure."""


class NamespaceError(EngineError):
    """A namespace-level failure (unknown, exists, or cross-namespace)."""


class UnknownNamespaceError(NamespaceError):
    """The requested namespace does not exist in this registry.

    Raised on any operation bound to a namespace that has not been
    provisioned.  A lookup for tenant A inside namespace B never falls back
    to tenant A's data (the no-cross-tenant doctrine).
    """


class NamespaceExistsError(NamespaceError):
    """Provisioning a namespace id that is already present."""


class CrossNamespaceError(NamespaceError):
    """An operation attempted to reach across a namespace boundary.

    Raised when a workflow of one namespace is addressed from another or a
    namespace-agnostic lookup would leak tenant data.  There is no
    cross-namespace fallback anywhere in the engine.
    """


class WorkflowError(EngineError):
    """A workflow-level failure."""


class UnknownWorkflowError(WorkflowError):
    """No event history exists for (namespace_id, workflow_id).

    Because every lookup is scoped to exactly one namespace, a workflow that
    lives in tenant A is ``UnknownWorkflowError`` from tenant B.
    """


class WorkflowValidationError(WorkflowError):
    """A workflow spec failed validation (unknown kind, bad handler, ...)."""


class InvalidTransitionError(WorkflowError):
    """An event would move a workflow through an illegal state transition.

    Enforced both while running and while replaying a stored log, so a
    corrupt or hand-edited history is refused rather than silently accepted.
    """


class UnknownStepHandlerError(EngineError):
    """A step/compensation names a handler that is not registered.

    The engine fails closed: an unregistered handler is never silently
    treated as a no-op.
    """


class StepFailure(EngineError):
    """Raised by a step/compensation handler to signal a domain failure.

    The message is captured into the event log (STEP_FAILED / COMPENSATION_FAILED).
    """


class CompensationError(EngineError):
    """A compensation handler failed (reported into the event log)."""


class PersistenceError(EngineError):
    """The event store refused a record (unparseable/corrupt JSONL, ...)."""
