"""ERP CRM / projects / quality / support (issue #650, EPIC #645).

The ERP module's CRM-family lane: the **document surface and flows** for leads,
opportunities, customers, projects, tasks, timesheets, quality inspections and
support issues, under ``integrations/erp/crm/``.

Read ``README.md`` for the surface, the flows and the lane boundary. In short:

* documents are frozen values validated against a declaration set
  (``catalog/definitions.json``, reachable only through ``definitions.load`` —
  the seam the indexer-fed catalogue of EPIC #645 replaces);
* every state change goes through ``workflow.advance`` and lands on a
  hash-chained audit rail (``audit.Rail``);
* cost is a derived rollup (``timesheet.accumulate``) and SLA ageing is a pure
  function of an injected clock (``sla.age``), so both are reproducible;
* every refusal is provoked by ``negative_control.run``, which fails when the
  provoked set and the closed refusal vocabulary diverge.

This package imports nothing from ``integrations/erp/core`` or
``integrations/erp/catalog``: those were concurrent sibling lanes, so the one
shape this lane needed is declared locally and the seam is named.
"""

from __future__ import annotations

from .audit import Entry, Rail
from .definitions import DefinitionSet, KindDefinition, SLAPolicy
from .definitions import load as load_definitions
from .documents import parse, validate_fields
from .flows import (
    ACTOR,
    GoldenPath,
    INSPECTION_OUTCOMES,
    T,
    Workspace,
    age_issue,
    advance,
    approve_timesheet,
    convert_lead,
    create_document,
    golden_path,
    inspection_outcomes,
    log_timesheet,
    open_issue,
    patch,
    project_costs,
    record_inspection_outcome,
    record_sla_check,
    reinspect,
    reject_timesheet,
    reopen_issue,
    resolve_issue,
    respond_to_issue,
    start_inspection,
    start_project,
    start_task,
    triage_issue,
    win_opportunity,
    workspace,
)
from .model import (
    ACTIONS,
    KINDS,
    REFUSALS,
    SCHEMA_VERSION,
    Document,
    Finding,
    Refused,
)
from .provenance import Harvest, Provenance
from .provenance import load as load_provenance
from .sla import SLAState
from .timesheet import Line, Rollup
from .timesheet import accumulate as accumulate_costs
from .workflow import accepts_child_work, assert_transition, is_terminal, transition_targets

__all__ = [
    "ACTIONS",
    "ACTOR",
    "DefinitionSet",
    "Document",
    "Entry",
    "Finding",
    "GoldenPath",
    "Harvest",
    "INSPECTION_OUTCOMES",
    "KINDS",
    "KindDefinition",
    "Line",
    "Provenance",
    "REFUSALS",
    "Rail",
    "Refused",
    "Rollup",
    "SCHEMA_VERSION",
    "SLAPolicy",
    "SLAState",
    "T",
    "Workspace",
    "accepts_child_work",
    "accumulate_costs",
    "advance",
    "age_issue",
    "approve_timesheet",
    "assert_transition",
    "convert_lead",
    "create_document",
    "golden_path",
    "inspection_outcomes",
    "is_terminal",
    "load_definitions",
    "load_provenance",
    "log_timesheet",
    "open_issue",
    "parse",
    "patch",
    "project_costs",
    "record_inspection_outcome",
    "record_sla_check",
    "reinspect",
    "reject_timesheet",
    "reopen_issue",
    "resolve_issue",
    "respond_to_issue",
    "start_inspection",
    "start_project",
    "start_task",
    "transition_targets",
    "triage_issue",
    "validate_fields",
    "win_opportunity",
    "workspace",
]
