"""engine.core — durable state-machine orchestration engine core.

Public surface of the phase-3 execution pillar's durable core (issue #21):
per-tenant namespaces, event-sourced durable workflows that resume after
interruption, saga/compensation in reverse order, retry with backoff, and a
provider-agnostic model-gateway seam.

Import as ``core`` with ``engine/`` on ``sys.path`` (the tests arrange this
in ``tests/conftest.py``), mirroring the sibling packages
(``registry/service``, ``gateway/proxy``).
"""

from __future__ import annotations

from .errors import (
    CompensationError,
    CrossNamespaceError,
    EngineError,
    InvalidTransitionError,
    NamespaceError,
    NamespaceExistsError,
    PersistenceError,
    StepFailure,
    UnknownNamespaceError,
    UnknownStepHandlerError,
    UnknownWorkflowError,
    WorkflowError,
    WorkflowValidationError,
)
from .events import EventRecord, EventStore, FileJsonlEventStore, InMemoryEventStore
from .gateway_port import GatewayRequest, GatewayResult, ModelGateway, NullGateway
from .handlers import BUILTIN_HANDLERS, DEFAULT_HANDLERS, Handler, StepContext
from .machine import legal_moves, next_status
from .model import (
    Compensation,
    CostEntry,
    EventKind,
    RetryPolicy,
    Step,
    StepKind,
    StepStatus,
    WorkflowKind,
    WorkflowSpec,
    WorkflowStatus,
    spec_from_dict,
    spec_to_dict,
)
from .namespaces import Namespace, NamespaceRegistry
from .runtime import Engine, ExecutionReport, RealClock, provisioning_spec
from .workflow import StepExecutionState, WorkflowExecution

__all__ = [
    # errors
    "EngineError",
    "NamespaceError",
    "UnknownNamespaceError",
    "NamespaceExistsError",
    "CrossNamespaceError",
    "WorkflowError",
    "UnknownWorkflowError",
    "WorkflowValidationError",
    "InvalidTransitionError",
    "UnknownStepHandlerError",
    "StepFailure",
    "CompensationError",
    "PersistenceError",
    # events / persistence
    "EventRecord",
    "EventStore",
    "InMemoryEventStore",
    "FileJsonlEventStore",
    # gateway port
    "ModelGateway",
    "NullGateway",
    "GatewayRequest",
    "GatewayResult",
    # handlers
    "Handler",
    "StepContext",
    "BUILTIN_HANDLERS",
    "DEFAULT_HANDLERS",
    # model
    "WorkflowStatus",
    "StepStatus",
    "WorkflowKind",
    "StepKind",
    "EventKind",
    "RetryPolicy",
    "Compensation",
    "Step",
    "WorkflowSpec",
    "CostEntry",
    "spec_to_dict",
    "spec_from_dict",
    # namespaces
    "Namespace",
    "NamespaceRegistry",
    # machine
    "next_status",
    "legal_moves",
    # workflow projection
    "WorkflowExecution",
    "StepExecutionState",
    # runtime
    "Engine",
    "ExecutionReport",
    "RealClock",
    "provisioning_spec",
]
