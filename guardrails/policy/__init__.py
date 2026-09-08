"""guardrails.policy — policy-as-code + gate engine (issue #26, phase 4).

The guardrail execution core of the AI-agent-orchestration control plane: a
declarative policy DSL (YAML + JSON-Schema), a startup validation gate that
rejects malformed/unsafe policies at deploy time, a gate engine that answers
``evaluate(action, context) -> BLOCK | WARN | LOG`` with structured evidence
and fail-closed defaults, a toggleable controls registry (default OFF,
AO-GR-6), an append-only audit seam, and an optional OPA integration backend
for enterprise policy engines.

Import as ``policy`` with ``guardrails/`` on ``sys.path`` (the tests arrange
this in ``tests/conftest.py``), mirroring the sibling packages
(``engine/core``, ``registry/service``).
"""

from __future__ import annotations

from .audit import AuditLog, AuditRecord, InMemoryAuditLog, JsonlAuditLog
from .bundle import PolicyBundle, assemble
from .conditions import evaluate_condition, validate_condition
from .controls import Control, ControlRegistry
from .decision import DecisionLevel, DecisionResult, RuleHit, strongest
from .engine import PolicyEngine
from .errors import (
    ConditionError,
    ControlError,
    DuplicatePolicyError,
    EvaluationError,
    PolicyError,
    PolicyLoadError,
    PolicyValidationError,
    UnknownPolicyError,
)
from .loader import (
    discover_policy_files,
    load_policy_file,
    policy_from_mapping,
    read_documents,
)
from .model import DEFAULT_POLICY_DECISION, Policy, PolicyRule
from .opa import LocalBackend, OpaBackend, PolicyBackend, UrllibTransport
from .schemas import SchemaValidationError
from .startup import (
    StartupReport,
    build_bundle,
    build_engine,
    default_bundle_dir,
    default_controls_file,
    validate_paths,
)

__version__ = "0.1.0"

__all__ = [
    # version
    "__version__",
    # decision + evidence
    "DecisionLevel",
    "DecisionResult",
    "RuleHit",
    "strongest",
    # model + bundle
    "Policy",
    "PolicyRule",
    "DEFAULT_POLICY_DECISION",
    "PolicyBundle",
    "assemble",
    # engine
    "PolicyEngine",
    # controls
    "Control",
    "ControlRegistry",
    # audit
    "AuditLog",
    "AuditRecord",
    "InMemoryAuditLog",
    "JsonlAuditLog",
    # backends (OPA option)
    "PolicyBackend",
    "LocalBackend",
    "OpaBackend",
    "UrllibTransport",
    # loader / conditions
    "discover_policy_files",
    "load_policy_file",
    "policy_from_mapping",
    "read_documents",
    "evaluate_condition",
    "validate_condition",
    # startup validation gate
    "StartupReport",
    "validate_paths",
    "build_bundle",
    "build_engine",
    "default_bundle_dir",
    "default_controls_file",
    # schemas
    "SchemaValidationError",
    # errors
    "PolicyError",
    "PolicyValidationError",
    "PolicyLoadError",
    "DuplicatePolicyError",
    "UnknownPolicyError",
    "ControlError",
    "EvaluationError",
    "ConditionError",
]
