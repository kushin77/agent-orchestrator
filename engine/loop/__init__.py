"""engine/loop — deterministic agent-loop runtime (bounded actor loop + escalation).

Issue ``kushin77/agent-orchestrator#23`` ("19 Deterministic agent-loop runtime
(bounded actor loop + escalation)", work item 19, phase 3), engine lane.  The
execution primitive for a *single agent session*: a bounded iterative actor
loop (plan -> act(tool) -> observe -> repeat, <= N iterations per tier) with
tool allowlisting, schema-validated outputs, an explicit parse-failure policy
(``retry`` or ``CANNOT-ASSESS``), monotonic tier escalation and
human-in-the-loop hand-off, whole-loop token + iteration cost caps, and one
:class:`AgentDecision` audit record per loop.

Guarantees (all enforced by construction and negative-tested):

* **Bounded / guaranteed termination** — never an infinite silent loop.
* **Deterministic + resumable** — same inputs + same injected tool results =>
  same step trace and same :class:`AgentDecision`; a partially run loop
  resumes from a JSON checkpoint (the durable-engine hand-off).
* **No false pass** — SUCCEEDED only when a final answer meets the confidence
  threshold and its schema; everything else is an honest terminal outcome.

Public surface
--------------

Vocabulary
    ``ModelTier``/``TIER_ORDER``/``next_tier``, ``LoopOutcome``,
    ``ActionKind``, ``EscalationTrigger``, ``EscalationTargetKind``,
    ``ParseStatus`` (``engine.loop.model``).

Value objects
    ``StepRecord`` (trace row), ``EscalationEvent``, ``ToolCall``,
    ``ToolResult``, ``AgentDecision`` (per-loop audit record) + JSON-safe
    (de)serialization and ``decisions_equal`` (``engine.loop.model``).

Policies
    ``LoopPolicy`` (bounds/caps + gmail 0.9/0.6 confidence thresholds),
    ``ComplexityPolicy`` (hermes 40/70 banding), ``Profile`` (registry-shaped
    agent-profile view) (``engine.loop.policy``).

Schema + tools
    ``SchemaValidator``/``coerce_json``/``validate_output``
    (``engine.loop.schema``); ``ToolRegistry``/``ToolExecutor``/
    ``ToolSpec``/``FunctionExecutor`` (``engine.loop.tools``).

Escalation + runtime
    ``ComplexityScorer``/``EscalationEngine``/``EscalationDecision``
    (``engine.loop.escalate``); ``AgentLoop``/``Action``/``Actor``/
    ``DecisionContext`` (``engine.loop.runtime``).

Integration adapters (imported on demand)
    ``register_agent_loop_handler`` (``engine.loop.core_adapter``) — host the
    loop as a durable ``engine.core`` workflow step (key ``loop.agent_loop``);
    ``LoopTaskProcessor`` (``engine.loop.queue_adapter``) — resolve
    ``engine.queue`` tasks with the loop (ack only on SUCCEEDED).

Contract doc: ``engine/loop/README.md``.  Owner lane: engine
(``docs/EXECUTION-PLAN.md``).  Import as ``engine.loop.*`` from the repo root
(``engine/`` is a PEP-420 namespace package) — the adapters additionally
require ``engine.core``/``engine.queue`` (repo root on ``sys.path``).
"""

from .escalate import (
    ComplexityScore,
    ComplexityScorer,
    EscalationDecision,
    EscalationEngine,
)
from .model import (
    ActionKind,
    AgentDecision,
    EscalationEvent,
    EscalationTargetKind,
    EscalationTrigger,
    LoopOutcome,
    ModelTier,
    ParseStatus,
    StepRecord,
    TIER_ORDER,
    ToolCall,
    ToolResult,
    decision_from_dict,
    decision_to_dict,
    decisions_equal,
    next_tier,
    tier_rank,
)
from .policy import ComplexityPolicy, LoopPolicy, Profile
from .runtime import Action, Actor, AgentLoop, DecisionContext
from .schema import SchemaValidator, coerce_json, validate_output
from .tools import (
    FunctionExecutor,
    ToolExecutionError,
    ToolExecutor,
    ToolRegistry,
    ToolSpec,
)

__all__ = [
    # model vocabulary
    "ModelTier",
    "TIER_ORDER",
    "next_tier",
    "tier_rank",
    "LoopOutcome",
    "ActionKind",
    "EscalationTrigger",
    "EscalationTargetKind",
    "ParseStatus",
    "StepRecord",
    "EscalationEvent",
    "ToolCall",
    "ToolResult",
    "AgentDecision",
    "decision_to_dict",
    "decision_from_dict",
    "decisions_equal",
    # policies
    "LoopPolicy",
    "ComplexityPolicy",
    "Profile",
    # schema + tools
    "SchemaValidator",
    "coerce_json",
    "validate_output",
    "ToolRegistry",
    "ToolExecutor",
    "ToolSpec",
    "FunctionExecutor",
    "ToolExecutionError",
    # escalation + runtime
    "ComplexityScorer",
    "ComplexityScore",
    "EscalationEngine",
    "EscalationDecision",
    "AgentLoop",
    "Action",
    "Actor",
    "DecisionContext",
]
