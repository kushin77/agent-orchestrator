"""The emitted OpenAPI document for the paperclip-shaped surface (issue #413).

:func:`build_document` assembles the document from its sources — the frozen seam
contracts (:mod:`~integrations.paperclip.api.contracts`), the route surface read
from the adapter's own client (:mod:`~integrations.paperclip.api.surface`), the
mapped error taxonomy (:mod:`~integrations.paperclip.api.taxonomy`), the one
declared company mapping (:mod:`~integrations.paperclip.api.company`) and the
real health read (:mod:`~integrations.paperclip.api.health`). It restates none
of them.

:func:`serialize` is deterministic — sorted keys, fixed indentation, one trailing
newline — so two builds over one revision are byte-identical and a diff means a
real change. :func:`validate_document` re-derives every source and refuses, by
name, a document that has drifted from its contract, from the client's own
routes, from the taxonomy, or that has lost the company mapping or health path.

---knowledge---
module_id: integrations.paperclip.api.openapi
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: [health_schema, unshaped_schema, build_document, serialize, emit, validate_document]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import company as _company
from . import contracts as _contracts
from . import health as _health
from . import surface as _surface
from . import taxonomy as _taxonomy

#: The committed artifact the gate re-emits and compares against.
EMITTED_ARTIFACT = Path("integrations") / "paperclip" / "api" / "openapi.json"

#: The path prefix every route shares (client.API_PREFIX).
API_PREFIX = _surface.API_PREFIX

#: The company-scoped path template, exactly the upstream route shape.
COMPANY_PATH = _surface.COMPANY_PATH

#: Company resource segment -> the component its collection projects onto. A
#: segment the client serves but this map does not name is a finding, not a
#: silent omission (fail closed).
SEGMENT_COMPONENT: Dict[str, str] = {
    "agents": "Agent",
    "issues": "Ticket",
    "costs": "Budget",
    "activity": "Activity",
    "approvals": "Approval",
    "dashboard": "Dashboard",
}

#: Segment -> the human summary of the collection.
SEGMENT_SUMMARY: Dict[str, str] = {
    "agents": "The agents the fleet registry hires for this company (profiles + persona cards).",
    "issues": "The company's tickets — the ticket contract v2 projected from the claim ledger and board snapshot.",
    "costs": "The company's budget seam records projected from the fleet budget rail.",
    "activity": "The company's append-only activity, projected from the audit ledger.",
    "approvals": "The company's approvals (budget top-ups and policy exceptions).",
    "dashboard": "The company's dashboard rollup.",
}

#: Segments whose response shape the adapter does not define. The document says
#: so rather than inventing a second description (the lane's own instruction):
#: the schema is permissive and carries the reason.
UNSHAPED_SEGMENTS: Tuple[str, ...] = ("dashboard",)


def health_schema() -> Dict[str, Any]:
    """The ``/api/health`` response schema, derived from the report's own shape."""
    sample = _health.HealthReport(
        status=_health.STATUS_OK,
        http_status=200,
        dependencies=(
            _health.DependencyState("claim_ledger", _health.STATE_OK, "0 event(s) readable"),
            _health.DependencyState("ticket_projection", _health.STATE_OK, "the projection is 0.0 min old"),
        ),
    )
    schema = _contracts.derive_schema(sample.to_dict())
    schema["title"] = "HealthReport"
    schema["description"] = (
        "The health verdict and the real per-dependency readings behind it "
        "(integrations/paperclip/api/health.py). A non-ok dependency is never reported as ok."
    )
    return schema


def unshaped_schema(segment: str) -> Dict[str, Any]:
    """A permissive schema for a segment the adapter defines no typed shape for.

    The adapter's client serves the route but ``integrations/paperclip/model.py``
    declares no record for it, so there is nothing to introspect. Rather than
    invent a second description, the schema states the gap by name and stays
    permissive — an honest unknown, not a fabricated shape.
    """
    return {
        "title": segment.capitalize(),
        "description": (
            f"The adapter's client serves the company's {segment} route, but "
            "integrations/paperclip/model.py declares no typed record for it, so no shape "
            "is introspectable. This schema is deliberately permissive and records that gap "
            "rather than inventing a parallel description."
        ),
        "type": "object",
        "x-shape-provenance": (
            "not introspectable — integrations/paperclip/model.py defines no "
            f"{segment.capitalize()} record; described permissively, not invented"
        ),
    }


def _error_responses() -> Dict[str, Any]:
    """Every taxonomy status, as a ``$ref`` to the shared response component."""
    return {
        str(status): {"$ref": f"#/components/responses/{status}"}
        for status in _taxonomy.statuses()
    }


def _collection_response(component: str) -> Dict[str, Any]:
    return {
        "description": f"The company's {component} collection.",
        "content": {
            "application/json": {
                "schema": {
                    "type": "array",
                    "items": {"$ref": f"#/components/schemas/{component}"},
                }
            }
        },
    }


def _company_operation(route: _surface.Route, component: str) -> Dict[str, Any]:
    responses: Dict[str, Any] = {"200": _collection_response(component)}
    responses.update(_error_responses())
    return {
        "operationId": route.operation_id,
        "tags": ["company"],
        "summary": SEGMENT_SUMMARY.get(route.segment, f"The company's {route.segment} collection."),
        "parameters": [{"$ref": "#/components/parameters/companyId"}],
        "security": [{"agentKey": []}, {"boardToken": []}],
        "responses": responses,
    }


def _health_operation() -> Dict[str, Any]:
    return {
        "operationId": "getHealth",
        "tags": ["platform"],
        "summary": "Real health of the dependencies the surface names.",
        "description": (
            "Reads the claim ledger (readable) and the ticket projection (fresh). "
            "Reports 503 when a named dependency is unreachable, never a green lie."
        ),
        "security": [],
        "responses": {
            "200": {
                "description": "Every named dependency is ok (or merely stale — see status).",
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/HealthReport"}}
                },
            },
            "503": {"$ref": "#/components/responses/503"},
        },
    }


def _openapi_operation() -> Dict[str, Any]:
    return {
        "operationId": "getOpenApi",
        "tags": ["platform"],
        "summary": "This OpenAPI document.",
        "security": [],
        "responses": {
            "200": {
                "description": "The OpenAPI 3.1 document.",
                "content": {"application/json": {"schema": {"type": "object"}}},
            }
        },
    }


def _paths() -> Dict[str, Any]:
    """Every path the adapter's client emits, described once.

    The route set is read from the client (``surface.client_routes``): the two
    unscoped routes get their own response shapes, and each company-scoped route
    is described with its component and the full error taxonomy. A company
    segment with no declared component is left out and reported by
    :func:`validate_document`, never silently invented.
    """
    paths: Dict[str, Any] = {
        f"{API_PREFIX}/health": {"get": _health_operation()},
        f"{API_PREFIX}/openapi.json": {"get": _openapi_operation()},
    }
    for route in _surface.company_routes():
        component = SEGMENT_COMPONENT.get(route.segment)
        if component is None:
            continue
        paths[route.path] = {route.method.lower(): _company_operation(route, component)}
    return paths


def build_document(root: Path) -> Dict[str, Any]:
    """Assemble the OpenAPI document from every source (see the module docstring)."""
    schemas: Dict[str, Any] = {}
    schemas.update(_contracts.contract_components(Path(root)))
    schemas.update(_contracts.adapter_components())
    schemas["Error"] = _contracts.error_schema()
    schemas["HealthReport"] = health_schema()
    for segment in UNSHAPED_SEGMENTS:
        schemas[SEGMENT_COMPONENT[segment]] = unshaped_schema(segment)

    declared = _company.mapping()
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Fleet paperclip projection API",
            "version": "1.0.0",
            "description": (
                "The fleet's own state projected over the upstream paperclip HTTP surface "
                "(docs/PAPERCLIP-ING-INTEGRATION.md, ADR-0013). The document is emitted from the "
                "frozen contracts in docs/contracts/paperclip/, the adapter's own to_dict shapes "
                "and the adapter client's own routes; company scope projects the fleet's tenancy; "
                "the error taxonomy is mapped."
            ),
        },
        "servers": [
            {
                "url": "http://localhost:3100",
                "description": "local dev base (the upstream surface runs its own process)",
            }
        ],
        "paths": _paths(),
        "components": {
            "schemas": schemas,
            "responses": _taxonomy.responses(),
            "parameters": {
                "companyId": {
                    "name": "companyId",
                    "in": "path",
                    "required": True,
                    "description": (
                        "The upstream company scope. It is the fleet's own Org/Tenant id through the "
                        "one declared mapping — never a second tenancy."
                    ),
                    "schema": {"type": "string", "minLength": 1},
                    "x-company-mapping": declared["id"],
                }
            },
            "securitySchemes": {
                "agentKey": {"type": "http", "scheme": "bearer", "description": "agent key/JWT"},
                "boardToken": {"type": "http", "scheme": "bearer", "description": "board token"},
            },
        },
        "x-company-mapping": declared,
        "x-error-taxonomy": _taxonomy.entries(),
        "x-contract-sources": _contracts.contract_sources(),
        "x-shape-provenance": {
            name: schema["x-shape-provenance"]
            for name, schema in _contracts.adapter_components().items()
        },
        "x-client-routes": [
            {"method": route.method, "path": route.path, "operationId": route.operation_id}
            for route in _surface.client_routes()
        ],
    }


def serialize(document: Dict[str, Any]) -> str:
    """The canonical serialization: sorted keys, 2-space indent, trailing newline."""
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def emit(root: Path, out: Optional[Path] = None) -> str:
    """Serialize the document and (when ``out`` is given) write it to disk."""
    text = serialize(build_document(Path(root)))
    if out is not None:
        Path(out).write_text(text, encoding="utf-8")
    return text


def validate_document(document: Dict[str, Any], root: Path) -> List[str]:
    """Re-derive every source and return a finding per drift; empty is OK.

    Each finding names the offender: the contract file a schema drifted from, the
    client route the document omits or invents, the taxonomy status that is
    missing, the undeclared company mapping, the lost health path.
    """
    findings: List[str] = []
    components = document.get("components") or {}
    schemas = components.get("schemas") or {}
    paths = document.get("paths") or {}

    # 1. the contracts are the source — a drifted schema is refused by name.
    fresh_contracts = _contracts.contract_components(Path(root))
    sources = _contracts.contract_sources()
    for name, schema in fresh_contracts.items():
        if schemas.get(name) != schema:
            findings.append(
                f"schema '{name}' drifts from the frozen contract {sources[name]}"
            )

    # 2. the adapter shapes are the adapter's own.
    for name, schema in _contracts.adapter_components().items():
        if schemas.get(name) != schema:
            findings.append(
                f"schema '{name}' no longer matches the adapter's own to_dict shape"
            )
    if schemas.get("HealthReport") != health_schema():
        findings.append("schema 'HealthReport' no longer matches the health module's report shape")
    for segment in UNSHAPED_SEGMENTS:
        if schemas.get(SEGMENT_COMPONENT[segment]) != unshaped_schema(segment):
            findings.append(
                f"schema '{SEGMENT_COMPONENT[segment]}' no longer records the unshaped '{segment}' segment honestly"
            )

    # 3. the routes are the client's own — an omitted or invented route is refused.
    routes = _surface.client_routes()
    for route in routes:
        operation = (paths.get(route.path) or {}).get(route.method.lower())
        if not operation:
            findings.append(
                f"the adapter client route '{route.method} {route.path}' is missing from the document"
            )
            continue
        if route.company_scoped and operation.get("operationId") != route.operation_id:
            findings.append(
                f"the route '{route.path}' is described with a different operation id than the client emits"
            )
    for path, item in sorted(paths.items()):
        for method in sorted(item):
            if not any(r.method.lower() == method and r.path == path for r in routes):
                findings.append(
                    f"the document describes a route '{method.upper()} {path}' the client does not emit"
                )

    # 4. the taxonomy is mapped — a missing status entry is refused by name.
    responses = components.get("responses") or {}
    for entry in _taxonomy.entries():
        status = str(entry["status"])
        if status not in responses:
            findings.append(
                f"the error taxonomy entry '{status}' is missing from components.responses"
            )

    # 5. company scope is one declared mapping.
    declared = document.get("x-company-mapping")
    if not isinstance(declared, dict) or declared.get("id") != _company.MAPPING_ID:
        findings.append("the company mapping is undeclared (x-company-mapping)")
    parameter = (components.get("parameters") or {}).get("companyId") or {}
    if parameter.get("x-company-mapping") != _company.MAPPING_ID:
        findings.append("the companyId path parameter carries no declared company mapping")

    # 6. the company routes are company-scoped and carry every taxonomy status.
    for route in _surface.company_routes():
        if route.segment not in SEGMENT_COMPONENT:
            findings.append(
                f"the company route '{route.path}' has no declared response component "
                "(a new resource family must be mapped, not silently omitted)"
            )
            continue
        operation = (paths.get(route.path) or {}).get(route.method.lower()) or {}
        if not operation:
            continue
        refs = [(param or {}).get("$ref") for param in operation.get("parameters") or []]
        if "#/components/parameters/companyId" not in refs:
            findings.append(f"the route '{route.path}' is not company-scoped (no companyId parameter)")
        missing = [
            str(status)
            for status in _taxonomy.statuses()
            if str(status) not in (operation.get("responses") or {})
        ]
        if missing:
            findings.append(f"the route '{route.path}' omits the error-taxonomy statuses {missing}")

    # 7. the health path exists and can report a non-ok state.
    health_path = (paths.get(f"{API_PREFIX}/health") or {}).get("get") or {}
    if not health_path:
        findings.append(f"the health path '{API_PREFIX}/health' is missing")
    elif "503" not in (health_path.get("responses") or {}):
        findings.append(f"the health path '{API_PREFIX}/health' cannot report a 503")

    return findings
