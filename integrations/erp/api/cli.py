#!/usr/bin/env python3
"""``python3 -m integrations.erp.api.cli`` — emit, check, routes, demo, controls.

Exit contract, the repository's tri-state convention (``guardrails/honesty``):

* ``0`` **OK** — every invariant measured and satisfied;
* ``1`` **NOT-OK** — a measured invariant is violated, named on stderr;
* ``2`` **CANNOT-ASSESS** — the question cannot be answered (the model, a
  declaration or the document will not load). Never a pass: a surface that cannot
  read its own model cannot report on it, and reporting OK would be the false
  green this repository's doctrine forbids.

``check`` measures six separable things, each able to fail on its own:

1. **the model loads and its assets agree** — ERP-02's own ``check_assets``;
2. **the document matches its sources** — ``openapi.validate_document`` re-derives
   every component, route, status and parameter, and resolves every ``$ref``;
3. **the committed artifact is a fresh emission** — byte for byte, so the artifact
   cannot be hand-maintained;
4. **the declarations hold** — the role map covers every kind the model declares,
   the field rules govern fields the family schemas actually declare, and the
   roles they name are declared by the map (a rule about a field no family has, or
   a role no map names, is inert — a formality with a filename);
5. **the offline corpus is the model's** — every fixture document validates, so
   the CRUD path is exercised against real documents rather than convenient ones;
6. **every refusal is provoked by name** — computed by :mod:`negative_control`,
   which fails when a provocation is not refused, is refused under the wrong code,
   or when a code has no provocation and no declared reason for its absence.

``demo`` runs the golden path twice and prints both transcripts, so
determinism is visible rather than asserted; ``routes`` prints the route table;
``emit`` writes (or prints) the document; ``controls`` reports the refusals alone.

---knowledge---
module_id: integrations.erp.api.cli
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [golden_path, command_check, command_emit, command_routes, command_demo, command_controls, build_parser, main]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, TextIO, Tuple

if __package__ in (None, ""):  # executed as a script, not imported as a package
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from integrations.erp.api import (  # noqa: E402
    fixtures,
    health as health_module,
    negative_control,
    openapi as openapi_module,
    provenance,
    routes as routes_module,
    surface as surface_module,
)
from integrations.erp.auth.model import Principal, Refused  # noqa: E402
from integrations.erp.core.errors import ErpError  # noqa: E402

OK = 0
NOT_OK = 1
CANNOT_ASSESS = 2

#: A model, a declaration or a document that will not load is CANNOT-ASSESS — never
#: a pass, and never a failure of the artifact it could not read.
LOAD_FAILURES = (Refused, ErpError)


def _counter_ids() -> Any:
    """A deterministic request-id generator, so a transcript digest is stable.

    The surface's own default is a fresh uuid, which is right for serving and
    useless for comparing two runs; the golden path pins the ids instead of
    pretending the random ones are stable.
    """
    counter = {"n": 0}

    def next_id() -> str:
        counter["n"] += 1
        return f"req-{counter['n']:04d}"

    return next_id


def _digest(payload: Any) -> str:
    """A stable digest of a transcript, so two runs can be compared exactly."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]


# --- the surface under test -------------------------------------------------


class _Rig:
    """A surface, its declarations and its store, in one object."""

    def __init__(self, role: str = "ERP Clerk", request_ids: Any = None) -> None:
        self.model = fixtures.model()
        self.declarations = fixtures.declarations(self.model, role=role)
        self.documents = fixtures.seeded_store(self.model)
        self.role = role
        self.surface = surface_module.Surface(
            model=self.model,
            documents=self.documents,
            role_map=self.declarations.role_map,
            policy_set=self.declarations.policy_set,
            rbac_store=self.declarations.rbac_store,
            team=self.declarations.team,
            root=fixtures.repository_root(),
            request_ids=request_ids,
        )
        self.principal = Principal(
            tenant=self.declarations.tenant,
            subject=fixtures.PLATFORM_SUBJECT,
            roles=(role,),
        )

    def call(self, method: str, path: str, body: Any = None) -> Dict[str, Any]:
        return self.surface.handle(method, path, principal=self.principal, body=body)


#: The golden path: one of every verb, a read that projects by omission, a
#: refusal, and a health read — in one transcript, so a determinism check compares
#: the whole surface rather than one route of it.
GOLDEN_STEPS: Tuple[Tuple[str, str, Any], ...] = (
    ("GET", "/v1/erp/documents/sales-order", None),
    ("GET", "/v1/erp/documents/sales-order/SALES-ORDER-0001", None),
    ("POST", "/v1/erp/documents/party", fixtures.documents()["party"][0]),
    ("PUT", "/v1/erp/documents/party/CUST-0001", {**fixtures.documents()["party"][0], "name": "Northwind Trading Co"}),
    ("POST", "/v1/erp/documents/sales-order/SALES-ORDER-0001/transitions/submit", None),
    ("POST", "/v1/erp/documents/party/CUST-9999/transitions/submit", None),
    ("DELETE", "/v1/erp/documents/item/ITEM-1", None),
    ("GET", "/v1/erp/documents/item", None),
    ("GET", "/v1/erp/health", None),
)


def golden_path(role: str = "ERP Clerk") -> Dict[str, Any]:
    """The end-to-end transcript, built from the shipped declarations."""
    rig = _Rig(role, request_ids=_counter_ids())
    steps: List[Dict[str, Any]] = []
    for index, (method, path, body) in enumerate(GOLDEN_STEPS):
        envelope = rig.call(method, path, body)
        steps.append(
            {
                "step": index,
                "request": {"method": method, "path": path},
                "status": envelope["status"],
                "ok": envelope["ok"],
                "code": (envelope.get("error") or {}).get("code"),
                "requestId": envelope["requestId"],
                "data": envelope["data"],
            }
        )
    return {"role": role, "steps": steps, "digest": _digest(steps)}


# --- checks -----------------------------------------------------------------


def _load_declarations(sink: TextIO, err: TextIO) -> Optional[_Rig]:
    try:
        rig = _Rig()
    except LOAD_FAILURES as refusal:
        print(f"  CANNOT-ASSESS  declarations: {refusal}", file=err)
        return None
    print(
        f"  OK    declarations: {len(rig.declarations.role_map.kinds)} kind(s), "
        f"{len(rig.declarations.role_map.role_names)} role(s), "
        f"{len(rig.declarations.policy_set.rules)} field rule(s)",
        file=sink,
    )
    return rig


def _check_model(rig: _Rig, sink: TextIO) -> List[str]:
    problems = list(rig.model.check_assets())
    if problems:
        return [f"ERP-02's assets: {problem}" for problem in problems]
    print(
        f"  OK    the model loads and its assets agree ({len(rig.model.document_kinds())} kind(s), "
        f"{len(rig.model.lifecycle_kinds())} with a lifecycle)",
        file=sink,
    )
    return []


def _check_declarations(rig: _Rig, sink: TextIO) -> List[str]:
    problems: List[str] = []
    uncovered = [kind for kind in rig.model.document_kinds() if kind not in rig.declarations.role_map.kinds]
    if uncovered:
        problems.append(f"the role map covers no kind for: {', '.join(uncovered)}")
    declared_roles = set(rig.declarations.role_map.role_names)
    for rule in rig.declarations.policy_set.rules:
        for role in rule.roles:
            if role not in declared_roles:
                problems.append(
                    f"field rule {rule.id!r} names the role {role!r}, which the role map does not "
                    "declare — the rule can never fire"
                )
        schema = rig.model.schemas.get(rule.kind)
        if schema is None:
            problems.append(f"field rule {rule.id!r} governs the kind {rule.kind!r}, which has no schema")
            continue
        if rule.field not in (schema.get("properties") or {}):
            problems.append(
                f"field rule {rule.id!r} governs {rule.kind}.{rule.field}, which the family schema "
                "does not declare — the rule governs nothing"
            )
    if not problems:
        print(
            f"  OK    the declarations hold: every kind covered, and every field rule governs a "
            f"field its family schema declares ({len(rig.declarations.policy_set.rules)} rule(s))",
            file=sink,
        )
    return problems


def _check_corpus(rig: _Rig, sink: TextIO) -> List[str]:
    """Every fixture document validates — the offline corpus is the model's."""
    problems: List[str] = []
    for kind in sorted(fixtures.documents()):
        for document in fixtures.documents()[kind]:
            try:
                rig.model.validate_document(kind, document)
            except Exception as refusal:  # noqa: BLE001 - any refusal is a finding
                problems.append(f"the fixture {kind}/{document.get('id')} does not validate: {refusal}")
    if not problems:
        stored = sum(len(fixtures.documents()[kind]) for kind in fixtures.documents())
        print(f"  OK    the offline corpus validates: {stored} document(s) over "
              f"{len(fixtures.documents())} kind(s)", file=sink)
    return problems


def _check_openapi(sink: TextIO, err: TextIO) -> List[str]:
    root = fixtures.repository_root()
    try:
        document = openapi_module.build_document(root)
    except LOAD_FAILURES as refusal:
        print(f"  CANNOT-ASSESS  the document: {refusal}", file=err)
        return ["__cannot_assess__"]
    findings = list(openapi_module.validate_document(document, root))
    artifact = root / openapi_module.EMITTED_ARTIFACT
    if not artifact.is_file():
        findings.append(f"{openapi_module.EMITTED_ARTIFACT} is not committed")
    elif artifact.read_text(encoding="utf-8") != openapi_module.serialize(document):
        findings.append(
            f"{openapi_module.EMITTED_ARTIFACT} is stale or hand-edited (it differs from a fresh "
            "emission; re-emit it with `cli.py emit --out <path>`)"
        )
    if not findings:
        print(
            f"  OK    the document matches its sources and the committed artifact "
            f"({openapi_module.byte_size(document)} bytes, {len(document['paths'])} path(s), "
            f"{len(document['components']['schemas'])} component(s))",
            file=sink,
        )
    return findings


def _check_wiring(rig: _Rig, sink: TextIO) -> List[str]:
    """The route table, the handlers and the model agree, and one call site decides."""
    problems = list(routes_module.problems(rig.model))
    problems.extend(surface_module._handlers_check())  # noqa: SLF001 - the module's own check
    source = Path(surface_module.__file__).read_text(encoding="utf-8")
    call_sites = source.count("auth_scope.authorize(")
    if call_sites != 1:
        problems.append(
            f"integrations/erp/api/surface.py calls the authorization layer {call_sites} time(s); "
            "exactly one call site is what makes 'no back door' a property rather than a promise"
        )
    if not problems:
        print(
            "  OK    the route table agrees with the model, every route has a handler, and the "
            "authorization layer is reached from exactly one place",
            file=sink,
        )
    return problems


def _check_controls(sink: TextIO) -> List[str]:
    if negative_control.run(sink) != 0:
        return ["negative-control: one or more refusals were not provoked"]
    return []


def _check_provenance(sink: TextIO, err: TextIO) -> List[str]:
    try:
        record = provenance.load()
    except LOAD_FAILURES as refusal:
        print(f"  CANNOT-ASSESS  the harvest record: {refusal}", file=err)
        return ["__cannot_assess__"]
    print(
        f"  OK    the harvest record holds: {len(record['harvests'])} harvest(s), "
        f"no copy, upstream domain pattern pointed at rather than restated",
        file=sink,
    )
    return []


def _check_health(rig: _Rig, sink: TextIO) -> List[str]:
    report = health_module.health(
        fixtures.repository_root(), model=rig.model, role_map=rig.declarations.role_map
    )
    findings = list(
        health_module.check_report(
            report, fixtures.repository_root(), model=rig.model, role_map=rig.declarations.role_map
        )
    )
    if findings:
        return findings
    readings = ", ".join(f"{state.name}={state.state}" for state in report.dependencies)
    print(f"  OK    the health read is honest and {report.status} ({readings})", file=sink)
    return []


def _check_determinism(sink: TextIO) -> List[str]:
    first = golden_path()
    second = golden_path()
    if first["digest"] != second["digest"]:
        return [
            "the golden path is not deterministic: two runs produced different transcripts "
            f"({first['digest']} and {second['digest']})"
        ]
    print(f"  OK    the golden path is deterministic ({first['digest']})", file=sink)
    return []


def command_check(args: argparse.Namespace, sink: TextIO, err: TextIO) -> int:
    print("erp-api check", file=sink)
    rig = _load_declarations(sink, err)
    if rig is None:
        print("erp-api check: CANNOT-ASSESS", file=err)
        return CANNOT_ASSESS

    problems: List[str] = []
    problems.extend(_check_model(rig, sink))
    problems.extend(_check_declarations(rig, sink))
    problems.extend(_check_corpus(rig, sink))
    findings = _check_openapi(sink, err)
    problems.extend(findings)
    problems.extend(_check_provenance(sink, err))
    problems.extend(_check_wiring(rig, sink))
    problems.extend(_check_health(rig, sink))
    problems.extend(_check_determinism(sink))
    problems.extend(_check_controls(sink))

    if "__cannot_assess__" in problems:
        print("erp-api check: CANNOT-ASSESS", file=err)
        return CANNOT_ASSESS
    if problems:
        print(f"erp-api check: NOT-OK — {len(problems)} problem(s)", file=err)
        for problem in problems:
            print(f"  FAIL  {problem}", file=err)
        return NOT_OK
    print("erp-api check: OK — the document, the artifact, the declarations and every refusal", file=sink)
    return OK


def command_emit(args: argparse.Namespace, sink: TextIO, err: TextIO) -> int:
    root = fixtures.repository_root()
    out = Path(args.out) if args.out else None
    try:
        text = openapi_module.emit(root, out)
    except LOAD_FAILURES as refusal:
        print(f"  CANNOT-ASSESS  emit: {refusal}", file=err)
        return CANNOT_ASSESS
    if out is None:
        sink.write(text)
        return OK
    print(f"  OK    emitted {out} ({len(text)} bytes)", file=sink)
    return OK


def command_routes(args: argparse.Namespace, sink: TextIO, err: TextIO) -> int:
    try:
        rig = _Rig(args.role)
    except LOAD_FAILURES as refusal:
        print(f"  CANNOT-ASSESS  routes: {refusal}", file=err)
        return CANNOT_ASSESS
    sink.write(
        json.dumps(
            {
                "routes": openapi_module.route_declarations(),
                "transitionActions": dict(sorted(routes_module.ACTION_PERMISSION.items())),
                "kinds": list(rig.model.document_kinds()),
                "declarations": {
                    "roles": list(rig.declarations.role_map.role_names),
                    "kinds": list(rig.declarations.role_map.kinds),
                    "fieldRules": [rule.id for rule in rig.declarations.policy_set.rules],
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return OK


def command_demo(args: argparse.Namespace, sink: TextIO, err: TextIO) -> int:
    try:
        first = golden_path(args.role)
        second = golden_path(args.role)
    except LOAD_FAILURES as refusal:
        print(f"  CANNOT-ASSESS  demo: {refusal}", file=err)
        return CANNOT_ASSESS
    sink.write(
        json.dumps(
            {"first": first, "second": second, "deterministic": first["digest"] == second["digest"]},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return OK if first["digest"] == second["digest"] else NOT_OK


def command_controls(args: argparse.Namespace, sink: TextIO, err: TextIO) -> int:
    return OK if negative_control.run(sink) == 0 else NOT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="integrations.erp.api.cli", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--role", default="ERP Clerk", help="the role the golden path is driven with")
    verbs = parser.add_subparsers(dest="command", required=True)

    emit = verbs.add_parser("emit", help="write or print the OpenAPI document")
    emit.add_argument("--out", default="", help="output path (default: stdout)")
    emit.set_defaults(func=command_emit)

    checks = verbs.add_parser(
        "check", help="the model, the document, the artifact, the declarations, the controls"
    )
    checks.set_defaults(func=command_check)
    routes_verb = verbs.add_parser("routes", help="the route table and the declarations, as JSON")
    routes_verb.set_defaults(func=command_routes)
    demo = verbs.add_parser("demo", help="the golden-path transcript, twice")
    demo.set_defaults(func=command_demo)
    controls = verbs.add_parser("controls", help="provoke every refusal, by name")
    controls.set_defaults(func=command_controls)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args, sys.stdout, sys.stderr))


if __name__ == "__main__":
    raise SystemExit(main())
