"""Determinism + resumability: same inputs + same tool results -> same trace.

Issue #23 acceptance #3 (deterministic + resumable state handed to the
durable engine).  The positive cases prove two independent runs over the same
actor + tool results yield byte-identical decisions; the negative case proves
:func:`decisions_equal` genuinely fails when the injected tools are not
replay-stable; the chunked cases prove an interrupted loop resumes to the
same decision as an uninterrupted control.
"""

from __future__ import annotations

import json

from engine.loop.model import decision_to_dict, decisions_equal
from loop_support import (
    AGENT,
    AlwaysToolActor,
    BASE_INPUT,
    CountingExecutor,
    TASK,
    TENANT,
    ToolThenFinalActor,
    make_loop,
    make_policy,
    make_tools,
)

IDS = {"loop_id": "loop:det", "tenant_id": TENANT, "agent_id": AGENT, "task_id": TASK}


def _run(loop):
    return loop.run(BASE_INPUT, **IDS)


def test_two_runs_produce_byte_identical_decisions():
    first = _run(make_loop(ToolThenFinalActor()))
    second = _run(make_loop(ToolThenFinalActor()))
    assert first.finished and second.finished
    assert decisions_equal(first.decision, second.decision)
    assert json.dumps(decision_to_dict(first.decision), sort_keys=True) == json.dumps(
        decision_to_dict(second.decision), sort_keys=True
    )


def test_nondeterministic_tools_are_caught():
    """Negative case: decisions_equal can genuinely fail on non-replay tools."""
    shared_executor = CountingExecutor()
    tools = make_tools(executor=shared_executor)
    first = _run(make_loop(ToolThenFinalActor(), tools=tools))
    second = _run(make_loop(ToolThenFinalActor(), tools=tools))  # counter advanced
    assert decisions_equal(first.decision, second.decision) is False
    assert first.decision.trace[0].content != second.decision.trace[0].content


def test_chunked_resume_equals_continuous_run():
    chunked_loop = make_loop(ToolThenFinalActor())
    partial = chunked_loop.run(BASE_INPUT, max_steps=1, **IDS)
    assert partial.finished is False
    assert partial.steps_executed == 1
    resumed = chunked_loop.run(BASE_INPUT, resume_from=partial.checkpoint(), max_steps=1)
    assert resumed.finished is True

    continuous = _run(make_loop(ToolThenFinalActor()))
    assert decisions_equal(resumed.decision, continuous.decision)
    assert json.dumps(decision_to_dict(resumed.decision), sort_keys=True) == json.dumps(
        decision_to_dict(continuous.decision), sort_keys=True
    )


def test_multi_chunk_resume_reaches_the_same_decision():
    loop = make_loop(AlwaysToolActor(), policy=make_policy(max_iterations=3))
    checkpoint = None
    chunks = 0
    while True:
        result = loop.run(
            BASE_INPUT, resume_from=checkpoint, max_steps=2, **IDS
        )
        chunks += 1
        if result.finished:
            break
        checkpoint = result.checkpoint()
    assert chunks > 1  # the run really was interrupted and resumed
    continuous = _run(make_loop(AlwaysToolActor(), policy=make_policy(max_iterations=3)))
    assert decisions_equal(result.decision, continuous.decision)


def test_checkpoint_round_trips_through_json():
    loop = make_loop(ToolThenFinalActor())
    partial = loop.run(BASE_INPUT, max_steps=1, **IDS)
    checkpoint = partial.checkpoint()
    # JSON round trip: a persisted checkpoint must resume identically.
    persisted = json.loads(json.dumps(checkpoint, sort_keys=True))
    resumed = loop.run(BASE_INPUT, resume_from=persisted, max_steps=1)
    assert resumed.finished is True
    continuous = _run(make_loop(ToolThenFinalActor()))
    assert decisions_equal(resumed.decision, continuous.decision)
