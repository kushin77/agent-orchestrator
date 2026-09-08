"""engine/loop/core_adapter — host the agent loop as a durable engine step.

``engine.core`` (issue #21) declares the ``AGENT_LOOP`` step kind and
exposes the handler seam, but does not own the loop's semantics — this lane
does.  This adapter registers an agent-loop handler under the key
``loop.agent_loop`` on an ``engine.core.Engine`` so a workflow can run an
agent loop as one durable step: the handler executes the deterministic
bounded loop and returns its :class:`AgentDecision` (JSON-safe) as the step
output, which the engine appends to the workflow's event log.

Outcome mapping (never a false pass):

* ``SUCCEEDED`` / ``ESCALATED`` / ``CANNOT_ASSESS`` — the step *completes*
  and the full decision dict (outcome, confidence, trace, escalations) is
  recorded as the step output.  ``ESCALATED`` is a legitimate hand-off, not
  an engine failure; ``CANNOT_ASSESS`` is the honest terminal.
* ``FAILED`` / ``BUDGET_EXHAUSTED`` — the handler raises :class:`StepFailure`
  so the engine records ``STEP_FAILED`` (its retry/dead-letter machinery can
  then act) instead of pretending the step resolved.

Because the loop is deterministic and the engine is durable + resumable, an
interrupted workflow resumes by replaying its event log: a loop step that was
interrupted before completion is re-run to the *identical* decision (the
engine never re-executes a step already recorded SUCCEEDED — see
``engine/core/tests/test_durable_resume.py`` for that invariant).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Optional

from engine.core.errors import StepFailure
from engine.core.handlers import Handler, StepContext
from engine.core.model import Step
from .model import AgentDecision, LoopOutcome, decision_to_dict
from .policy import LoopPolicy, Profile
from .runtime import AgentLoop
from .tools import ToolRegistry

HANDLER_KEY = "loop.agent_loop"

# Outcome values that mean the step itself failed (raise StepFailure) rather
# than completed with a recorded decision.
_FAILING_OUTCOMES = (LoopOutcome.FAILED.value, LoopOutcome.BUDGET_EXHAUSTED.value)

_POLICY_FIELDS = (
    "max_iterations", "token_budget", "max_consecutive_failures",
    "on_allowlist_violation", "on_parse_failure", "confirm_confidence",
    "human_confidence", "tiered_escalation", "human_in_loop", "max_tier",
)


def policy_with_overrides(base: LoopPolicy, overrides: Mapping[str, Any]) -> LoopPolicy:
    """A copy of ``base`` with the (validated) field overrides applied."""
    if not overrides:
        return base
    kwargs = {k: overrides[k] for k in _POLICY_FIELDS if k in overrides}
    return replace(base, **kwargs)


def profile_with_overrides(
    base: Profile, overrides: Mapping[str, Any]
) -> Profile:
    """A copy of ``base`` with registry-shaped overrides (``tool_allowlist``…)."""
    if not overrides:
        return base
    return Profile.from_mapping(
        {
            "agent_id": overrides.get("agent_id", base.agent_id),
            "default_tier": overrides.get("default_tier", base.default_tier.value),
            "tool_allowlist": overrides.get("tool_allowlist", list(base.tool_allowlist)),
            "final_schema": overrides.get("final_schema", base.final_schema),
        }
    )


def register_agent_loop_handler(
    engine: Any,
    *,
    actor: Any,
    tools: ToolRegistry,
    profile: Profile,
    policy: Optional[LoopPolicy] = None,
    complexity_scorer: Any = None,
    handler_key: str = HANDLER_KEY,
) -> str:
    """Register the ``loop.agent_loop`` handler on an ``engine.core.Engine``.

    ``actor`` / ``tools`` / ``profile`` are the session's deterministic seams;
    per-step overrides may arrive through the step's ``args`` (see
    :func:`run_step`).  The engine's own clock is used for the loop, so a
    deterministic engine clock makes the loop's timestamps deterministic too.
    Returns ``handler_key``.
    """
    base_policy = policy or LoopPolicy()

    def _run(step: Step, ctx: StepContext) -> Mapping[str, Any]:
        decision = run_loop_step(
            step,
            ctx,
            actor=actor,
            tools=tools,
            profile=profile,
            policy=base_policy,
            complexity_scorer=complexity_scorer,
        )
        if decision.outcome in _FAILING_OUTCOMES:
            raise StepFailure(
                f"agent loop step {step.step_id!r} ended {decision.outcome}: "
                f"{decision.reason}"
            )
        return decision_to_dict(decision)

    engine.register_handler(handler_key, Handler(run=_run))
    return handler_key


def run_loop_step(
    step: Step,
    ctx: StepContext,
    *,
    actor: Any,
    tools: ToolRegistry,
    profile: Profile,
    policy: LoopPolicy,
    complexity_scorer: Any = None,
) -> AgentDecision:
    """Execute one agent-loop step and return its :class:`AgentDecision`.

    ``step.args`` may carry ``task_input`` (a mapping of session inputs;
    defaults to the workflow inputs), and ``profile``/``policy`` override
    mappings (registry-shaped), letting one registered handler serve many
    profiles.  Runs the loop deterministically with the engine's clock.
    """
    args = dict(step.args)
    task_input = args.get("task_input")
    if task_input is None:
        task_input = dict(ctx.inputs)
    resolved_profile = profile_with_overrides(profile, args.get("profile", {}))
    resolved_policy = policy_with_overrides(policy, args.get("policy", {}))
    loop = AgentLoop(
        actor=actor,
        tools=tools,
        profile=resolved_profile,
        policy=resolved_policy,
        complexity_scorer=complexity_scorer,
        clock=ctx.engine.clock if ctx.engine is not None else None,
    )
    run = loop.run(
        task_input,
        loop_id=f"{ctx.namespace_id}:{ctx.workflow_id}:{step.step_id}",
        tenant_id=ctx.namespace_id,
        agent_id=resolved_profile.agent_id,
        task_id=ctx.workflow_id,
    )
    if not run.finished:  # the engine step runs the loop to completion
        raise RuntimeError("agent loop did not finish within a durable engine step")
    return run.decision
