"""Ticket lifecycle as a durable engine workflow spec (issue #634).

Builds the ``ENGINEER_TASK`` workflow the engine already declares as a
:class:`core.model.WorkflowKind`: six steps, one per lifecycle state, each
appending its own event so the ticket state is *derived from the log* and a
resumed run reconstructs it with no hidden in-memory state::

    anchor      ENGINEER_TASK_CREATED  -> created       (workflow started)
    decompose   plan_decomposed        -> decomposed
    dispatch    ticket_dispatched      -> dispatched
    execute     ticket_executed        -> executed
    review      ticket_reviewed        -> reviewed
    close       ENGINEER_TASK_CLOSED   -> closed        (workflow completed)

The anchors are the two ``EventKind`` members the engine ships for exactly
this surface.  They are emitted by the :class:`TicketRuntime` (the engine
appends ``workflow_started`` / ``workflow_completed`` itself), so the spec
stays a plain ordered list of steps and no ``EventKind`` member is added.
"""

from __future__ import annotations

import itertools
from typing import Any, Mapping, Optional, Sequence

from core.model import Step, StepKind, WorkflowKind, WorkflowSpec

from .handlers import (
    DEFAULT_DECOMPOSE_REF,
    DEFAULT_STEP_IDS,
    HANDLER_CLOSE,
    HANDLER_DECOMPOSE,
    HANDLER_DISPATCH,
    HANDLER_EXECUTE,
    HANDLER_REVIEW,
    register_decomposer,
)

#: name of the ticket lifecycle workflow.
TICKET_WORKFLOW_NAME = "tenant-ticket-lifecycle"

#: monotonic counter making every spec's ``plan_ref`` unique within a process,
#: so two tickets with the same tenant/id never share a registered plan.
_SPEC_NONCE = itertools.count()


def _next_spec_nonce() -> int:
    return next(_SPEC_NONCE)


def ticket_workflow(
    *,
    tenant: str,
    ticket_id: str,
    mission_id: str,
    objective: str = "",
    decomposer: Any = None,
    decompose_ref: str = DEFAULT_DECOMPOSE_REF,
    dispatch_lane: str = "default",
    dispatch_queue: str = "",
    review: Optional[Mapping[str, Any]] = None,
    review_gate: str = "tenant-review",
    step_ids: Optional[Mapping[str, str]] = None,
    mission_context: Optional[Mapping[str, Any]] = None,
    sla_seconds: Optional[float] = None,
    extra_steps: Sequence[Step] = (),
) -> WorkflowSpec:
    """Build the durable ticket-lifecycle spec for one tenant ticket.

    ``decomposer`` is the injected decomposition callable (the value the
    :class:`engine.multiagent.planner.DecomposeCallable` seam expects).  It is
    **not** stored in the spec — a spec embeds in the ``workflow_started``
    event and must stay JSON-safe — so it is registered with the runtime
    alongside a ``decompose_ref`` name that the handler resolves.
    ``review`` is the injected review verdict; absent means "un-reviewed",
    which the review step records explicitly.
    """
    if not tenant:
        raise ValueError("tenant must be non-empty")
    if not ticket_id:
        raise ValueError("ticket_id must be non-empty")
    if not mission_id:
        raise ValueError("mission_id must be non-empty")
    if decomposer is not None and not callable(decomposer):
        raise ValueError("decomposer must be callable when provided")
    # The round-level plan function is what the planner calls each round.  It is
    # registered under a ref derived from the ticketing identity *plus a
    # per-spec nonce*, and never persisted: a spec is embedded in an event and
    # must stay JSON-safe, and a shared ref would let one ticket's plan leak
    # into another ticket with the same id (a real hazard in tests and re-runs,
    # where the same tenant/ticket pair is created more than once).
    plan_ref = f"{decompose_ref}:{tenant}:{ticket_id}:{_next_spec_nonce()}:plan"
    if decomposer is not None:
        register_decomposer(plan_ref, decomposer)
    ids = dict(DEFAULT_STEP_IDS)
    ids.update(step_ids or {})

    decompose = Step(
        step_id=ids["decompose"],
        kind=StepKind.AGENT_LOOP,
        name="decompose ticket into an ordered plan",
        handler=HANDLER_DECOMPOSE,
        args={
            "ticket_id": ticket_id,
            "mission_id": mission_id,
            "objective": objective,
            # The live decomposition seam is injected through the runtime,
            # not persisted in the spec: a workflow spec is embedded in an
            # event and must stay JSON-safe (``core.model.spec_to_dict``
            # rejects a live callable).  ``decomposer_ref`` names the planner
            # and ``plan_ref`` names the per-round plan function.
            "decomposer_ref": decompose_ref,
            "plan_ref": plan_ref,
            "mission_context": dict(mission_context or {}),
        },
    )
    dispatch = Step(
        step_id=ids["dispatch"],
        kind=StepKind.NOTIFY,
        name="dispatch the plan to the tenant lane",
        handler=HANDLER_DISPATCH,
        args={
            "ticket_id": ticket_id,
            "lane": dispatch_lane,
            "queue": dispatch_queue,
        },
    )
    execute = Step(
        step_id=ids["execute"],
        kind=StepKind.AGENT_LOOP,
        name="execute the decomposition",
        handler=HANDLER_EXECUTE,
        args={"ticket_id": ticket_id, "decompose_step_id": ids["decompose"]},
    )
    review_step = Step(
        step_id=ids["review"],
        kind=StepKind.NOTIFY,
        name="review gate",
        handler=HANDLER_REVIEW,
        args={
            "ticket_id": ticket_id,
            "gate": review_gate,
            "review": dict(review) if review is not None else None,
        },
    )
    # The close step carries the CLOSED anchor: it appends the
    # ``engineer_task_closed`` workflow event, which must precede the engine's
    # own terminal ``workflow_completed`` (the projection refuses any event
    # after a terminal one).  The ticket therefore reaches CLOSED only through
    # a run that actually reached this step.
    close = Step(
        step_id=ids["close"],
        kind=StepKind.NOOP,
        name="close the ticket",
        handler=HANDLER_CLOSE,
        args={"ticket_id": ticket_id},
    )
    steps = [decompose, dispatch, execute, review_step, *extra_steps, close]
    return WorkflowSpec(
        name=TICKET_WORKFLOW_NAME,
        kind=WorkflowKind.ENGINEER_TASK,
        steps=steps,
        saga=False,
        sla_seconds=sla_seconds,
        description=f"tenant ticket {ticket_id} for {tenant}: {objective}",
    )


__all__ = ["TICKET_WORKFLOW_NAME", "ticket_workflow"]
