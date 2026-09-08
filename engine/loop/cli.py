#!/usr/bin/env python3
"""engine/loop — offline CLI demo of the deterministic agent-loop runtime.

Issue kushin77/agent-orchestrator#23.  Fully OFFLINE and deterministic: a
scripted actor + a canned tool executor drive the same :class:`AgentLoop`
code path a live model gateway + tool sandbox would.  Every demo prints
deterministic JSON to stdout and exits 0.

Run from the repo root (so ``engine.loop`` / ``engine.core`` resolve via the
PEP-420 namespace):

    python3 -m engine.loop.cli            # all demos
    python3 -m engine.loop.cli basic      # a successful bounded loop
    python3 -m engine.loop.cli escalation # low-confidence human-in-loop hand-off
    python3 -m engine.loop.cli determinism# two runs -> byte-equal decisions
    python3 -m engine.loop.cli core       # the loop hosted as a durable engine step

Run from anywhere (self-bootstrapping):

    python3 engine/loop/cli.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))  # .../engine/loop
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from engine.loop.escalate import ComplexityScorer  # noqa: E402
from engine.loop.model import (  # noqa: E402
    AgentDecision,
    ModelTier,
    decision_to_dict,
    decisions_equal,
)
from engine.loop.policy import LoopPolicy, Profile  # noqa: E402
from engine.loop.runtime import Action, AgentLoop, DecisionContext  # noqa: E402
from engine.loop.tools import FunctionExecutor, ToolRegistry  # noqa: E402

FINAL_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "escalate": {"type": "boolean"},
    },
    "required": ["reply"],
}


class FixedClock:
    """Deterministic clock so demo output is byte-stable."""

    def now_iso(self) -> str:
        return "2026-09-08T00:00:00+00:00"


class TriageActor:
    """A stateless scripted actor: look the ticket up, then answer confidently."""

    def decide(self, ctx: DecisionContext) -> Action:
        if not ctx.observations:
            return Action.tool_call(
                "lookup_ticket", {"ticket": "T-1024"}, tokens_used=24
            )
        # Deterministic canned final (a JSON string, as a raw model output).
        payload = '{"reply": "Your refund for T-1024 is processing.", "escalate": false}'
        return Action.final(payload, confidence=0.97, tokens_used=41)


class LowConfidenceActor:
    """A scripted actor that always answers with low confidence."""

    def decide(self, ctx: DecisionContext) -> Action:
        payload = '{"reply": "I am not sure about this refund.", "escalate": true}'
        return Action.final(payload, confidence=0.42, tokens_used=30)


def _canned_lookup(name: str, arguments: dict) -> object:
    if name == "lookup_ticket":
        ticket = arguments.get("ticket", "?")
        return {"ticket": ticket, "status": "open", "priority": "high"}
    raise RuntimeError(f"unknown tool {name!r}")


def make_profile() -> Profile:
    return Profile(
        agent_id="support-bot",
        default_tier=ModelTier.TIER1,
        tool_allowlist=("lookup_ticket", "send_reply"),
        final_schema=FINAL_SCHEMA,
    )


def make_runtime(actor: object) -> AgentLoop:
    tools = ToolRegistry(
        executor=FunctionExecutor(_canned_lookup),
        allowlist=("lookup_ticket", "send_reply"),
    )
    return AgentLoop(
        actor=actor,
        tools=tools,
        profile=make_profile(),
        policy=LoopPolicy(),
        complexity_scorer=ComplexityScorer(),
        clock=FixedClock(),
    )


def demo_basic() -> AgentDecision:
    run = make_runtime(TriageActor()).run(
        {"prompt": "Reply to the customer about ticket T-1024."},
        loop_id="demo:basic",
        tenant_id="acme",
        agent_id="support-bot",
        task_id="T-1024",
    )
    return run.decision


def demo_escalation() -> AgentDecision:
    run = make_runtime(LowConfidenceActor()).run(
        {"prompt": "Reply to the customer about ticket T-1024."},
        loop_id="demo:escalation",
        tenant_id="acme",
        agent_id="support-bot",
        task_id="T-1024",
    )
    return run.decision


def demo_determinism() -> dict:
    first = demo_basic()
    second = demo_basic()
    return {"deterministic": bool(decisions_equal(first, second))}


def demo_core() -> dict:
    """Host the loop as a durable engine.core workflow step."""
    from engine.core.events import InMemoryEventStore
    from engine.core.model import Step, StepKind, WorkflowKind, WorkflowSpec
    from engine.core.namespaces import NamespaceRegistry
    from engine.core.runtime import Engine

    from engine.loop.core_adapter import HANDLER_KEY, register_agent_loop_handler

    namespaces = NamespaceRegistry()
    namespaces.create(namespace_id="acme", created_at=FixedClock().now_iso())
    engine = Engine(store=InMemoryEventStore(), namespaces=namespaces, clock=FixedClock())
    register_agent_loop_handler(
        engine,
        actor=TriageActor(),
        tools=ToolRegistry(
            executor=FunctionExecutor(_canned_lookup),
            allowlist=("lookup_ticket", "send_reply"),
        ),
        profile=make_profile(),
    )
    wf = engine.run_workflow(
        "acme",
        WorkflowSpec(
            name="agent-run",
            kind=WorkflowKind.AGENT_LOOP,
            steps=[
                Step(
                    step_id="loop-1",
                    kind=StepKind.AGENT_LOOP,
                    handler=HANDLER_KEY,
                    args={"task_input": {"prompt": "Reply about ticket T-1024."}},
                )
            ],
        ),
        inputs={"prompt": "Reply about ticket T-1024."},
        workflow_id="wf-agent-1",
    )
    output = None
    for event in wf.events:
        if event.kind.value == "step_succeeded":
            output = event.payload.get("output")
    return {
        "workflow_id": wf.workflow_id,
        "status": wf.status.value,
        "loop_handler": HANDLER_KEY,
        "decision_outcome": output.get("outcome") if isinstance(output, dict) else None,
    }


_DEMOS = {
    "basic": demo_basic,
    "escalation": demo_escalation,
    "determinism": demo_determinism,
    "core": demo_core,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="engine.loop offline demo (issue #23)")
    parser.add_argument(
        "demo",
        nargs="?",
        default="all",
        choices=["all", *sorted(_DEMOS)],
        help="which demo to run (default: all)",
    )
    args = parser.parse_args()
    chosen = sorted(_DEMOS) if args.demo == "all" else [args.demo]
    for name in chosen:
        result = _DEMOS[name]()
        if isinstance(result, AgentDecision):
            rendered = decision_to_dict(result)
        else:
            rendered = result
        print(json.dumps({"demo": name, **rendered}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
