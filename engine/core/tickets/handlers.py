"""Ticket-lifecycle step handlers — decompose, dispatch, execute, review.

Four durable engine steps carry the ticket lifecycle.  Each one is a thin
wrapper over an already-merged primitive — this module adds the *lifecycle*,
never a second copy of the machinery:

* ``TicketDecomposeHandler`` — calls the injected **decomposer** and projects
  the resulting plan.  In production this is a
  :class:`engine.multiagent.planner.HierarchicalPlanner` (whose
  ``decomposer`` is a :class:`engine.multiagent.planner.DecomposeCallable`
  and whose ``max_escalation_rounds`` bound guarantees termination), or the
  ``multiagent.fan_out`` handler for a single-round plan.  Anything exposing
  ``run(mission, decomposer) -> HierarchyReport`` works.
* ``TicketDispatchHandler`` — the board/dispatch bookkeeping step: it *dispatches*
  the ticket into the tenant's lane (no re-planning).
* ``TicketExecuteHandler`` — the execution step: it reads the bounded
  decomposition back **out of the durable transcript** (never from memory)
  and records the execution envelope (rounds used, subtask count, outcome).
* ``TicketReviewHandler`` — the review gate.  With no verdict injected it is a
  no-op (the pipeline still reaches REVIEWED); with a
  :class:`core.tickets.model.TicketReview` it records the verdict and
  **refuses any downstream state transition** when the review did not approve
  — a rejection is an explicit, recorded, non-closeable outcome, never a
  silent pass.

Every handler writes its payload into the workflow's event log, so a resumed
run reconstructs the whole ticket with no hidden in-memory state.
"""

from __future__ import annotations

import sys
from typing import Any, Callable, Dict, Mapping, Optional

from core.errors import StepFailure
from core.handlers import Handler, StepContext
from core.model import Step

from .model import (
    ENGINEER_TASK_CLOSED,
    PLAN_DECOMPOSED,
    TICKET_DISPATCHED,
    TICKET_EXECUTED,
    TICKET_REVIEWED,
    DecompositionOutcome,
    TicketReview,
)

HANDLER_DECOMPOSE = "tickets.decompose"
HANDLER_DISPATCH = "tickets.dispatch"
HANDLER_EXECUTE = "tickets.execute"
HANDLER_REVIEW = "tickets.review"
HANDLER_CLOSE = "tickets.close"

#: handler key -> the ticket-lifecycle event the runtime emits once that step
#: has succeeded.  Handlers *return* their payload; the runtime appends the
#: event afterwards, so a step that emits its own event can never be mistaken
#: for an incomplete step and re-run.  ``HANDLER_CLOSE`` maps to the CLOSED
#: anchor, which must land before the engine's terminal event.
HANDLER_LIFECYCLE_EVENT: Mapping[str, str] = {
    HANDLER_DECOMPOSE: PLAN_DECOMPOSED,
    HANDLER_DISPATCH: TICKET_DISPATCHED,
    HANDLER_EXECUTE: TICKET_EXECUTED,
    HANDLER_REVIEW: TICKET_REVIEWED,
    HANDLER_CLOSE: ENGINEER_TASK_CLOSED,
}

#: step ids owned by this lane's pipeline (review reads the execution by id).
DEFAULT_STEP_IDS: Mapping[str, str] = {
    "decompose": "decompose",
    "dispatch": "dispatch",
    "execute": "execute",
    "review": "review",
    "close": "close",
}


#: process-wide registry of the injected *decomposition seam*, keyed by
#: ``decompose_ref``.  Two shapes are accepted:
#:
#: * a **planner-shaped** object exposing ``run(mission, plan_fn)`` — it owns
#:   the escalation loop and the ``max_escalation_rounds`` bound, and calls
#:   ``plan_fn`` for each round; or
#: * a **bare callable** ``plan_fn(ctx) -> FanOutPlan`` — a single round.
#:
#: A workflow spec embeds in an event and must stay JSON-safe, so the live
#: object is held here and referenced from the step by name only.
_DECOMPOSERS: Dict[str, Any] = {}

#: the default reference used when a spec names none.
DEFAULT_DECOMPOSE_REF = "default"


def register_decomposer(reference: str, decomposer: Any) -> str:
    """Register the decomposition seam under ``reference`` (idempotent).

    ``decomposer`` is the planner-shaped object (anything exposing
    ``run(mission, plan_fn)``) or a bare single-round ``plan_fn``.  The
    workflow spec only carries ``reference`` — the live object stays here, out
    of the JSON-safe event payload.
    """
    if not reference:
        raise ValueError("reference must be non-empty")
    if not callable(decomposer) and not callable(getattr(decomposer, "run", None)):
        raise ValueError(
            "decomposer must expose run(mission, plan_fn) or be callable"
        )
    _DECOMPOSERS[reference] = decomposer
    return reference


def resolve_decomposer(reference: str = "") -> Any:
    """The registered decomposition seam for ``reference`` (fail closed)."""
    key = reference or DEFAULT_DECOMPOSE_REF
    decomposer = _DECOMPOSERS.get(key)
    if decomposer is None:
        raise StepFailure(
            f"no decomposer registered under {key!r}: call "
            "core.tickets.register_decomposer(ref, seam) before running the "
            "ticket (the workflow spec is JSON-safe and cannot carry a live "
            "object)"
        )
    return decomposer


def _fanout_dispatcher() -> Any:
    """Resolve ``FanOutDispatcher`` from the already-imported multiagent seam.

    Looked up through ``sys.modules`` for the same reason the mission builder
    is: importing the multi-agent package from inside a running ticket step
    can deadlock while the ticket package is mid-import.
    """
    fanout = sys.modules.get("engine.multiagent.fanout")
    if fanout is None:
        return None
    return getattr(fanout, "FanOutDispatcher", None)


def _append_ticket_event(
    ctx: StepContext, event_kind: str, payload: Mapping[str, Any]
) -> None:
    """Append one ticket-lifecycle event to the running workflow's transcript.

    Only :class:`TicketCloseHandler` calls this (see its docstring for why);
    every other lifecycle event is emitted by
    :meth:`core.tickets.runtime.TicketRuntime._emit_lifecycle` after its step
    has succeeded.
    """
    from core.model import EventKind

    workflow = ctx.engine.get_workflow(ctx.namespace_id, ctx.workflow_id)
    ctx.engine._append(workflow, EventKind(event_kind), dict(payload))


def _require_str(args: Mapping[str, Any], key: str, handler: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise StepFailure(f"{handler} step requires a non-empty {key!r}")
    return value


def decomposition_from_report(report: Any) -> DecompositionOutcome:
    """Project a planner :class:`HierarchyReport` onto the ticket vocabulary.

    Accepts the real object or its JSON-safe ``as_dict()`` form (a workflow
    spec is embedded in an event, so the engine may hand back either).
    """
    if isinstance(report, Mapping):
        data = dict(report)
        mission = data.get("mission") or {}
        aggregate = data.get("aggregate") or {}
        rounds = len(data.get("reports") or ())
        config = data.get("config") or {}
        return DecompositionOutcome(
            mission_id=str(mission.get("mission_id", data.get("mission_id", ""))),
            planner_lane_id=str(data.get("planner_lane_id", "")),
            rounds=rounds,
            max_escalation_rounds=int(
                data.get(
                    "max_escalation_rounds", config.get("max_escalation_rounds", 0)
                )
            ),
            escalation_count=int(data.get("escalation_count", 0)),
            subtask_count=sum(
                len(plan.get("subtasks", ())) for plan in data.get("plans", ())
            ),
            findings=tuple(aggregate.get("findings", ())),
            failures=tuple(aggregate.get("failures", ())),
            escalated=tuple(aggregate.get("escalated", ())),
            summary=str(aggregate.get("summary", "")),
        )
    # Object form (engine.multiagent.model.HierarchyReport).
    mission = getattr(report, "mission", None)
    aggregate = getattr(report, "aggregate", None)
    plans = tuple(getattr(report, "plans", ()) or ())
    config = getattr(report, "config", None)
    max_rounds = getattr(config, "max_escalation_rounds", None)
    if max_rounds is None:
        max_rounds = getattr(report, "max_escalation_rounds", 0)
    return DecompositionOutcome(
        mission_id=getattr(mission, "mission_id", ""),
        planner_lane_id=getattr(report, "planner_lane_id", ""),
        rounds=len(tuple(getattr(report, "reports", ()) or ())),
        max_escalation_rounds=int(max_rounds or 0),
        escalation_count=int(getattr(report, "escalation_count", 0)),
        subtask_count=sum(len(getattr(plan, "subtasks", ()) or ()) for plan in plans),
        findings=tuple(getattr(aggregate, "findings", ()) or ()),
        failures=tuple(getattr(aggregate, "failures", ()) or ()),
        escalated=tuple(getattr(aggregate, "escalated", ()) or ()),
        summary=str(getattr(aggregate, "summary", "") or ""),
    )


class TicketDecomposeHandler:
    """Decompose the ticket through the injected decomposer (bounded).

    ``decomposer`` is either an object exposing
    ``run(mission, decomposer) -> report`` (the
    :class:`engine.multiagent.planner.HierarchicalPlanner` shape, which
    enforces ``max_escalation_rounds``), or a bare callable taking the mission
    and returning a report/plan.  ``report`` carries the plans, the per-round
    fan-out reports, ``escalation_count`` and the aggregate.
    """

    def __init__(
        self,
        decomposer: Any,
        *,
        max_escalation_rounds: int = 0,
        mission_builder: Optional[Callable[[Step, StepContext], Any]] = None,
    ) -> None:
        has_run = callable(getattr(decomposer, "run", None))
        if not has_run and not callable(decomposer):
            raise TypeError(
                "decomposer must expose run(mission, decomposer) or be callable"
            )
        if max_escalation_rounds < 0:
            raise ValueError("max_escalation_rounds must be >= 0")
        self._decomposer = decomposer
        self._bound = int(max_escalation_rounds)
        self._mission_builder = mission_builder or _default_mission

    @property
    def max_escalation_rounds(self) -> int:
        return self._bound

    def _invoke(self, seam: Any, mission: Any, plan_fn: Callable[..., Any]) -> Any:
        """Run the decomposition seam: ``run(mission, plan_fn)`` or ``plan_fn(mission)``.

        A bare callable returns a plan for *one* round, so it is wrapped into a
        single-round report by :meth:`_single_round_report` — the same shape
        :class:`engine.multiagent.planner.HierarchicalPlanner` produces, so
        downstream projection is identical either way.
        """
        runner = getattr(seam, "run", None)
        if callable(runner):
            # The planner-shaped seam: it owns the escalation loop and the
            # bound, and calls ``plan_fn`` once per round itself.
            return runner(mission, plan_fn)
        if callable(seam):
            return self._single_round_report(mission, seam(mission))
        raise StepFailure("the registered decomposer seam is not callable")

    def _single_round_report(self, mission: Any, plan: Any) -> Mapping[str, Any]:
        """Wrap a bare decomposer's single-round plan as a HierarchyReport shape."""
        model = sys.modules.get("engine.multiagent.model")
        if model is None:
            raise StepFailure(
                "the decomposer seam is not loaded: import engine.multiagent.model "
                "before running a ticket"
            )
        dispatcher_cls = _fanout_dispatcher()
        if dispatcher_cls is None:
            raise StepFailure(
                "the fan-out seam is not loaded: import engine.multiagent.fanout "
                "before running a ticket"
            )
        report = dispatcher_cls.dispatch(plan)
        subtasks = tuple(getattr(plan, "subtasks", ()) or ())
        return {
            "mission": mission.as_dict(),
            "planner_lane_id": getattr(plan, "planner_lane_id", ""),
            "plans": [model.fan_out_plan_to_dict(plan)],
            "reports": [report.as_dict()],
            "escalation_count": 0,
            "max_escalation_rounds": self._bound,
            "aggregate": {
                "planner_lane_id": getattr(plan, "planner_lane_id", ""),
                "summary": (
                    f"single-round decomposition: {len(subtasks)} subtask(s)"
                ),
                "findings": [
                    {
                        "subtask_id": item.subtask.subtask_id,
                        "lane_id": item.subtask.lane_id,
                        "agent_id": item.result.agent_id,
                        "confidence": item.result.confidence,
                        "output": item.result.output,
                    }
                    for item in report.items
                    if item.ok
                ],
                "failures": [
                    item.subtask.subtask_id for item in report.items if not item.ok
                ],
                "escalated": [
                    item.subtask.subtask_id
                    for item in report.items
                    if item.subtask.required and not item.ok
                ],
            },
        }

    def run(self, step: Step, ctx: StepContext) -> Mapping[str, Any]:
        # ``plan_ref`` names the per-round plan function the planner calls;
        # ``decomposer_ref`` names the planner seam that owns the bound.
        plan_fn = resolve_decomposer(str(step.args.get("plan_ref", "")))
        seam = self._decomposer
        ref = step.args.get("decomposer_ref")
        if ref:
            seam = resolve_decomposer(str(ref))
        mission = self._mission_builder(step, ctx)
        report = self._invoke(seam, mission, plan_fn)
        outcome = decomposition_from_report(report)
        if outcome.max_escalation_rounds == 0:
            # The planner owns the bound; record the bound the lane demanded so
            # a reviewer can see whether it was honoured (never silently drop).
            outcome = DecompositionOutcome(
                **{**outcome.as_dict(), "max_escalation_rounds": self._bound}
            )
        if outcome.rounds > outcome.max_escalation_rounds + 1:
            raise StepFailure(
                f"decomposition ran {outcome.rounds} round(s) for bound "
                f"max_escalation_rounds={outcome.max_escalation_rounds} "
                "(the escalation bound was not honoured)"
            )
        payload = dict(outcome.as_dict())
        payload["objective"] = getattr(mission, "objective", "")
        payload["tenant"] = ctx.namespace_id
        payload["ticket_id"] = str(step.args.get("ticket_id", ""))
        return payload


def _default_mission(step: Step, ctx: StepContext) -> Any:
    """Build the mission handed to the decomposer.

    The ``Mission`` class is resolved through the *already-imported* module so
    this never re-enters the multi-agent package while a ticket step is being
    dispatched (an import inside a running handler can deadlock when the
    ticket package is itself mid-import).
    """
    model = sys.modules.get("engine.multiagent.model")
    if model is None:
        raise StepFailure(
            "the decomposer seam is not loaded: import engine.multiagent.model "
            "before registering ticket handlers"
        )
    context = dict(step.args.get("mission_context") or {})
    # The tenant scope always travels with the mission (never inferable).
    context.setdefault("tenant", ctx.namespace_id)
    return model.Mission(
        mission_id=str(step.args["mission_id"]),
        objective=str(step.args.get("objective", "")),
        context=context,
    )


class TicketDispatchHandler:
    """Dispatch a decomposed ticket to the tenant's lane/board (bookkeeping)."""

    def run(self, step: Step, ctx: StepContext) -> Mapping[str, Any]:
        lane = _require_str(step.args, "lane", HANDLER_DISPATCH)
        queue = str(step.args.get("queue") or f"{ctx.namespace_id}:default")
        payload = {
            "tenant": ctx.namespace_id,
            "ticket_id": _require_str(step.args, "ticket_id", HANDLER_DISPATCH),
            "lane": lane,
            "queue": queue,
            "dispatched_at": ctx.clock.now_iso() if ctx.clock is not None else "",
        }
        return payload


class TicketExecuteHandler:
    """Record the execution envelope, read back from the durable transcript."""

    def __init__(self, *, decompose_step_id: str = DEFAULT_STEP_IDS["decompose"]) -> None:
        self._decompose_step_id = decompose_step_id

    def run(self, step: Step, ctx: StepContext) -> Mapping[str, Any]:
        decompose_step_id = str(
            step.args.get("decompose_step_id") or self._decompose_step_id
        )
        workflow = ctx.engine.get_workflow(ctx.namespace_id, ctx.workflow_id)
        state = workflow.state_for(decompose_step_id)
        projected = state.output if state is not None else None
        if not isinstance(projected, Mapping):
            raise StepFailure(
                f"{HANDLER_EXECUTE} step found no durable decomposition output "
                f"from step {decompose_step_id!r} (cannot execute an "
                "undecomposed ticket)"
            )
        outcome = DecompositionOutcome.from_dict(projected)
        payload = {
            "tenant": ctx.namespace_id,
            "ticket_id": _require_str(step.args, "ticket_id", HANDLER_EXECUTE),
            "decompose_step_id": decompose_step_id,
            "rounds": outcome.rounds,
            "subtask_count": outcome.subtask_count,
            "escalation_count": outcome.escalation_count,
            "max_escalation_rounds": outcome.max_escalation_rounds,
            "resolved": outcome.resolved,
            "escalated": list(outcome.escalated),
            "failures": list(outcome.failures),
            "executed_at": ctx.clock.now_iso() if ctx.clock is not None else "",
        }
        return payload


class TicketReviewHandler:
    """The review gate: record the verdict and refuse a non-approved close.

    The verdict arrives as ``step.args['review']`` (a
    :class:`TicketReview` or its JSON-safe dict), so it replays from the log.
    With no verdict the gate records an un-reviewed execution and leaves the
    ticket eligible for closing (the default documented in the README).
    """

    def run(self, step: Step, ctx: StepContext) -> Mapping[str, Any]:
        raw = step.args.get("review")
        review: Optional[TicketReview]
        if raw is None:
            review = None
        elif isinstance(raw, TicketReview):
            review = raw
        elif isinstance(raw, Mapping):
            review = TicketReview.from_dict(raw)
        else:
            raise StepFailure(
                f"{HANDLER_REVIEW} step args['review'] must be a TicketReview "
                f"or a mapping, got {type(raw).__name__}"
            )
        payload: Dict[str, Any] = {
            "tenant": ctx.namespace_id,
            "ticket_id": _require_str(step.args, "ticket_id", HANDLER_REVIEW),
            "gate": str(step.args.get("gate") or "tenant-review"),
        }
        if review is None:
            payload["reviewed_by"] = ""
            payload["approved"] = True
            payload["rationale"] = "no reviewer verdict injected"
            payload["gate_open"] = True
        else:
            verdict = review.as_dict()
            payload.update(verdict)
            payload["gate_open"] = bool(review.approved)
            if not review.approved:
                payload["blocked_reason"] = (
                    f"review by {review.reviewed_by or 'unknown'} did not approve "
                    "the ticket; the ticket cannot be closed"
                )
        payload["reviewed_at"] = payload.get("reviewed_at") or (
            ctx.clock.now_iso() if ctx.clock is not None else ""
        )
        return payload


class TicketCloseHandler:
    """The terminal step: append the ``ENGINEER_TASK_CLOSED`` anchor.

    This is the *one* place a ticket lane appends its own event mid-step, and
    it must be: the engine appends ``workflow_completed`` immediately after
    this handler returns, and the projection refuses any event after a
    terminal one — so the closed anchor has to land here or not at all.  The
    ticket therefore reaches CLOSED only through a run that reached this step.

    The review gate is enforced here: a recorded verdict that did **not**
    approve fails the step closed (``StepFailure``), so the ticket ends at
    REVIEWED and the workflow never reports success.  A rejection is an
    explicit, recorded outcome — never a silent close.
    """

    def run(self, step: Step, ctx: StepContext) -> Mapping[str, Any]:
        ticket_id = _require_str(step.args, "ticket_id", HANDLER_CLOSE)
        self._require_approved_review(ctx)

        payload = {
            "tenant": ctx.namespace_id,
            "ticket_id": ticket_id,
            "closed_at": ctx.clock.now_iso() if ctx.clock is not None else "",
        }
        _append_ticket_event(ctx, ENGINEER_TASK_CLOSED, payload)
        return payload

    @staticmethod
    def _require_approved_review(ctx: StepContext) -> None:
        """Refuse to close a ticket whose review did not approve it."""
        workflow = ctx.engine.get_workflow(ctx.namespace_id, ctx.workflow_id)
        for record in workflow.events:
            kind = (
                record.kind.value
                if hasattr(record.kind, "value")
                else str(record.kind)
            )
            if kind != TICKET_REVIEWED:
                continue
            if record.payload.get("gate_open") is False:
                reason = record.payload.get("blocked_reason") or (
                    "the review did not approve this ticket"
                )
                raise StepFailure(
                    f"refusing to close ticket {ctx.step_id!r}-owning workflow: "
                    f"{reason}"
                )


def register_ticket_handlers(
    engine: Any,
    *,
    decomposer: Any,
    max_escalation_rounds: int = 0,
    mission_builder: Optional[Callable[[Step, StepContext], Any]] = None,
    decompose_ref: str = DEFAULT_DECOMPOSE_REF,
    decompose_step_id: str = DEFAULT_STEP_IDS["decompose"],
) -> Mapping[str, Handler]:
    """Register the five ticket handlers on a core ``Engine``.

    The injected ``decomposer`` (a
    :class:`engine.multiagent.planner.HierarchicalPlanner` or anything exposing
    ``run(mission, decomposer) -> HierarchyReport``) is registered under
    ``decompose_ref``.  The workflow spec names that reference rather than
    embedding a live callable, because a spec is persisted inside the
    ``workflow_started`` event and must stay JSON-safe.

    ``engine`` is any object exposing ``register_handler(key, handler)`` —
    typically :class:`core.runtime.Engine`.  Returns the registry it wrote so
    callers can assert on it.
    """
    register_decomposer(decompose_ref, decomposer)
    handlers: Dict[str, Handler] = {
        HANDLER_DECOMPOSE: Handler(
            run=TicketDecomposeHandler(
                decomposer,
                max_escalation_rounds=max_escalation_rounds,
                mission_builder=mission_builder,
            ).run
        ),
        HANDLER_DISPATCH: Handler(run=TicketDispatchHandler().run),
        HANDLER_EXECUTE: Handler(
            run=TicketExecuteHandler(decompose_step_id=decompose_step_id).run
        ),
        HANDLER_REVIEW: Handler(run=TicketReviewHandler().run),
        HANDLER_CLOSE: Handler(run=TicketCloseHandler().run),
    }
    for key, handler in handlers.items():
        engine.register_handler(key, handler)
    return dict(handlers)


__all__ = [
    "DEFAULT_DECOMPOSE_REF",
    "DEFAULT_STEP_IDS",
    "HANDLER_CLOSE",
    "HANDLER_DECOMPOSE",
    "HANDLER_DISPATCH",
    "HANDLER_EXECUTE",
    "HANDLER_REVIEW",
    "TicketCloseHandler",
    "TicketDecomposeHandler",
    "TicketDispatchHandler",
    "TicketExecuteHandler",
    "TicketReviewHandler",
    "decomposition_from_report",
    "register_decomposer",
    "register_ticket_handlers",
    "resolve_decomposer",
]
