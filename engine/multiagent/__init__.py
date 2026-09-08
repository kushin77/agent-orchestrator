"""engine/multiagent - multi-agent orchestration (hierarchical lanes + consensus).

Issue kushin77/agent-orchestrator#24 ("20 Multi-agent orchestration"),
engine lane, phase 3 (EPIC-00 issue #4). Two patterns over an injected,
duck-typed agent-runner seam (``run_agent(agent_id, task) -> AgentResult``):

* *hierarchical* - a planner/lead lane decomposes a mission into specialist
  subtasks, fans them out to disjoint specialist lanes (bounded, allSettled)
  and aggregates; required failures escalate back to the planner within a
  bound.
* *peer* - consensus/voting over voter lanes with threshold + quorum rules;
  a ballot always lands on a defined outcome (PASSED / REJECTED /
  NO_CONSENSUS) - never a silent pass.

Public surface
--------------

Vocabulary (``engine.multiagent.model``)
    ``Lane`` / ``LaneRole`` (planner/specialist/voter) with disjoint agent
    pools (``validate_lanes``), ``Mission``, ``Subtask`` / ``AgentTask`` /
    ``AgentResult`` / ``ResultStatus``, ``FanOutPlan`` / ``FanOutReport`` /
    ``FanOutItem``, ``AggregationReport`` / ``HierarchyReport`` /
    ``OrchestrationReport``, vote vocabulary (``VoteChoice``,
    ``ThresholdRule``, ``Vote``, ``ConsensusConfig``, ``ConsensusRequest``,
    ``ConsensusResult``, ``ConsensusOutcome``), ``HierarchyConfig``.

Runner seam (``engine.multiagent.runner``)
    The duck-typed ``run_agent`` contract, ``ScriptedRunner`` (deterministic
    injection), ``coerce_agent_result``, ``is_agent_runner``.

Logic (``fanout`` / ``planner`` / ``consensus`` / ``orchestrator``)
    ``FanOutDispatcher`` (bounded allSettled), ``HierarchicalPlanner``
    (decompose -> fan-out -> escalate -> aggregate), ``tally_votes`` /
    ``run_consensus``, ``MultiAgentOrchestrator`` (composition).

Integration seams
    ``engine.multiagent.core_seam`` (register ``multiagent.fan_out`` /
    ``multiagent.join`` handlers on the engine-core durable steps),
    ``engine.multiagent.queue_seam`` (fan-out tasks enqueued/claimed through
    engine/queue), ``engine.multiagent.loop_seam`` (engine/loop issue #23:
    ``AgentLoop`` -> runner adapter + ``AgentDecision`` -> ``AgentResult``
    mapping, wired to the real merged engine/loop).  The seams import their
    sibling engine package lazily and are imported directly, not via this
    ``__init__``.

Contract doc: ``engine/multiagent/README.md``. Import as
``engine.multiagent.*`` from the repo root (engine/ is a PEP-420 namespace
package); the base modules are importable from ``engine/`` on ``sys.path``
too. Everything is offline, stdlib-only Python 3.14 + PyYAML.
"""

from .consensus import (
    ConsensusConfig,
    ConsensusRequest,
    ConsensusResult,
    run_consensus,
    tally_votes,
)
from .fanout import (
    FanOutDispatcher,
    FanOutError,
    FanOutLimitError,
    PlanValidationError,
)
from .loop_seam import (
    AgentLoopRunner,
    LoopAgentRunner,
    LoopSeamError,
    decision_to_agent_result,
    loop_runner_if_present,
)
from .model import (
    AgentResult,
    AgentTask,
    AggregationReport,
    ConsensusOutcome,
    EscalationContext,
    FanOutItem,
    FanOutPlan,
    FanOutReport,
    HierarchyConfig,
    HierarchyReport,
    Lane,
    LaneRole,
    Mission,
    OrchestrationReport,
    ResultStatus,
    Subtask,
    TaskKind,
    ThresholdRule,
    Vote,
    VoteChoice,
    fail_result,
    fan_out_plan_from_dict,
    fan_out_plan_to_dict,
    ok_result,
    validate_lanes,
)
from .orchestrator import MultiAgentOrchestrator
from .planner import HierarchicalPlanner, static_decomposer
from .runner import (
    RunnerContractError,
    ScriptedRunner,
    coerce_agent_result,
    is_agent_runner,
)

__all__ = [
    # vocabulary
    "Lane",
    "LaneRole",
    "Mission",
    "Subtask",
    "AgentTask",
    "AgentResult",
    "ResultStatus",
    "TaskKind",
    "FanOutItem",
    "FanOutPlan",
    "FanOutReport",
    "AggregationReport",
    "HierarchyReport",
    "OrchestrationReport",
    "EscalationContext",
    "HierarchyConfig",
    # consensus vocabulary
    "VoteChoice",
    "ThresholdRule",
    "Vote",
    "ConsensusConfig",
    "ConsensusRequest",
    "ConsensusResult",
    "ConsensusOutcome",
    # errors
    "FanOutError",
    "FanOutLimitError",
    "PlanValidationError",
    "RunnerContractError",
    "LoopSeamError",
    # model helpers
    "ok_result",
    "fail_result",
    "validate_lanes",
    "fan_out_plan_to_dict",
    "fan_out_plan_from_dict",
    # runner seam
    "ScriptedRunner",
    "coerce_agent_result",
    "is_agent_runner",
    "LoopAgentRunner",
    "AgentLoopRunner",
    "decision_to_agent_result",
    "loop_runner_if_present",
    # logic
    "FanOutDispatcher",
    "HierarchicalPlanner",
    "static_decomposer",
    "tally_votes",
    "run_consensus",
    "MultiAgentOrchestrator",
]
