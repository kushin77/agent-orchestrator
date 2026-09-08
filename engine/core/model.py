"""Durable orchestration engine core — domain model.

The vocabulary of the state-machine execution pillar (pillar 3, phase 3):
namespaces (per tenant), workflow specs composed of steps, retry/backoff
policies, saga compensations, and the closed enums used everywhere in the
engine.  Every value object here round-trips through plain JSON-safe dicts
(:func:`spec_to_dict` / :func:`spec_from_dict` and friends) so a workflow
spec can be embedded in a ``workflow_started`` event and later replayed
byte-for-byte without an external registry.

Adapted patterns (see ``docs/CANNIBALIZATION.md`` and ``engine/core/README.md``
for provenance): the namespace-per-tenant provisioning and saga-reverse-
compensation shapes from ``shared-temporal/patterns/multi_tenancy.go`` and
``shared-temporal/patterns/advanced_patterns.go``; the per-workflow cost
tracking shape from ``shared-temporal/governance/cost-tracking.ts``; the
event-sourced workflow transcript shape from ``git-rca-workspace/
src/core/workflow_engine.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Sequence


# --------------------------------------------------------------------------
# Closed vocabularies
# --------------------------------------------------------------------------


class WorkflowStatus(str, Enum):
    """Closed workflow lifecycle vocabulary.

    Transitions between these are enforced by ``core.machine`` and are
    deterministic: PENDING --started--> RUNNING and RUNNING then reaches one
    of the three terminal states (SUCCEEDED / FAILED / ROLLED_BACK).  A saga
    that compensates every registered step ends ROLLED_BACK; a saga whose
    compensation itself fails ends FAILED with the failures recorded.
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"

    @property
    def terminal(self) -> bool:
        return self in (
            WorkflowStatus.SUCCEEDED,
            WorkflowStatus.FAILED,
            WorkflowStatus.ROLLED_BACK,
        )


class StepStatus(str, Enum):
    """Closed per-step vocabulary inside a running/replayed workflow."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    COMPENSATED = "compensated"  # step succeeded, its compensation ran


class WorkflowKind(str, Enum):
    """Declared workflow kinds the engine hosts (issue #21 acceptance #1).

    The engine core is kind-agnostic: a workflow is a list of steps and any
    step kind runs through the same durable, retryable, saga-capable
    scheduler.  Sibling phase-3 lanes register real handlers for the
    agent-loop / fan-out / join step kinds; the core ships handlers for
    ``task`` (via an injected model gateway), ``provision*`` (tenant
    provisioning) and trivial ``noop``/``log``/``notify`` steps.
    """

    TASK_EXECUTION = "task_execution"
    AGENT_LOOP = "agent_loop"
    FAN_OUT_JOIN = "fan_out_join"
    TENANT_PROVISIONING = "tenant_provisioning"
    SAGA = "saga"


class StepKind(str, Enum):
    """Closed step-kind vocabulary understood by the engine."""

    TASK = "task"  # a model-gateway call (provider-agnostic)
    AGENT_LOOP = "agent_loop"  # one agent-loop iteration (handler registered by a sibling lane)
    FAN_OUT = "fan_out"  # fan a batch of sub-work steps (handler registered by a sibling lane)
    JOIN = "join"  # barrier joining fan-out results (handler registered by a sibling lane)
    PROVISION = "provision"  # tenant/namespace provisioning effect
    NOTIFY = "notify"  # side-effect-free notification marker
    NOOP = "noop"  # explicit no-op step


class EventKind(str, Enum):
    """Append-only event vocabulary of the workflow transcript.

    Each event is a single immutable line in the event log (JSONL) and the
    *only* thing that mutates a workflow — the engine projects events onto
    state, never mutates state directly and then records it.
    """

    WORKFLOW_STARTED = "workflow_started"
    STEP_STARTED = "step_started"
    STEP_SUCCEEDED = "step_succeeded"
    STEP_FAILED = "step_failed"
    RETRY_SCHEDULED = "retry_scheduled"
    COMPENSATION_BEGAN = "compensation_began"
    COMPENSATION_STARTED = "compensation_started"
    COMPENSATION_SUCCEEDED = "compensation_succeeded"
    COMPENSATION_FAILED = "compensation_failed"
    STEP_COMPENSATED = "step_compensated"
    WORKFLOW_COMPLETED = "workflow_completed"
    WORKFLOW_FAILED = "workflow_failed"
    WORKFLOW_ROLLED_BACK = "workflow_rolled_back"
    WORKFLOW_RESUMED = "workflow_resumed"


# --------------------------------------------------------------------------
# Value objects
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff policy for a non-saga step.

    A saga step never retries (the Temporal saga pattern runs each activity
    once and compensates on failure — ``advanced_patterns.go`` sets
    ``MaximumAttempts: 1``); the runtime therefore ignores ``retry`` on steps
    of a saga workflow.  The delay before retry number ``n`` (1-based, n>=2)
    is ``initial_interval * backoff_coefficient ** (n - 2)`` capped at
    ``max_interval_seconds``.
    """

    max_attempts: int = 1
    initial_interval_seconds: float = 1.0
    backoff_coefficient: float = 2.0
    max_interval_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.initial_interval_seconds < 0 or self.backoff_coefficient <= 0:
            raise ValueError("invalid backoff parameters")

    def delay_before_retry(self, retry_number: int) -> float:
        """Seconds to wait before the given 1-based retry attempt.

        retry_number == 1 has no delay (it is the first attempt); retry #2
        waits ``initial_interval``; retry #3 waits ``initial*coefficient``; ...
        """
        if retry_number < 2:
            return 0.0
        interval = self.initial_interval_seconds * (
            self.backoff_coefficient ** (retry_number - 2)
        )
        return min(interval, self.max_interval_seconds)


@dataclass(frozen=True)
class CostEntry:
    """One per-step cost line (harvested from cost-tracking.ts)."""

    step_id: str
    resource_type: str  # compute | storage | network | api_calls
    quantity: float = 1.0
    unit_cost: float = 0.0
    total_cost: float = 0.0
    ts: str = ""


@dataclass(frozen=True)
class Compensation:
    """Reverse action registered after its step succeeds.

    ``handler`` names a handler registered on the engine; ``args`` are the
    immutable inputs captured when the step ran (so compensation replays
    against the same inputs after a resume).
    """

    name: str
    handler: str
    args: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Step:
    """One executable unit of a workflow spec.

    ``kind`` selects from the closed :class:`StepKind` vocabulary;
    ``handler`` optionally overrides the handler key (defaults to the kind's
    built-in handler when the engine ships one).  ``retry`` applies to
    non-saga workflows only; ``compensate`` registers a saga compensation
    that runs in reverse completion order if a later step fails.
    """

    step_id: str
    kind: StepKind
    name: str = ""
    handler: str = ""
    args: Mapping[str, Any] = field(default_factory=dict)
    retry: Optional[RetryPolicy] = None
    compensate: Optional[Compensation] = None
    cost_unit: float = 0.0  # fixed charge applied when a step succeeds


@dataclass(frozen=True)
class WorkflowSpec:
    """Immutable blueprint of a workflow execution.

    ``kind`` classifies the workflow for the engine hosts it (issue #21
    acceptance #1); ``steps`` run in order; ``saga=True`` turns the workflow
    into a saga — a failing step triggers the reverse-order compensation of
    every earlier step that succeeded and registered a compensation.
    """

    name: str
    kind: WorkflowKind = WorkflowKind.TASK_EXECUTION
    steps: Sequence[Step] = ()
    saga: bool = False
    sla_seconds: Optional[float] = None  # soft deadline; tracked, not enforced
    description: str = ""


# --------------------------------------------------------------------------
# JSON-safe (de)serialization — specs embed in events, so no registry needed
# --------------------------------------------------------------------------


def retry_to_dict(policy: Optional[RetryPolicy]) -> Optional[Mapping[str, Any]]:
    if policy is None:
        return None
    return {
        "max_attempts": policy.max_attempts,
        "initial_interval_seconds": policy.initial_interval_seconds,
        "backoff_coefficient": policy.backoff_coefficient,
        "max_interval_seconds": policy.max_interval_seconds,
    }


def retry_from_dict(data: Optional[Mapping[str, Any]]) -> Optional[RetryPolicy]:
    if data is None:
        return None
    return RetryPolicy(
        max_attempts=int(data["max_attempts"]),
        initial_interval_seconds=float(data["initial_interval_seconds"]),
        backoff_coefficient=float(data["backoff_coefficient"]),
        max_interval_seconds=float(data["max_interval_seconds"]),
    )


def compensation_to_dict(comp: Optional[Compensation]) -> Optional[Mapping[str, Any]]:
    if comp is None:
        return None
    return {"name": comp.name, "handler": comp.handler, "args": dict(comp.args)}


def compensation_from_dict(data: Optional[Mapping[str, Any]]) -> Optional[Compensation]:
    if data is None:
        return None
    return Compensation(name=data["name"], handler=data["handler"], args=data.get("args", {}))


def step_to_dict(step: Step) -> Mapping[str, Any]:
    return {
        "step_id": step.step_id,
        "kind": step.kind.value if isinstance(step.kind, StepKind) else step.kind,
        "name": step.name,
        "handler": step.handler,
        "args": dict(step.args),
        "retry": retry_to_dict(step.retry),
        "compensate": compensation_to_dict(step.compensate),
        "cost_unit": step.cost_unit,
    }


def step_from_dict(data: Mapping[str, Any]) -> Step:
    kind = data["kind"]
    return Step(
        step_id=data["step_id"],
        kind=StepKind(kind),
        name=data.get("name", ""),
        handler=data.get("handler", ""),
        args=data.get("args", {}),
        retry=retry_from_dict(data.get("retry")),
        compensate=compensation_from_dict(data.get("compensate")),
        cost_unit=float(data.get("cost_unit", 0.0)),
    )


def spec_to_dict(spec: WorkflowSpec) -> Mapping[str, Any]:
    return {
        "name": spec.name,
        "kind": spec.kind.value if isinstance(spec.kind, WorkflowKind) else spec.kind,
        "steps": [step_to_dict(s) for s in spec.steps],
        "saga": spec.saga,
        "sla_seconds": spec.sla_seconds,
        "description": spec.description,
    }


def spec_from_dict(data: Mapping[str, Any]) -> WorkflowSpec:
    return WorkflowSpec(
        name=data["name"],
        kind=WorkflowKind(data["kind"]),
        steps=[step_from_dict(s) for s in data.get("steps", [])],
        saga=bool(data.get("saga", False)),
        sla_seconds=data.get("sla_seconds"),
        description=data.get("description", ""),
    )
