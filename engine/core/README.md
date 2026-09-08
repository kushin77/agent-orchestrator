# engine/core — Durable state-machine orchestration engine

> Owner lane: **engine** (issue `kushin77/agent-orchestrator#21`, "17 Durable
> state-machine orchestration engine (Temporal, namespaces, saga)", work item
> 17, phase 3). Parent: EPIC-00 (issue #4). Doctrine:
> [`../../AGENTS.md`](../../AGENTS.md),
> [`../../docs/EXECUTION-PLAN.md`](../../docs/EXECUTION-PLAN.md),
> [`../../docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md),
> [`../../docs/GOLDEN-RULES.md`](../../docs/GOLDEN-RULES.md).
> Cannibalization index: [`../../docs/CANNIBALIZATION.md`](../../docs/CANNIBALIZATION.md).
> The engine pillar's landing doc is [`../README.md`](../README.md).

This tree is the **durable orchestration engine core** of the State-machine
execution pillar (pillar 3). It executes multi-step agent workflows as
durable, tenant-namespaced state machines: a workflow is a list of steps run
through a deterministic scheduler; every mutation is an append-only event; a
workflow survives a process restart and **resumes mid-flight** by replaying
its event log. Saga workflows register a compensation per succeeded step and,
on a step failure, run those compensations in **reverse** order. The engine
calls the model gateway through an injected port — it never touches a
provider directly.

The engine is **Temporal-shaped but Temporal-free**: namespaces, task queues,
retry policies and sagas are first-class concepts here, and a
Temporal-transport contract ([`temporal.py`](temporal.py)) fixes the mapping —
but the durable substrate is the offline event-sourced store, so everything
runs deterministically with no server and no network.

## What the engine core is (30 seconds)

```python
import sys
sys.path.insert(0, "engine")            # engine/ has no __init__.py yet
from core import Engine, WorkflowSpec, Step, StepKind
from core.events import InMemoryEventStore
from core.namespaces import NamespaceRegistry

reg = NamespaceRegistry(); reg.create("acme")
engine = Engine(store=InMemoryEventStore(), namespaces=reg)  # gateway injected

wf = engine.run_workflow("acme", WorkflowSpec(
    name="my-run",
    steps=[Step(step_id="s1", kind=StepKind.NOOP),
           Step(step_id="s2", kind=StepKind.NOOP)],
))
wf.status.value          # "succeeded" — deterministic terminal state
wf.events                # the full append-only transcript
```

## Acceptance criteria (issue #21)

| Criterion | Where |
|---|---|
| Engine hosts workflows: task execution, agent-loop steps, multi-agent fan-out/join, tenant provisioning, retry with backoff + compensation | [`model.py`](model.py) `WorkflowKind`/`StepKind`, [`runtime.py`](runtime.py) `Engine`, [`runtime.py`](runtime.py) `provision_tenant` (provisioning saga), [`handlers.py`](handlers.py) (handler seam + built-ins), [`tests/test_workflow_hosting.py`](tests/test_workflow_hosting.py) |
| Namespace/queue isolation per tenant; per-workflow cost + SLA tracking | [`namespaces.py`](namespaces.py) `Namespace`/`NamespaceRegistry` (+ cost/SLA aggregates), [`tests/test_namespace_isolation.py`](tests/test_namespace_isolation.py) (negatives) |
| State persistence = durable (survives restart); resume mid-flight | [`events.py`](events.py) `FileJsonlEventStore`, [`workflow.py`](workflow.py) replay, [`runtime.py`](runtime.py) `resume`, [`tests/test_durable_resume.py`](tests/test_durable_resume.py) |
| Engine is provider-agnostic (calls the model gateway, never a provider directly) | [`gateway_port.py`](gateway_port.py) `ModelGateway`, [`handlers.py`](handlers.py) `core.task`, [`tests/test_gateway_cost_sla.py`](tests/test_gateway_cost_sla.py) |
| Proxy contract doc | this `README.md` |
| Tests | [`tests/`](tests/) (transitions, saga, durable resume, isolation, retry, hosting, gateway/cost/SLA) |

Sibling phase-3 lanes own the full **agent-loop** (engine/loop), **task
queue** (engine/queue), **multi-agent** (engine/multiagent) and **memory**
(engine/memory) subsystems; this core provides the durable workflow
scaffolding those lanes build on and register their real handlers into (see
*Handlers are the seam* below). This lane touches only `engine/core/**`.

## Model overview

```
tenant namespace (per-tenant isolation + task queues)
   └─ workflow (WorkflowSpec: ordered Steps, saga flag, SLA)
        └─ Step: kind (task | agent_loop | fan_out | join | provision | ...)
                 retry (RetryPolicy, non-saga only)
                 compensate (Compensation run in reverse on saga failure)
        events (append-only transcript)  -> projection (WorkflowExecution)
```

* **Namespace = tenant.** One `Namespace` per tenant with plan-derived
  retention and its own task queues (`tenant:default`, `tenant:high-priority`,
  `tenant:scheduled`) — the namespace-per-tenant pattern from
  `shared-temporal/patterns/multi_tenancy.go`. Every engine call is scoped to
  exactly one namespace; a workflow in tenant A is *unknown* in tenant B
  (no cross-namespace fallback, negative-tested).
* **State machine.** The workflow lifecycle is a closed, deterministic
  transition table in [`machine.py`](machine.py):
  `PENDING --workflow_started--> RUNNING` then terminal
  `SUCCEEDED` / `FAILED` / `ROLLED_BACK`. Illegal moves raise
  `InvalidTransitionError` — both while running and while replaying a stored
  log, so a corrupt history is refused.
* **Event sourcing.** A workflow's only state is its append-only event log
  ([`events.py`](events.py)); [`workflow.py`](workflow.py) projects events
  onto state (`from_events` replays a log). Durability = every event is
  flushed before the next handler runs, so an interruption between events
  loses nothing.
* **Saga.** When `spec.saga` is true, each step's compensation is registered
  only after that step *succeeds*; a step failure (single attempt — sagas
  never retry, matching `advanced_patterns.go`) runs the registered
  compensations in reverse completion order and ends `ROLLED_BACK`. A
  compensation that itself fails is recorded and the saga ends `FAILED`.
* **Provider-agnostic gateway.** [`gateway_port.py`](gateway_port.py) declares
  the duck-typed `ModelGateway` port (mirrors the merged
  [`gateway/proxy`](../../gateway/proxy/README.md) dispatch contract). The
  built-in `core.task` handler dispatches through whatever gateway is injected
  and never names a provider (structurally tested).
* **Cost + SLA.** Every successful step records a `CostEntry`; on terminal the
  workflow's cost and SLA outcome land on its own namespace's ledger
  (per-workflow cost tracking + SLA enforcement shapes from
  `shared-temporal/governance/cost-tracking.ts` / `sla-enforcement.ts`).

## Layout

| Path | Purpose |
|---|---|
| [`errors.py`](errors.py) | Typed error taxonomy (namespace, workflow, transition, handler, persistence). |
| [`model.py`](model.py) | Closed enums + value objects (`RetryPolicy`, `Step`, `Compensation`, `WorkflowSpec`, `CostEntry`) and JSON-safe spec (de)serialization. |
| [`machine.py`](machine.py) | Deterministic workflow-level transition table + guard. |
| [`namespaces.py`](namespaces.py) | `Namespace` (per-tenant, queues, cost/SLA aggregates) + `NamespaceRegistry`. |
| [`events.py`](events.py) | `EventRecord` + `EventStore` seam: `InMemoryEventStore`, `FileJsonlEventStore` (append-only JSONL, corrupt-line refusal). |
| [`gateway_port.py`](gateway_port.py) | `ModelGateway` protocol + `GatewayRequest`/`GatewayResult` (provider-agnostic seam). |
| [`workflow.py`](workflow.py) | `WorkflowExecution` — live projection + deterministic replay (`from_events`/`apply`). |
| [`handlers.py`](handlers.py) | `Handler` (run/compensate) seam, `StepContext`, built-in handlers (`core.task`, `core.noop`, `core.provision_*`, ...). |
| [`runtime.py`](runtime.py) | `Engine` — start/advance/resume, saga rollback, retry/backoff, provisioning saga, cost/SLA recording. |
| [`temporal.py`](temporal.py) | Temporal-transport contract: `TemporalAdapter` protocol + concept mapping table (offline; no server required). |
| [`cli.py`](cli.py) | Offline demo (`python3 engine/core/cli.py`). |
| [`tests/`](tests/) | pytest suite (see *Verification*). |

## Handlers are the seam

`Engine.register_handler(key, handler)` binds a `Handler(run=..., compensate=...)`.
A step names a handler by key (or falls back to the built-in for its kind).
The core ships `core.task` (gateway dispatch), trivial `core.noop`/`core.log`/
`core.notify`, and `core.provision_*` (tenant provisioning saga effects).
Sibling lanes register their own `agent_loop` / `fan_out` / `join` handlers on
this seam — the engine already hosts those workflow/step kinds (they are
closed enum members, validated, run durably and saga-capably), it simply does
not own their multi-agent semantics. A step whose handler is missing fails
closed at `start_workflow` (never silently skipped).

## Deterministic replay and resume

`advance(execution, max_handlers=N)` bounds handler calls per invocation, so
callers simulate an interruption by advancing a step at a time (each event
persisted before the next). After a "crash", a fresh engine over the same
JSONL log resumes:

```python
store = FileJsonlEventStore("run.jsonl")
eng2  = Engine(store=store, ...)
resumed = eng2.resume("acme", wf.workflow_id)   # rebuilt from the log
eng2.advance(resumed)                            # continues mid-flight
```

The suite proves an interrupted+resumed run and an uninterrupted control run
produce the **same event log** (deterministic replay), that completed steps
are **never re-executed**, that a pure `from_events` projection equals the
live object, and that a crash *during* saga compensation resumes and finishes
the remaining reverse-order compensations.

## Importing and running the tests

`engine/core` is importable as `core` when `engine/` is on `sys.path` (the
tests arrange this in `tests/conftest.py`):

```bash
# from the repo root
python3 -m pytest engine/core/tests -q -p no:cacheprovider
make verify   # repo gate must stay green (8/8: shell, yaml, json, docs, secrets, flags, cloudbuild, terraform)
python3 engine/core/cli.py   # offline demo
```

## Provenance (cannibalized and adapted — see CANNIBALIZATION.md)

| Source (under `.research/fleet/**`) | Adapted into |
|---|---|
| `shared-temporal/patterns/multi_tenancy.go` (ProvisionTenantWorkflow, namespace-per-tenant, per-tenant task queues) | `namespaces.py` `Namespace`/queues, `runtime.py` `provision_tenant` (provisioning saga) |
| `shared-temporal/patterns/advanced_patterns.go` (Saga — compensation registered per succeeded step, run in reverse; no retries in a saga) | `model.py` `Compensation`, saga flag, `runtime.py` rollback logic |
| `shared-temporal/patterns/saas_subscriptions.go` | per-plan retention mapping in `namespaces.py` |
| `shared-temporal/governance/cost-tracking.ts` | `CostEntry` + per-namespace cost report |
| `shared-temporal/governance/sla-enforcement.ts` | `sla_seconds` + per-namespace SLA report |
| `git-rca-workspace/src/core/workflow_engine.py` (+ `dprs` duplicate) | workflow status vocabulary, event-sourced transcript, `WorkflowExecution` projection |
| `leaderboard/lib/agent-loop.sh` + `scripts/agent/ds-actor.sh` | deterministic actor/runner loop discipline (bounded, resumable advancement) |
| `CMR board/epics/EPIC-19-ephemeral-mechanics.md` | ephemeral state discipline: nothing outlives the workflow; the event log is the only persistent artifact |

Temporal transport itself (server-backed execution) ships later, flag-gated
OFF (AO-GR-6), behind the [`temporal.py`](temporal.py) contract.

## Contract notes

* Import as `core` with `engine/` on `sys.path` (mirrors `proxy` under
  `gateway/`, `service` under `registry/`). `engine/` has no `__init__.py`
  yet; `engine/core` is a regular package.
* Workflow ids are unique per namespace; the same id in two namespaces is two
  independent workflows.
* No prints/logging from library modules (deterministic); only `cli.py` prints
  (as its function).
* Everything is stdlib-only and offline (Python 3.14). No network, no
  containers, no Temporal server required to run or test.
