"""engine/loop.runtime — the deterministic bounded actor loop.

Issue #23 (work item 19, phase 3).  The execution primitive for a *single
agent session*: a bounded iterative actor loop

    plan -> act(tool) -> observe -> repeat  (<= N iterations per tier)

with tool allowlisting, schema-validated outputs, an explicit parse-failure
policy (``retry`` or ``CANNOT-ASSESS``), monotonic tier escalation and
human-in-the-loop hand-off, whole-loop token + iteration cost caps, and one
:class:`AgentDecision` audit record per loop.

Guarantees (each is enforced by construction and negative-tested):

* **Bounded / guaranteed termination.**  Escalation only ever moves a session
  *up* the tier order (never down, never a cycle) and then to a human or a
  terminal outcome; each tier has an iteration budget; the loop has a token
  budget checked before every step.  A session that can never resolve ends in
  ESCALATED / CANNOT_ASSESS / BUDGET_EXHAUSTED / FAILED — it can never spin
  silently or forever (``LoopPolicy.hard_step_bound`` is the absolute cap).
* **Deterministic + resumable.**  The runtime is a pure per-step advance; a
  given actor (same decisions) + given tool results + given inputs always
  produce the same step trace and the same :class:`AgentDecision`
  (:func:`engine.loop.model.decisions_equal` proves it).  The loop also runs
  in *chunks* — ``run(task_input, max_steps=k)`` returns a non-terminal
  checkpoint that ``run(resume_from=...)`` continues, so an interrupted loop
  resumes exactly where it stopped (the durable-engine hand-off the issue
  asks for; ``engine/loop/core_adapter.py`` hosts the same loop inside an
  ``engine.core`` workflow step).
* **No false pass.**  A loop only records SUCCEEDED when a final answer meets
  the confidence threshold and its schema; everything else is a first-class
  terminal outcome with a recorded reason.

The ``actor`` is injected and deterministic-for-replay: it receives a
:class:`DecisionContext` and returns an :class:`Action` (a tool call or a
final answer with confidence + token usage).  The ``tools`` registry is the
allowlist gate.  Neither is called by name here — both are duck-typed seams,
so the real loop (over a live model gateway + tool sandbox) and the offline
suite run the exact same code path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Protocol, Sequence

from .escalate import ComplexityScorer, EscalationDecision, EscalationEngine
from .model import (
    ActionKind,
    AgentDecision,
    EscalationEvent,
    EscalationTargetKind,
    EscalationTrigger,
    LoopOutcome,
    ModelTier,
    ParseStatus,
    StepRecord,
    ToolCall,
    escalation_event_from_dict,
    escalation_event_to_dict,
    step_record_from_dict,
    step_record_to_dict,
    tier_rank,
)
from .policy import LoopPolicy, Profile
from .schema import coerce_json, validate_output
from .tools import ToolRegistry


class Action:
    """One actor decision: ask for a tool call, or produce a final answer.

    ``payload`` carries the unparsed step output the loop must schema-check:

    * ``TOOL_CALL`` — ``{"tool": str, "arguments": {...}}`` (may be a JSON
      string, mirroring raw model output);
    * ``FINAL`` — the final answer value (may be a JSON string that the
      profile's ``final_schema`` parses, the gmail ``OutputSchema.parse``
      discipline).
    """

    __slots__ = ("kind", "payload", "confidence", "tokens_used")

    def __init__(
        self,
        kind: ActionKind,
        payload: Any = None,
        confidence: float = 0.0,
        tokens_used: int = 0,
    ) -> None:
        self.kind = kind
        self.payload = payload
        self.confidence = confidence
        self.tokens_used = tokens_used

    @classmethod
    def tool_call(cls, tool: str, arguments: Mapping[str, Any], tokens_used: int = 0) -> "Action":
        return cls(
            ActionKind.TOOL_CALL,
            {"tool": tool, "arguments": dict(arguments)},
            tokens_used=tokens_used,
        )

    @classmethod
    def final(cls, payload: Any, confidence: float, tokens_used: int = 0) -> "Action":
        return cls(ActionKind.FINAL, payload, confidence=confidence, tokens_used=tokens_used)


class Actor(Protocol):
    """The injected decision maker (deterministic-for-replay).

    ``decide`` receives a :class:`DecisionContext` and returns an
    :class:`Action`.  It never touches the tool executor — it only *proposes*
    tool calls, which the loop validates against the allowlist and schema
    before execution.
    """

    def decide(self, ctx: "DecisionContext") -> Action:
        ...


@dataclass(frozen=True)
class DecisionContext:
    """The deterministic view an actor decides from."""

    task_input: Mapping[str, Any]
    tier: ModelTier
    iteration: int  # 1-based iteration about to run within this tier
    tokens_used: int
    global_steps: int
    observations: Sequence[Mapping[str, Any]]  # tool results so far

    def as_dict(self) -> Mapping[str, Any]:
        return {
            "task_input": dict(self.task_input),
            "tier": self.tier.value,
            "iteration": self.iteration,
            "tokens_used": self.tokens_used,
            "global_steps": self.global_steps,
            "observations": [dict(o) for o in self.observations],
        }


class _LoopState:
    """Mutable per-run state of one agent loop (checkpoint-able, JSON-safe).

    This is the *resumable state handed to the durable engine*: every field
    round-trips through :meth:`to_dict` / :meth:`from_dict`, so a partially
    run loop can be persisted and resumed from its exact position.
    """

    __slots__ = (
        "loop_id", "tenant_id", "agent_id", "task_id",
        "task_input", "tier", "iteration", "global_steps", "tokens_used",
        "failure_count", "trace", "escalations", "outcome", "content",
        "confidence", "reasons", "reason", "started_at", "finished_at",
    )

    def __init__(
        self,
        *,
        loop_id: str,
        tenant_id: str,
        agent_id: str,
        task_id: str,
        task_input: Mapping[str, Any],
        tier: ModelTier,
        started_at: str,
    ) -> None:
        self.loop_id = loop_id
        self.tenant_id = tenant_id
        self.agent_id = agent_id
        self.task_id = task_id
        self.task_input = dict(task_input)
        self.tier = tier
        self.iteration = 0  # iterations already used in the current tier
        self.global_steps = 0
        self.tokens_used = 0
        self.failure_count = 0
        self.trace: list[StepRecord] = []
        self.escalations: list[EscalationEvent] = []
        self.outcome: Optional[LoopOutcome] = None
        self.content: Any = None
        self.confidence = 0.0
        self.reasons: list[str] = []
        self.reason = ""
        self.started_at = started_at
        self.finished_at = ""

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "loop_id": self.loop_id,
            "tenant_id": self.tenant_id,
            "agent_id": self.agent_id,
            "task_id": self.task_id,
            "task_input": dict(self.task_input),
            "tier": self.tier.value,
            "iteration": self.iteration,
            "global_steps": self.global_steps,
            "tokens_used": self.tokens_used,
            "failure_count": self.failure_count,
            "trace": [step_record_to_dict(r) for r in self.trace],
            "escalations": [escalation_event_to_dict(e) for e in self.escalations],
            "outcome": self.outcome.value if self.outcome else None,
            "content": self.content,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "reason": self.reason,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "_LoopState":
        state = cls(
            loop_id=data["loop_id"],
            tenant_id=data["tenant_id"],
            agent_id=data["agent_id"],
            task_id=data["task_id"],
            task_input=data["task_input"],
            tier=ModelTier(data["tier"]),
            started_at=data["started_at"],
        )
        state.iteration = int(data["iteration"])
        state.global_steps = int(data["global_steps"])
        state.tokens_used = int(data["tokens_used"])
        state.failure_count = int(data["failure_count"])
        state.trace = [step_record_from_dict(r) for r in data["trace"]]
        state.escalations = [escalation_event_from_dict(e) for e in data["escalations"]]
        outcome = data.get("outcome")
        state.outcome = LoopOutcome(outcome) if outcome else None
        state.content = data.get("content")
        state.confidence = float(data.get("confidence", 0.0))
        state.reasons = list(data.get("reasons", []))
        state.reason = data.get("reason", "")
        state.finished_at = data.get("finished_at", "")
        return state


class RealClock:
    """Wall clock producing RFC-3339-ish UTC timestamps (injectable)."""

    def now_iso(self) -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()


class AgentLoop:
    """Deterministic bounded actor-loop runtime for one agent session.

    Parameters
    ----------
    actor:
        The injected decision maker (see :class:`Actor`).
    tools:
        The allowlist-gated tool registry (see :class:`ToolRegistry`).
    profile:
        Registry-compatible agent profile view (allowlist, default tier,
        declared final schema) — see :class:`engine.loop.policy.Profile`.
    policy:
        Loop bounds/caps and escalation thresholds (defaults
        :class:`LoopPolicy`).
    complexity_scorer:
        Optional deterministic scorer used to route genuinely complex work to
        a deeper starting tier (hermes routing pattern).
    clock:
        Optional time source (deterministic in tests).
    """

    def __init__(
        self,
        *,
        actor: Actor,
        tools: ToolRegistry,
        profile: Profile,
        policy: Optional[LoopPolicy] = None,
        complexity_scorer: Optional[ComplexityScorer] = None,
        clock: Any = None,
    ) -> None:
        self.actor = actor
        self.tools = tools
        self.profile = profile
        self.policy = policy or LoopPolicy()
        self.complexity_scorer = complexity_scorer
        self.clock = clock or RealClock()
        self.escalation = EscalationEngine(self.policy)

    # -- public API --------------------------------------------------------

    def run(
        self,
        task_input: Mapping[str, Any],
        *,
        resume_from: Optional[Mapping[str, Any]] = None,
        max_steps: Optional[int] = None,
        loop_id: str = "",
        tenant_id: str = "",
        agent_id: str = "",
        task_id: str = "",
    ) -> "_LoopRun":
        """Run (or resume) the loop to a terminal outcome or a chunk boundary.

        With ``resume_from`` a previously returned checkpoint, the loop
        continues from exactly where it stopped (the actor/tools/policy must
        be the same deterministic ones).  ``max_steps`` bounds the number of
        actor decisions executed in this call; when the bound is hit before a
        terminal outcome the returned run has ``finished=False`` and a
        checkpoint to resume from.
        """
        if resume_from is not None:
            state = _LoopState.from_dict(resume_from)
        else:
            initial = self._initial_tier(task_input)
            state = _LoopState(
                loop_id=loop_id or self._default_loop_id(tenant_id, agent_id, task_id),
                tenant_id=tenant_id or self.profile.agent_id,
                agent_id=agent_id or self.profile.agent_id,
                task_id=task_id or "",
                task_input=task_input,
                tier=initial,
                started_at=self.clock.now_iso(),
            )
            if initial is not self.profile.default_tier:
                self._record_escalation(
                    state,
                    EscalationTrigger.COMPLEXITY,
                    from_tier=self.profile.default_tier,
                    to_tier=initial,
                    target_kind=EscalationTargetKind.TIER,
                    iteration=0,
                    reason="task complexity routed the session to a deeper starting tier",
                )

        executed = 0
        while state.outcome is None:
            if max_steps is not None and executed >= max_steps:
                break
            self._advance(state)
            executed += 1

        if state.outcome is None:
            return _LoopRunPartial(self, state)
        return _LoopRunFinal(self, state)

    def _default_loop_id(self, tenant_id: str, agent_id: str, task_id: str) -> str:
        parts = [p for p in (tenant_id, agent_id, task_id) if p]
        return ":".join(parts) if parts else "loop"

    def _initial_tier(self, task_input: Mapping[str, Any]) -> ModelTier:
        start = self.profile.default_tier
        if self.complexity_scorer is None:
            return start
        routed = self._clamp_tier(self.complexity_scorer.route(task_input))
        return routed if tier_rank(routed) > tier_rank(start) else start

    def _clamp_tier(self, tier: ModelTier) -> ModelTier:
        if tier_rank(tier) > tier_rank(self.policy.max_tier):
            return self.policy.max_tier
        return tier

    # -- the deterministic per-step advance --------------------------------

    def _advance(self, state: _LoopState) -> None:
        # Bounds first: guaranteed termination before any actor decision.
        if state.tokens_used > self.policy.token_budget:
            self._token_budget_exhausted(state)
            return
        if state.iteration >= self.policy.max_iterations:
            self._step_budget_exceeded(state)
            return
        if state.global_steps >= self.policy.hard_step_bound():  # defensive invariant
            self._terminal(state, LoopOutcome.FAILED, "step_budget_exceeded",
                           "hard step bound reached (termination invariant)")
            return

        action = self.actor.decide(self._context(state))
        state.tokens_used += max(0, int(action.tokens_used))
        # Cost cap: a thought that pushes the loop over the token budget is
        # never executed/recorded as an iteration — the budget is a hard cap
        # (a SUCCEEDED outcome can never exceed it).
        if state.tokens_used > self.policy.token_budget:
            self._token_budget_exhausted(state)
            return

        state.iteration += 1
        state.global_steps += 1
        if action.kind is ActionKind.TOOL_CALL:
            self._act(state, action)
        else:
            self._settle_final(state, action)

    def _context(self, state: _LoopState) -> DecisionContext:
        observations = [
            {
                "tool": r.tool,
                "ok": r.tool_ok,
                "content": r.content,
                "tier": r.tier,
            }
            for r in state.trace
            if r.action == ActionKind.TOOL_CALL.value and r.tool
        ]
        return DecisionContext(
            task_input=state.task_input,
            tier=state.tier,
            iteration=state.iteration + 1,  # the step about to run
            tokens_used=state.tokens_used,
            global_steps=state.global_steps,
            observations=observations,
        )

    # -- act(tool) ---------------------------------------------------------

    def _act(self, state: _LoopState, action: Action) -> None:
        payload, payload_error = coerce_json(action.payload)
        if payload_error is not None or not isinstance(payload, dict):
            error = payload_error or "tool-call payload must be an object"
            self._record(state, action, tool=action_tool(action),
                         tool_allowed=False,
                         parse_status=ParseStatus.PARSE_FAILED.value,
                         content=None, error=error)
            self._parse_failure_policy(state, error)
            return
        tool = str(payload.get("tool", ""))
        raw_args = payload.get("arguments", {})
        arguments = raw_args if isinstance(raw_args, dict) else {}

        if not self.tools.allows(tool):
            self._record(state, action, tool=tool, tool_allowed=False,
                         parse_status=ParseStatus.NOT_APPLICABLE.value,
                         content=None, error="")
            self._allowlist_violation(state, tool)
            return

        arg_error = self.tools.validate_arguments(tool, arguments)
        if arg_error is not None:
            self._record(state, action, tool=tool, tool_allowed=True,
                         parse_status=ParseStatus.PARSE_FAILED.value,
                         content=arguments, error=arg_error)
            self._parse_failure_policy(state, arg_error)
            return

        result = self.tools.run(ToolCall(name=tool, arguments=arguments))
        state.tokens_used += result.tokens()
        self._record(state, action, tool=tool, tool_allowed=True,
                     parse_status=ParseStatus.PARSED.value,
                     tool_ok=result.ok, content=result.content,
                     error=result.error)
        if not result.ok:
            self._failure_up(state, f"tool {tool!r} failed: {result.error}")
        else:
            state.failure_count = 0

    # -- final -------------------------------------------------------------

    def _settle_final(self, state: _LoopState, action: Action) -> None:
        value, value_error = coerce_json(action.payload)
        if value_error is None and self.profile.final_schema is not None:
            value, value_error = validate_output(value, self.profile.final_schema)
        if value_error is not None:
            self._record(state, action, parse_status=ParseStatus.PARSE_FAILED.value,
                         confidence=action.confidence, content=action.payload,
                         error=value_error)
            self._parse_failure_policy(state, value_error)
            return

        confidence = float(action.confidence)
        self._record(state, action, parse_status=ParseStatus.PARSED.value,
                     confidence=confidence, content=value, error="")
        if confidence >= self.policy.confirm_confidence:
            self._succeed(state, value, confidence)
            return
        if confidence < self.policy.human_confidence:
            # Below the gmail 0.6 floor: too uncertain to trust any tier.
            self._escalate(
                state,
                EscalationTrigger.LOW_CONFIDENCE,
                reason=(f"final confidence {confidence:.2f} below the human floor "
                        f"{self.policy.human_confidence:.2f}"),
                allow_tier=False,
                terminal_reason="low_confidence_cannot_confirm",
            )
            return
        # Uncertain (0.6 <= c < 0.9): bump to a higher-tier persona for a
        # second opinion (hermes pattern); hand to a human at the top tier.
        self._escalate(
            state,
            EscalationTrigger.LOW_CONFIDENCE,
            reason=(f"final confidence {confidence:.2f} below the confirm threshold "
                    f"{self.policy.confirm_confidence:.2f}"),
            terminal_reason="uncertain_cannot_confirm",
        )

    # -- failure / parse / allowlist policy --------------------------------

    def _parse_failure_policy(self, state: _LoopState, error: str) -> None:
        if self.policy.on_parse_failure == "cannot_assess":
            self._terminal(state, LoopOutcome.CANNOT_ASSESS, "cannot_assess",
                           f"output failed schema validation: {error}")
            return
        self._failure_up(state, f"output failed schema validation: {error}")

    def _allowlist_violation(self, state: _LoopState, tool: str) -> None:
        message = f"tool {tool!r} is not in the profile tool allowlist"
        if self.policy.on_allowlist_violation == "cannot_assess":
            self._terminal(state, LoopOutcome.CANNOT_ASSESS, "cannot_assess", message)
            return
        self._failure_up(state, message)

    def _failure_up(self, state: _LoopState, reason: str) -> None:
        state.failure_count += 1
        if state.failure_count >= self.policy.max_consecutive_failures:
            self._escalate(
                state,
                EscalationTrigger.REPEATED_FAILURE,
                reason=(f"{reason} (consecutive failures {state.failure_count} >= "
                        f"{self.policy.max_consecutive_failures})"),
                terminal_reason="repeated_failure_cannot_resolve",
            )

    # -- escalation --------------------------------------------------------

    def _escalate(
        self,
        state: _LoopState,
        trigger: EscalationTrigger,
        *,
        reason: str,
        allow_tier: bool = True,
        terminal_reason: str = "",
    ) -> None:
        decision = self.escalation.next_target(
            state.tier, trigger, reason=reason, allow_tier=allow_tier
        )
        self._apply(state, decision, terminal_reason=terminal_reason)

    def _apply(
        self,
        state: _LoopState,
        decision: EscalationDecision,
        *,
        terminal_reason: str = "",
    ) -> None:
        if decision.kind == "tier":
            self._record_escalation(
                state,
                EscalationTrigger(decision.trigger),
                from_tier=state.tier,
                to_tier=ModelTier(decision.to_tier),
                target_kind=EscalationTargetKind.TIER,
                iteration=state.iteration,
                reason=decision.reason,
            )
            state.tier = ModelTier(decision.to_tier)
            state.iteration = 0
            state.failure_count = 0
            return
        if decision.kind == "human":
            self._record_escalation(
                state,
                EscalationTrigger(decision.trigger),
                from_tier=state.tier,
                to_tier=None,
                target_kind=EscalationTargetKind.HUMAN,
                iteration=state.iteration,
                reason=decision.reason,
            )
            self._terminal(state, LoopOutcome.ESCALATED, decision.trigger,
                           f"handed to human-in-the-loop: {decision.reason}")
            return
        # kind == "none": no tier left and no human in the loop — honest terminal.
        self._terminal(state, LoopOutcome.FAILED, terminal_reason or decision.trigger,
                       decision.reason or "no escalation target remains")

    def _record_escalation(
        self,
        state: _LoopState,
        trigger: EscalationTrigger,
        *,
        from_tier: ModelTier,
        to_tier: Optional[ModelTier],
        target_kind: EscalationTargetKind,
        iteration: int,
        reason: str,
    ) -> None:
        state.escalations.append(
            EscalationEvent(
                seq=len(state.escalations) + 1,
                trigger=trigger.value,
                from_tier=from_tier.value,
                to_tier=to_tier.value if to_tier else None,
                target_kind=target_kind.value,
                iteration=iteration,
                reason=reason,
            )
        )

    # -- terminal helpers --------------------------------------------------

    def _token_budget_exhausted(self, state: _LoopState) -> None:
        decision = self.escalation.human_target(
            state.tier,
            EscalationTrigger.TOKEN_BUDGET_EXCEEDED,
            reason=f"token budget {self.policy.token_budget} exhausted",
        )
        if decision.kind == "human":
            self._apply(state, decision)
            return
        self._terminal(state, LoopOutcome.BUDGET_EXHAUSTED, "budget_exhausted",
                       "token budget exhausted with no human in the loop")

    def _step_budget_exceeded(self, state: _LoopState) -> None:
        self._escalate(
            state,
            EscalationTrigger.STEP_BUDGET_EXCEEDED,
            reason=(f"iteration budget {self.policy.max_iterations} exhausted "
                    f"at tier {state.tier.value}"),
            terminal_reason="iteration_budget_exhausted",
        )

    def _succeed(self, state: _LoopState, value: Any, confidence: float) -> None:
        self._terminal(state, LoopOutcome.SUCCEEDED, "confirmed",
                       "final answer met the confirm confidence threshold")
        state.content = value
        state.confidence = confidence

    def _terminal(
        self,
        state: _LoopState,
        outcome: LoopOutcome,
        reason_code: str,
        message: str,
    ) -> None:
        if state.outcome is not None:  # already terminal; keep the first terminal
            return
        state.outcome = outcome
        state.reason = message
        state.reasons.append(reason_code)
        state.finished_at = self.clock.now_iso()

    # -- trace -------------------------------------------------------------

    def _record(
        self,
        state: _LoopState,
        action: Action,
        *,
        tool: str = "",
        tool_allowed: bool = True,
        tool_ok: Optional[bool] = None,
        parse_status: str = ParseStatus.NOT_APPLICABLE.value,
        confidence: Optional[float] = None,
        content: Any = None,
        error: str = "",
    ) -> None:
        del error  # the outcome of the step is captured by tool_ok/parse_status
        state.trace.append(
            StepRecord(
                iteration=state.iteration,
                tier=state.tier.value,
                action=action.kind.value,
                tool=tool,
                tool_allowed=tool_allowed,
                tool_ok=tool_ok,
                parse_status=parse_status,
                confidence=confidence,
                tokens_used=int(action.tokens_used),
                content=content,
            )
        )


def action_tool(action: Action) -> str:
    """Best-effort tool name from a malformed payload (for the trace)."""
    try:
        payload, _ = coerce_json(action.payload)
        if isinstance(payload, dict):
            return str(payload.get("tool", ""))
    except Exception:  # noqa: BLE001 - trace-only best effort
        pass
    return ""


class _LoopRun:
    """Common surface of a run result (partial or final)."""

    @property
    def finished(self) -> bool:
        raise NotImplementedError  # pragma: no cover

    @property
    def decision(self) -> Optional[AgentDecision]:
        raise NotImplementedError  # pragma: no cover

    def checkpoint(self) -> Mapping[str, Any]:
        raise NotImplementedError  # pragma: no cover


class _LoopRunPartial(_LoopRun):
    """Non-terminal chunk result: persist :meth:`checkpoint`, resume later."""

    def __init__(self, loop: AgentLoop, state: _LoopState) -> None:
        self._loop = loop
        self._state = state

    @property
    def finished(self) -> bool:
        return False

    @property
    def decision(self) -> None:
        return None

    def checkpoint(self) -> Mapping[str, Any]:
        return self._state.to_dict()

    @property
    def steps_executed(self) -> int:
        return self._state.global_steps


class _LoopRunFinal(_LoopRun):
    """Terminal chunk result carrying the full :class:`AgentDecision` audit."""

    def __init__(self, loop: AgentLoop, state: _LoopState) -> None:
        self._loop = loop
        self._state = state
        if state.finished_at == "":
            state.finished_at = loop.clock.now_iso()
        self._decision = AgentDecision(
            loop_id=state.loop_id,
            tenant_id=state.tenant_id,
            agent_id=state.agent_id,
            task_id=state.task_id,
            outcome=state.outcome.value,
            content=state.content,
            confidence=state.confidence,
            tier=state.tier.value,
            iterations_used=state.global_steps,
            tokens_used=state.tokens_used,
            failure_count=state.failure_count,
            reasons=tuple(state.reasons),
            reason=state.reason,
            escalation_events=tuple(state.escalations),
            trace=tuple(state.trace),
            started_at=state.started_at,
            finished_at=state.finished_at,
        )

    @property
    def finished(self) -> bool:
        return True

    @property
    def decision(self) -> AgentDecision:
        return self._decision

    def checkpoint(self) -> Mapping[str, Any]:
        return self._state.to_dict()
