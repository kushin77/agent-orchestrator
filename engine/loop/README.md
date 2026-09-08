# engine/loop — Deterministic agent-loop runtime (bounded actor loop + escalation)

> Owner lane: **engine** (issue `kushin77/agent-orchestrator#23`, "19
> Deterministic agent-loop runtime (bounded actor loop + escalation)", work
> item 19, phase 3). Parent: EPIC-00 (issue #4). Doctrine:
> [`../../AGENTS.md`](../../AGENTS.md),
> [`../../docs/EXECUTION-PLAN.md`](../../docs/EXECUTION-PLAN.md),
> [`../../docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md),
> [`../../docs/GOLDEN-RULES.md`](../../docs/GOLDEN-RULES.md).
> Cannibalization index: [`../../docs/CANNIBALIZATION.md`](../../docs/CANNIBALIZATION.md).
> The engine pillar's landing doc is [`../README.md`](../README.md).

This tree is the **deterministic agent-loop runtime** of the State-machine
execution pillar (pillar 3): the execution primitive for a *single agent
session*.  A session is a bounded iterative actor loop — **plan → act(tool)
→ observe → repeat**, at most `N` iterations per tier — with tool
allowlisting, schema-validated outputs, an explicit parse-failure policy, and
**guaranteed termination**: when a loop cannot resolve within its bounds
(step budget, token budget, confidence) it *escalates* to a higher-tier
persona or a human-in-the-loop, and it always records one
`AgentDecision` audit record per loop.  Deterministic means the same inputs
+ the same injected tool results always produce the same step trace — which
is what makes the loop replayable, resumable and testable offline.

The loop is the engine-core sibling for the `AGENT_LOOP` workflow/step kind:
[`engine/core`](../core/README.md) (issue #21) declares the kind and the
handler seam but does not own the loop's semantics; this lane registers a
`loop.agent_loop` handler on that seam ([`core_adapter.py`](core_adapter.py))
so the loop runs as a **durable, resumable workflow step**.  And
[`engine/queue`](../queue/README.md) (issue #22) tasks are claimed and
resolved by the loop through [`queue_adapter.py`](queue_adapter.py) — ack
only on a genuine SUCCEEDED decision, never a false pass.

## What the agent loop is (30 seconds)

```python
import sys
sys.path.insert(0, ".")                       # repo root (engine/ is a namespace)
from engine.loop import AgentLoop, Action
from engine.loop.model import ModelTier
from engine.loop.policy import Profile
from engine.loop.tools import FunctionExecutor, ToolRegistry

tools = ToolRegistry(executor=FunctionExecutor(my_executor),
                     allowlist=("lookup", "finalize"))
profile = Profile(agent_id="coder", default_tier=ModelTier.TIER1,
                  tool_allowlist=("lookup", "finalize"))

class Actor:                                   # injected, deterministic-for-replay
    def decide(self, ctx):
        if not ctx.observations:
            return Action.tool_call("lookup", {"key": "X"})
        return Action.final({"answer": "done"}, confidence=0.97)

run = AgentLoop(actor=Actor(), tools=tools, profile=profile).run(task_input)
run.finished            # True — guaranteed to terminate
run.decision.outcome    # "succeeded" (or escalated / cannot_assess / ...)
run.decision.trace      # the deterministic step transcript
```

A loop that cannot resolve never spins silently: it ends in one of the
closed [`LoopOutcome`](model.py) terminals with a recorded reason.

## Acceptance criteria (issue #23)

| Criterion | Where |
|---|---|
| Bound iterations; every step tool-call validated against the profile tool allowlist; schema-validated outputs; parse-failure policy (retry or CANNOT-ASSESS) | [`runtime.py`](runtime.py) `_advance`/`_act`, [`policy.py`](policy.py) `LoopPolicy`, [`tools.py`](tools.py) `ToolRegistry`, [`schema.py`](schema.py) `SchemaValidator`, [`tests/test_allowlist_schema.py`](tests/test_allowlist_schema.py) |
| Escalation: complexity/uncertainty → higher-tier persona (hermes) or human-in-loop above the confidence threshold (gmail 0.9/0.6 triage) | [`escalate.py`](escalate.py) `ComplexityScorer`/`EscalationEngine`, [`runtime.py`](runtime.py) confidence bands, [`tests/test_escalation.py`](tests/test_escalation.py) |
| Deterministic + resumable state handed to the durable engine (phase 3.1) | [`runtime.py`](runtime.py) checkpoints + [`model.py`](model.py) `decisions_equal`, [`core_adapter.py`](core_adapter.py) (durable `engine.core` step), [`tests/test_determinism.py`](tests/test_determinism.py) + [`tests/test_core_integration.py`](tests/test_core_integration.py) |
| Loop cost caps enforced (token budget + iteration cap) | [`policy.py`](policy.py) `token_budget`/`max_iterations`, [`runtime.py`](runtime.py) budget gates, [`tests/test_cost_caps.py`](tests/test_cost_caps.py) |
| `AgentDecision` audit record per loop (gmail pattern) | [`model.py`](model.py) `AgentDecision`, [`tests/test_audit_decision.py`](tests/test_audit_decision.py) |
| Proxy contract doc | this `README.md` |
| Tests | [`tests/`](tests/) (termination, allowlist/schema, escalation, caps, determinism, audit, core + queue integration) |

## Model overview

```
one agent session = AgentLoop(task_input, profile, policy)
   └─ actor (injected, deterministic-for-replay): plan -> Action
        ├─ TOOL_CALL -> allowlist gate (profile toolAllowlist) -> schema check
        │                -> ToolRegistry.run -> ToolResult (observe)
        └─ FINAL    -> schema-validated output -> confidence band
             >= 0.9 confirm   -> SUCCEEDED
             0.6 <= c < 0.9   -> escalate to higher-tier persona (repeat)
             <  0.6 (floor)   -> human-in-the-loop  (gmail 0.9/0.6 triage)
   bounds: <= max_iterations per tier, whole-loop token_budget, hard_step_bound
   monotonic tier escalation (tier1 -> tier2 -> tier3 -> human) -> guaranteed termination
   one AgentDecision per loop (audit record: outcome/trace/escalations/accounting)
```

* **Bounded / guaranteed termination.**  Every actor step is gated by the
  iteration budget of the current tier, the whole-loop token budget (checked
  before every step — a SUCCEEDED outcome can never exceed it), and the
  defensive `hard_step_bound`.  Escalation is **monotonic upward** through
  [`TIER_ORDER`](model.py) (never down, never a cycle) and then to a human or
  a terminal outcome, so a session that can never resolve ends in
  `ESCALATED` / `CANNOT_ASSESS` / `BUDGET_EXHAUSTED` / `FAILED`.  The
  negative tests drive an actor that *never* finalizes and assert it still
  terminates inside the bound (`tests/test_bounded_termination.py`).
* **Deterministic + resumable.**  The runtime is a pure per-step advance with
  no randomness; the actor and tool executor are injected and
  deterministic-for-replay.  Same inputs + same tool results ⇒ the same step
  trace and the same `AgentDecision` (`decisions_equal` is a strict,
  genuinely-failing equality).  A partially run loop returns a JSON
  checkpoint (`run(..., max_steps=k)` → `LoopRun.checkpoint()`) that
  `run(..., resume_from=...)` continues — interrupted+resumed == continuous
  (byte-identical), negative-tested with a non-replay tool executor in
  `tests/test_determinism.py`.
* **No false pass.**  A loop only records SUCCEEDED when a final answer meets
  the confidence threshold *and* its schema.  A parse failure follows the
  policy (`retry` bounded by the failure budget, or `CANNOT_ASSESS`); a tool
  outside the profile allowlist never reaches the executor; escalation hands
  the session off instead of pretending to resolve.

## Escalation contract

Escalation is decided deterministically by [`EscalationEngine`](escalate.py)
over the [`LoopPolicy`](policy.py):

| Trigger | Condition | Action |
|---|---|---|
| `complexity` | pre-loop score ≥ deep threshold (hermes 70) | start on a deeper tier (`ComplexityScorer`, deterministic) |
| `low_confidence` (mid) | `human_confidence ≤ c < confirm_confidence` | bump to the next tier persona; at the top tier → human |
| `low_confidence` (floor) | `c < human_confidence` (gmail 0.6) | hand to the human-in-the-loop |
| `repeated_failure` | `failure_count ≥ max_consecutive_failures` | bump tier, else human, else FAILED |
| `step_budget_exceeded` | tier iteration budget spent | bump tier, else human, else FAILED |
| `token_budget_exceeded` | whole-loop token cap hit | hand to human (a pricier tier is unaffordable), else BUDGET_EXHAUSTED |

When neither a higher tier nor a human is available the loop ends in an
honest terminal (`FAILED`/`BUDGET_EXHAUSTED`) — never a silent pass and
never an infinite loop.  Every escalation is recorded on the decision as an
[`EscalationEvent`](model.py) (seq-ordered, monotonic).

## Integration with engine/core and engine/queue

* **Durable engine step** ([`core_adapter.py`](core_adapter.py)) —
  `register_agent_loop_handler(engine, actor=..., tools=..., profile=...)`
  registers `loop.agent_loop` on an `engine.core.Engine`.  A workflow step
  `Step(kind=StepKind.AGENT_LOOP, handler="loop.agent_loop", args={...})`
  runs the full deterministic loop; the `AgentDecision` dict is recorded as
  the step output in the workflow event log.  `FAILED`/`BUDGET_EXHAUSTED`
  raise `StepFailure` so the engine records `STEP_FAILED` (its
  retry/dead-letter machinery can act); `SUCCEEDED`/`ESCALATED`/
  `CANNOT_ASSESS` complete the step with the recorded decision.  Because the
  loop is deterministic, a workflow interrupted before the loop step resumes
  over its JSONL log to the *identical* decision
  (`tests/test_core_integration.py`).
* **Queue task resolution** ([`queue_adapter.py`](queue_adapter.py)) —
  `LoopTaskProcessor(queue, agent_id, actor=..., tools=..., profile=...)`
  claims an `engine.queue` task, resolves its payload with the loop, and
  acks **only** on SUCCEEDED (optionally after an independent `verifier`);
  every other outcome fails the task with the decision's reason — the queue's
  never-a-false-PASS doctrine (`tests/test_queue_integration.py`).

## Layout

| Path | Purpose |
|---|---|
| [`model.py`](model.py) | Closed vocabulary (`ModelTier`, `LoopOutcome`, `ActionKind`, `EscalationTrigger`, `ParseStatus`) + value objects (`StepRecord`, `EscalationEvent`, `AgentDecision`, `ToolCall`/`ToolResult`) + JSON-safe (de)serialization + `decisions_equal`. |
| [`policy.py`](policy.py) | `LoopPolicy` (bounds/caps + gmail 0.9/0.6 thresholds), `ComplexityPolicy` (hermes 40/70 banding), `Profile` (registry-shaped view of `toolAllowlist`/`defaultModelTier`). |
| [`schema.py`](schema.py) | `SchemaValidator` — the schema-validated-output gate (honest, can fail). |
| [`tools.py`](tools.py) | `ToolRegistry` allowlist gate + `ToolExecutor`/`ToolSpec`/`FunctionExecutor` seam. |
| [`escalate.py`](escalate.py) | `ComplexityScorer` (deterministic routing) + `EscalationEngine`/`EscalationDecision`. |
| [`runtime.py`](runtime.py) | `AgentLoop` — the deterministic bounded actor loop (chunked + resumable), `Action`/`Actor`/`DecisionContext`. |
| [`core_adapter.py`](core_adapter.py) | Register the loop as a durable `engine.core` workflow step (`loop.agent_loop`). |
| [`queue_adapter.py`](queue_adapter.py) | `LoopTaskProcessor` — resolve `engine.queue` tasks with the loop. |
| [`cli.py`](cli.py) | Offline deterministic demo (`python3 -m engine.loop.cli`). |
| [`tests/`](tests/) | pytest suite (46 tests incl. negatives). |

## CLI demo

```bash
python3 -m engine.loop.cli            # all demos
python3 -m engine.loop.cli basic      # a successful bounded loop
python3 -m engine.loop.cli escalation # low-confidence human-in-loop hand-off
python3 -m engine.loop.cli determinism# two runs -> byte-equal decisions
python3 -m engine.loop.cli core       # the loop as a durable engine step
```

## Tests

```bash
python3 -m pytest engine/loop/tests -p no:cacheprovider -q
```

Run from the repo root.  The suite covers guaranteed termination (negative:
a never-final actor still terminates inside the bound), allowlist +
schema-validated outputs + parse-failure policies, the confidence/tier/
complexity escalation bands (including honest FAILED when no escalation
target remains), hard token + iteration caps, byte-identical determinism
(negative: a non-replay tool executor is caught by `decisions_equal`),
chunked resume == continuous run, the `AgentDecision` audit round-trip, and
both integrations (durable engine step + queue task resolution, never a
false pass).  Sibling engine suites are run separately (their tests share the
plain module name `conftest`).

## Provenance (cannibalized and adapted — see CANNIBALIZATION.md)

| Source (under `.research/**`) | Adapted into |
|---|---|
| `fleet/gmail-agent/src/agent/claude.ts` — <=10-iteration agentic tool loop, `OutputSchema.parse` on every model output, model tiers, per-decision audit log (`claude.draft_complete`) | `runtime.py` bounded loop + `max_iterations` default 10; `schema.py` parse discipline; `model.py` `AgentDecision`; `policy.py` 0.9/0.6 confidence thresholds |
| `leaderboard/lib/agent-loop.sh` + `scripts/agent/ds-actor.sh` — deterministic bounded actor/runner loop discipline | `runtime.py` pure per-step advance + chunked resume; bounded/guaranteed-termination invariant |
| `fleet/hermes-agents/src/hermes_agent/services/escalation_handler.py` — `EscalationTrigger` (confidence/error/timeout), tier escalation rules, escalate-at-max-tier guard | `model.py` `EscalationTrigger`/`ModelTier`, `escalate.py` `EscalationEngine` |
| `fleet/hermes-agents/src/hermes_agent/services/complexity_scorer.py` — component-weighted 0..100 score, fast(<40)/standard(<70)/deep path banding | `escalate.py` `ComplexityScorer`, `policy.py` `ComplexityPolicy` banding |
| `fleet/hermes-agents/src/hermes_agent/services/router.py` — task→tier/path routing | `runtime.py` `_initial_tier` complexity routing |
| `fleet/ollama/_legacy/group_a/agents/templates.py` — SME-persona specialization factory | tiered-persona escalation vocabulary (pattern only) |

## Contract notes

* Import as `engine.loop.*` from the repo root (`engine/` is a PEP-420
  namespace package).  Loop-internal imports are relative; the integration
  adapters additionally import `engine.core`/`engine.queue`, so they are only
  used where the repo root is on `sys.path`.
* The loop never imports a model provider or a tool sandbox — the actor and
  the tool registry are injected seams (mirroring how `engine.core` depends
  only on its `ModelGateway` port).  A live deployment injects the real
  gateway-backed actor + sandboxed tool executor.
* No prints/logging from library modules (deterministic); only `cli.py`
  prints.  Everything is stdlib-only and offline (Python 3.14).
* Determinism requires replay-stable injected actor + tool executor and an
  injected clock in tests; a production actor drives real model calls but the
  *loop's own* state machine stays pure.
