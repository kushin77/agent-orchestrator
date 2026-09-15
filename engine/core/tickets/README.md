# engine/core/tickets — tenant-facing agentic task manager

> Owner lane: **engine** (issue `kushin77/agent-orchestrator#634`, workbook-3,
> "tenant-facing agentic task manager — board → decomposition → durable
> execution"). Parent: `#631` (Paperclip enterprise-workbook four-pillar
> delta) → `#614`. Doctrine: [`../../../AGENTS.md`](../../../AGENTS.md),
> [`../../../docs/EXECUTION-PLAN.md`](../../../docs/EXECUTION-PLAN.md),
> [`../../../docs/ARCHITECTURE.md`](../../../docs/ARCHITECTURE.md).

This package is the **tenant-facing ticket lifecycle** the durable engine core
hosts. It is a *join*, not a reimplementation: it owns the lifecycle vocabulary
and reads the decomposition and the execution result out of the durable
transcript, reusing the two primitives that already exist —

* the **injected decomposer** ([`engine/multiagent/planner.py`](../../multiagent/planner.py),
  `HierarchicalPlanner` → bounded fan-out with `max_escalation_rounds`), and
* the **durable, tenant-namespaced, event-sourced engine** ([`core`](../)).

The gap this closes, as measured in the issue: ticket tooling existed only at
*repo-governance* level (`governance/board`, `governance/ticket`,
`governance/dispatch`) and decomposition existed only as a primitive
(`engine/multiagent/planner.py`, `fleet/brain.py`) — nothing joined them into a
tenant-facing task lifecycle that the engine executes durably.

## The lifecycle (30 seconds)

```
        workflow_started                 engineer_task_created   (anchor)
                │
        decompose ─┬─ plan_decomposed     created  ──▶ decomposed
        dispatch  ─┼─ ticket_dispatched   decomposed ─▶ dispatched
        execute   ─┼─ ticket_executed     dispatched ─▶ executed
        review    ─┼─ ticket_reviewed     executed  ──▶ reviewed
        close     ─┴─ engineer_task_closed reviewed ──▶ closed   (anchor)
                │
        workflow_completed
```

```python
import sys
sys.path.insert(0, "engine")            # engine/ has no __init__.py
sys.path.insert(0, ".")                 # so `engine.multiagent` resolves
from core.tickets import TicketRuntime, register_ticket_handlers, ticket_workflow

register_ticket_handlers(engine, decomposer=planner, max_escalation_rounds=2)
spec = ticket_workflow(
    tenant="acme", ticket_id="TCK-1042", mission_id="m-1042",
    objective="restore the nightly reconciliation feed",
    decomposer=plan_fn,                 # ctx -> FanOutPlan (per round)
    dispatch_lane="analyst",
    review={"reviewed_by": "ops-lead", "approved": True, "rationale": "verified"},
)
projection = TicketRuntime(engine, tenant="acme").run("TCK-1042", spec)
projection.state.value      # "closed"
projection.lifecycle()      # the six states, in order
projection.decomposition    # the planner's own numbers, projected from the log
```

Run the offline demo: `python3 engine/core/tickets/cli.py`.

## Why the anchors are the engine's own events

`created` and `closed` are the two `EventKind` members the engine already
declares — `ENGINEER_TASK_CREATED` and `ENGINEER_TASK_CLOSED`. Anchoring on
them is what makes "durable through the file-backed event store" true *by
construction*: there is no way to create or close a ticket except through the
engine's own append path, and the tenant scope travels in the event envelope
(and in each payload).

The `closed` anchor is appended by the `close` step handler itself, because the
engine appends `workflow_completed` immediately after that handler returns and
the projection **refuses any event after a terminal one**. It is the one place
this lane appends an event mid-step, and it is reachable only by a run that
actually reached the close step.

## The review gate

The review verdict is injected (and persisted verbatim, so it replays):

| Injected verdict | Result |
|---|---|
| none | the ticket still reaches `reviewed` (an un-reviewed run is recorded as such, gate open) |
| `approved=True` | the gate opens; the ticket closes |
| `approved=False` | the `close` step **fails closed** — the ticket ends at `reviewed` and the workflow never reports success |

A rejection is an explicit, recorded outcome, never a silent pass.

## Escalation bound

Decomposition goes through `HierarchicalPlanner`, whose
`max_escalation_rounds` guarantees termination: a round runs at most
`1 + max_escalation_rounds` times. The ticket lane additionally **validates the
planner's own accounting** — a report claiming more rounds than the bound
permits is refused (`StepFailure`), so an unbounded planner cannot smuggle
extra rounds through the lifecycle. See
[`tests/test_escalation_bounds.py`](tests/test_escalation_bounds.py).

## Layout

| Path | Purpose |
|---|---|
| [`model.py`](model.py) | `TicketState` (closed six-state vocabulary), the transition table, `DecompositionOutcome`, `TicketReview`, `TicketProjection`, `TicketLifecycleError`. |
| [`handlers.py`](handlers.py) | The five durable step handlers (`tickets.decompose` / `.dispatch` / `.execute` / `.review` / `.close`), the decomposer registry, `register_ticket_handlers`. |
| [`workflow.py`](workflow.py) | `ticket_workflow(...)` — the six-step `WorkflowKind.ENGINEER_TASK` spec. |
| [`runtime.py`](runtime.py) | `TicketRuntime` — start/run/resume, the event→state projection, tenant-scope enforcement. |
| [`cli.py`](cli.py) | Offline end-to-end demo (file-backed store + durability replay + bound demo). |
| [`tests/`](tests/) | happy path · escalation bounds · durability replay · lifecycle guards · CLI demo. |

## Verification

```bash
# from the repo root
python3 -m pytest engine/core/tickets/tests -q -p no:cacheprovider
python3 engine/core/tickets/cli.py
make verify
```

`engine/core/tickets` is a sub-suite of the declared `engine/core` suite, so it
is reached by `make gate` through that entry — no manifest edit is needed (and
`scripts/pytest-suites.txt` is owned by another lane regardless).

## Contract notes

* Import as `core.tickets` with `engine/` on `sys.path`, and the repo root on
  `sys.path` too so the `engine.multiagent` namespace resolves.
* A workflow spec is embedded in the `workflow_started` event, so it must stay
  **JSON-safe**: the live decomposer is registered out-of-band under a
  per-spec reference (`register_decomposer`) and never persisted.
* Ticket state is **derived from the log**, never stored beside it; the
  projection refuses a log that skips a state, revisits one, closes without a
  review, or changes tenant mid-flight.
* Everything is stdlib-only and offline (Python 3.14). No network, no
  containers, no model provider required to run or test.
