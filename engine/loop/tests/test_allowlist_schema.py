"""Tool allowlist + output-schema validation and the parse-failure policy.

Issue #23 acceptance #1: every step tool-call is validated against the
profile tool allowlist, outputs are schema-validated, and parse failures
follow an explicit policy (``retry`` or ``CANNOT-ASSESS``).
"""

from __future__ import annotations

from engine.loop.model import LoopOutcome, ParseStatus
from engine.loop.policy import Profile
from engine.loop.runtime import Action
from engine.loop.tools import FunctionExecutor, ToolSpec
from loop_support import (
    BadArgsActor,
    DisallowedToolActor,
    InvalidFinalActor,
    InvalidThenValidActor,
    make_policy,
    make_profile,
    make_tools,
    run_loop,
)

READ_SCHEMA: dict = {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
}


class ReadToolActor:
    """Try to read a path, then answer."""

    def decide(self, ctx) -> object:  # noqa: ANN001 - duck-typed DecisionContext
        if not ctx.observations:
            return Action.tool_call("read", {"path": "/etc/hosts"}, tokens_used=5)
        return Action.final({"answer": "done"}, confidence=0.98, tokens_used=5)


def _any_executor(name: str, arguments: dict) -> dict:
    return {"ran": name, "args": arguments}


def test_disallowed_tool_with_cannot_assess_policy_terminates_honestly():
    run = run_loop(
        DisallowedToolActor(),
        policy=make_policy(on_allowlist_violation="cannot_assess"),
    )
    decision = run.decision
    assert decision.outcome == LoopOutcome.CANNOT_ASSESS.value
    assert decision.trace[0].tool_allowed is False
    assert decision.trace[0].tool == "shell_exec"


def test_disallowed_tool_with_retry_policy_escalates_on_repeated_failure():
    run = run_loop(DisallowedToolActor(), policy=make_policy())
    decision = run.decision
    # Retry keeps proposing a banned tool -> repeated failures -> escalation.
    assert decision.outcome in (
        LoopOutcome.ESCALATED.value,
        LoopOutcome.FAILED.value,
    )
    assert any(
        e.trigger == "repeated_failure" for e in decision.escalation_events
    )
    assert all(r.tool_allowed is False for r in decision.trace)


def test_allowlist_violation_never_executes_the_tool():
    """A banned tool must never reach the executor (fail closed)."""
    calls: list[str] = []

    def spy_executor(name: str, arguments: dict) -> dict:
        calls.append(name)
        return {"ok": True}

    tools = make_tools(allowlist=("lookup",), executor=FunctionExecutor(spy_executor))
    run_loop(
        DisallowedToolActor(),
        tools=tools,
        profile=make_profile(allowlist=("lookup",)),
        policy=make_policy(on_allowlist_violation="cannot_assess"),
    )
    assert calls == []  # shell_exec never executed


def test_read_rejected_under_narrow_allowlist():
    """The registry allowlist gate (built from the profile) rejects 'read'."""
    tools = make_tools(allowlist=("lookup",))  # read is NOT allowlisted here
    run = run_loop(
        ReadToolActor(),
        tools=tools,
        profile=make_profile(allowlist=("lookup",)),
        policy=make_policy(on_allowlist_violation="cannot_assess"),
    )
    decision = run.decision
    assert decision.outcome == LoopOutcome.CANNOT_ASSESS.value
    assert decision.trace[0].tool == "read"
    assert decision.trace[0].tool_allowed is False


def test_profile_allows_reflects_the_allowlist_contract():
    wide = make_profile(allowlist=("lookup", "read"))
    narrow = make_profile(allowlist=("lookup",))
    unrestricted = make_profile(allowlist=())
    assert wide.allows("lookup") and wide.allows("read")
    assert narrow.allows("lookup") and not narrow.allows("read")
    assert unrestricted.allows("anything")  # absent allowlist = no restriction
    assert Profile.from_mapping(
        {"toolAllowlist": ["lookup"], "defaultModelTier": "tier2"}
    ).allows("lookup")


def test_invalid_final_with_cannot_assess_policy():
    run = run_loop(
        InvalidFinalActor(), policy=make_policy(on_parse_failure="cannot_assess")
    )
    decision = run.decision
    assert decision.outcome == LoopOutcome.CANNOT_ASSESS.value
    assert decision.trace[0].parse_status == ParseStatus.PARSE_FAILED.value


def test_invalid_final_with_retry_policy_recovers():
    run = run_loop(InvalidThenValidActor(), policy=make_policy())
    decision = run.decision
    assert decision.outcome == LoopOutcome.SUCCEEDED.value
    assert decision.trace[0].parse_status == ParseStatus.PARSE_FAILED.value
    assert decision.trace[1].parse_status == ParseStatus.PARSED.value
    assert decision.content == {"answer": "recovered"}


def test_tool_arg_schema_violation_is_a_parse_failure():
    specs = {"read": ToolSpec(name="read", arg_schema=READ_SCHEMA)}
    run = run_loop(
        BadArgsActor(),
        tools=make_tools(specs=specs),
        policy=make_policy(on_parse_failure="cannot_assess"),
    )
    decision = run.decision
    assert decision.outcome == LoopOutcome.CANNOT_ASSESS.value
    assert decision.trace[0].parse_status == ParseStatus.PARSE_FAILED.value


def test_empty_allowlist_admits_any_tool_the_executor_knows():
    """An absent allowlist means 'no restriction declared' (registry default)."""

    class AdHocActor:
        def decide(self, ctx) -> object:
            if not ctx.observations:
                return Action.tool_call("mystery_tool", {}, tokens_used=5)
            return Action.final({"answer": "ok"}, confidence=0.99, tokens_used=5)

    tools = make_tools(allowlist=(), executor=FunctionExecutor(_any_executor))
    run = run_loop(
        AdHocActor(),
        tools=tools,
        profile=make_profile(allowlist=()),
    )
    decision = run.decision
    assert decision.outcome == LoopOutcome.SUCCEEDED.value
    assert decision.trace[0].tool == "mystery_tool"
    assert decision.trace[0].tool_allowed is True
