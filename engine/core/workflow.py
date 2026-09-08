"""WorkflowExecution — the live projection of a workflow's event log.

State is *derived*, never stored independently: :meth:`WorkflowExecution.apply`
projects one :class:`EventRecord` onto the in-memory object exactly as
:meth:`WorkflowExecution.from_events` replays a stored log.  A fresh object
built from a persisted log is therefore byte-for-byte equivalent to the live
object the engine was mutating before an interruption — that equivalence is
what makes resume-after-restart deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .errors import PersistenceError
from .events import EventRecord
from .machine import is_workflow_event, next_status
from .model import (
    CostEntry,
    EventKind,
    Step,
    StepStatus,
    WorkflowSpec,
    WorkflowStatus,
    spec_from_dict,
)


@dataclass
class StepExecutionState:
    """Projected state of one step within a workflow."""

    step_id: str
    status: StepStatus = StepStatus.PENDING
    attempts: int = 0
    last_error: Optional[str] = None
    completed_order: Optional[int] = None  # completion order (for reverse compensation)
    output: Any = None
    compensation_attempted: bool = False  # a COMPENSATION_STARTED was seen


@dataclass
class WorkflowExecution:
    """Projection of one workflow's event history."""

    namespace_id: str
    workflow_id: str
    spec: WorkflowSpec
    inputs: Mapping[str, Any] = field(default_factory=dict)
    status: WorkflowStatus = WorkflowStatus.PENDING
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    step_states: Dict[str, StepExecutionState] = field(default_factory=dict)
    rollback_in_progress: bool = False
    failed_step_id: Optional[str] = None
    compensation_failures: List[Dict[str, Any]] = field(default_factory=list)
    cost_entries: List[CostEntry] = field(default_factory=list)
    events: List[EventRecord] = field(default_factory=list)
    resumed_count: int = 0

    _completed_counter: int = 0
    _steps: List[Step] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self._steps = list(self.spec.steps)
        for step in self._steps:
            self.step_states.setdefault(
                step.step_id, StepExecutionState(step_id=step.step_id)
            )

    # -- construction -------------------------------------------------------

    @classmethod
    def from_events(cls, records: Sequence[EventRecord]) -> "WorkflowExecution":
        """Rebuild a workflow purely from its event log (deterministic)."""
        if not records:
            raise PersistenceError("cannot project a workflow from an empty event log")
        first = records[0]
        if first.kind is not EventKind.WORKFLOW_STARTED:
            raise PersistenceError("event log does not start with workflow_started")
        spec = spec_from_dict(first.payload["spec"])
        execution = cls(
            namespace_id=first.namespace_id,
            workflow_id=first.workflow_id,
            spec=spec,
            inputs=dict(first.payload.get("inputs") or {}),
        )
        for record in records:
            execution.apply(record)
        return execution

    # -- projection ---------------------------------------------------------

    def apply(self, record: EventRecord) -> None:
        """Project one event onto this execution (used live and on replay)."""
        kind = record.kind
        if kind is EventKind.WORKFLOW_STARTED:
            if self.status is not WorkflowStatus.PENDING:
                raise PersistenceError(
                    f"duplicate workflow_started for {self.workflow_id} "
                    f"(corrupt or out-of-order log)"
                )
            self.status = WorkflowStatus.RUNNING
            self.started_at = record.payload.get("started_at") or record.ts
            self.events.append(record)
            return

        # Every other event requires an already-running workflow.
        if kind is not EventKind.WORKFLOW_RESUMED and self.status is not WorkflowStatus.RUNNING:
            raise PersistenceError(
                f"workflow {self.workflow_id} is {self.status.value}, "
                f"cannot apply {kind.value} (corrupt or out-of-order log)"
            )

        if kind is EventKind.WORKFLOW_RESUMED:
            self.resumed_count += 1
            self.events.append(record)
            return

        if is_workflow_event(kind):
            self._apply_workflow_event(kind, record)
        elif kind is EventKind.COMPENSATION_BEGAN:
            self.rollback_in_progress = True
            self.failed_step_id = record.payload.get("failed_step_id")
            self.events.append(record)
        else:
            self._apply_step_event(kind, record)

    def _apply_workflow_event(self, kind: EventKind, record: EventRecord) -> None:
        self.status = next_status(self.status, kind)
        if kind is EventKind.WORKFLOW_COMPLETED:
            self.completed_at = record.payload.get("completed_at") or record.ts
        elif kind in (EventKind.WORKFLOW_FAILED, EventKind.WORKFLOW_ROLLED_BACK):
            # Individual COMPENSATION_FAILED events already populated
            # compensation_failures; the terminal event only carries a
            # transcript copy and must not double-add.
            self.completed_at = record.payload.get("completed_at") or record.ts
        self.events.append(record)

    def _apply_step_event(self, kind: EventKind, record: EventRecord) -> None:
        step_id = record.payload.get("step_id")
        state = self.step_states.get(step_id)
        if state is None:
            raise PersistenceError(f"event references unknown step {step_id!r}")
        if kind is EventKind.STEP_STARTED:
            state.attempts = max(state.attempts, int(record.payload.get("attempt", 0)))
        elif kind is EventKind.STEP_SUCCEEDED:
            state.status = StepStatus.SUCCEEDED
            state.attempts = max(state.attempts, int(record.payload.get("attempt", 0)))
            self._completed_counter += 1
            state.completed_order = self._completed_counter
            state.output = record.payload.get("output")
            cost = record.payload.get("cost")
            if isinstance(cost, dict):
                self.cost_entries.append(
                    CostEntry(
                        step_id=str(cost.get("step_id", step_id)),
                        resource_type=str(cost.get("resource_type", "api_calls")),
                        quantity=float(cost.get("quantity", 1.0)),
                        unit_cost=float(cost.get("unit_cost", 0.0)),
                        total_cost=float(cost.get("total_cost", 0.0)),
                        ts=cost.get("ts") or record.ts,
                    )
                )
        elif kind is EventKind.STEP_FAILED:
            state.status = StepStatus.FAILED
            state.attempts = max(state.attempts, int(record.payload.get("attempt", 0)))
            state.last_error = record.payload.get("error")
        elif kind is EventKind.STEP_COMPENSATED:
            if state.status is not StepStatus.SUCCEEDED:
                # A step that never succeeded cannot be compensated; refuse.
                raise PersistenceError(
                    f"step {step_id!r} marked compensated without succeeding"
                )
            state.status = StepStatus.COMPENSATED
        elif kind is EventKind.COMPENSATION_STARTED:
            # Marked attempted even on failure so the runtime never re-runs a
            # failed compensation forever (each step is compensated at most once).
            state.compensation_attempted = True
        elif kind is EventKind.COMPENSATION_FAILED:
            self.compensation_failures.append(
                {"step_id": step_id, "error": record.payload.get("error")}
            )
        self.events.append(record)

    # -- queries used by the runtime ----------------------------------------

    def step(self, step_id: str) -> Step:
        for step in self._steps:
            if step.step_id == step_id:
                return step
        raise KeyError(step_id)

    def steps(self) -> Sequence[Step]:
        return self._steps

    def state_for(self, step_id: str) -> StepExecutionState:
        return self.step_states[step_id]

    def succeeded_steps_in_completion_order(self) -> List[Step]:
        """Steps that succeeded, oldest-completed first (compensation runs reverse)."""
        ordered = sorted(
            (
                (state, self.step(state.step_id))
                for state in self.step_states.values()
                if state.status is StepStatus.SUCCEEDED
            ),
            key=lambda pair: pair[0].completed_order or 0,
        )
        return [step for _state, step in ordered]

    def pending_compensations(self) -> List[Tuple[Step, StepExecutionState]]:
        """Succeeded steps with a registered compensation, reverse completion order.

        A step whose compensation was already attempted (successfully or not) is
        excluded so a failed compensation is recorded once and the saga moves on
        to the remaining compensations instead of retrying forever.
        """
        candidates = [
            (step, self.step_states[step.step_id])
            for step in self.succeeded_steps_in_completion_order()
            if step.compensate is not None
            and self.step_states[step.step_id].status is StepStatus.SUCCEEDED
            and not self.step_states[step.step_id].compensation_attempted
        ]
        return list(reversed(candidates))

    def elapsed_seconds(self) -> Optional[float]:
        """Seconds between started_at and completed_at (None until terminal)."""
        if not self.started_at or not self.completed_at:
            return None
        from datetime import datetime

        def _parse(value: str):
            return datetime.fromisoformat(value)

        return (_parse(self.completed_at) - _parse(self.started_at)).total_seconds()
