"""engine.multiagent — integration seam onto engine/core durable steps.

The engine core (issue #21) already *hosts* ``FAN_OUT`` and ``JOIN`` workflow
steps as closed, durable, saga-capable step kinds; sibling phase-3 lanes
register the real handlers on the core's handler seam.  This module
registers the multi-agent handlers:

* ``multiagent.fan_out`` — executes a bounded, allSettled fan-out of an
  embedded :class:`FanOutPlan` through the injected runner and returns a
  JSON-safe report (persisted into the workflow's event log).
* ``multiagent.join`` — the aggregation barrier: reads the fan-out step's
  *persisted* output from the durable transcript (``ctx.engine`` ->
  ``get_workflow`` -> ``state_for``) and synthesizes the planner's summary —
  no hidden in-memory state, so a resumed run reconstructs it from events.

Both handlers import ``engine.core`` at call time via the repo-root PEP-420
namespace (``engine/`` has no ``__init__.py``), matching the sibling
``engine.queue`` / ``engine.memory`` packages.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

from .fanout import FanOutDispatcher
from .model import (
    FanOutPlan,
    HierarchyConfig,
    Lane,
    fan_out_plan_from_dict,
    fan_out_plan_to_dict,
)

HANDLER_FAN_OUT = "multiagent.fan_out"
HANDLER_JOIN = "multiagent.join"
WORKFLOW_KIND = "fan_out_join"  # engine.core WorkflowKind.FAN_OUT_JOIN value


def register_multiagent_handlers(
    engine: Any,
    runner: object,
    *,
    specialist_lanes: Sequence[Lane] = (),
    config: Optional[HierarchyConfig] = None,
) -> None:
    """Register ``multiagent.fan_out`` / ``multiagent.join`` on a core Engine.

    ``engine`` is any object exposing the core seam
    (``register_handler(key, handler)``); typically ``engine.core.runtime.Engine``.
    """
    from engine.core.handlers import Handler
    from engine.core.errors import StepFailure

    config = config or HierarchyConfig()
    dispatcher = FanOutDispatcher(
        runner,
        max_fan_out=config.max_fan_out,
        specialist_lanes=tuple(specialist_lanes),
    )

    def _fan_out_run(step: Any, ctx: Any) -> Dict[str, Any]:
        payload = step.args.get("plan")
        if payload is None:
            raise StepFailure(f"{HANDLER_FAN_OUT} step requires args['plan']")
        plan = fan_out_plan_from_dict(payload)
        report = dispatcher.dispatch(plan)  # bounded (FanOutLimitError on overflow)
        return {
            "plan": fan_out_plan_to_dict(plan),
            "report": report.as_dict(),
        }

    def _join_run(step: Any, ctx: Any) -> Dict[str, Any]:
        fan_step_id = step.args.get("fan_step_id")
        if not fan_step_id:
            raise StepFailure(f"{HANDLER_JOIN} step requires args['fan_step_id']")
        workflow = ctx.engine.get_workflow(ctx.namespace_id, ctx.workflow_id)
        fan_state = workflow.state_for(fan_step_id)
        fan_output = (fan_state.output or {}) if fan_state is not None else {}
        report = fan_output.get("report", {}) if isinstance(fan_output, dict) else {}
        items = report.get("items", [])
        succeeded = 0
        failures: list = []
        for item in items:
            result = item.get("result", {})
            if result.get("status") == "succeeded":
                succeeded += 1
            else:
                subtask = item.get("subtask", {})
                failures.append(subtask.get("subtask_id", "?"))
        return {
            "fan_step_id": fan_step_id,
            "summary": (
                f"joined {succeeded} succeeded, {len(failures)} failed "
                f"specialist subtask(s)"
            ),
            "succeeded": succeeded,
            "failed": len(failures),
            "failures": failures,
        }

    engine.register_handler(HANDLER_FAN_OUT, Handler(run=_fan_out_run))
    engine.register_handler(HANDLER_JOIN, Handler(run=_join_run))


def fan_join_workflow(
    name: str,
    plan: FanOutPlan,
    *,
    fan_step_id: str = "fan",
    join_step_id: str = "join",
) -> Any:
    """Build an engine-core ``FAN_OUT`` + ``JOIN`` WorkflowSpec for a mission."""
    from engine.core.model import Step, StepKind, WorkflowKind, WorkflowSpec

    return WorkflowSpec(
        name=name,
        kind=WorkflowKind.FAN_OUT_JOIN,
        steps=[
            Step(
                step_id=fan_step_id,
                kind=StepKind.FAN_OUT,
                handler=HANDLER_FAN_OUT,
                args={"plan": fan_out_plan_to_dict(plan)},
            ),
            Step(
                step_id=join_step_id,
                kind=StepKind.JOIN,
                handler=HANDLER_JOIN,
                args={"fan_step_id": fan_step_id},
            ),
        ],
    )


def aggregate_report_from_join_output(output: Dict[str, Any]) -> Dict[str, Any]:
    """Read the planner-style aggregation out of a join step's persisted output."""
    return {
        "summary": output.get("summary", ""),
        "succeeded": output.get("succeeded", 0),
        "failed": output.get("failed", 0),
        "failures": list(output.get("failures", [])),
    }
