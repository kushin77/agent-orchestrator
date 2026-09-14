"""Ticket runtime — durable lifecycle projection (issue #634).

The lifecycle has two halves, and this module is the join:

* the **anchor** half is owned by the already-merged durable engine — the
  ``workflow_started`` event emits ``ENGINEER_TASK_CREATED`` and the terminal
  ``workflow_completed`` event emits ``ENGINEER_TASK_CLOSED`` (both
  :class:`core.model.EventKind` members the core ships and previously left
  unused).  Anchoring on the engine's own events is what makes "durable
  through the file-backed event store" true by construction: there is no way
  to start or finish a ticket except through the engine's own append path.
* the **lifecycle** half is the four events this lane appends
  (``plan_decomposed``, ``ticket_dispatched``, ``ticket_executed``,
  ``ticket_reviewed``), whose payloads carry the replayable inputs.

:meth:`TicketRuntime.project` replays *any* event list — the live transcript
or a log re-read from :class:`core.events.FileJsonlEventStore` after a
restart — and refuses a log that skips a state, revisits one, or changes the
tenant mid-flight.  Replay is deterministic: the same log always projects to
the same :class:`TicketProjection`.
"""

from __future__ import annotations

from typing import Any, List, Mapping, Optional, Sequence

from core.events import EventRecord
from core.model import EventKind, StepStatus

from .handlers import HANDLER_LIFECYCLE_EVENT
from .model import (
    ENGINEER_TASK_CLOSED,
    ENGINEER_TASK_CREATED,
    PLAN_DECOMPOSED,
    TICKET_DISPATCHED,
    TICKET_EVENT_TRANSITIONS,
    TICKET_EXECUTED,
    TICKET_REVIEWED,
    DecompositionOutcome,
    TicketLifecycleError,
    TicketProjection,
    TicketReview,
    TicketState,
    next_ticket_state,
)

#: engine event kind -> ticket lifecycle event kind (the anchor mapping).
ENGINE_EVENT_TO_TICKET: Mapping[str, str] = {
    EventKind.WORKFLOW_STARTED.value: ENGINEER_TASK_CREATED,
    EventKind.WORKFLOW_COMPLETED.value: ENGINEER_TASK_CLOSED,
}

#: ticket lifecycle event kind -> engine event kind (the inverse mapping).
TICKET_EVENT_TO_ENGINE: Mapping[str, str] = {
    ticket: engine for engine, ticket in ENGINE_EVENT_TO_TICKET.items()
}

#: the four lane-owned lifecycle events, in lifecycle order.
TICKET_LIFECYCLE_EVENTS: Sequence[str] = (
    PLAN_DECOMPOSED,
    TICKET_DISPATCHED,
    TICKET_EXECUTED,
    TICKET_REVIEWED,
)


def _require_tenant(record: EventRecord, project: "_Builder") -> None:
    """Fail closed when a payload's tenant contradicts the event's namespace."""
    payload_tenant = record.payload.get("tenant")
    if payload_tenant is not None and str(payload_tenant) != record.namespace_id:
        raise TicketLifecycleError(
            f"ticket event {record.kind} carries tenant {payload_tenant!r} but "
            f"was appended to namespace {record.namespace_id!r} "
            "(cross-tenant ticket leak refused)"
        )
    if project.tenant and project.tenant != record.namespace_id:
        raise TicketLifecycleError(
            f"ticket log changes tenant mid-flight: {project.tenant!r} -> "
            f"{record.namespace_id!r} (corrupt or spliced log)"
        )


class _Builder:
    """Mutable accumulator used while replaying one ticket's event log."""

    def __init__(self, tenant: str, ticket_id: str) -> None:
        self.tenant = tenant
        self.ticket_id = ticket_id
        self.state = TicketState.CREATED
        self.title = ""
        self.correlation_id = ""
        self.decomposition: Optional[DecompositionOutcome] = None
        self.dispatch: Optional[dict] = None
        self.execution: Optional[dict] = None
        self.review: Optional[TicketReview] = None
        self.outcomes: List[dict] = []

    def apply(self, kind: str, payload: Mapping[str, Any], record: EventRecord) -> None:
        _require_tenant(record, self)
        # Engine events map onto their ticket anchor (workflow_started ->
        # engineer_task_created); step events carry no ticket-lifecycle meaning
        # and are ignored.  ENGINEER_TASK_CLOSED is handled explicitly below
        # because, unlike the created anchor, it must not be accepted blind.
        anchor = ENGINE_EVENT_TO_TICKET.get(kind)
        if anchor is not None and anchor != ENGINEER_TASK_CLOSED:
            self.state = next_ticket_state(
                self.state, anchor, ticket_id=self.ticket_id
            )
        elif kind == ENGINEER_TASK_CLOSED:
            # The close step appends this anchor *before* the engine's own
            # terminal event.  It is authoritative only for a run that reached
            # the close step, so a bare workflow (no ``tickets.*`` steps) is
            # refused rather than silently closed.
            if self.state is not TicketState.REVIEWED:
                raise TicketLifecycleError(
                    f"ticket {self.ticket_id!r} received engineer_task_closed "
                    f"while {self.state.value} (expected 'reviewed'); a ticket "
                    "can only close through its own close step"
                )
            self.state = next_ticket_state(
                self.state, kind, ticket_id=self.ticket_id
            )
        elif kind in TICKET_EVENT_TRANSITIONS:
            self.state = next_ticket_state(
                self.state, kind, ticket_id=self.ticket_id
            )
        else:
            return
        if kind == PLAN_DECOMPOSED:
            self.decomposition = DecompositionOutcome.from_dict(payload)
        elif kind == TICKET_DISPATCHED:
            self.dispatch = dict(payload)
        elif kind == TICKET_EXECUTED:
            self.execution = dict(payload)
            outcome = payload.get("outcome")
            if isinstance(outcome, Mapping):
                self.outcomes.append(dict(outcome))
        elif kind == TICKET_REVIEWED:
            if payload.get("gate_open") is False:
                self.review = TicketReview.from_dict(payload)

    def build(self) -> TicketProjection:
        return TicketProjection(
            tenant=self.tenant,
            ticket_id=self.ticket_id,
            state=self.state,
            title=self.title,
            correlation_id=self.correlation_id,
            decomposition=self.decomposition,
            dispatch=self.dispatch,
            execution=self.execution,
            review=self.review,
            outcomes=tuple(self.outcomes),
        )


class TicketRuntime:
    """Owns the ticket lifecycle over a durable ``core.runtime.Engine``.

    The runtime never keeps ticket state of its own: every read is a
    projection of the engine's transcript, so a restarted process (or a
    second process reading the same JSONL log) sees exactly the same ticket.
    """

    def __init__(self, engine: Any, *, tenant: str) -> None:
        if not tenant:
            raise ValueError("tenant must be non-empty")
        for attr in ("run_workflow", "start_workflow", "advance", "get_workflow", "store"):
            if not hasattr(engine, attr):
                raise TypeError(f"engine must expose {attr}(); got {type(engine).__name__}")
        self._engine = engine
        self._tenant = tenant

    @property
    def engine(self) -> Any:
        return self._engine

    @property
    def tenant(self) -> str:
        return self._tenant

    # -- run -----------------------------------------------------------------

    def start(
        self,
        ticket_id: str,
        spec: Any,
        *,
        title: str = "",
        correlation_id: str = "",
        inputs: Optional[Mapping[str, Any]] = None,
        workflow_id: Optional[str] = None,
    ) -> Any:
        """Start the ticket workflow; the tenant must already be provisioned."""
        self._engine.namespaces.require(self._tenant)
        payload = dict(inputs or {})
        payload.setdefault("tenant", self._tenant)
        payload.setdefault("ticket_id", ticket_id)
        if title:
            payload.setdefault("title", title)
        if correlation_id:
            payload.setdefault("correlation_id", correlation_id)
        execution = self._engine.start_workflow(
            self._tenant,
            spec,
            inputs=payload,
            workflow_id=workflow_id or ticket_id,
        )
        # The CREATED anchor needs no extra event: the engine's own
        # ``workflow_started`` already carries the tenant scope, and
        # ``ENGINE_EVENT_TO_TICKET`` maps it onto ``engineer_task_created``.
        return execution

    def run(
        self,
        ticket_id: str,
        spec: Any,
        *,
        title: str = "",
        correlation_id: str = "",
        inputs: Optional[Mapping[str, Any]] = None,
        workflow_id: Optional[str] = None,
    ) -> TicketProjection:
        """Start the ticket and drive it to a terminal state.

        The lifecycle events are appended by :meth:`_emit_lifecycle` as each
        step completes — never from inside a handler, because an event emitted
        mid-step leaves the step itself unfinished and the scheduler would
        re-run it forever.
        """
        execution = self.start(
            ticket_id,
            spec,
            title=title,
            correlation_id=correlation_id,
            inputs=inputs,
            workflow_id=workflow_id,
        )
        # Drive one handler at a time so each lifecycle event lands *after* its
        # step succeeded and before the next step runs.  ``max_handlers=1``
        # also makes the loop provably progressing: every iteration either
        # executes exactly one handler or the workflow reached a terminal
        # state, so a stalled ticket can never spin here.
        budget = self._max_iterations(spec)
        for _ in range(budget):
            if execution.status.terminal:
                break
            self._emit_lifecycle(execution, ticket_id)
            before = len(execution.events)
            report = self._engine.advance(execution, max_handlers=1)
            if execution.status.terminal:
                # The engine appended its terminal event last, so nothing more
                # may be appended.  The CLOSED anchor is therefore emitted
                # *before* the terminal event, from the close step's success —
                # see ``_emit_lifecycle`` (called above on the next pass) and
                # ``ENGINE_EVENT_TO_TICKET``, which maps the engine's own
                # ``workflow_completed`` onto the closed anchor.
                break
            self._emit_lifecycle(execution, ticket_id)
            if report.handlers_executed == 0 and len(execution.events) == before:
                raise TicketLifecycleError(
                    f"ticket {ticket_id!r} made no progress at "
                    f"{execution.status.value}; refusing to spin"
                )
        else:
            raise TicketLifecycleError(
                f"ticket {ticket_id!r} exceeded its step budget ({budget}); "
                "the lifecycle did not terminate"
            )
        return self.project(execution.events)

    @staticmethod
    def _max_iterations(spec: Any) -> int:
        """A hard cap on driver iterations: 4 x (steps + 1), always finite."""
        return 4 * (len(tuple(getattr(spec, "steps", ()) or ())) + 1)

    def resume(self, ticket_id: str) -> TicketProjection:
        """Replay a *persisted* ticket log into a projection (no engine state read)."""
        records = self._engine.store.events(self._tenant, ticket_id)
        return TicketRuntime.project(
            records, tenant=self._tenant, ticket_id=ticket_id
        )

    # -- projection ----------------------------------------------------------

    @staticmethod
    def project(
        records: Sequence[EventRecord],
        *,
        tenant: str = "",
        ticket_id: str = "",
    ) -> TicketProjection:
        """Replay one ticket's event log into a tenant-scoped projection."""
        builder = _Builder(
            tenant=tenant or (records[0].namespace_id if records else ""),
            ticket_id=ticket_id or (records[0].workflow_id if records else ""),
        )
        for record in records:
            kind = record.kind.value if isinstance(record.kind, EventKind) else str(record.kind)
            builder.apply(kind, record.payload, record)
        return builder.build()

    # -- internals -----------------------------------------------------------

    def _append_anchor(self, execution: Any, kind: str, payload: Mapping[str, Any]) -> None:
        engine = self._engine
        engine._append(execution, EventKind(kind), dict(payload))

    def _emit_lifecycle(self, execution: Any, ticket_id: str) -> None:
        """Append the lifecycle event for every step that has succeeded.

        Reads the step's *persisted* output back out of the transcript (never
        a captured local), so what is emitted is exactly what a replay sees.
        The CLOSED anchor is emitted here too — before the engine appends its
        terminal ``workflow_completed``, which must stay the last event.
        Idempotent: an event already present in the log is never re-emitted.
        """
        for handler_key, event_kind in HANDLER_LIFECYCLE_EVENT.items():
            if event_kind == ENGINEER_TASK_CLOSED:
                # The close handler appends its own anchor mid-step (it must:
                # the engine's terminal event follows immediately and nothing
                # may be appended after it).  Re-emitting here would duplicate it.
                continue
            step = self._step_by_handler(execution, handler_key)
            if step is None:
                continue
            state = execution.state_for(step.step_id)
            if state.status is not StepStatus.SUCCEEDED:
                continue
            if self._already_emitted(execution, event_kind):
                continue
            output = state.output if isinstance(state.output, Mapping) else {}
            payload = dict(output)
            payload.setdefault("tenant", self._tenant)
            payload.setdefault("ticket_id", ticket_id)
            self._append_anchor(execution, event_kind, payload)

    def _step_by_handler(self, execution: Any, handler_key: str) -> Any:
        for step in execution.steps():
            if getattr(step, "handler", "") == handler_key:
                return step
        return None

    @staticmethod
    def _already_emitted(execution: Any, event_kind: str) -> bool:
        for record in execution.events:
            kind = record.kind.value if isinstance(record.kind, EventKind) else str(record.kind)
            if kind == event_kind:
                return True
        return False


__all__ = [
    "ENGINE_EVENT_TO_TICKET",
    "TICKET_EVENT_TO_ENGINE",
    "TICKET_LIFECYCLE_EVENTS",
    "TicketRuntime",
]
