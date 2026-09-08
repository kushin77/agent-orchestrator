# engine/multiagent — Multi-agent orchestration (hierarchical lanes + consensus/voting)

> Owner lane: **engine** (issue `kushin77/agent-orchestrator#24`, "20
> Multi-agent orchestration (hierarchical lanes + consensus/voting)", work
> item 20, phase 3). Parent: EPIC-00 (issue #4). Doctrine:
> [`../../AGENTS.md`](../../AGENTS.md),
> [`../../docs/EXECUTION-PLAN.md`](../../docs/EXECUTION-PLAN.md),
> [`../../docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md),
> [`../../docs/GOLDEN-RULES.md`](../../docs/GOLDEN-RULES.md).
> Cannibalization index:
> [`../../docs/CANNIBALIZATION.md`](../../docs/CANNIBALIZATION.md). The engine
> pillar's landing doc is [`../README.md`](../README.md).

This tree is the **multi-agent orchestration** subsystem of the
State-machine execution pillar (pillar 3). It sits on top of the durable
engine core ([`../core/`](../core/README.md), issue #21) and the task queue
([`../queue/`](../queue/README.md), issue #22) and models two coordination
patterns over an injected, duck-typed agent-runner seam:

* **Hierarchical** — a *planner/lead lane* decomposes a mission into
  specialist subtasks, fans them out to *specialist lanes* (bounded,
  allSettled semantics) and aggregates their results; a required specialist
  failure (or a result below the planner's confidence bar) **escalates back
  to the planner** for re-planning, within a hard round budget.
* **Peer** — a *consensus/voting* protocol over voter lanes: every voter
  casts a *weighted* vote (approve / reject / abstain), a threshold rule
  (majority / supermajority / unanimous) plus a quorum decide the outcome,
  and a ballot **always** lands on a defined verdict — PASSED, REJECTED or
  NO_CONSENSUS — never a silent pass.

Everything is **deterministic and offline**: the only way this package talks
to an agent is the injected runner
`run_agent(agent_id, task) -> AgentResult`, so tests script every result and
no model call, network or container is ever required (Python 3.14 stdlib +
PyYAML).

## What it looks like (30 seconds)

```python
import sys
sys.path.insert(0, ".")               # repo root on sys.path (PEP-420 engine/)

from engine.multiagent import HierarchicalPlanner, ScriptedRunner, ok_result
from engine.multiagent.model import (
    FanOutPlan, Lane, LaneRole, Mission, Subtask,
)

planner_lane = Lane("planner", LaneRole.PLANNER, agent_ids=("planner-1",))
specialists  = [
    Lane("facts",  LaneRole.SPECIALIST, agent_ids=("fact-agent",)),
    Lane("review", LaneRole.SPECIALIST, agent_ids=("review-agent",)),
]
runner = (ScriptedRunner()
          .on("fact-agent",   "t1", ok_result("fact-agent",   "t1", output={"facts": 3}))
          .on("review-agent", "t2", ok_result("review-agent", "t2", output={"risk": "low"})))

mission = Mission(mission_id="m1", objective="assess launch")

def decomposer(ctx):  # the planner's (deterministic) decomposition
    return FanOutPlan(
        mission_id=mission.mission_id,
        planner_lane_id=planner_lane.lane_id,
        subtasks=(
            Subtask(subtask_id="t1", objective="check facts", lane_id="facts"),
            Subtask(subtask_id="t2", objective="review risk", lane_id="review"),
        ),
    )

planner = HierarchicalPlanner(runner, planner_lane=planner_lane, specialist_lanes=specialists)
report  = planner.run(mission, decomposer)
print(report.aggregate.summary)   # deterministic planner aggregation
```

## The two patterns

### 1. Hierarchical orchestration (`planner.py` + `fanout.py`)

```
mission
  └─ planner lane (PLANNER): decompose -> FanOutPlan (bounded)
        └─ fan-out to specialist lanes (SPECIALIST): allSettled dispatch
              ├─ specialist-a (facts)    -> AgentResult
              ├─ specialist-b (review)   -> AgentResult
              └─ specialist-c (build)    -> AgentResult
        └─ required failure / low confidence
              └─ escalate back to the planner (re-plan) — bounded rounds
  └─ planner aggregation (findings / failures / escalated) -> HierarchyReport
```

* **Lanes are disjoint worker pools** (`Lane`, `validate_lanes`): no two
  lanes share an agent, so work dispatched to different lanes can never
  collide — the model-level collision-avoidance / disjoint-work guarantee
  (leaderboard fanout doctrine). A *planner* lane never executes its own
  subtasks (`validate_plan` fails closed).
* **Bounded fan-out** (`FanOutDispatcher`, `HierarchyConfig.max_fan_out`): a
  plan that exceeds the cap raises `FanOutLimitError` — work is never
  silently trimmed.
* **allSettled semantics**: every subtask runs; a crashing/failing
  specialist is recorded as a failed `FanOutItem` and never aborts the round
  (gov-ai-scout `allSettled` subtask pipeline pattern).
* **Escalation back to the planner**: a *required* subtask that fails (or
  lands below `require_confidence`) triggers a re-plan through the same
  `decomposer`, bounded by `max_escalation_rounds`. The planner's
  `EscalationContext` exposes the mission + prior plan/report so it can
  re-plan around the exact failures. Leftover unresolved required subtasks
  are surfaced in `AggregationReport.escalated` — never dropped, never a
  silent pass.
* Decomposition is an injected `decomposer(ctx) -> FanOutPlan` callable; in
  production it is typically an agent loop exposed through the runner seam
  (see `loop_seam.py`).

### 2. Consensus / voting (`consensus.py`)

```
proposal (ConsensusRequest)
  └─ voter lanes (VOTER): each agent casts a weighted Vote via the runner
        ├─ approve (weight = expertise)      tally (pure)
        ├─ reject
        └─ abstain  -> quorum check
  └─ tally_votes()  -> ConsensusResult (ALWAYS defined)
        PASSED        (threshold met)
        REJECTED      (reject side clears the bar: rejected / vetoed)
        NO_CONSENSUS  (all_abstained / no_quorum / tie / unresolved)
```

* **N voters, threshold + quorum** (`ConsensusConfig`): `rule` is
  MAJORITY (> half), SUPERMAJORITY (>= 2/3) or UNANIMOUS (no rejects);
  `quorum_ratio` × registered voters must cast a non-abstaining vote.
* **Weighted expertise**: each vote carries a `weight`; the decision is made
  on approve-vs-reject *weight* (news-feed-engine majority-vote + weighted
  expertise).
* **Never a silent pass**: every non-passing ballot is a *defined* outcome
  with a machine-readable `reason` — `all_abstained`, `no_quorum`, `tie`
  (exact 50/50 under majority), `unresolved` (a supermajority split that
  meets neither bar), `vetoed` (unanimous blocked) or `rejected`. A voter
  agent that crashes or returns an unreadable vote is recorded as an
  explicit ABSTAIN (fail-closed), which the default quorum turns into
  NO_CONSENSUS rather than a pass.
* `tally_votes()` is the pure decision core (no I/O); `run_consensus()`
  obtains the votes through the runner seam. Both are fully unit-tested
  including the no-consensus negatives.

`MultiAgentOrchestrator` (`orchestrator.py`) composes both patterns into one
deterministic `OrchestrationReport` (hierarchy + optional verdict).

## Integration with the sibling engine subsystems

| Seam | Purpose |
|---|---|
| [`core_seam.py`](core_seam.py) | Registers the real `FAN_OUT`/`JOIN` step handlers (`multiagent.fan_out`, `multiagent.join`) on the **engine core** handler seam (issue #21). `fan_join_workflow()` builds a durable `WorkflowKind.FAN_OUT_JOIN` spec; the JOIN step rebuilds its aggregation from the *persisted* fan-out output in the event log — a resumed run reconstructs it with no hidden in-memory state (`tests/test_core_integration.py` proves crash + resume). |
| [`queue_seam.py`](queue_seam.py) | `QueueFanOut` turns a planner plan into engine/queue tasks and runs a queue `Worker`: fan-out tasks are **enqueued, claimed (leased), run, and acked/failed** through the real lifecycle (issue #22). A failed agent is never recorded SUCCEEDED (never-a-false-PASS) — `tests/test_queue_integration.py`. |
| [`loop_seam.py`](loop_seam.py) | **engine/loop (issue #23) merged on master in this wave.** The multi-agent contract is the duck-typed seam `run_agent(agent_id, task) -> AgentResult`. `AgentLoopRunner` adapts a real `engine/loop.AgentLoop`, `decision_to_agent_result` maps its `AgentDecision` audit record onto `AgentResult` (a loop that ended ESCALATED / CANNOT_ASSESS / BUDGET_EXHAUSTED / FAILED is never a silent multi-agent success), and `loop_runner_if_present()` discovers a duck-typed runner if one is exposed — `tests/test_loop_integration.py` runs against the *real* merged `engine.loop` (importorskip-guarded), `tests/test_loop_seam.py` covers the duck-typed adapter. |

## Layout

| Path | Purpose |
|---|---|
| [`model.py`](model.py) | Closed enums + JSON-safe value objects: lanes, missions, subtasks, agent tasks/results, fan-out plans/reports, vote/consensus vocabulary, hierarchy + orchestration reports, `HierarchyConfig`. |
| [`runner.py`](runner.py) | The injected `AgentRunner` seam (`run_agent`), `ScriptedRunner` (deterministic result injection), `coerce_agent_result` (fail-closed coercion). |
| [`fanout.py`](fanout.py) | `FanOutDispatcher` — bounded allSettled dispatch + `validate_plan` disjoint-work checks. |
| [`planner.py`](planner.py) | `HierarchicalPlanner` — decompose -> fan-out -> escalate (bounded) -> aggregate. |
| [`consensus.py`](consensus.py) | `tally_votes` (pure) + `run_consensus` (runner-driven ballot). |
| [`orchestrator.py`](orchestrator.py) | `MultiAgentOrchestrator` — composition of hierarchy + optional consensus. |
| [`core_seam.py`](core_seam.py) | engine/core durable `FAN_OUT`/`JOIN` handler registration + workflow builder. |
| [`queue_seam.py`](queue_seam.py) | engine/queue fan-out (`QueueFanOut`, `QueueFanOutReport`). |
| [`loop_seam.py`](loop_seam.py) | engine/loop (issue #23) duck-typed adapter + `AgentDecision` -> `AgentResult` mapping + discovery. |
| [`tests/`](tests/) | pytest suite (75 tests incl. negatives + falsifiability). |

## Acceptance criteria (issue #24) — where each is met

| Criterion | Where |
|---|---|
| Hierarchical orchestration: planner/lead lane -> specialist lanes -> aggregation | [`planner.py`](planner.py) `HierarchicalPlanner`, [`tests/test_hierarchical_fanout.py`](tests/test_hierarchical_fanout.py) |
| No two lanes share a file at the model level; planner fans out to specialists at runtime | [`model.py`](model.py) `Lane`/`validate_lanes`, [`fanout.py`](fanout.py) `validate_plan` |
| Consensus/voting: N voters, threshold/quorum, tie handling, defined no-consensus | [`consensus.py`](consensus.py), [`tests/test_consensus_threshold.py`](tests/test_consensus_threshold.py), [`tests/test_no_consensus.py`](tests/test_no_consensus.py) |
| Bounded fan-out + escalation back to the planner | [`fanout.py`](fanout.py) `FanOutLimitError`, [`planner.py`](planner.py), [`tests/test_bounded_fanout.py`](tests/test_bounded_fanout.py), [`tests/test_escalation.py`](tests/test_escalation.py) |
| Deterministic + testable with injected runner results | [`runner.py`](runner.py) `ScriptedRunner`, all suites |
| Integration with engine/core steps + engine/queue (fan-out enqueued/claimed) | [`core_seam.py`](core_seam.py), [`queue_seam.py`](queue_seam.py), [`tests/test_core_integration.py`](tests/test_core_integration.py), [`tests/test_queue_integration.py`](tests/test_queue_integration.py) |
| engine/loop seam (issue #23, parallel lane) | [`loop_seam.py`](loop_seam.py), [`tests/test_loop_seam.py`](tests/test_loop_seam.py), [`tests/test_loop_integration.py`](tests/test_loop_integration.py) (real merged `engine.loop`) |

## Provenance (cannibalized and adapted — see CANNIBALIZATION.md)

| Source (read-only, under `.research/**`) | Adapted into |
|---|---|
| `leaderboard scripts/{platoon,soldier,elite}/*` + `dispatch/fanout.sh` (concurrent dispatch, claims, collision avoidance) + `docs/doctrine/` (platoon/leader doctrine) | lane vocabulary + disjoint-work fan-out + bounded dispatch in `model.py` / `fanout.py` / `planner.py` |
| `news-feed-engine/services/multi-agent-system` (majority vote + weighted expertise, per-check thresholds, audit, escalation to review) | `consensus.py` threshold/quorum rules + `NO_CONSENSUS` reasons + fail-closed voter handling |
| `news-feed-engine/services/ai-personas` (persona archetype schema) | `Lane.persona`/`specialization` archetype fields |
| `capital-underwriting/scripts/agent/*` (worker daemons, dispatch/ack, auditor) | the injected runner seam + worker/ack discipline consumed via `engine/queue` (`queue_seam.py`) |
| `leaderboard` never-a-false-PASS / independent-verification doctrine | queue seam's fail-closed Worker handling (`tests/test_queue_integration.py`) |

## Importing and running the tests

`engine/multiagent` is importable as `engine.multiagent.*` when the repo root
is on `sys.path` (tests arrange this in `tests/conftest.py`; `engine/` is a
PEP-420 namespace package). The integration seams import `engine.core` /
`engine.queue` the same way.

```bash
# from the repo root
python3 -m pytest engine/multiagent/tests -q -p no:cacheprovider
python3 -m pytest engine/loop/tests engine/multiagent/tests -q -p no:cacheprovider  # with the merged loop lane
make verify   # repo gate stays green
```

## Contract notes

* One mission = one `Mission`; a plan is bounded per round; a required
  failure escalates within `max_escalation_rounds`; unresolved required
  subtasks are always reported (`escalated`), never dropped.
* A ballot always returns a defined `ConsensusResult`; no-consensus is a
  first-class outcome, never a silent pass.
* Handlers / modules never print (deterministic); reports are JSON-safe so
  plans and transcripts embed cleanly in engine-core workflow events.
* Everything is stdlib-only, offline, Python 3.14 + PyYAML — no network, no
  containers, no model providers required to run or test.
