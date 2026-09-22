"""The ERP document workflow as **data** (ERP-02, issue #647).

A workflow here is not engine code. It is a state/transition document —
``draft -> submitted -> completed | cancelled`` for the transaction families —
that the durable execution engine can consume, and that anything else can read
without the engine: loading a workflow imports nothing from ``engine/`` and
edits nothing there. That separation is an acceptance criterion of #647, and it
is what lets ERP-03 wire the transactional spine (#648) onto the same data
without either lane owning the other's files.

What is harvested, and what is not
----------------------------------

The *shape* is ERPNext's: documents carry a closed ``docstatus`` vocabulary
(``0`` draft, ``1`` submitted, ``2`` cancelled), a workflow has one initial
state and named transitions, and every transition is an action a user takes.
None of that is novel, and it is exactly the part that must agree with the
upstream semantics to be useful. No upstream code, text or file is copied —
upstream is GPL-3.0 and a pattern source only (``provenance.json``, GR-10).

Structural invariants
---------------------

Every one is refused by name when violated, and each has a negative control in
``tests/test_workflow.py``:

* one initial state, declared, and it is the draft (``docstatus`` 0);
* states are unique, and every transition names declared states;
* at most one transition per ``(state, action)`` — a non-deterministic workflow
  cannot be executed, so it is not a workflow;
* a terminal state has no outgoing transition, and every declared state is
  reachable from the initial one (a state nothing can reach is a state nothing
  can leave, which is a modelling error rather than a lifecycle);
* ``docstatus`` never decreases along a transition — a document cannot go from
  submitted back to draft by a declared action; cancellation is the only way
  out of ``submitted``, and it is forward.

---knowledge---
module_id: integrations.erp.core.workflow
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [State, Transition, Workflow, WorkflowSet, load_workflow_file, load_workflows]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .errors import (
    ErpError,
    unknown_action,
    unknown_state,
    unknown_workflow,
    workflow_invalid,
    yaml_unavailable,
)

__all__ = [
    "DOCSTATUS_CANCELLED",
    "DOCSTATUS_DRAFT",
    "DOCSTATUS_SUBMITTED",
    "State",
    "Transition",
    "Workflow",
    "WorkflowSet",
    "load_workflow_file",
    "load_workflows",
]

#: The closed ``docstatus`` vocabulary, harvested from the upstream doctype
#: shape (0 draft / 1 submitted / 2 cancelled). Declared here as the single
#: definition the workflow data and the document schemas both cite.
DOCSTATUS_DRAFT = 0
DOCSTATUS_SUBMITTED = 1
DOCSTATUS_CANCELLED = 2

DOCSTATUS_VOCABULARY: Tuple[int, ...] = (
    DOCSTATUS_DRAFT,
    DOCSTATUS_SUBMITTED,
    DOCSTATUS_CANCELLED,
)


@dataclass(frozen=True)
class State:
    """One workflow state."""

    name: str
    docstatus: int
    label: str
    terminal: bool

    @classmethod
    def from_data(cls, data: Any, *, index: int) -> "State":
        where = f"states[{index}]"
        if not isinstance(data, Mapping):
            raise workflow_invalid(f"{where} must be a mapping")
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            raise workflow_invalid(f"{where}.name must be a non-empty string")
        docstatus = data.get("docstatus")
        if isinstance(docstatus, bool) or docstatus not in DOCSTATUS_VOCABULARY:
            raise workflow_invalid(
                f"{where}.docstatus must be one of {list(DOCSTATUS_VOCABULARY)}",
                state=name,
                docstatus=docstatus,
            )
        label = data.get("label", name)
        if not isinstance(label, str) or not label.strip():
            raise workflow_invalid(f"{where}.label must be a non-empty string")
        terminal = data.get("terminal", False)
        if not isinstance(terminal, bool):
            raise workflow_invalid(f"{where}.terminal must be a boolean")
        return cls(name=name, docstatus=docstatus, label=label, terminal=terminal)

    def to_data(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "docstatus": self.docstatus,
            "label": self.label,
            "terminal": self.terminal,
        }


@dataclass(frozen=True)
class Transition:
    """One named move between two states."""

    action: str
    from_state: str
    to: str
    label: str

    @classmethod
    def from_data(cls, data: Any, *, index: int) -> "Transition":
        where = f"transitions[{index}]"
        if not isinstance(data, Mapping):
            raise workflow_invalid(f"{where} must be a mapping")
        for field in ("action", "from", "to"):
            value = data.get(field)
            if not isinstance(value, str) or not value.strip():
                raise workflow_invalid(f"{where}.{field} must be a non-empty string")
        label = data.get("label", data.get("action"))
        if not isinstance(label, str) or not label.strip():
            raise workflow_invalid(f"{where}.label must be a non-empty string")
        return cls(
            action=data["action"], from_state=data["from"], to=data["to"], label=label
        )

    def to_data(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "from": self.from_state,
            "to": self.to,
            "label": self.label,
        }


@dataclass(frozen=True)
class Workflow:
    """One document's lifecycle, as data."""

    workflow: str
    document: str
    version: int
    initial: str
    states: Tuple[State, ...]
    transitions: Tuple[Transition, ...]
    source: str = "<memory>"

    # --- construction -----------------------------------------------------

    @classmethod
    def from_data(cls, data: Any, *, source: str = "<memory>") -> "Workflow":
        """Build a workflow from parsed data, refusing anything unexecutable."""
        if not isinstance(data, Mapping):
            raise workflow_invalid(f"{source}: workflow data must be a mapping")

        workflow = data.get("workflow")
        if not isinstance(workflow, str) or not workflow.strip():
            raise workflow_invalid(f"{source}: workflow must be a non-empty string")
        document = data.get("document")
        if not isinstance(document, str) or not document.strip():
            raise workflow_invalid(
                f"{source}: document must be a non-empty string", workflow=workflow
            )
        version = data.get("version", 1)
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise workflow_invalid(
                f"{source}: version must be a positive integer", workflow=workflow
            )
        initial = data.get("initial")
        if not isinstance(initial, str) or not initial.strip():
            raise workflow_invalid(
                f"{source}: initial must be a non-empty string", workflow=workflow
            )

        raw_states = data.get("states")
        if not isinstance(raw_states, list) or not raw_states:
            raise workflow_invalid(
                f"{source}: states must be a non-empty list", workflow=workflow
            )
        states = tuple(
            State.from_data(entry, index=index)
            for index, entry in enumerate(raw_states)
        )
        seen: Dict[str, State] = {}
        for state in states:
            if state.name in seen:
                raise workflow_invalid(
                    f"{source}: duplicate state {state.name!r}", workflow=workflow
                )
            seen[state.name] = state

        if initial not in seen:
            raise workflow_invalid(
                f"{source}: initial state {initial!r} is not declared",
                workflow=workflow,
                known=sorted(seen),
            )
        if seen[initial].docstatus != DOCSTATUS_DRAFT:
            raise workflow_invalid(
                f"{source}: initial state {initial!r} must be the draft "
                f"(docstatus {DOCSTATUS_DRAFT})",
                workflow=workflow,
                docstatus=seen[initial].docstatus,
            )

        raw_transitions = data.get("transitions", [])
        if not isinstance(raw_transitions, list):
            raise workflow_invalid(
                f"{source}: transitions must be a list", workflow=workflow
            )
        transitions = tuple(
            Transition.from_data(entry, index=index)
            for index, entry in enumerate(raw_transitions)
        )

        pairs: Dict[Tuple[str, str], Transition] = {}
        for transition in transitions:
            for field, value in (("from", transition.from_state), ("to", transition.to)):
                if value not in seen:
                    raise workflow_invalid(
                        f"{source}: transition {transition.action!r} names an "
                        f"undeclared {field} state {value!r}",
                        workflow=workflow,
                        state=value,
                    )
            key = (transition.from_state, transition.action)
            if key in pairs:
                raise workflow_invalid(
                    f"{source}: state {transition.from_state!r} has two transitions "
                    f"for action {transition.action!r} — the next state would be "
                    "undecidable",
                    workflow=workflow,
                    action=transition.action,
                )
            pairs[key] = transition
            if seen[transition.from_state].terminal:
                raise workflow_invalid(
                    f"{source}: terminal state {transition.from_state!r} has an "
                    f"outgoing transition {transition.action!r}",
                    workflow=workflow,
                )
            if seen[transition.to].docstatus < seen[transition.from_state].docstatus:
                raise workflow_invalid(
                    f"{source}: transition {transition.action!r} moves docstatus "
                    f"backwards ({seen[transition.from_state].docstatus} -> "
                    f"{seen[transition.to].docstatus})",
                    workflow=workflow,
                )

        built = cls(
            workflow=workflow,
            document=document,
            version=version,
            initial=initial,
            states=states,
            transitions=transitions,
            source=source,
        )
        unreachable = sorted(set(seen) - set(built.reachable_from(initial)))
        if unreachable:
            raise workflow_invalid(
                f"{source}: state(s) unreachable from the initial state: {unreachable}",
                workflow=workflow,
                unreachable=unreachable,
            )
        return built

    # --- inspection -------------------------------------------------------

    def state(self, name: str) -> State:
        for candidate in self.states:
            if candidate.name == name:
                return candidate
        raise unknown_state(name, self.workflow, [state.name for state in self.states])

    def state_names(self) -> Tuple[str, ...]:
        return tuple(state.name for state in self.states)

    def terminal_states(self) -> Tuple[str, ...]:
        return tuple(state.name for state in self.states if state.terminal)

    def actions_from(self, state: str) -> Tuple[str, ...]:
        return tuple(
            transition.action
            for transition in self.transitions
            if transition.from_state == state
        )

    def legal_targets(self, state: str) -> Tuple[str, ...]:
        return tuple(
            transition.to
            for transition in self.transitions
            if transition.from_state == state
        )

    def reachable_from(self, state: str) -> Tuple[str, ...]:
        """Every state reachable from ``state`` (including itself)."""
        frontier = [state]
        seen = {state}
        while frontier:
            current = frontier.pop()
            for transition in self.transitions:
                if transition.from_state != current or transition.to in seen:
                    continue
                seen.add(transition.to)
                frontier.append(transition.to)
        return tuple(sorted(seen))

    def path(self, from_state: str, to_state: str) -> List[str]:
        """A shortest legal path ``from_state`` -> ``to_state``; empty if none."""
        self.state(from_state)
        self.state(to_state)
        if from_state == to_state:
            return [from_state]
        queue: List[List[str]] = [[from_state]]
        explored = {from_state}
        while queue:
            trail = queue.pop(0)
            for transition in self.transitions:
                if transition.from_state != trail[-1]:
                    continue
                if transition.to == to_state:
                    return trail + [transition.to]
                if transition.to not in explored:
                    explored.add(transition.to)
                    queue.append(trail + [transition.to])
        return []

    # --- the state machine ------------------------------------------------

    def assert_declared(self, state: str) -> State:
        return self.state(state)

    def next_state(
        self, current: str, action: str, *, target: Optional[str] = None
    ) -> str:
        """The state ``action`` moves a document in ``current`` to.

        Refuses by name: an unknown current state, an action this state does
        not declare, or a ``target`` that is not the state the action reaches
        (the caller asserting a state jump).
        """
        self.state(current)
        candidates = [
            transition
            for transition in self.transitions
            if transition.from_state == current and transition.action == action
        ]
        if not candidates:
            raise unknown_action(
                action, current, self.workflow, sorted(self.actions_from(current))
            )
        reached = candidates[0].to
        if target is not None and target != reached:
            raise ErpError(
                409,
                "state_jumped",
                f"{self.workflow!r}: action {action!r} from {current!r} reaches "
                f"{reached!r}, not the asserted {target!r}",
                {"workflow": self.workflow, "action": action, "reached": reached,
                 "asserted": target},
            )
        return reached

    def assert_move(self, from_state: str, to_state: str) -> str:
        """Refuse unless ``to_state`` is a declared move from ``from_state``.

        This is the direct expression of "a document that jumps states is
        rejected": ``draft -> completed`` is refused by name because no
        transition connects them, however legitimate both states are.
        """
        self.state(from_state)
        self.state(to_state)
        for transition in self.transitions:
            if transition.from_state == from_state and transition.to == to_state:
                return to_state
        raise ErpError(
            409,
            "state_jumped",
            f"{self.workflow!r} does not allow a move from {from_state!r} to "
            f"{to_state!r}",
            {
                "workflow": self.workflow,
                "from": from_state,
                "to": to_state,
                "legal": sorted(self.legal_targets(from_state)),
            },
        )

    # --- serialisation ----------------------------------------------------

    def to_data(self) -> Dict[str, Any]:
        """Render back to the data shape, so the YAML round-trips."""
        return {
            "schema": "erp.workflow/v1",
            "workflow": self.workflow,
            "document": self.document,
            "version": self.version,
            "initial": self.initial,
            "states": [state.to_data() for state in self.states],
            "transitions": [transition.to_data() for transition in self.transitions],
        }


@dataclass(frozen=True)
class WorkflowSet:
    """Every workflow the module ships, addressable by document."""

    workflows: Tuple[Workflow, ...]

    def by_document(self, document: str) -> Workflow:
        for workflow in self.workflows:
            if workflow.document == document:
                return workflow
        raise unknown_workflow(document, sorted(self.documents()))

    def documents(self) -> Tuple[str, ...]:
        return tuple(sorted(workflow.document for workflow in self.workflows))

    def names(self) -> Tuple[str, ...]:
        return tuple(sorted(workflow.workflow for workflow in self.workflows))

    def __iter__(self) -> Iterable[Workflow]:
        return iter(self.workflows)

    def __len__(self) -> int:
        return len(self.workflows)


def _yaml_module():
    """Import the YAML parser lazily, so the state machine needs no parser."""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - the gate itself requires PyYAML
        raise yaml_unavailable(
            f"reading workflow data needs a YAML parser, and PyYAML is not "
            f"importable: {exc}"
        ) from exc
    return yaml


def load_workflow_file(path: Path | str) -> Workflow:
    """Read one workflow YAML file into a :class:`Workflow`."""
    resolved = Path(path)
    if not resolved.is_file():
        raise workflow_invalid(f"workflow file not found: {resolved}")
    yaml = _yaml_module()
    try:
        data = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise workflow_invalid(f"{resolved}: not valid YAML: {exc}") from exc
    return Workflow.from_data(data, source=resolved.name)


def load_workflows(directory: Path | str) -> WorkflowSet:
    """Load every ``*.yaml`` workflow in ``directory`` (stable order)."""
    resolved = Path(directory)
    if not resolved.is_dir():
        raise workflow_invalid(f"workflow directory not found: {resolved}")
    files = sorted(resolved.glob("*.yaml"), key=lambda item: item.name)
    if not files:
        raise workflow_invalid(f"no workflow data under {resolved}")
    workflows = [load_workflow_file(item) for item in files]
    duplicates = {
        workflow.document
        for workflow in workflows
        if sum(1 for other in workflows if other.document == workflow.document) > 1
    }
    if duplicates:
        raise workflow_invalid(
            f"more than one workflow for document(s) {sorted(duplicates)}"
        )
    return WorkflowSet(workflows=tuple(workflows))
