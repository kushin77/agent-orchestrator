"""Offline CLI demo of the durable engine core.

Run from anywhere:

    python3 engine/core/cli.py

It stands up an in-memory engine, provisions a tenant via the provisioning
saga, runs a provider-agnostic task workflow through an injected fake model
gateway, simulates an interruption + resume on a JSONL event log, and prints
the resulting transcripts.  Everything is offline and deterministic.
"""

from __future__ import annotations

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_engine_root = os.path.dirname(_here)  # engine/
if _engine_root not in sys.path:
    sys.path.insert(0, _engine_root)

from core.events import FileJsonlEventStore, InMemoryEventStore  # noqa: E402
from core.gateway_port import GatewayRequest, GatewayResult  # noqa: E402
from core.model import (  # noqa: E402
    Compensation,
    RetryPolicy,
    Step,
    StepKind,
    WorkflowKind,
    WorkflowSpec,
)
from core.runtime import Engine  # noqa: E402


def _make_gateway() -> object:
    class _FakeGateway:
        def dispatch(self, request: GatewayRequest) -> GatewayResult:
            return GatewayResult(
                outcome="success",
                content={"answer": "ok", "task": request.task_type},
                provider="fake",
                model="fake-model",
                cost=0.25,
                usage={"input_tokens": 10, "output_tokens": 5},
            )

    return _FakeGateway()


def _make_task_spec() -> WorkflowSpec:
    steps = [
        Step(
            step_id="s1",
            kind=StepKind.TASK,
            name="classify",
            handler="core.task",
            args={"agent_id": "classifier", "task_type": "classify-route"},
            compensate=Compensation(name="undo-s1", handler="core.noop", args={}),
            retry=RetryPolicy(max_attempts=3, initial_interval_seconds=1.0, backoff_coefficient=2.0),
        ),
        Step(step_id="s2", kind=StepKind.TASK, name="summarise", handler="core.task",
             args={"agent_id": "summariser", "task_type": "summarise"}),
        Step(step_id="s3", kind=StepKind.NOOP, name="done"),
    ]
    return WorkflowSpec(
        name="classify-and-summarise",
        kind=WorkflowKind.TASK_EXECUTION,
        steps=steps,
        sla_seconds=5.0,
    )


def _run_demo() -> int:
    # 1. Tenant provisioning saga (in-memory).
    engine = Engine(store=InMemoryEventStore(), gateway=_make_gateway())
    provision = engine.provision_tenant("acme", plan="standard")
    print("provisioning:", provision.status.value)
    print("  acme namespace queues:", engine.namespaces.require("acme").queues)

    # 2. Task workflow through the gateway port.
    task_wf = engine.run_workflow("acme", _make_task_spec(), inputs={"text": "billing outage"})
    print("task workflow:", task_wf.status.value)
    for entry in engine.namespaces.cost_report("acme")["breakdown"].items():
        print("  cost breakdown:", entry)

    # 3. Durable resume from a JSONL event log (interruption simulated).
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".demo-events.jsonl")
    store1 = FileJsonlEventStore(path)
    eng1 = Engine(store=store1, gateway=_make_gateway())
    eng1.namespaces.create("acme", plan="standard")
    wf = eng1.start_workflow("acme", _make_task_spec(), inputs={"text": "durable"})
    eng1.advance(wf, max_handlers=1)  # one step, then "process dies"
    print("interrupted mid-flight:", wf.status.value, "steps so far:",
          len([e for e in wf.events if e.kind.value == "step_succeeded"]))
    # A brand-new process over the same log resumes to completion.
    eng2 = Engine(store=FileJsonlEventStore(path), gateway=_make_gateway())
    eng2.namespaces.create("acme", plan="standard")
    resumed = eng2.resume("acme", wf.workflow_id)
    eng2.advance(resumed)
    print("resumed:", resumed.status.value, "events:", len(resumed.events))
    os.remove(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_demo())
