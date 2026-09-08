"""Shared helpers for the engine/loop pytest suite (imported as ``loop_support``).

Deterministic scripted actors, a canned tool executor, profile/policy
factories and a fixed clock.  Everything here is stateless or
deterministic-for-replay so the suite can assert byte-equal decisions.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from engine.loop.model import ModelTier, LoopOutcome
from engine.loop.policy import ComplexityPolicy, LoopPolicy, Profile
from engine.loop.runtime import Action, DecisionContext
from engine.loop.tools import FunctionExecutor, ToolRegistry, ToolSpec

TENANT = "tenant-acme"
AGENT = "support-bot"
TASK = "task-1"

FINAL_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}
ALLOWED_TOOLS = ("lookup", "read")
BASE_INPUT: Mapping[str, Any] = {"prompt": "resolve ticket T-1024"}


class FakeClock:
    """Deterministic clock (byte-stable timestamps)."""

    def __init__(self, iso: str = "2026-09-08T00:00:00+00:00") -> None:
        self._iso = iso

    def now_iso(self) -> str:
        return self._iso


# --------------------------------------------------------------------------
# Canned tool executor
# --------------------------------------------------------------------------


def canned_executor(name: str, arguments: Mapping[str, Any]) -> Any:
    """Deterministic executor for the demo tools (raises on unknown tools)."""
    if name == "lookup":
        key = arguments.get("key", "?")
        return {"found": True, "value": f"record:{key}"}
    if name == "read":
        return {"content": "canned knowledge"}
    raise RuntimeError(f"unknown tool {name!r}")


class CountingExecutor:
    """A stateful executor that returns a different value on every call.

    Deliberately NON-deterministic across runs — used by the negative
    determinism test to prove the runtime's ``decisions_equal`` check can
    genuinely fail when the injected tools are not replay-stable.
    """

    def __init__(self) -> None:
        self._calls = 0

    def execute(self, name: str, arguments: Mapping[str, Any]) -> Any:
        self._calls += 1
        return {"n": self._calls}


# --------------------------------------------------------------------------
# Factory helpers
# --------------------------------------------------------------------------


def make_profile(
    tier: ModelTier = ModelTier.TIER1,
    allowlist: tuple = ALLOWED_TOOLS,
    final_schema: Optional[Mapping[str, Any]] = FINAL_SCHEMA,
) -> Profile:
    return Profile(
        agent_id=AGENT,
        default_tier=tier,
        tool_allowlist=allowlist,
        final_schema=final_schema,
    )


def make_tools(
    allowlist: tuple = ALLOWED_TOOLS,
    executor: Any = None,
    specs: Optional[Mapping[str, ToolSpec]] = None,
) -> ToolRegistry:
    return ToolRegistry(
        executor=executor if executor is not None else FunctionExecutor(canned_executor),
        allowlist=allowlist,
        specs=specs,
    )


def make_policy(**kwargs: Any) -> LoopPolicy:
    return LoopPolicy(**kwargs)


def make_complexity() -> ComplexityPolicy:
    return ComplexityPolicy()


# --------------------------------------------------------------------------
# Scripted actors (deterministic, stateless)
# --------------------------------------------------------------------------


class ToolThenFinalActor:
    """Look the key up once, then answer confidently."""

    def decide(self, ctx: DecisionContext) -> Action:
        if not ctx.observations:
            return Action.tool_call("lookup", {"key": "T-1024"}, tokens_used=10)
        return Action.final({"answer": "resolved"}, confidence=0.98, tokens_used=10)


class FinalActor:
    """Immediately answer with a fixed confidence/payload."""

    def __init__(self, confidence: float, payload: Any = None, tokens: int = 10) -> None:
        self._confidence = confidence
        self._payload = payload if payload is not None else {"answer": "done"}
        self._tokens = tokens

    def decide(self, ctx: DecisionContext) -> Action:
        del ctx
        return Action.final(self._payload, confidence=self._confidence, tokens_used=self._tokens)


class TierAwareActor:
    """Mid-confidence on tier1, high-confidence on any higher tier."""

    def decide(self, ctx: DecisionContext) -> Action:
        if ctx.tier is ModelTier.TIER1:
            return Action.final({"answer": "draft"}, confidence=0.7, tokens_used=10)
        return Action.final({"answer": "verified"}, confidence=0.95, tokens_used=20)


class AlwaysToolActor:
    """Never produce a final answer; keep calling an allowlisted tool."""

    def decide(self, ctx: DecisionContext) -> Action:
        return Action.tool_call("lookup", {"key": "T-1024"}, tokens_used=5)


class DisallowedToolActor:
    """Always propose a tool that is NOT in the profile allowlist."""

    def decide(self, ctx: DecisionContext) -> Action:
        return Action.tool_call("shell_exec", {"cmd": "rm -rf /"}, tokens_used=5)


class InvalidFinalActor:
    """Always produce a final answer that fails the output schema."""

    def decide(self, ctx: DecisionContext) -> Action:
        return Action.final({"wrong": "field"}, confidence=0.95, tokens_used=10)


class InvalidThenValidActor:
    """First final fails the output schema, the second one is valid."""

    def decide(self, ctx: DecisionContext) -> Action:
        if ctx.iteration == 1:
            return Action.final({"wrong": "field"}, confidence=0.95, tokens_used=10)
        return Action.final({"answer": "recovered"}, confidence=0.98, tokens_used=10)


class BadArgsActor:
    """Call an allowlisted tool with arguments that fail its arg schema."""

    def decide(self, ctx: DecisionContext) -> Action:
        return Action.tool_call("read", {"path": 12345}, tokens_used=10)


# --------------------------------------------------------------------------
# Assertions helpers
# --------------------------------------------------------------------------


def final_content(decision) -> Any:
    assert decision.outcome == LoopOutcome.SUCCEEDED.value
    return decision.content


def make_loop(
    actor: Any,
    *,
    profile: Optional[Profile] = None,
    policy: Optional[LoopPolicy] = None,
    tools: Optional[ToolRegistry] = None,
    complexity: Any = None,
    clock: Any = None,
) -> Any:
    from engine.loop.runtime import AgentLoop

    return AgentLoop(
        actor=actor,
        tools=tools if tools is not None else make_tools(),
        profile=profile if profile is not None else make_profile(),
        policy=policy,
        complexity_scorer=complexity,
        clock=clock if clock is not None else FakeClock(),
    )


def run_loop(
    actor: Any,
    *,
    profile: Optional[Profile] = None,
    policy: Optional[LoopPolicy] = None,
    tools: Optional[ToolRegistry] = None,
    input_: Mapping[str, Any] = BASE_INPUT,
    complexity: Any = None,
) -> Any:
    loop = make_loop(actor, profile=profile, policy=policy, tools=tools,
                     complexity=complexity)
    return loop.run(
        input_,
        loop_id="loop:test",
        tenant_id=TENANT,
        agent_id=AGENT,
        task_id=TASK,
    )
