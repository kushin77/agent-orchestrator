"""The definition set: states, transitions, vocabularies and SLA policies (issue #650).

EPIC #645 gives this module one data contract: **the definitions it operates on
are indexer-fed**. Lead stages, task types, inspection templates and SLA
policies are declarations, not code, and they arrive from
``governance/knowledge`` (the indexer) rather than being written into this
package.

This module is that seam, and it is the *whole* of it: :func:`load` accepts a
path **or an already-parsed mapping**, validates it, and every other module in
this package reaches its declarations only through the resulting
:class:`DefinitionSet`. Swapping this lane's local declaration
(``catalog/definitions.json``) for the indexer's catalogue is therefore a change
of *source*, not of code — which is what makes the lane's commitment "the
indexer feeds us" true today instead of aspirational.

Two disciplines hold the seam honest:

* **a declaration is validated before it is trusted.** :func:`load` runs the
  frozen subset validator over the document *and* a cross-check over the graph
  the document describes — initial state present, every transition target
  declared, the machine total over its own states, every referenced vocabulary
  declared, every required field allowed, every SLA window ordered. A
  declaration document is refused as a whole (``definitions-invalid``), naming
  every problem, because a half-trusted declaration set is worse than none.
* **an undeclared name is refused, never defaulted.** ``kind``, ``vocabulary``
  and ``policy`` raise rather than returning an empty value, so an input naming
  a stage or policy this module does not have fails by name instead of silently
  taking a default the caller never chose.

The declaration set a document was built under travels with the document
(``flows.Workspace``), so a workspace cannot be read with a different
declaration set than the one that licensed its transitions.

---knowledge---
module_id: integrations.erp.crm.definitions
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [SLAPolicy, KindDefinition, DefinitionSet, load]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import schema as schemas
from .model import KINDS, Refused

DEFINITIONS_SCHEMA = Path(__file__).resolve().parent / "schema" / "definitions.schema.json"
LOCAL_DECLARATION = Path(__file__).resolve().parent / "catalog" / "definitions.json"

#: The declaration source this lane ships. The indexer-fed value is a different
#: string, so the provenance of a loaded set is visible in its own record.
SOURCE_LOCAL = "local-lane-declaration"


@dataclass(frozen=True)
class SLAPolicy:
    """One support response/resolution window, in whole minutes."""

    name: str
    respond_minutes: int
    resolve_minutes: int
    warn_percent: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "respondMinutes": self.respond_minutes,
            "resolveMinutes": self.resolve_minutes,
            "warnPercent": self.warn_percent,
        }


@dataclass(frozen=True)
class KindDefinition:
    """One document kind's machine and field contract."""

    name: str
    states: Tuple[str, ...]
    initial: str
    transitions: Mapping[str, Tuple[str, ...]]
    allowed_fields: Tuple[str, ...]
    required_fields: Tuple[str, ...]
    vocabularies: Mapping[str, str] = field(default_factory=dict)
    field_types: Mapping[str, str] = field(default_factory=dict)
    open_states: Tuple[str, ...] = field(default_factory=tuple)

    def is_state(self, state: str) -> bool:
        return state in self.states

    def allows(self, from_state: str, to_state: str) -> bool:
        return to_state in self.transitions.get(from_state, ())

    def is_open(self, state: str) -> bool:
        """Whether a document in ``state`` still accepts child work.

        Only kinds that declare ``openStates`` answer meaningfully; a kind that
        declares none is never "open", which is the fail-closed reading (a task
        family that forgot to declare its open states does not silently accept
        timesheet entries against closed work).
        """
        return state in self.open_states

    @property
    def terminal_states(self) -> Tuple[str, ...]:
        return tuple(state for state in self.states if not self.transitions.get(state))

    def field_type(self, name: str) -> Optional[str]:
        return self.field_types.get(name)

    def vocabulary_for(self, name: str) -> Optional[str]:
        return self.vocabularies.get(name)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "initial": self.initial,
            "states": list(self.states),
            "transitions": {k: list(v) for k, v in sorted(self.transitions.items())},
            "allowedFields": list(self.allowed_fields),
            "requiredFields": list(self.required_fields),
            "vocabularies": dict(sorted(self.vocabularies.items())),
            "fieldTypes": dict(sorted(self.field_types.items())),
            "openStates": list(self.open_states),
        }


@dataclass(frozen=True)
class DefinitionSet:
    """A validated declaration set: the only vocabulary this module consults."""

    schema_version: int
    source: str
    kinds: Mapping[str, KindDefinition]
    vocabularies: Mapping[str, Tuple[str, ...]]
    sla_policies: Mapping[str, SLAPolicy]

    def has_kind(self, name: str) -> bool:
        return name in self.kinds

    def kind(self, name: str) -> KindDefinition:
        declared = self.kinds.get(name)
        if declared is None:
            raise Refused(
                "unknown-kind",
                f"kind {name!r} is not declared "
                f"(declared: {', '.join(sorted(self.kinds))})",
            )
        return declared

    def vocabulary(self, name: str) -> Tuple[str, ...]:
        declared = self.vocabularies.get(name)
        if declared is None:
            raise Refused(
                "unknown-vocabulary",
                f"vocabulary {name!r} is not declared "
                f"(declared: {', '.join(sorted(self.vocabularies))})",
            )
        return declared

    def policy(self, name: str) -> SLAPolicy:
        declared = self.sla_policies.get(name)
        if declared is None:
            raise Refused(
                "unknown-policy",
                f"SLA policy {name!r} is not declared "
                f"(declared: {', '.join(sorted(self.sla_policies))})",
            )
        return declared

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "source": self.source,
            "kinds": {name: self.kinds[name].to_dict() for name in sorted(self.kinds)},
            "vocabularies": {name: list(self.vocabularies[name]) for name in sorted(self.vocabularies)},
            "slaPolicies": {name: self.sla_policies[name].to_dict() for name in sorted(self.sla_policies)},
        }


def _as_text_tuple(value: Any, where: str, problems: List[str]) -> Tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        problems.append(f"{where}: expected a list of strings")
        return ()
    if len(set(value)) != len(value):
        problems.append(f"{where}: contains a duplicate")
    return tuple(value)


def _cross_check(document: Mapping[str, Any]) -> List[str]:
    """The graph invariants a declaration document must satisfy.

    The subset validator judges *shape*; these are the relationships shape
    cannot express (a transition target that is not a state, a vocabulary name
    nobody declared), and they are the ones that actually break a workflow.
    """
    problems: List[str] = []

    vocabularies_raw = document.get("vocabularies")
    vocabularies: Dict[str, Tuple[str, ...]] = {}
    if isinstance(vocabularies_raw, dict):
        for name in sorted(vocabularies_raw):
            terms = _as_text_tuple(
                vocabularies_raw[name], f"vocabularies.{name}", problems
            )
            if terms:
                vocabularies[name] = terms
            else:
                problems.append(f"vocabularies.{name}: declares no terms")

    kinds_raw = document.get("kinds")
    declared_kinds: List[str] = []
    if isinstance(kinds_raw, dict):
        for name in sorted(kinds_raw):
            entry = kinds_raw[name]
            where = f"kinds.{name}"
            if not isinstance(entry, dict):
                problems.append(f"{where}: expected an object")
                continue
            declared_kinds.append(name)

            states = _as_text_tuple(entry.get("states"), f"{where}.states", problems)
            if not states:
                problems.append(f"{where}.states: declares no states")
                continue
            initial = entry.get("initial")
            if initial not in states:
                problems.append(
                    f"{where}.initial: {initial!r} is not one of its own states"
                )

            transitions_raw = entry.get("transitions")
            if not isinstance(transitions_raw, dict):
                problems.append(f"{where}.transitions: expected an object")
            else:
                undeclared = sorted(set(transitions_raw) - set(states))
                if undeclared:
                    problems.append(
                        f"{where}.transitions: names undeclared state(s) "
                        f"{', '.join(undeclared)}"
                    )
                missing = sorted(set(states) - set(transitions_raw))
                if missing:
                    problems.append(
                        f"{where}.transitions: no entry for declared state(s) "
                        f"{', '.join(missing)} (a terminal state declares an empty list)"
                    )
                for state in sorted(set(transitions_raw) & set(states)):
                    targets = _as_text_tuple(
                        transitions_raw[state], f"{where}.transitions.{state}", problems
                    )
                    unknown = sorted(set(targets) - set(states))
                    if unknown:
                        problems.append(
                            f"{where}.transitions.{state}: targets undeclared state(s) "
                            f"{', '.join(unknown)}"
                        )

            allowed = set(_as_text_tuple(entry.get("allowedFields"), f"{where}.allowedFields", problems))
            required = set(
                _as_text_tuple(entry.get("requiredFields"), f"{where}.requiredFields", problems)
            )
            if not required <= allowed:
                problems.append(
                    f"{where}: required field(s) not allowed: "
                    f"{', '.join(sorted(required - allowed))}"
                )

            for keyword, table in (("vocabularies", entry.get("vocabularies")),
                                   ("fieldTypes", entry.get("fieldTypes"))):
                if table is None:
                    continue
                if not isinstance(table, dict):
                    problems.append(f"{where}.{keyword}: expected an object")
                    continue
                for field_name in sorted(table):
                    if field_name not in allowed:
                        problems.append(
                            f"{where}.{keyword}: declares {field_name!r}, which is not "
                            "an allowed field of the kind"
                        )
                if keyword == "vocabularies":
                    for field_name in sorted(table):
                        referenced = table[field_name]
                        if referenced not in vocabularies:
                            problems.append(
                                f"{where}.vocabularies.{field_name}: references "
                                f"undeclared vocabulary {referenced!r}"
                            )
                else:
                    for field_name in sorted(table):
                        if table[field_name] not in schemas.SIMPLE_TYPES:
                            problems.append(
                                f"{where}.fieldTypes.{field_name}: unknown type "
                                f"{table[field_name]!r} (known: "
                                f"{', '.join(schemas.SIMPLE_TYPES)})"
                            )

            open_states = _as_text_tuple(
                entry.get("openStates", []), f"{where}.openStates", problems
            )
            unknown_open = sorted(set(open_states) - set(states))
            if unknown_open:
                problems.append(
                    f"{where}.openStates: names undeclared state(s) "
                    f"{', '.join(unknown_open)}"
                )
    else:
        problems.append("kinds: expected an object")

    missing_kinds = sorted(set(KINDS) - set(declared_kinds))
    if missing_kinds:
        problems.append(
            "kinds: this module owns "
            f"{', '.join(KINDS)} but the declaration omits {', '.join(missing_kinds)}"
        )

    policies_raw = document.get("slaPolicies")
    if not isinstance(policies_raw, dict) or not policies_raw:
        problems.append("slaPolicies: expected a non-empty object")
    else:
        for name in sorted(policies_raw):
            entry = policies_raw[name]
            where = f"slaPolicies.{name}"
            if not isinstance(entry, dict):
                problems.append(f"{where}: expected an object")
                continue
            respond = entry.get("respondMinutes")
            resolve = entry.get("resolveMinutes")
            warn = entry.get("warnPercent")
            if isinstance(respond, bool) or not isinstance(respond, int) or respond < 1:
                problems.append(f"{where}.respondMinutes: expected an integer >= 1")
            if isinstance(resolve, bool) or not isinstance(resolve, int) or resolve < 1:
                problems.append(f"{where}.resolveMinutes: expected an integer >= 1")
            if (
                isinstance(respond, int)
                and not isinstance(respond, bool)
                and isinstance(resolve, int)
                and not isinstance(resolve, bool)
                and resolve < respond
            ):
                problems.append(
                    f"{where}: resolveMinutes ({resolve}) is shorter than "
                    f"respondMinutes ({respond})"
                )
            if isinstance(warn, bool) or not isinstance(warn, int) or not 0 < warn < 100:
                problems.append(f"{where}.warnPercent: expected an integer in 1..99")
    return problems


def load(
    source: Path | str | Mapping[str, Any] = LOCAL_DECLARATION,
    *,
    schema_path: Path | str = DEFINITIONS_SCHEMA,
) -> DefinitionSet:
    """Load and validate a declaration set from a path or an in-memory mapping."""
    if isinstance(source, Mapping):
        document: Any = dict(source)
        origin = "in-memory declaration"
    else:
        path = Path(source)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise Refused("definitions-invalid", f"{path}: not found") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise Refused("definitions-invalid", f"{path}: {exc}") from exc
        origin = str(path)

    if not isinstance(document, dict):
        raise Refused("definitions-invalid", f"{origin}: must be a JSON object")

    try:
        schema = schemas.load(schema_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise Refused("definitions-invalid", f"unreadable definitions schema: {exc}") from exc

    problems = schemas.validate_named(
        document, schema, origin, schema_label=str(schema_path)
    )
    if problems:
        raise Refused(
            "definitions-invalid",
            f"{origin}: " + "; ".join(schemas.sorted_problems(problems)),
        )

    graph = _cross_check(document)
    if graph:
        raise Refused(
            "definitions-invalid",
            f"{origin}: " + "; ".join(schemas.sorted_problems(graph)),
        )

    kinds: Dict[str, KindDefinition] = {}
    for name in sorted(document["kinds"]):
        entry = document["kinds"][name]
        kinds[name] = KindDefinition(
            name=name,
            states=tuple(entry["states"]),
            initial=entry["initial"],
            transitions={
                state: tuple(targets) for state, targets in entry["transitions"].items()
            },
            allowed_fields=tuple(entry["allowedFields"]),
            required_fields=tuple(entry["requiredFields"]),
            vocabularies=dict(entry.get("vocabularies", {})),
            field_types=dict(entry.get("fieldTypes", {})),
            open_states=tuple(entry.get("openStates", [])),
        )

    vocabularies = {
        name: tuple(terms) for name, terms in document["vocabularies"].items()
    }
    policies = {
        name: SLAPolicy(
            name=name,
            respond_minutes=entry["respondMinutes"],
            resolve_minutes=entry["resolveMinutes"],
            warn_percent=entry["warnPercent"],
        )
        for name, entry in document["slaPolicies"].items()
    }
    return DefinitionSet(
        schema_version=document["schemaVersion"],
        source=document["source"],
        kinds=kinds,
        vocabularies=vocabularies,
        sla_policies=policies,
    )
