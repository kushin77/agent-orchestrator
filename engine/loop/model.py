"""engine/loop.model — domain vocabulary of the deterministic agent loop.

Closed enums and value objects for the bounded actor loop (issue #23, work
item 19, phase 3): the model tiers a session may run at, the terminal
outcomes a loop may reach, the escalation vocabulary (hermes-adapted), and
the per-loop :class:`AgentDecision` audit record plus its per-step trace.

Every value object round-trips through plain JSON-safe dicts
(:func:`..._to_dict` / :func:`..._from_dict`) with sorted keys so two equal
objects serialize byte-for-byte identically — determinism is the point of
this lane.  The vocabulary is closed (:class:`str`-backed ``Enum``s), so a
logical mis-step (an unknown outcome, a tier outside the order) is a
``ValueError`` at construction time, never a silent surprise.

Adapted patterns (see ``engine/loop/README.md`` for provenance): the
<=10-iteration tool loop + per-decision audit record from ``gmail-agent
src/agent/claude.ts``; the model-tier escalation vocabulary (TIER1..TIER3,
confidence/error/timeout triggers) from ``hermes-agents
services/escalation_handler.py``; and the deterministic bounded-runner
discipline from ``leaderboard lib/agent-loop.sh``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Sequence


# --------------------------------------------------------------------------
# Closed vocabularies
# --------------------------------------------------------------------------


class ModelTier(str, Enum):
    """Closed model/persona tier vocabulary (hermes ``ModelTier`` shape).

    ``TIER1`` is the fast/cheap persona, ``TIER3`` the deepest/most capable
    persona.  Escalation is *monotonic upward* through this order and stops
    at ``TIER3`` — that monotonicity is what makes the loop guaranteed to
    terminate (a session can be escalated at most ``len(TIER_ORDER)-1``
    times before it must settle or hand off).
    """

    TIER1 = "tier1"
    TIER2 = "tier2"
    TIER3 = "tier3"


TIER_ORDER: tuple[ModelTier, ...] = (ModelTier.TIER1, ModelTier.TIER2, ModelTier.TIER3)


def next_tier(tier: ModelTier) -> Optional[ModelTier]:
    """The next-higher tier, or ``None`` when ``tier`` is already the deepest."""
    try:
        idx = TIER_ORDER.index(tier)
    except ValueError as exc:  # pragma: no cover - enum is closed
        raise ValueError(f"unknown tier: {tier!r}") from exc
    if idx + 1 >= len(TIER_ORDER):
        return None
    return TIER_ORDER[idx + 1]


def tier_rank(tier: ModelTier) -> int:
    """0-based rank of a tier in :data:`TIER_ORDER` (deterministic ordering)."""
    return TIER_ORDER.index(tier)


class LoopOutcome(str, Enum):
    """Closed terminal vocabulary of one agent-loop run.

    A loop is *guaranteed* to reach exactly one of these (bounded iterations
    + bounded tokens + monotonic escalation); it never silently stops and
    never loops forever.  ``ESCALATED`` means the loop handed off — to a
    higher-tier persona or to a human-in-the-loop — rather than pretending to
    resolve.  ``CANNOT_ASSESS`` is the honest "I cannot answer" terminal
    (gmail/leaderboard no-false-pass doctrine).
    """

    SUCCEEDED = "succeeded"
    ESCALATED = "escalated"
    CANNOT_ASSESS = "cannot_assess"
    BUDGET_EXHAUSTED = "budget_exhausted"
    FAILED = "failed"

    @property
    def terminal(self) -> bool:
        return True


class ActionKind(str, Enum):
    """What one actor decision asks the loop to do (plan -> act -> observe)."""

    TOOL_CALL = "tool_call"
    FINAL = "final"


class EscalationTrigger(str, Enum):
    """Why a loop escalated (hermes trigger vocabulary, widened)."""

    COMPLEXITY = "complexity"
    LOW_CONFIDENCE = "low_confidence"
    REPEATED_FAILURE = "repeated_failure"
    STEP_BUDGET_EXCEEDED = "step_budget_exceeded"
    TOKEN_BUDGET_EXCEEDED = "token_budget_exceeded"
    ALLOWLIST_VIOLATION = "allowlist_violation"
    PARSE_FAILURE = "parse_failure"


class EscalationTargetKind(str, Enum):
    """Where an escalation hands the session: a higher persona tier or a human."""

    TIER = "tier"
    HUMAN = "human"


class ParseStatus(str, Enum):
    """Schema-validation status of one step's output."""

    PARSED = "parsed"
    PARSE_FAILED = "parse_failed"
    NOT_APPLICABLE = "not_applicable"


# --------------------------------------------------------------------------
# Value objects
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolCall:
    """One allowlist-checked tool invocation (the *act* of a loop step)."""

    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    tokens_used: int = 0  # model tokens spent emitting this call


@dataclass(frozen=True)
class ToolResult:
    """The observed result of one tool call (the *observe* of a loop step)."""

    name: str
    ok: bool
    content: Any = None
    usage: Mapping[str, int] = field(default_factory=dict)
    error: str = ""

    def tokens(self) -> int:
        """Deterministic token accounting for a result (usage wins when set)."""
        if self.usage.get("tokens", 0) or self.usage.get("output_tokens", 0):
            return int(self.usage.get("tokens", self.usage.get("output_tokens", 0)))
        # Fallback estimator: 1 token ~ 4 chars (deterministic, offline).
        return max(1, len(str(self.content)) // 4)


@dataclass(frozen=True)
class StepRecord:
    """One deterministic trace row of the actor loop.

    Exactly one record is appended per actor decision (iteration), so the
    ``trace`` of an :class:`AgentDecision` is a faithful replayable transcript
    of the loop: what the actor asked for, whether the allowlist admitted it,
    whether the output parsed, and (for tool calls) what the tool returned.
    """

    iteration: int  # 1-based within the tier budget
    tier: str
    action: str  # ActionKind.value
    tool: str = ""
    tool_allowed: bool = True
    tool_ok: Optional[bool] = None
    parse_status: str = ParseStatus.NOT_APPLICABLE.value
    confidence: Optional[float] = None
    tokens_used: int = 0
    content: Any = None


@dataclass(frozen=True)
class EscalationEvent:
    """One escalation the loop performed (append-only, seq-ordered)."""

    seq: int
    trigger: str  # EscalationTrigger.value
    from_tier: str
    to_tier: Optional[str]  # None when the target is a human
    target_kind: str  # EscalationTargetKind.value
    iteration: int
    reason: str = ""


@dataclass(frozen=True)
class AgentDecision:
    """The per-loop audit record (gmail ``claude.draft_complete`` shape).

    One :class:`AgentDecision` is produced per loop and carries the full
    deterministic audit trail: terminal outcome, final content + confidence,
    the tier(s) used, iteration/token accounting, the escalation events and
    the step trace.  It is JSON-safe and deterministic — two loops over the
    same inputs with the same injected tool results produce two byte-equal
    decisions.
    """

    loop_id: str
    tenant_id: str
    agent_id: str
    task_id: str
    outcome: str  # LoopOutcome.value
    content: Any = None
    confidence: float = 0.0
    tier: str = ModelTier.TIER1.value
    iterations_used: int = 0
    tokens_used: int = 0
    failure_count: int = 0
    reasons: Sequence[str] = ()
    reason: str = ""
    escalation_events: Sequence[EscalationEvent] = ()
    trace: Sequence[StepRecord] = ()
    started_at: str = ""
    finished_at: str = ""

    @property
    def succeeded(self) -> bool:
        return self.outcome == LoopOutcome.SUCCEEDED.value


# --------------------------------------------------------------------------
# JSON-safe (de)serialization — checkpoints/decisions embed in stores
# --------------------------------------------------------------------------


def tool_call_to_dict(call: ToolCall) -> Mapping[str, Any]:
    return {"name": call.name, "arguments": dict(call.arguments), "tokens_used": call.tokens_used}


def tool_call_from_dict(data: Mapping[str, Any]) -> ToolCall:
    return ToolCall(
        name=data["name"],
        arguments=data.get("arguments", {}),
        tokens_used=int(data.get("tokens_used", 0)),
    )


def tool_result_to_dict(result: ToolResult) -> Mapping[str, Any]:
    return {
        "name": result.name,
        "ok": result.ok,
        "content": result.content,
        "usage": dict(result.usage),
        "error": result.error,
    }


def tool_result_from_dict(data: Mapping[str, Any]) -> ToolResult:
    return ToolResult(
        name=data["name"],
        ok=bool(data["ok"]),
        content=data.get("content"),
        usage=data.get("usage", {}),
        error=data.get("error", ""),
    )


def step_record_to_dict(rec: StepRecord) -> Mapping[str, Any]:
    return {
        "iteration": rec.iteration,
        "tier": rec.tier,
        "action": rec.action,
        "tool": rec.tool,
        "tool_allowed": rec.tool_allowed,
        "tool_ok": rec.tool_ok,
        "parse_status": rec.parse_status,
        "confidence": rec.confidence,
        "tokens_used": rec.tokens_used,
        "content": rec.content,
    }


def step_record_from_dict(data: Mapping[str, Any]) -> StepRecord:
    return StepRecord(
        iteration=int(data["iteration"]),
        tier=data["tier"],
        action=data["action"],
        tool=data.get("tool", ""),
        tool_allowed=bool(data.get("tool_allowed", True)),
        tool_ok=data.get("tool_ok"),
        parse_status=data.get("parse_status", ParseStatus.NOT_APPLICABLE.value),
        confidence=data.get("confidence"),
        tokens_used=int(data.get("tokens_used", 0)),
        content=data.get("content"),
    )


def escalation_event_to_dict(ev: EscalationEvent) -> Mapping[str, Any]:
    return {
        "seq": ev.seq,
        "trigger": ev.trigger,
        "from_tier": ev.from_tier,
        "to_tier": ev.to_tier,
        "target_kind": ev.target_kind,
        "iteration": ev.iteration,
        "reason": ev.reason,
    }


def escalation_event_from_dict(data: Mapping[str, Any]) -> EscalationEvent:
    return EscalationEvent(
        seq=int(data["seq"]),
        trigger=data["trigger"],
        from_tier=data["from_tier"],
        to_tier=data.get("to_tier"),
        target_kind=data["target_kind"],
        iteration=int(data.get("iteration", 0)),
        reason=data.get("reason", ""),
    )


def decision_to_dict(decision: AgentDecision) -> Mapping[str, Any]:
    return {
        "loop_id": decision.loop_id,
        "tenant_id": decision.tenant_id,
        "agent_id": decision.agent_id,
        "task_id": decision.task_id,
        "outcome": decision.outcome,
        "content": decision.content,
        "confidence": decision.confidence,
        "tier": decision.tier,
        "iterations_used": decision.iterations_used,
        "tokens_used": decision.tokens_used,
        "failure_count": decision.failure_count,
        "reasons": list(decision.reasons),
        "reason": decision.reason,
        "escalation_events": [escalation_event_to_dict(e) for e in decision.escalation_events],
        "trace": [step_record_to_dict(r) for r in decision.trace],
        "started_at": decision.started_at,
        "finished_at": decision.finished_at,
    }


def decision_from_dict(data: Mapping[str, Any]) -> AgentDecision:
    return AgentDecision(
        loop_id=data["loop_id"],
        tenant_id=data["tenant_id"],
        agent_id=data["agent_id"],
        task_id=data["task_id"],
        outcome=data["outcome"],
        content=data.get("content"),
        confidence=float(data.get("confidence", 0.0)),
        tier=data.get("tier", ModelTier.TIER1.value),
        iterations_used=int(data.get("iterations_used", 0)),
        tokens_used=int(data.get("tokens_used", 0)),
        failure_count=int(data.get("failure_count", 0)),
        reasons=tuple(data.get("reasons", [])),
        reason=data.get("reason", ""),
        escalation_events=tuple(
            escalation_event_from_dict(e) for e in data.get("escalation_events", [])
        ),
        trace=tuple(step_record_from_dict(r) for r in data.get("trace", [])),
        started_at=data.get("started_at", ""),
        finished_at=data.get("finished_at", ""),
    )


def decisions_equal(a: AgentDecision, b: AgentDecision) -> bool:
    """Deep deterministic equality of two audit decisions.

    This is the loop's no-false-green determinism check: it can genuinely
    return ``False`` (any difference in outcome, content, confidence, tier,
    accounting, escalation events or the step trace makes two decisions
    unequal).  Serializing with sorted keys means byte-equal dicts imply
    byte-equal JSON, so the check is a strict structural equality, not a
    loosened comparison.
    """
    return decision_to_dict(a) == decision_to_dict(b)
