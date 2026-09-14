#!/usr/bin/env python3
"""The cockpit function registry — validator and headless renderer (#565, RC-10).

`validate` is the check. It loads ``functions.yaml`` and enforces the contract
the registry exists for, reusing every authority that already owns a part of it
instead of restating one:

  * the **effect classes** and every command's **capability** and **audit**
    action come from RC-2's vocabulary (``control-plane/control/verbs.yaml``),
    which this module reads — a command that names a class, a capability or an
    audit action RC-2 does not declare is refused by name;
  * the **routes** a panel or view may bind must be routes the surface's own
    declaration names (``infra/feature-flags/registry.yaml``), so no endpoint is
    invented;
  * a panel's **capability** must be a permission identity/rbac (or the route
    that serves the surface) already declares, so this registry cannot mint one;
  * role suitability is a **recommendation**: additive filtering only.

Two directions are checked, and both matter:

  * MISSING — an exposed RC-2 verb, or a declared surface, that no function
    declares and `undeclared` does not excuse. That is how a domain would
    arrive as a bespoke panel instead of a declaration.
  * ABSENT — a function whose endpoint no longer resolves (a verb withdrawn, a
    route renamed). Rot: the registry would describe a plane that is gone.

`render` is the headless half: it renders every declared function against
committed fixtures with no live plane, no network and no app object, and refuses
an unknown function, an unknown parameter, a missing required parameter or a
value of the wrong type — each by name.

Exit contract (this repo's tri-state): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
CANNOT-ASSESS must never read as a pass.

**This module authorises nothing.** It holds no permit/deny/role table. The
capability a function declares is the one its owner already declares, and the
decision stays with ``identity/rbac``'s ``guard`` — rechecked per call by RC-3
(``portal/server/control_api.py`` `_require_capability`). See the README.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "control-plane" / "functions" / "functions.yaml"
SCHEMA = ROOT / "control-plane" / "functions" / "schema" / "functions.schema.json"

#: RC-3's route family (ADR-0025 D2): ``POST /api/control/<family>/<action>``,
#: with the action segment owned by RC-2's vocabulary.
CONTROL_ROUTE = "/api/control/{family}/{action}"

#: The files that DECLARE a permission this registry may cite for a panel or a
#: view. A capability that appears in none of them is one this registry invented.
PERMISSION_SOURCES = (
    "identity/rbac/model.py",
    "portal/server/app.py",
    "portal/server/fleet_authz.py",
)

#: The closed parameter-name shape (camelCase, like RC-3's ``commandId``).
PARAM_NAME = re.compile(r"^[a-z][A-Za-z0-9]*$")
MNEMONIC = re.compile(r"^[A-Z][A-Z0-9]{1,7}$")
CAPABILITY = re.compile(r"^[a-z][a-z0-9-]*:[a-z][a-z0-9-]*$")
DECLARED_ROUTE = re.compile(r"\b(?:GET|POST|PUT|DELETE)\s+(/api[^\s,;)]*)")


class Refused(Exception):
    """A caller asked for something the registry does not declare."""


# ---------------------------------------------------------------------------
# the declared authorities (consumed, never restated)
# ---------------------------------------------------------------------------
def _read_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_registry(path: Path | None = None) -> dict:
    return _read_yaml(path or REGISTRY)


def load_vocabulary(root: Path = ROOT) -> dict:
    """RC-2's control-verb vocabulary: the effect classes and every command's own facts."""
    return _read_yaml(root / "control-plane" / "control" / "verbs.yaml")


def load_surfaces(root: Path = ROOT) -> dict:
    """The feature-flag registry's surface map: which surfaces exist, and their routes."""
    document = _read_yaml(root / "infra" / "feature-flags" / "registry.yaml")
    return document.get("surfaces") or {}


def surface_routes(declaration: dict) -> set[str]:
    """The routes a surface declares FOR ITSELF, read out of its own declaration.

    A declaration names its routes in prose (``GET /api/fleet/snapshot``). Only
    the path is taken: a query string is not part of a route, so
    ``GET /api/ops/agents?tenant=<id>`` declares ``/api/ops/agents``.
    """
    found: set[str] = set()
    for token in DECLARED_ROUTE.findall(str(declaration.get("description", ""))):
        found.add(token.split("?", 1)[0].rstrip(".,;:`"))
    return found


def declared_permission_sources(root: Path = ROOT) -> str:
    """The text of every file that declares a permission — the citation corpus."""
    return "\n".join(
        (root / relative).read_text(encoding="utf-8")
        for relative in PERMISSION_SOURCES
        if (root / relative).exists()
    )


def vocabulary_facts(vocabulary: dict) -> tuple[set[str], dict[str, dict]]:
    """The closed effect classes, and each verb by id — RC-2's own words."""
    classes = set((vocabulary.get("effect_classes") or {}).keys())
    verbs = {str(verb.get("id")): verb for verb in (vocabulary.get("verbs") or [])}
    return classes, verbs


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------
def _check_endpoint(
    where: str,
    endpoint: dict,
    kind: str,
    verbs: dict[str, dict],
    surfaces: dict,
    findings: list[str],
) -> None:
    route = str(endpoint.get("route", ""))
    verb_id = endpoint.get("verb")
    surface = endpoint.get("surface")

    if not route.startswith("/api/"):
        findings.append(f"{where}: route {route!r} is not an /api/ route")
    if (verb_id is None) == (surface is None):
        findings.append(
            f"{where}: exactly one of `verb` / `surface` is required "
            f"(got verb={verb_id!r}, surface={surface!r})"
        )
        return

    if verb_id is not None:
        if kind != "command":
            findings.append(
                f"{where}: a {kind} endpoint must bind a surface, not the verb {verb_id!r}"
            )
            return
        verb = verbs.get(str(verb_id))
        if verb is None:
            findings.append(
                f"{where}: endpoint names the verb {verb_id!r}, which RC-2's "
                "vocabulary does not declare"
            )
            return
        if not verb.get("exposed"):
            findings.append(
                f"{where}: endpoint names {verb_id!r}, which RC-2 declares with "
                "exposed: false — the cockpit cannot reach it over RC-3's API "
                f"({verb.get('why_not_exposed', 'no reason declared')})"
            )
        family, _, action = str(verb_id).partition(".")
        expected = CONTROL_ROUTE.format(family=family, action=action)
        if route != expected:
            findings.append(
                f"{where}: route {route!r} is not RC-3's declared route for {verb_id!r} "
                f"(RC-3 serves {expected!r} for every exposed verb)"
            )
        return

    if kind == "command":
        findings.append(
            f"{where}: a command endpoint must bind an RC-2 verb, not the surface {surface!r}"
        )
        return
    declaration = surfaces.get(str(surface))
    if declaration is None:
        findings.append(
            f"{where}: endpoint names the surface {surface!r}, which "
            "infra/feature-flags/registry.yaml does not declare"
        )
        return
    declared = surface_routes(declaration)
    if not declared:
        findings.append(
            f"{where}: surface {surface!r} declares no route for a client to read, "
            "so no endpoint can bind to it"
        )
        return
    if route not in declared:
        findings.append(
            f"{where}: route {route!r} is not declared by surface {surface!r} "
            f"(its declaration names {sorted(declared)})"
        )


def _check_parameters(
    where: str, parameters: list, parameter_types: set[str], findings: list[str]
) -> None:
    seen: set[str] = set()
    for index, parameter in enumerate(parameters):
        label = f"{where} parameters[{index}]"
        name = parameter.get("name")
        if not isinstance(name, str) or not PARAM_NAME.match(name):
            findings.append(f"{label}: name {name!r} is not a camelCase parameter name")
            continue
        if name in seen:
            findings.append(f"{label}: duplicate parameter name {name!r} in this function")
        seen.add(name)
        if "type" not in parameter:
            findings.append(f"{label} ({name}): the parameter declares no type")
        elif parameter["type"] not in parameter_types:
            findings.append(
                f"{label} ({name}): type {parameter['type']!r} is not in the closed "
                f"parameter type set {sorted(parameter_types)}"
            )
        required = parameter.get("required")
        if not isinstance(required, bool):
            findings.append(f"{label} ({name}): `required` must be a boolean")
        elif required and "default" in parameter:
            findings.append(
                f"{label} ({name}): a required parameter must not declare a default"
            )
        elif not required and "default" not in parameter:
            findings.append(
                f"{label} ({name}): an optional parameter must declare its default"
            )


def _check_scope(
    where: str,
    function: dict,
    verbs: dict[str, dict],
    citation: str,
    findings: list[str],
) -> None:
    scope = function.get("scope") or {}
    capability = scope.get("capability")
    kind = function.get("kind")
    verb_id = None
    if kind == "command":
        endpoints = function.get("endpoints") or [{}]
        verb_id = endpoints[0].get("verb")

    if capability is None:
        if not scope.get("why_no_capability"):
            findings.append(
                f"{where}: capability is null and why_no_capability is missing "
                "(a function is never silently capability-less)"
            )
        if kind == "command":
            findings.append(
                f"{where}: a command must declare the capability RC-2 declares for it"
            )
        return

    if not isinstance(capability, str) or not CAPABILITY.match(capability):
        findings.append(f"{where}: capability {capability!r} is not `resource:action`")
        return
    if scope.get("why_no_capability"):
        findings.append(f"{where}: why_no_capability is set but a capability is declared")

    if kind == "command":
        verb = verbs.get(str(verb_id)) or {}
        declared = verb.get("capability")
        if capability != declared:
            findings.append(
                f"{where}: capability {capability!r} is not RC-2's capability for "
                f"{verb_id!r} (RC-2 declares {declared!r}) — a capability may be "
                "cited, never minted"
            )
    elif capability not in citation:
        findings.append(
            f"{where}: capability {capability!r} is not declared by "
            "identity/rbac or by the route that serves the surface "
            f"({', '.join(PERMISSION_SOURCES)}) — a capability may be cited, never minted"
        )


def validate(
    root: Path = ROOT,
    registry_path: Path | None = None,
    document: dict | None = None,
) -> tuple[list[str], dict]:
    """Every rule, both cross-reference directions. Returns (findings, counts).

    ``document`` lets a caller (the unit tests, and the gate's negative controls)
    hand in a mutated copy of the registry and prove that a rule can fail, without
    editing the registry on disk.
    """
    document = document if document is not None else load_registry(registry_path)
    classes, verbs = vocabulary_facts(load_vocabulary(root))
    surfaces = load_surfaces(root)
    citation = declared_permission_sources(root)
    schema = json.loads((root / SCHEMA.relative_to(ROOT)).read_text(encoding="utf-8"))

    findings: list[str] = []
    counts: dict[str, int] = {}

    if document.get("schema") != schema["properties"]["schema"]["const"]:
        findings.append(
            f"schema: expected {schema['properties']['schema']['const']!r}, "
            f"got {document.get('schema')!r}"
        )

    consumed = document.get("consumes") or {}
    for key, expected in (
        ("vocabulary", "control-plane/control/verbs.yaml"),
        ("surfaces", "infra/feature-flags/registry.yaml"),
        ("permissions", "identity/rbac/model.py"),
    ):
        if consumed.get(key) != expected:
            findings.append(f"consumes.{key}: expected {expected!r}, got {consumed.get(key)!r}")

    roles = set((document.get("roles") or {}).keys())
    wanted_roles = set(schema["properties"]["roles"]["required"])
    if roles != wanted_roles:
        findings.append(
            f"roles: the closed role set must be exactly {sorted(wanted_roles)}, "
            f"got {sorted(roles)}"
        )
    kinds = set((document.get("kinds") or {}).keys())
    wanted_kinds = set(schema["properties"]["kinds"]["required"])
    if kinds != wanted_kinds:
        findings.append(f"kinds: must be exactly {sorted(wanted_kinds)}, got {sorted(kinds)}")
    parameter_types = set((document.get("parameter_types") or {}).keys())
    wanted_types = set(schema["properties"]["parameter_types"]["required"])
    if parameter_types != wanted_types:
        findings.append(
            f"parameter_types: must be exactly {sorted(wanted_types)}, got {sorted(parameter_types)}"
        )

    functions = document.get("functions") or []
    excused_verbs: set[str] = set()
    excused_surfaces: set[str] = set()
    for index, excusal in enumerate(document.get("undeclared") or []):
        label = f"undeclared[{index}]"
        if excusal.get("kind") not in ("verb", "surface"):
            findings.append(f"{label}: kind must be `verb` or `surface`")
        if not excusal.get("reason"):
            findings.append(f"{label}: a surface or verb is never silently dropped — a reason is required")
        ref = str(excusal.get("ref"))
        target = excused_verbs if excusal.get("kind") == "verb" else excused_surfaces
        if ref in target:
            findings.append(f"{label}: duplicate excusal for {ref!r}")
        target.add(ref)
        if excusal.get("kind") == "verb" and ref not in verbs:
            findings.append(f"{label}: excuses {ref!r}, which RC-2 does not declare")
        if excusal.get("kind") == "surface" and ref not in surfaces:
            findings.append(
                f"{label}: excuses {ref!r}, which the feature-flag registry does not declare"
            )

    seen_ids: dict[str, int] = {}
    declared_verbs: set[str] = set()
    declared_surfaces: set[str] = set()
    for index, function in enumerate(functions):
        fid = function.get("id")
        where = f"functions[{index}] ({fid})"

        for field in schema["properties"]["functions"]["items"]["required"]:
            if field not in function:
                findings.append(f"{where}: missing required field {field!r}")

        if not isinstance(fid, str) or not MNEMONIC.match(fid):
            findings.append(f"{where}: id {fid!r} is not an uppercase mnemonic")
        else:
            seen_ids[fid] = seen_ids.get(fid, 0) + 1

        kind = function.get("kind")
        if kind not in kinds:
            findings.append(f"{where}: kind {kind!r} is not one of {sorted(kinds)}")

        endpoints = function.get("endpoints") or []
        if not endpoints:
            findings.append(f"{where}: a function with no endpoint is not a function")
        for endpoint_index, endpoint in enumerate(endpoints):
            _check_endpoint(
                f"{where} endpoints[{endpoint_index}]",
                endpoint,
                str(kind),
                verbs,
                surfaces,
                findings,
            )
            if endpoint.get("verb") is not None and len(endpoints) > 1:
                findings.append(f"{where}: a command declares exactly one control route")
            if endpoint.get("verb") is not None:
                declared_verbs.add(str(endpoint["verb"]))
            if endpoint.get("surface") is not None:
                declared_surfaces.add(str(endpoint["surface"]))

        _check_parameters(
            where, function.get("parameters") or [], parameter_types, findings
        )
        _check_scope(where, function, verbs, citation, findings)

        effect_class = function.get("effect_class")
        if effect_class not in classes:
            findings.append(
                f"{where}: effect_class {effect_class!r} is not one of RC-2's closed "
                f"set {sorted(classes)}"
            )

        audit = function.get("audit")
        if effect_class == "read" and audit is not None:
            findings.append(f"{where}: a read function must not declare an audit action")
        if effect_class in ("hold", "stop", "irreversible") and not audit:
            findings.append(f"{where}: effect_class {effect_class!r} requires an audit action")
        if kind == "command" and endpoints:
            verb_id = endpoints[0].get("verb")
            verb = verbs.get(str(verb_id)) or {}
            if verb:
                if effect_class != verb.get("effect_class"):
                    findings.append(
                        f"{where}: effect_class {effect_class!r} is not the class RC-2 "
                        f"declares for {verb_id!r} ({verb.get('effect_class')!r})"
                    )
                if audit != verb.get("audit"):
                    findings.append(
                        f"{where}: audit {audit!r} is not the action RC-2 declares for "
                        f"{verb_id!r} ({verb.get('audit')!r})"
                    )
        elif kind in ("panel", "view") and effect_class != "read":
            findings.append(f"{where}: a {kind} reads, so its effect_class must be `read`")

        stream = function.get("stream")
        if kind == "view":
            if not stream:
                findings.append(f"{where}: a view requires the stream it subscribes to")
            elif stream not in surfaces:
                findings.append(
                    f"{where}: stream {stream!r} is not a surface the feature-flag "
                    "registry declares"
                )
            elif "SSE" not in str((surfaces.get(stream) or {}).get("description", "")):
                findings.append(
                    f"{where}: surface {stream!r} declares no push channel, so it is "
                    "not a stream a view can subscribe to"
                )
        elif stream is not None:
            findings.append(f"{where}: stream is set but the function is not a view")

        function_roles = function.get("roles") or []
        if not function_roles:
            findings.append(f"{where}: roles must name at least one role it is designed for")
        for role in function_roles:
            if role not in roles:
                findings.append(
                    f"{where}: role {role!r} is not one of the closed role set "
                    f"{sorted(roles)}"
                )
        if len(set(function_roles)) != len(function_roles):
            findings.append(f"{where}: roles names a role twice")

    duplicates = sorted(fid for fid, count in seen_ids.items() if count > 1)
    if duplicates:
        findings.append(f"duplicate function id(s): {duplicates}")

    # Direction 1: a declaration nobody renders is a declaration that will be
    # re-invented. Every exposed verb and every declared surface is declared
    # here or excused by name.
    exposed = {vid for vid, verb in verbs.items() if verb.get("exposed")}
    for verb_id in sorted(exposed - declared_verbs - excused_verbs):
        findings.append(
            f"MISSING: RC-2 declares the exposed verb {verb_id!r}, which no cockpit "
            "function declares and `undeclared` does not excuse"
        )
    for surface in sorted(set(surfaces) - declared_surfaces - excused_surfaces):
        findings.append(
            f"MISSING: the feature-flag registry declares the surface {surface!r}, "
            "which no cockpit function declares and `undeclared` does not excuse"
        )
    for verb_id in sorted(excused_verbs & declared_verbs):
        findings.append(f"undeclared: {verb_id!r} is both declared and excused")

    counts = {
        "functions": len(functions),
        "commands": sum(1 for f in functions if f.get("kind") == "command"),
        "panels": sum(1 for f in functions if f.get("kind") == "panel"),
        "views": sum(1 for f in functions if f.get("kind") == "view"),
        "exposed_verbs": len(exposed),
        "surfaces": len(surfaces),
        "excused": len(excused_verbs) + len(excused_surfaces),
    }
    return findings, counts


# ---------------------------------------------------------------------------
# render (headless)
# ---------------------------------------------------------------------------
def _coerce(name: str, declared: dict, value, where: str) -> object:
    kind = declared.get("type")
    if kind == "string":
        if not isinstance(value, str):
            raise Refused(f"{where} parameter {name!r} expects a string, got {type(value).__name__}")
    elif kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise Refused(f"{where} parameter {name!r} expects an integer, got {type(value).__name__}")
    elif kind == "boolean":
        if not isinstance(value, bool):
            raise Refused(f"{where} parameter {name!r} expects a boolean, got {type(value).__name__}")
    elif kind == "string-list":
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise Refused(f"{where} parameter {name!r} expects a list of strings")
    return value


def render(
    document: dict,
    fixtures: dict,
    role: str | None = None,
    roles: set[str] | None = None,
) -> list[dict]:
    """Render each declared function against fixtures. No live plane is touched."""
    known_roles = roles if roles is not None else set((document.get("roles") or {}).keys())
    if role is not None and role not in known_roles:
        raise Refused(
            f"unknown role {role!r}: the closed role set is {sorted(known_roles)}"
        )

    functions = {str(f.get("id")): f for f in (document.get("functions") or [])}
    calls = fixtures.get("calls") or {}
    if not isinstance(calls, dict):
        raise Refused("the fixtures must carry a `calls` map of function id -> parameter values")

    for fid in calls:
        if fid not in functions:
            raise Refused(
                f"unknown function {fid!r} in the fixtures: no cockpit function declares it"
            )

    rendered: list[dict] = []
    for fid, function in functions.items():
        if role is not None and role not in (function.get("roles") or []):
            continue
        where = f"function {fid}"
        declared = {str(p.get("name")): p for p in (function.get("parameters") or [])}
        supplied = calls.get(fid) or {}
        if not isinstance(supplied, dict):
            raise Refused(f"{where}: its fixture entry must be a map of parameter name -> value")

        values: dict[str, object] = {}
        for name, value in supplied.items():
            if name not in declared:
                raise Refused(
                    f"unknown parameter {name!r} on {where}: it declares only "
                    f"{sorted(declared)}"
                )
            values[name] = _coerce(name, declared[name], value, where)
        for name, parameter in declared.items():
            if name in values:
                continue
            if parameter.get("required"):
                raise Refused(f"{where}: required parameter {name!r} was not supplied")
            values[name] = parameter.get("default")

        rendered.append(
            {
                "id": fid,
                "title": function.get("title"),
                "kind": function.get("kind"),
                "endpoints": function.get("endpoints"),
                "parameters": values,
                "scope": function.get("scope"),
                "effect_class": function.get("effect_class"),
                "audit": function.get("audit"),
                "stream": function.get("stream"),
                "roles": function.get("roles"),
            }
        )
    return rendered


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------
def cmd_validate(args: argparse.Namespace) -> int:
    for required in (
        REGISTRY,
        SCHEMA,
        ROOT / "control-plane" / "control" / "verbs.yaml",
        ROOT / "infra" / "feature-flags" / "registry.yaml",
    ):
        if not required.exists():
            print(f"control-functions: CANNOT-ASSESS — {required} is missing", file=sys.stderr)
            return 2
    try:
        findings, counts = validate()
    except yaml.YAMLError as exc:
        print(f"control-functions: CANNOT-ASSESS — the registry is not valid YAML: {exc}", file=sys.stderr)
        return 2

    if findings:
        for finding in findings:
            print(f"  FAIL  {finding}", file=sys.stderr)
        print(
            f"control-functions: FAIL — {len(findings)} finding(s) over "
            f"{counts.get('functions', 0)} function(s)",
            file=sys.stderr,
        )
        return 1

    print(
        "  OK    {functions} function(s) declared: {commands} command(s), {panels} panel(s), "
        "{views} view(s)".format(**counts)
    )
    print(
        "  OK    coverage: {exposed_verbs} exposed RC-2 verb(s) and {surfaces} declared "
        "surface(s), all declared or excused ({excused} excusal(s))".format(**counts)
    )
    print("control-functions: OK — every cockpit function is declared once, and none is invented")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    fixtures_path = Path(args.fixtures)
    if not fixtures_path.exists():
        print(f"control-functions: CANNOT-ASSESS — {fixtures_path} is missing", file=sys.stderr)
        return 2
    try:
        document = load_registry()
        fixtures = json.loads(fixtures_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        print(f"control-functions: CANNOT-ASSESS — {fixtures_path}: {exc}", file=sys.stderr)
        return 2

    try:
        rendered = render(document, fixtures, role=args.role)
    except Refused as exc:
        print(f"  FAIL  {exc}", file=sys.stderr)
        print("control-functions: FAIL — the fixtures do not match the registry", file=sys.stderr)
        return 1

    for row in rendered:
        print(json.dumps(row, sort_keys=True))
    scope = f" (role {args.role})" if args.role else ""
    print(
        f"control-functions: OK — rendered {len(rendered)} function(s) headlessly{scope}",
        file=sys.stderr,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="control-functions", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validator = sub.add_parser("validate", help="validate the registry and both cross-references")
    validator.set_defaults(func=cmd_validate)
    renderer = sub.add_parser("render", help="render every function headlessly against fixtures")
    renderer.add_argument("--fixtures", required=True, help="the fixtures file")
    renderer.add_argument("--role", default=None, help="render only this role's recommendations")
    renderer.set_defaults(func=cmd_render)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
