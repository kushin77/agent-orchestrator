"""The emitted OpenAPI document — generated from ERP-02, never hand-maintained (#651).

:func:`build_document` assembles the contract from sources, and restates none of
them:

* the **schemas** come from ``integrations/erp/core/schemas/*.json`` verbatim,
  with only one edit: every ``$ref`` is rewritten to point at the component the
  referenced file became (``document.schema.json#/$defs/identifier`` →
  ``#/components/schemas/document/$defs/identifier``, and a file-local
  ``#/$defs/x`` → the same component's ``/$defs/x``). OpenAPI 3.1's Schema Object
  *is* JSON Schema 2020-12, so an ERP-02 schema needs no translation at all — the
  document carries the model's own constraints, titles, descriptions and
  ``x-erp-*`` harvest records. The only key dropped is ``$id``, whose job (naming
  the file it came from) is done by :data:`x-erp-schema-sources` instead, because
  a URI inside a component claims a resolution the component does not have.
* the **routes** come from :mod:`integrations.erp.api.routes`, whose kind
  vocabulary and transition actions are re-derived from the model here.
* the **refusals** come from :mod:`integrations.erp.api.errors` — both the
  surface's own closed vocabulary and the model's, whose statuses that module
  reads out of the model's own factories.
* the **identity** statement is declared once, because it is the one thing ERP-02
  cannot supply: who the caller is.

:func:`serialize` is deterministic — sorted keys, two-space indent, one trailing
newline — so two builds over one revision are byte-identical and a diff means a
real change. ``scripts/check-erp-api.sh`` therefore compares the committed
``openapi.json`` to a fresh emission, which is what makes "never hand-maintained"
a measured property instead of a promise.

:func:`validate_document` re-derives every source and returns a finding per
divergence, naming the file, the component, the route or the status that drifted.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from integrations.erp.core import validators as core_validators

from . import errors as err
from . import health as health_module
from . import routes as routes_module

__all__ = [
    "EMITTED_ARTIFACT",
    "MODEL_ROOT",
    "PAYLOAD_COMPONENTS",
    "SCHEMAS_RELATIVE",
    "build_document",
    "byte_size",
    "component_name",
    "emit",
    "serialize",
    "validate_document",
]

#: The committed artifact the gate re-emits and compares against.
EMITTED_ARTIFACT = Path("integrations") / "erp" / "api" / "openapi.json"
#: Where the model lives, relative to the repository root.
MODEL_ROOT = Path("integrations") / "erp" / "core"
#: The schema directory, relative to the repository root (the provenance surface).
SCHEMAS_RELATIVE = MODEL_ROOT / "schemas"

#: The component reference prefix.
_COMPONENTS = "#/components/schemas"

#: The keys a component carries from its ERP-02 source. ``$id`` is the one
#: exception, and it is dropped: see the module docstring.
_DROPPED_SOURCE_KEYS = ("$id",)

#: The success shape of each handler verb: ``(status, the component it returns)``.
#: Declared rather than derived because a *choice* of status is a design decision;
#: :func:`validate_document` refuses a route with no entry, so the map cannot fall
#: behind the route table.
_SUCCESS: Mapping[str, Tuple[int, str]] = {
    "list": (200, "CollectionResponse"),
    "create": (201, "DocumentResponse"),
    "get": (200, "DocumentResponse"),
    "replace": (200, "DocumentResponse"),
    "delete": (200, "DeletedResponse"),
    "advance": (200, "TransitionResponse"),
    "openapi": (200, "OpenApiDocument"),
    "health": (200, "HealthReport"),
}

#: The components the document declares that are *not* ERP-02 schema files. Named
#: once, so :func:`validate_document` can refuse a component that is neither
#: derived from the model nor a declared payload of this surface.
PAYLOAD_COMPONENTS: Tuple[str, ...] = (
    "ErpDocument",
    "DocumentResponse",
    "CollectionResponse",
    "DeletedResponse",
    "OpenApiDocument",
    "TransitionResponse",
    "Envelope",
    "HealthReport",
)

#: One line of prose per route verb: what the operation does, and which module
#: implements it. Keyed by the route's handler name, like :data:`_SUCCESS` — and
#: the two maps are checked against the route table at import, below.
_OPERATION_DETAILS: Mapping[str, str] = {
    "list": "Reads every document of the kind in the caller's tenant; the field policy is applied by omission (integrations/erp/api/surface.py).",
    "create": "Validates the body against the kind's ERP-02 schema and its family rules before storing it (integrations/erp/api/store.py).",
    "get": "Answers 404 for a document that is absent and for one in another tenant: the surface is not an existence oracle.",
    "replace": "The body must describe the document the path addresses; a replace may not re-key a document.",
    "delete": "Removes the document and returns what was removed.",
    "advance": (
        "Moves the document through a transition its workflow declares, refusing an "
        "undeclared move with the model's own code (409). The reply reports the move — the "
        "document's identity and the state it reached — because a transition carries no "
        "caller-supplied fields; read the document through the read route to project it."
    ),
    "openapi": "The contract itself. It carries no tenant data, so it is not tenant-authorized — see x-erp-identity.",
    "health": "Real readings of the dependencies the surface names; answers 503 through `unavailable` when one is unreachable.",
}

#: The parameters no route may accept, whatever the caller asks for. The tenant is
#: the principal's and the scope team is the surface's, so a parameter naming
#: either would be a back door with a URL. :func:`validate_document` fails the
#: document if one ever appears.
FORBIDDEN_PARAMETERS: Tuple[str, ...] = ("tenant", "tenantId", "org", "orgId", "team", "teamId")

#: The five keys of the house envelope (``identity/cpapi/router.py``). Declared,
#: not derived — it is a frozen contract this lane consumes, and the check below
#: holds the declaration to it in both directions.
ENVELOPE_KEYS: Tuple[str, ...] = ("ok", "status", "requestId", "data", "error")


def component_name(filename: str) -> str:
    """The component a schema file becomes: ``sales-order.schema.json`` → ``sales-order``."""
    name = Path(filename).name
    for suffix in (".schema.json", ".json"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def schema_source(name: str) -> str:
    """The repository-relative path of the ERP-02 file a component came from."""
    return (SCHEMAS_RELATIVE / f"{name}.schema.json").as_posix()


def _rewrite_ref(ref: str, own: str) -> str:
    """Point one ``$ref`` at the component its target file became.

    The fragment *continues* the pointer rather than starting a second one:
    ``document.schema.json#/$defs/identifier`` and a file-local ``#/$defs/x`` both
    become ``#/components/schemas/document/$defs/...``, which is one JSON pointer
    into this document. Emitting a second ``#`` there would produce a reference no
    client — and no reader of this file — can resolve, which is why
    :func:`validate_document` resolves every reference it emits.
    """
    file_part, _, fragment = ref.partition("#")
    target = component_name(file_part) if file_part else own
    return f"{_COMPONENTS}/{target}{fragment}"


def _rewrite(node: Any, own: str) -> Any:
    """Rewrite every ``$ref`` in ``node``, leaving every other key exactly as it is."""
    if isinstance(node, dict):
        return {
            key: (_rewrite_ref(value, own) if key == "$ref" and isinstance(value, str) else _rewrite(value, own))
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_rewrite(item, own) for item in node]
    return node


def schema_components(model: Any) -> Dict[str, Dict[str, Any]]:
    """Every ERP-02 schema as a component, keyed by :func:`component_name`."""
    out: Dict[str, Dict[str, Any]] = {}
    for filename in sorted(model.schema_files):
        name = component_name(filename)
        source = model.schema_files[filename]
        out[name] = {
            key: _rewrite(value, name)
            for key, value in source.items()
            if key not in _DROPPED_SOURCE_KEYS
        }
    return out


def _kind_to_component(model: Any) -> Dict[str, str]:
    """``kind -> component name`` for every kind an ERP-02 schema declares."""
    mapping: Dict[str, str] = {}
    for filename in sorted(model.schema_files):
        kind = model.schema_files[filename].get(core_validators.DOCUMENT_KEY)
        if isinstance(kind, str):
            mapping[kind] = component_name(filename)
    return mapping


def erp_document_component(model: Any) -> Dict[str, Any]:
    """The union of the document families, discriminated by ``doctype``."""
    mapping = _kind_to_component(model)
    return {
        "title": "ErpDocument",
        "description": (
            "One ERP-02 document. The families are `oneOf` because a document is "
            "exactly one of them, and `discriminator.mapping` reproduces the "
            "`const` each family pins its `doctype` to — so a client can route a "
            "document to its family without a schema of its own."
        ),
        "oneOf": [{"$ref": f"{_COMPONENTS}/{mapping[kind]}"} for kind in sorted(mapping)],
        "discriminator": {
            "propertyName": "doctype",
            "mapping": {kind: f"{_COMPONENTS}/{mapping[kind]}" for kind in sorted(mapping)},
        },
    }


def error_codes() -> Tuple[str, ...]:
    """Every code this surface can emit, sorted: the model's and its own."""
    return tuple(sorted(set(err.BOUNDARY_CODES) | set(err.MODEL_STATUS)))


def envelope_component() -> Dict[str, Any]:
    """The house response envelope, with the closed code vocabulary it can carry."""
    return {
        "title": "Envelope",
        "description": (
            "The one response shape this surface emits — `identity/cpapi/router.py`'s "
            "`ok_envelope`/`error_envelope` — reused rather than restated, so a client "
            "that speaks the control-plane dialect needs no second one. `error.code` is "
            "the closed vocabulary: every code here is raised somewhere below this "
            "document, and `check` refuses a code with no source."
        ),
        "type": "object",
        "required": list(ENVELOPE_KEYS),
        "additionalProperties": False,
        "properties": {
            "ok": {"type": "boolean"},
            "status": {"type": "integer", "enum": sorted({200, 201, *err.STATUSES})},
            "requestId": {"type": "string"},
            "data": {"description": "The success payload; `null` when `ok` is false."},
            "error": {
                "oneOf": [
                    {"type": "null"},
                    {
                        "type": "object",
                        "required": ["code", "message", "details"],
                        "additionalProperties": False,
                        "properties": {
                            "code": {"type": "string", "enum": list(error_codes())},
                            "message": {"type": "string"},
                            "details": {"type": "object"},
                        },
                    },
                ]
            },
        },
    }


def _shape(value: Any) -> Dict[str, Any]:
    """A JSON Schema inferred from one value of the module's own shape."""
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, dict):
        return {
            "type": "object",
            "properties": {key: _shape(item) for key, item in sorted(value.items())},
            "required": sorted(value),
            "additionalProperties": False,
        }
    if isinstance(value, list):
        return {"type": "array", "items": _shape(value[0]) if value else {}}
    return {}


def health_component() -> Dict[str, Any]:
    """The health report's shape, derived from a report the module really builds."""
    sample = health_module.HealthReport(
        status=health_module.STATUS_OK,
        http_status=200,
        dependencies=(
            health_module.DependencyState("erp-02-model", health_module.STATE_OK, "the model's assets hold"),
        ),
    ).to_dict()
    schema = _shape(sample)
    schema["title"] = "HealthReport"
    schema["description"] = (
        "The health verdict and the real per-dependency readings behind it "
        "(integrations/erp/api/health.py). A dependency that is missing makes the "
        "route answer 503 through `unavailable`, so a non-ok dependency is never "
        "reported as ok."
    )
    return schema


def payload_components(model: Any) -> Dict[str, Any]:
    """The response payloads, written in terms of the derived document union."""
    return {
        "DocumentResponse": {
            "title": "DocumentResponse",
            "description": "One document as this principal may see it.",
            "type": "object",
            "required": ["document", "redacted"],
            "additionalProperties": False,
            "properties": {
                "document": {"$ref": f"{_COMPONENTS}/ErpDocument"},
                "redacted": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "The fields withheld from this response by field policy, named so the "
                        "withholding is auditable. A withheld field is absent from `document`, "
                        "never blanked — absent and empty are different claims."
                    ),
                },
            },
        },
        "CollectionResponse": {
            "title": "CollectionResponse",
            "description": "Every document of one kind in the caller's tenant, ordered by id.",
            "type": "object",
            "required": ["kind", "count", "items", "redacted"],
            "additionalProperties": False,
            "properties": {
                "kind": {"type": "string", "enum": list(model.document_kinds())},
                "count": {"type": "integer", "minimum": 0},
                "items": {"type": "array", "items": {"$ref": f"{_COMPONENTS}/ErpDocument"}},
                "redacted": {"type": "array", "items": {"type": "string"}},
            },
        },
        "DeletedResponse": {
            "title": "DeletedResponse",
            "description": "The document that was removed — an id, not an assertion that something happened.",
            "type": "object",
            "required": ["deleted"],
            "additionalProperties": False,
            "properties": {
                "deleted": {
                    "type": "object",
                    "required": ["kind", "id"],
                    "additionalProperties": False,
                    "properties": {
                        "kind": {"type": "string", "enum": list(model.document_kinds())},
                        "id": {"$ref": f"{_COMPONENTS}/document/$defs/identifier"},
                    },
                }
            },
        },
        "TransitionResponse": {
            "title": "TransitionResponse",
            "description": (
                "One declared transition, as it was carried out: the document's identity, "
                "the state it reached, and the action that moved it. It carries no fields, "
                "because a transition has none — `document` is deliberately not part of this "
                "shape, so a caller reads the document through the read route where field "
                "policy is applied by omission."
            ),
            "type": "object",
            "required": ["transition", "action"],
            "additionalProperties": False,
            "properties": {
                "transition": {
                    "type": "object",
                    "required": ["doctype", "id", "state", "docstatus"],
                    "additionalProperties": False,
                    "properties": {
                        "doctype": {"type": "string", "enum": list(model.document_kinds())},
                        "id": {"$ref": f"{_COMPONENTS}/document/$defs/identifier"},
                        "state": {"type": "string"},
                        "docstatus": {"$ref": f"{_COMPONENTS}/document/$defs/docstatus"},
                    },
                },
                "action": {"type": "string"},
            },
        },
        "OpenApiDocument": {
            "title": "OpenApiDocument",
            "description": "This document, as the surface serves it.",
            "type": "object",
        },
    }


def _refusal_responses() -> Dict[str, Any]:
    """One response component per status, naming every code that reaches it."""
    by_status: Dict[int, List[str]] = {}
    for status, code in err.EMITTED_REFUSALS:
        by_status.setdefault(status, []).append(code)
    return {
        str(status): {
            "description": (
                f"Refused ({status}) with code "
                + ", ".join(f"`{code}`" for code in sorted(by_status[status]))
                + "."
            ),
            "content": {"application/json": {"schema": {"$ref": f"{_COMPONENTS}/Envelope"}}},
        }
        for status in sorted(by_status)
    }


def parameters_component(model: Any) -> Dict[str, Any]:
    """The three path parameters. There is deliberately no fourth."""
    return {
        "kind": {
            "name": routes_module.KIND_PARAM,
            "in": "path",
            "required": True,
            "description": (
                "The document kind. The enum is the model's own closed kind vocabulary, "
                "derived from integrations/erp/core/schemas — an unknown kind is refused "
                "by the model before anything about the caller is considered."
            ),
            "schema": {"type": "string", "enum": list(model.document_kinds())},
        },
        "documentId": {
            "name": routes_module.DOCUMENT_ID_PARAM,
            "in": "path",
            "required": True,
            "description": "The document's identifier, in ERP-02's own identifier shape.",
            "schema": {"$ref": f"{_COMPONENTS}/document/$defs/identifier"},
        },
        "action": {
            "name": routes_module.ACTION_PARAM,
            "in": "path",
            "required": True,
            "description": (
                "A transition the addressed document's workflow declares from its current "
                "state; `x-erp-transitions` is the derivation, per kind and state. Whether "
                "the move is legal is the workflow's decision — a static enum here would "
                "have to union every state's actions and would describe moves that are "
                "refused."
            ),
            "schema": {"type": "string", "minLength": 1},
        },
    }


def _parameters_for(route: routes_module.Route) -> List[Dict[str, Any]]:
    """The parameter references an operation declares, in template order."""
    return [{"$ref": f"#/components/parameters/{name}"} for name in route.params]


def operation(route: routes_module.Route, model: Any) -> Dict[str, Any]:
    """One operation, described from the route's own declaration."""
    status, component = _SUCCESS[route.name]
    op: Dict[str, Any] = {
        "operationId": route.operation_id,
        "tags": [route.tag],
        "summary": route.summary,
        "description": _OPERATION_DETAILS[route.name],
        "x-erp-action": route.action or "none",
        "x-erp-mutation": route.mutation,
        "security": [],
        "responses": {
            str(status): {
                "description": route.summary,
                "content": {"application/json": {"schema": {"$ref": f"{_COMPONENTS}/{component}"}}},
            }
        },
    }
    if route.params:
        op["parameters"] = _parameters_for(route)
    if route.body_required:
        op["requestBody"] = {
            "required": True,
            "description": (
                "The document, validated by ERP-02 before it is stored: its family schema "
                "and its family rules (a GL posting's debits must equal its credits; a "
                "stock transfer must move between two warehouses)."
            ),
            "content": {"application/json": {"schema": {"$ref": f"{_COMPONENTS}/ErpDocument"}}},
        }
    for refused in err.STATUSES:
        op["responses"][str(refused)] = {"$ref": f"#/components/responses/{refused}"}
    return op


def paths(model: Any) -> Dict[str, Any]:
    """Every path the route table declares, described once."""
    out: Dict[str, Any] = {}
    for route in routes_module.TABLE.entries:
        out.setdefault(route.template, {})[route.method.lower()] = operation(route, model)
    return out


def route_declarations() -> List[Dict[str, Any]]:
    """The route table, as the document carries it — the derivation a client can diff."""
    return [
        {
            "method": route.method,
            "path": route.template,
            "operationId": route.operation_id,
            "action": route.action or "none",
            "kindScoped": route.kind_scoped,
            "mutation": route.mutation,
        }
        for route in routes_module.TABLE.entries
    ]


def _error_declarations() -> List[Dict[str, Any]]:
    return [
        {
            "status": status,
            "code": code,
            "origin": "model" if code in err.MODEL_STATUS else "surface",
        }
        for status, code in err.EMITTED_REFUSALS
    ]


def _identity_declaration() -> Dict[str, Any]:
    return {
        "scheme": "none-in-this-contract",
        "authorization": "integrations/erp/auth (ERP-08)",
        "tenantSource": "the principal, never the request",
        "teamSource": "the surface's declared scope team, never the request",
        "detail": (
            "This surface carries no credential and declares no security scheme, because "
            "it has none to declare (GR-6): the adapter that mounts it supplies a "
            "Principal, which is reference-based — a tenant id, a subject id and role "
            "names, with no field a token or password could occupy. Every tenant-scoped "
            "route is authorized by integrations/erp/auth *before* its body is read, and "
            "the request's tenant is the principal's own, so there is no parameter, header "
            "or body field that can select another tenant. The two routes marked with an "
            "empty security list carry no tenant data: one is this document, the other is "
            "the declarations' reachability."
        ),
    }


def build_document(root: Path) -> Dict[str, Any]:
    """Assemble the OpenAPI document from every source (see the module docstring)."""
    resolved = Path(root)
    model = core_validators.load_model(resolved / MODEL_ROOT)
    schemas: Dict[str, Any] = dict(schema_components(model))
    schemas["ErpDocument"] = erp_document_component(model)
    schemas.update(payload_components(model))
    schemas["Envelope"] = envelope_component()
    schemas["HealthReport"] = health_component()

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "ERP document API",
            "version": "1.0.0",
            "description": (
                "The REST surface of the ERP module (issue #651, EPIC #645): CRUD and "
                "declared workflow transitions over the indexer-fed document model of "
                "ERP-02, so the portal and external consumers integrate through one "
                "contract. Every component schema in this document is *emitted* from "
                "integrations/erp/core/schemas — no part of it is hand-maintained, and "
                "the gate compares the committed artifact to a fresh emission byte for "
                "byte. Authorization is not implemented here: it is delegated to "
                "integrations/erp/auth (ERP-08) and this surface has no path that "
                "bypasses it."
            ),
        },
        "servers": [
            {
                "url": "/",
                "description": (
                    "The surface is transport-free and mounted by an adapter; its routes are "
                    "absolute paths, and this document declares no host, port or scheme it "
                    "does not own."
                ),
            }
        ],
        "paths": paths(model),
        "components": {
            "schemas": schemas,
            "responses": _refusal_responses(),
            "parameters": parameters_component(model),
        },
        "x-erp-model": {
            "root": MODEL_ROOT.as_posix(),
            "kinds": list(model.document_kinds()),
            "lifecycleKinds": list(model.lifecycle_kinds()),
        },
        "x-erp-transitions": {
            kind: {state: list(actions) for state, actions in sorted(states.items())}
            for kind, states in sorted(routes_module.transitions(model).items())
        },
        "x-erp-action-permissions": dict(sorted(routes_module.ACTION_PERMISSION.items())),
        "x-erp-schema-sources": {
            name: schema_source(name)
            for name in sorted(set(schema_components(model)))
        },
        "x-erp-errors": _error_declarations(),
        "x-erp-identity": _identity_declaration(),
        "x-erp-routes": route_declarations(),
    }


def serialize(document: Mapping[str, Any]) -> str:
    """The canonical serialization: sorted keys, 2-space indent, trailing newline."""
    return json.dumps(dict(document), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def byte_size(document: Mapping[str, Any]) -> int:
    """The emitted document's size in **bytes**.

    ``len()`` on the serialization counts *characters*, and the ERP-02 descriptions
    carry non-ASCII (em dashes), so reporting ``len()`` as bytes made the check's
    number disagree with the ``wc -c`` on the very next line of the gate — the kind
    of small false precision that erodes trust in the rest of the evidence.
    """
    return len(serialize(document).encode("utf-8"))


def emit(root: Path, out: Optional[Path] = None) -> str:
    """Serialize the document and (when ``out`` is given) write it to disk."""
    text = serialize(build_document(Path(root)))
    if out is not None:
        Path(out).write_text(text, encoding="utf-8")
    return text


def validate_document(document: Mapping[str, Any], root: Path) -> List[str]:
    """Re-derive every source and return a finding per drift; empty is OK.

    Each finding names the offender — the ERP-02 file a component drifted from, the
    route the document omits or invents, the status it under- or over-declares, the
    parameter that would be a back door — so a failure points at the artifact.
    """
    findings: List[str] = []
    resolved = Path(root)
    model = core_validators.load_model(resolved / MODEL_ROOT)

    components = document.get("components") or {}
    schemas = components.get("schemas") or {}
    paths_declared = document.get("paths") or {}

    # 1. every component is a faithful view of its ERP-02 file (the only edit
    #    being the $ref rewrite, and the $id drop, both of which are re-derived).
    fresh_schemas = schema_components(model)
    for name, schema in sorted(fresh_schemas.items()):
        if schemas.get(name) != schema:
            findings.append(
                f"component '{name}' drifts from its ERP-02 source {schema_source(name)}"
            )
    for name in sorted(schemas):
        if name not in fresh_schemas and name not in PAYLOAD_COMPONENTS:
            findings.append(f"component '{name}' is not derived from any ERP-02 schema")

    # 2. the document union and its discriminator are the model's families.
    expected_union = erp_document_component(model)
    if schemas.get("ErpDocument") != expected_union:
        findings.append(
            "component 'ErpDocument' no longer matches the model's document families "
            "(its oneOf or its discriminator mapping drifted)"
        )

    # 3. the kind vocabulary is the model's.
    kind_param = (components.get("parameters") or {}).get(
        routes_module.KIND_PARAM
    ) or {}
    declared_enum = (kind_param.get("schema") or {}).get("enum")
    if declared_enum != list(model.document_kinds()):
        findings.append(
            f"the '{routes_module.KIND_PARAM}' parameter's enum is not the model's kind "
            f"vocabulary ({declared_enum})"
        )

    # 4. no parameter anywhere could select a tenant (or the scope team).
    for where, parameter in _walk_parameters(document):
        name = str(parameter.get("name", ""))
        if name in FORBIDDEN_PARAMETERS:
            findings.append(
                f"{where}: a parameter named {name!r} would let a client select the scope "
                "the surface decides for it — the tenant comes from the principal and the "
                "team from the surface, so no such parameter may exist"
            )

    # 5. no credential surface: the contract has no security scheme to declare.
    if components.get("securitySchemes"):
        findings.append(
            "the document declares a security scheme, but this surface carries no "
            "credential (GR-6) — the adapter establishes identity"
        )

    # 6. every route is described, and nothing is described that is not a route.
    described = {
        (method.upper(), path)
        for path, item in paths_declared.items()
        for method in item
    }
    declared = {(route.method, route.template) for route in routes_module.TABLE.entries}
    for method, path in sorted(declared - described):
        findings.append(f"the route {method} {path} is declared by the table but missing from the document")
    for method, path in sorted(described - declared):
        findings.append(f"the document describes {method} {path}, which the route table does not declare")

    # 7. every operation carries the whole refusal vocabulary it can emit, and the
    #    success shape its handler produces.
    for route in routes_module.TABLE.entries:
        operation_body = (paths_declared.get(route.template) or {}).get(route.method.lower())
        if not operation_body:
            continue
        success, component = _SUCCESS.get(route.name, (None, ""))
        if success is None:
            findings.append(f"the route {route.operation_id!r} has no declared success shape")
        elif str(success) not in (operation_body.get("responses") or {}):
            findings.append(
                f"the operation {route.operation_id!r} does not declare its {success} response"
            )
        elif (
            (operation_body["responses"][str(success)].get("content", {}).get("application/json", {}).get("schema", {}).get("$ref"))
            != f"{_COMPONENTS}/{component}"
        ):
            findings.append(
                f"the operation {route.operation_id!r} returns a different component than "
                f"'{component}'"
            )
        responses = operation_body.get("responses") or {}
        missing = [str(status) for status in err.STATUSES if str(status) not in responses]
        if missing:
            findings.append(
                f"the operation {route.operation_id!r} omits the refusal statuses {missing}"
            )
        if operation_body.get("security") != []:
            findings.append(
                f"the operation {route.operation_id!r} declares a security requirement, "
                "but this surface carries no credential"
            )
        for param in operation_body.get("parameters") or []:
            ref = (param or {}).get("$ref", "")
            if ref and ref.rsplit("/", 1)[-1] not in (components.get("parameters") or {}):
                findings.append(
                    f"the operation {route.operation_id!r} references the parameter {ref!r}, "
                    "which the document does not declare"
                )

    # 8. the refusals are declared exactly, and each one names its origin.
    declared_responses = set((components.get("responses") or {}))
    expected_responses = {str(status) for status in err.STATUSES}
    for status in sorted(expected_responses - declared_responses):
        findings.append(f"the document declares no response component for status {status}")
    for status in sorted(declared_responses - expected_responses):
        findings.append(
            f"the document declares a response component for status {status}, which the "
            "surface cannot emit"
        )
    error_declarations = document.get("x-erp-errors")
    if error_declarations != _error_declarations():
        findings.append(
            "x-erp-errors does not equal the surface's refusal vocabulary "
            f"(declared {error_declarations})"
        )

    # 9. the envelope carries the whole code vocabulary and the five house keys.
    envelope = schemas.get("Envelope") or {}
    if sorted(envelope.get("required") or []) != sorted(ENVELOPE_KEYS):
        findings.append(
            f"the Envelope component does not require the house keys {list(ENVELOPE_KEYS)}"
        )
    code_enum = (
        ((envelope.get("properties") or {}).get("error") or {}).get("oneOf") or [{}, {}]
    )[1]
    declared_codes = ((code_enum.get("properties") or {}).get("code") or {}).get("enum")
    if declared_codes != list(error_codes()):
        findings.append(
            "the Envelope's error code enum is not the surface's closed refusal vocabulary"
        )

    # 10. the transition derivation and the schema provenance are the model's.
    expected_transitions = {
        kind: {state: list(actions) for state, actions in sorted(states.items())}
        for kind, states in sorted(routes_module.transitions(model).items())
    }
    if document.get("x-erp-transitions") != expected_transitions:
        findings.append(
            "x-erp-transitions does not equal the model's workflow derivation "
            "(a state's declared moves drifted)"
        )
    sources = document.get("x-erp-schema-sources") or {}
    for name in sorted(fresh_schemas):
        if sources.get(name) != schema_source(name):
            findings.append(
                f"x-erp-schema-sources does not name the ERP-02 file for component '{name}'"
            )
        elif not (resolved / sources[name]).is_file():
            findings.append(f"the ERP-02 source {sources[name]} does not exist")

    # 11. the route declarations are the table's, verbatim.
    if document.get("x-erp-routes") != route_declarations():
        findings.append("x-erp-routes does not equal the route table")

    # 12. declared identity is the surface's real one.
    if (document.get("x-erp-identity") or {}).get("tenantSource") != "the principal, never the request":
        findings.append(
            "x-erp-identity no longer declares that the tenant comes from the principal"
        )

    # 13. every $ref resolves inside this document. A generated document's
    #     classic failure is a reference to a component that was renamed or never
    #     emitted, and a client cannot resolve it any better than this can.
    for where, ref in _walk_refs(document):
        if not str(ref).startswith("#"):
            findings.append(f"{where}: the $ref {ref!r} leaves the document")
            continue
        if _resolve_pointer(document, str(ref)) is None:
            findings.append(f"{where}: the $ref {ref!r} does not resolve in this document")
    return findings


def _walk_refs(node: Any, where: str = "$") -> List[Tuple[str, str]]:
    """Every ``$ref`` in the document, with the JSON path that carries it."""
    found: List[Tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                found.append((where, value))
            else:
                found.extend(_walk_refs(value, f"{where}.{key}"))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found.extend(_walk_refs(item, f"{where}[{index}]"))
    return found


def _resolve_pointer(document: Mapping[str, Any], pointer: str) -> Optional[Any]:
    """Resolve a ``#/a/b`` JSON pointer, or ``None`` when it does not resolve."""
    node: Any = document
    for token in pointer.lstrip("#").lstrip("/").split("/"):
        if token == "":
            return None
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, Mapping) and token in node:
            node = node[token]
        elif isinstance(node, list) and token.isdigit() and int(token) < len(node):
            node = node[int(token)]
        else:
            return None
    return node


def _walk_parameters(document: Mapping[str, Any]) -> Sequence[Tuple[str, Mapping[str, Any]]]:
    """Every declared parameter, with the place it is declared."""
    found: List[Tuple[str, Mapping[str, Any]]] = []
    components = document.get("components") or {}
    for name, parameter in sorted((components.get("parameters") or {}).items()):
        found.append((f"components.parameters.{name}", parameter))
    for path, item in sorted((document.get("paths") or {}).items()):
        for method, operation_body in sorted(item.items()):
            for index, parameter in enumerate((operation_body or {}).get("parameters") or []):
                if "$ref" in parameter:
                    continue  # a reference to a declared parameter, checked at its declaration
                found.append((f"{method.upper()} {path} parameters[{index}]", parameter))
    return tuple(found)


_ROUTE_NAMES = frozenset(route.name for route in routes_module.TABLE.entries)
if set(_SUCCESS) != _ROUTE_NAMES or set(_OPERATION_DETAILS) != _ROUTE_NAMES:
    # A route with no success shape or no description would raise a KeyError while
    # emitting the document — inside `emit`, next to a half-written artifact. It
    # fails here instead, naming the divergence, because a document that cannot be
    # emitted is not a contract this lane can ship.
    raise RuntimeError(
        "the route table, the success shapes and the operation descriptions disagree: "
        f"routes={sorted(_ROUTE_NAMES)} success={sorted(_SUCCESS)} "
        f"details={sorted(_OPERATION_DETAILS)}"
    )
