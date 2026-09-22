"""The ERP core document model — schemas, workflow data, and the validators (#647).

This is the public surface of ERP-02: the ERPNext *doctype* surface re-expressed
as our data-first document model. Three artifacts, one contract:

* ``schemas/*.json`` — the document families (party, item, the transaction
  families, stock entry, GL posting) plus the meta-schema the workflow data is
  validated against. Every schema carries ``$id``, ``title`` and an
  ``x-erp-provenance`` harvest record;
* ``workflows/*.yaml`` — each lifecycle as data (see :mod:`.workflow`);
* :class:`DocumentModel` — the validators that hold the two together and refuse
  by name.

The invariants that make this more than two directories of files
---------------------------------------------------------------

The model is only honest if the halves cannot drift, so :meth:`check_assets`
enforces, and the suite provokes, these:

* **coverage, both ways** — every lifecycle document has a workflow and every
  workflow names a schema, so an added doctype or an added workflow is
  incomplete until both exist;
* **state parity** — each lifecycle schema's ``state`` enum is exactly that
  document's workflow states, and its ``docstatus`` enum exactly the
  docstatuses those states declare. A workflow edited without the schema (or
  the reverse) fails by name;
* **provenance completeness** — every schema carries its harvest record, and
  ``provenance.json`` accounts for every schema file exactly once, in both
  directions.

Nothing here imports ``engine/``: the workflow data is consumable by the durable
execution engine, not owned by it.

Usage
-----

.. code-block:: python

    from integrations.erp.core import validators

    model = validators.load_model()
    model.validate_document("sales-order", document)   # raises ErpError
    model.advance(document, "submit")                  # -> "submitted"

---knowledge---
module_id: integrations.erp.core.validators
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [DocumentModel, load_model, validate_document, envelope_for, check_assets]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from .errors import (
    CODES,
    ErpError,
    error_envelope,
    invalid_body,
    invalid_transfer,
    missing_provenance,
    ok_envelope,
    schema_violation,
    unbalanced_posting,
    unknown_document_kind,
    workflow_invalid,
)
from .schema import SchemaError, Validator
from .workflow import Workflow, WorkflowSet, load_workflows

__all__ = [
    "CODES",
    "DOCUMENT_KEY",
    "DocumentModel",
    "FAMILY_RULES",
    "LIFECYCLE_KEY",
    "MODEL_ROOT",
    "PROVENANCE_FILENAME",
    "PROVENANCE_KEY",
    "PROVENANCE_REQUIRED",
    "SCHEMAS_DIR",
    "WORKFLOWS_DIR",
    "ErpError",
    "SchemaError",
    "check_assets",
    "envelope_for",
    "load_model",
    "validate_document",
]

#: The module's own root — the tree the shipped assets live in.
MODEL_ROOT = Path(__file__).resolve().parent
SCHEMAS_DIR = MODEL_ROOT / "schemas"
WORKFLOWS_DIR = MODEL_ROOT / "workflows"
PROVENANCE_FILENAME = "provenance.json"

#: The harvest record every schema must carry (GR-10).
PROVENANCE_KEY = "x-erp-provenance"
#: Declares the doctype a document-family schema describes. Absent on the
#: meta-schemas (``document``, ``workflow``), which are not document families.
DOCUMENT_KEY = "x-erp-document"
#: Declares that this document family has a lifecycle, so it must have a
#: workflow and the two must agree. Masters (party, item) do not declare it.
LIFECYCLE_KEY = "x-erp-lifecycle"

#: The harvest record every schema must carry (GR-10). ``upstream_doctype``
#: names the UPSTREAM doctype the shapes came from, which is a different thing
#: from ``x-erp-document`` (our own document kind) — recording them as one field
#: would make the provenance record claim a correspondence it does not have.
PROVENANCE_REQUIRED: Tuple[str, ...] = (
    "issue",
    "source",
    "upstream_doctype",
    "harvest",
    "note",
)
PROVENANCE_SOURCE_REQUIRED: Tuple[str, ...] = ("repo", "license", "mode")
#: The only harvest mode the doctrine permits: no upstream code, patterns only.
PROVENANCE_REQUIRED_MODE = "pattern-only"
#: Every schema in the family set is a JSON Schema 2020-12 object.
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def _rule_gl_balance(
    model: "DocumentModel", kind: str, document: Mapping[str, Any]
) -> None:
    """Double entry: total debits must equal total credits.

    A sum over the instance cannot be expressed in JSON Schema, so it is a
    declared family rule rather than a schema keyword — and it is applied by
    :meth:`DocumentModel.validate_document`, so a caller cannot reach the schema
    check while bypassing the balance. It refuses with its own code, so a test
    can prove the refusal came from this rule rather than from the schema.
    """
    debits = sum(float(line.get("debit", 0) or 0) for line in document.get("lines", []))
    credits = sum(float(line.get("credit", 0) or 0) for line in document.get("lines", []))
    if abs(debits - credits) > 0.005:
        raise unbalanced_posting(
            f"{kind}: debits {debits:.2f} do not equal credits {credits:.2f}",
            kind=kind,
            debits=round(debits, 2),
            credits=round(credits, 2),
        )


def _rule_stock_transfer(
    model: "DocumentModel", kind: str, document: Mapping[str, Any]
) -> None:
    """A transfer must move stock between two different warehouses.

    The schema already requires both warehouses for a transfer; JSON Schema
    cannot compare two fields of the same instance, so "and they must differ"
    is a rule here. A transfer to the warehouse it came from moves nothing.
    """
    if document.get("purpose") != "material_transfer":
        return
    source = document.get("from_warehouse")
    destination = document.get("to_warehouse")
    if source is not None and source == destination:
        raise invalid_transfer(
            f"{kind}: a material_transfer must move stock between two different "
            f"warehouses, but from_warehouse and to_warehouse are both {source!r}",
            kind=kind,
            from_warehouse=source,
            to_warehouse=destination,
        )


#: The family rules applied after every document's schema check, keyed by
#: document kind. A rule refuses by **raising** an :class:`ErpError` (its own
#: code, so a refusal names the invariant); returning normally means the
#: document satisfies it. Declared as data so a gate can prove each family that
#: has a rule is actually exercised.
FAMILY_RULES: Mapping[str, Tuple[Callable[..., None], ...]] = {
    "gl-posting": (_rule_gl_balance,),
    "stock-entry": (_rule_stock_transfer,),
}


@dataclass(frozen=True)
class DocumentModel:
    """The loaded model: schemas, workflows, and the validators over both.

    ``schema_files`` is keyed by **filename** and ``schemas`` by the document
    kind a schema declares. Both orderings are real: a lookup by kind serves
    callers, and a check keyed by filename is what lets the asset gate name the
    file a reviewer has to open.
    """

    root: Path
    schema_files: Mapping[str, Dict[str, Any]]
    workflows: WorkflowSet
    provenance: Mapping[str, Any]

    @property
    def schemas_dir(self) -> Path:
        return self.root / "schemas"

    @property
    def schemas(self) -> Dict[str, Dict[str, Any]]:
        """The schemas keyed by the document kind each one declares."""
        return {
            schema[DOCUMENT_KEY]: schema
            for schema in self.schema_files.values()
            if isinstance(schema.get(DOCUMENT_KEY), str)
        }

    def _validator(self) -> Validator:
        return Validator(base_dir=self.schemas_dir)

    # --- lookups ----------------------------------------------------------

    def document_kinds(self) -> Tuple[str, ...]:
        """Every doctype a schema declares (the addressable document kinds)."""
        return tuple(sorted(self.schemas))

    def schema_for(self, kind: str) -> Dict[str, Any]:
        schema = self.schemas.get(kind)
        if schema is None:
            raise unknown_document_kind(kind, self.document_kinds())
        return schema

    def workflow_for(self, kind: str) -> Workflow:
        return self.workflows.by_document(kind)

    def lifecycle_kinds(self) -> Tuple[str, ...]:
        return tuple(sorted(
            kind
            for kind, schema in self.schemas.items()
            if schema.get(LIFECYCLE_KEY) is True
        ))

    # --- validation -------------------------------------------------------

    def validate_document(self, kind: str, document: Any) -> Dict[str, Any]:
        """Validate one document against its family schema **and** its rules.

        Returns the document unchanged when it validates; raises
        :class:`ErpError` naming every violation otherwise. An unknown kind is a
        refusal, never a silent pass, and the family rules run in the same
        method as the schema check so no caller can reach one without the other.
        """
        if not isinstance(document, Mapping):
            raise invalid_body(
                f"{kind}: a document must be a mapping, got "
                f"{type(document).__name__}"
            )
        schema = self.schema_for(kind)
        violations = self._validator().violations(document, schema)
        if violations:
            raise schema_violation(
                f"{kind}: {len(violations)} schema violation(s)",
                kind=kind,
                problems=violations,
            )
        for rule in self.rules_for(kind):
            rule(self, kind, document)
        return dict(document)

    def rules_for(self, kind: str) -> Tuple[Callable[..., None], ...]:
        """The declared family rules applied to ``kind`` (empty when none)."""
        return FAMILY_RULES.get(kind, ())

    def advance(
        self, document: Mapping[str, Any], action: str, *, target: Optional[str] = None
    ) -> str:
        """The state ``action`` moves ``document`` to, refusing a jump."""
        kind = document.get("doctype")
        if not isinstance(kind, str) or not kind.strip():
            raise invalid_body("a document must declare its doctype")
        workflow = self.workflow_for(kind)
        current = document.get("state")
        if not isinstance(current, str) or not current.strip():
            raise invalid_body(f"{kind}: a document must declare its state")
        return workflow.next_state(current, action, target=target)

    def validate_move(self, kind: str, from_state: str, to_state: str) -> str:
        """Refuse unless the move is a declared transition of ``kind``."""
        return self.workflow_for(kind).assert_move(from_state, to_state)

    # --- the asset invariants --------------------------------------------

    def check_assets(self) -> List[str]:
        """Every way the shipped assets contradict their own contract.

        Empty means the model is coherent. Each problem is a sentence naming the
        file and the field, so a failure points at the artifact rather than at
        the checker.
        """
        problems: List[str] = []
        validator = self._validator()
        file_for_kind = {
            schema[DOCUMENT_KEY]: name
            for name, schema in self.schema_files.items()
            if isinstance(schema.get(DOCUMENT_KEY), str)
        }

        def named(kind: str) -> str:
            return f"schemas/{file_for_kind.get(kind, kind + '.schema.json')}"

        # 1. every schema is a 2020-12 object carrying $id, title and provenance.
        for filename in sorted(self.schema_files):
            schema = self.schema_files[filename]
            relative = f"schemas/{filename}"
            if schema.get("$schema") != SCHEMA_DIALECT:
                problems.append(f"{relative}: $schema is not {SCHEMA_DIALECT}")
            schema_id = schema.get("$id")
            if not isinstance(schema_id, str) or not schema_id:
                problems.append(f"{relative}: missing $id")
            elif not schema_id.endswith(relative):
                problems.append(f"{relative}: $id does not name this path ({schema_id})")
            title = schema.get("title")
            if not isinstance(title, str) or not title.strip():
                problems.append(f"{relative}: missing title")
            if schema.get("type") != "object":
                problems.append(f"{relative}: a schema in this model must be an object")
            harvest = schema.get(PROVENANCE_KEY)
            if not isinstance(harvest, Mapping):
                problems.append(f"{relative}: missing {PROVENANCE_KEY} harvest record")
                continue
            for field in PROVENANCE_REQUIRED:
                if field not in harvest:
                    problems.append(f"{relative}: {PROVENANCE_KEY}.{field} is missing")
            upstream_doctype = harvest.get("upstream_doctype")
            if not isinstance(upstream_doctype, str) or not upstream_doctype.strip():
                problems.append(
                    f"{relative}: {PROVENANCE_KEY}.upstream_doctype must name the "
                    "upstream doctype the shapes came from (a string, never null)"
                )
            source = harvest.get("source")
            if not isinstance(source, Mapping):
                problems.append(f"{relative}: {PROVENANCE_KEY}.source is missing")
            else:
                for field in PROVENANCE_SOURCE_REQUIRED:
                    if not source.get(field):
                        problems.append(
                            f"{relative}: {PROVENANCE_KEY}.source.{field} is missing"
                        )
                if source.get("mode") and source["mode"] != PROVENANCE_REQUIRED_MODE:
                    problems.append(
                        f"{relative}: harvest mode {source['mode']!r} is not "
                        f"{PROVENANCE_REQUIRED_MODE!r} — upstream code may not be "
                        "copied or vendored"
                    )
            harvest_list = harvest.get("harvest")
            if not isinstance(harvest_list, list) or not harvest_list:
                problems.append(f"{relative}: {PROVENANCE_KEY}.harvest is empty")

        # 2. provenance.json accounts for every schema, both directions.
        records = self.provenance.get("schemas")
        if not isinstance(records, Mapping):
            problems.append(
                f"{PROVENANCE_FILENAME}: 'schemas' must map every schema file to its "
                "record"
            )
            records = {}
        for filename in sorted(self.schema_files):
            if filename not in records:
                problems.append(
                    f"{PROVENANCE_FILENAME}: no record for schemas/{filename}"
                )
        for filename in sorted(records):
            if filename not in self.schema_files:
                problems.append(
                    f"{PROVENANCE_FILENAME}: record for schemas/{filename}, which "
                    "does not exist"
                )
        upstream = self.provenance.get("upstream")
        if not isinstance(upstream, Mapping) or not upstream.get("repo"):
            problems.append(f"{PROVENANCE_FILENAME}: missing 'upstream.repo'")
        elif "GPL" not in str(upstream.get("license", "")).upper():
            problems.append(
                f"{PROVENANCE_FILENAME}: upstream.license must record the GPL "
                "upstream licence the harvest is bounded by"
            )

        # 3. coverage, both ways, over the lifecycle-bearing families.
        lifecycle = set(self.lifecycle_kinds())
        documented = set(self.workflows.documents())
        for kind in sorted(lifecycle - documented):
            problems.append(
                f"{named(kind)}: declares {LIFECYCLE_KEY} but no workflow declares "
                f"document {kind!r}"
            )
        for kind in sorted(documented - lifecycle):
            problems.append(
                f"workflows: document {kind!r} has a workflow but the schema "
                f"{named(kind)} does not declare {LIFECYCLE_KEY}"
            )

        # 4. state + docstatus parity between each schema and its workflow.
        for kind in sorted(lifecycle & documented):
            schema = self.schema_for(kind)
            workflow = self.workflow_for(kind)
            properties = schema.get("properties", {})
            for field, expected in (
                ("state", sorted(workflow.state_names())),
                ("docstatus", sorted({state.docstatus for state in workflow.states})),
            ):
                node = properties.get(field)
                if node is None:
                    problems.append(
                        f"{named(kind)}: a lifecycle schema must declare {field}"
                    )
                    continue
                resolved = validator.resolve(node, schema, self.schemas_dir)
                enum = resolved.get("enum") if isinstance(resolved, Mapping) else None
                if not isinstance(enum, list):
                    problems.append(
                        f"{named(kind)}: must pin the {field} enum (a lifecycle "
                        "schema whose state set is open cannot be executed)"
                    )
                elif sorted(enum) != expected:
                    problems.append(
                        f"{named(kind)}: {field} enum {sorted(enum)} does not equal "
                        f"the workflow's {expected}"
                    )
        return problems


def load_model(root: Optional[Path | str] = None) -> DocumentModel:
    """Load the model from ``root`` (default: this module's own tree)."""
    resolved = Path(root).resolve() if root is not None else MODEL_ROOT
    schemas_dir = resolved / "schemas"
    if not schemas_dir.is_dir():
        raise workflow_invalid(f"no schemas directory under {resolved}")
    validator = Validator(base_dir=schemas_dir)
    schema_files: Dict[str, Dict[str, Any]] = {}
    kinds: Dict[str, str] = {}
    for path in sorted(schemas_dir.glob("*.json"), key=lambda item: item.name):
        document = validator.load(path)
        schema_files[path.name] = document
        kind = document.get(DOCUMENT_KEY)
        if isinstance(kind, str):
            if kind in kinds:
                raise workflow_invalid(
                    f"two schemas declare document kind {kind!r}: "
                    f"schemas/{kinds[kind]} and schemas/{path.name}"
                )
            kinds[kind] = path.name
    workflows = load_workflows(resolved / "workflows")
    provenance_path = resolved / PROVENANCE_FILENAME
    if not provenance_path.is_file():
        raise missing_provenance(
            f"{resolved}: {PROVENANCE_FILENAME} is missing, so no harvest record "
            "exists for this model"
        )
    with open(provenance_path, encoding="utf-8") as handle:
        provenance = json.load(handle)
    return DocumentModel(
        root=resolved,
        schema_files=schema_files,
        workflows=workflows,
        provenance=provenance,
    )


_DEFAULT_MODEL: Optional[DocumentModel] = None


def _default_model() -> DocumentModel:
    global _DEFAULT_MODEL
    if _DEFAULT_MODEL is None:
        _DEFAULT_MODEL = load_model()
    return _DEFAULT_MODEL


def validate_document(kind: str, document: Any) -> Dict[str, Any]:
    """Validate ``document`` against the shipped model (module-level default)."""
    return _default_model().validate_document(kind, document)


def envelope_for(kind: str, document: Any, request_id: str) -> Dict[str, Any]:
    """Validate and return the house envelope for the outcome.

    The success path and the refusal path are the same call, so a caller cannot
    accidentally report a refusal as a success — the envelope's ``ok`` flag is
    derived from the verdict, not passed in.
    """
    try:
        validated = _default_model().validate_document(kind, document)
    except ErpError as refusal:
        return error_envelope(refusal, request_id)
    return ok_envelope({"kind": kind, "document": validated}, request_id)


def check_assets(root: Optional[Path | str] = None) -> List[str]:
    """Every asset-contract violation in the model rooted at ``root``."""
    return load_model(root).check_assets()
