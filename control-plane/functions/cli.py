#!/usr/bin/env python3
"""Cockpit function registry — the CLI (issue #565, RC-10 of #551).

    validate   the check: closed sets + both cross-reference directions
    list       the declared functions (optionally a role's recommendation)
    show       one declared function, with everything it carries
    call       resolve an operator invocation (refuses an unknown parameter BY NAME)
    render     render one declared function against a fixture -- no live plane
    frames     render EVERY declared function against its fixture
    panels     the panels the existing cockpit renders, and the function that owns each
    derive     the consumed authorities, as the validator reads them

Exit contract (this repo's tri-state): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
CANNOT-ASSESS must never read as a pass.

Usage:
    python3 control-plane/functions/cli.py validate
    python3 control-plane/functions/cli.py call LOG tail=999 bogus=1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PACKAGE = Path(__file__).resolve().parent
if str(_PACKAGE) not in sys.path:
    sys.path.insert(0, str(_PACKAGE))

import cockpit_registry as reg  # noqa: E402
import cockpit_render as renderer  # noqa: E402

OK, NOT_OK, CANNOT_ASSESS = 0, 1, 2


def _load(path: str | None) -> reg.Registry:
    return reg.load(Path(path) if path else None)


def _report(findings: list[reg.Finding]) -> int:
    for finding in findings:
        sys.stderr.write(f"  FAIL  {finding}\n")
    if findings:
        sys.stderr.write(
            f"control-functions: FAIL — {len(findings)} finding(s); the registry does not "
            f"describe a closed cockpit function set\n"
        )
        return NOT_OK
    return OK


def cmd_validate(args: argparse.Namespace) -> int:
    registry = _load(args.registry)
    derived = reg.derive()
    findings = reg.validate(registry, derived)
    if _report(findings) != OK:
        return NOT_OK
    panels = len(derived.panels)
    print("== the registry ==")
    print(f"  OK    schema {reg.REGISTRY_SCHEMA} at {registry.path}")
    print(f"  OK    {len(registry)} functions declared, {panels} panel(s) rendered by the cockpit")
    print(f"  OK    every one of the {len(derived.routes)} exposed control verbs is declared")
    print("  OK    every endpoint resolves in RC-3's declared route set")
    print("  OK    every stream is a declared surface; every named flag exists")
    print("  OK    effect classes, capabilities and audit actions are RC-2's own")
    print("  OK    roles are additive filtering only (no role appears in a scope block)")
    print("control-functions: OK — every cockpit function is declared exactly once")
    return OK


def cmd_list(args: argparse.Namespace) -> int:
    registry = _load(args.registry)
    if args.role:
        if args.role not in registry.roles:
            sys.stderr.write(
                f"control-functions: FAIL — {args.role!r} is not a declared role "
                f"({sorted(registry.roles)})\n"
            )
            return NOT_OK
        for function in reg.recommended(registry, args.role):
            print(f"{function.id:<12} {function.kind:<8} {function.title}")
        return OK
    for function in registry.ordered:
        print(f"{function.id:<12} {function.kind:<8} {function.effect_class:<13} {function.title}")
    return OK


def cmd_show(args: argparse.Namespace) -> int:
    registry = _load(args.registry)
    if args.function not in registry.functions:
        sys.stderr.write(
            f"control-functions: FAIL — UNKNOWN-FUNCTION: {args.function!r} is not declared\n"
        )
        return NOT_OK
    function = registry.functions[args.function]
    payload = {
        "id": function.id,
        "title": function.title,
        "kind": function.kind,
        "endpoints": list(function.endpoints),
        "stream": function.stream,
        "parameters": [
            {
                "name": parameter.name,
                "type": parameter.type,
                "required": parameter.required,
                "default": parameter.default,
                "values": list(parameter.values),
            }
            for parameter in function.parameters
        ],
        "scope": {
            "capability": function.scope.capability,
            "level": function.scope.level,
            "tenant": function.scope.tenant,
        },
        "effectClass": function.effect_class,
        "audit": function.audit,
        "flags": list(function.flags),
        "roles": list(function.roles),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return OK


def cmd_call(args: argparse.Namespace) -> int:
    registry = _load(args.registry)
    if args.function not in registry.functions:
        sys.stderr.write(
            f"control-functions: FAIL — UNKNOWN-FUNCTION: {args.function!r} is not declared\n"
        )
        return NOT_OK
    function = registry.functions[args.function]

    given: dict[str, object] = {}
    findings: list[reg.Finding] = []
    for pair in args.argument:
        if "=" not in pair:
            sys.stderr.write(
                f"control-functions: FAIL — PARAMETER-SYNTAX: {pair!r} is not name=value\n"
            )
            return NOT_OK
        name, _, raw = pair.partition("=")
        parameter = function.parameter(name)
        if parameter is None:
            # Named here as well as by the resolver, because the coercion below
            # needs a type and an unknown parameter has none.
            findings.append(
                reg.Finding(
                    "UNKNOWN-PARAMETER",
                    f"{function.id} does not declare a parameter {name!r} (declared: "
                    f"{list(function.parameter_names) or 'none'})",
                )
            )
            continue
        try:
            given[name] = _coerce(parameter, raw)
        except ValueError as exc:
            findings.append(reg.Finding("PARAMETER-TYPE", f"{function.id}.{name}: {exc}"))
    if findings:
        return _report(findings)

    call, findings = reg.resolve_call(registry, args.function, given)
    if findings:
        return _report(findings)
    assert call is not None
    print(
        json.dumps(
            {
                "function": call.function_id,
                "endpoint": call.endpoint_path,
                "method": "POST",
                "argv": list(call.arguments),
                "effectClass": call.effect_class,
                "audit": call.audit,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return OK


def _coerce(parameter: reg.Parameter, raw: str) -> object:
    if parameter.type == "integer":
        return int(raw)
    if parameter.type == "number":
        return float(raw)
    if parameter.type == "boolean":
        lowered = raw.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
        raise ValueError(f"{raw!r} is not a boolean")
    return raw


def cmd_render(args: argparse.Namespace) -> int:
    registry = _load(args.registry)
    if args.function not in registry.functions:
        sys.stderr.write(
            f"control-functions: FAIL — UNKNOWN-FUNCTION: {args.function!r} is not declared\n"
        )
        return NOT_OK
    fixtures = renderer.load_fixtures(args.fixtures)
    fixture = fixtures.get(args.function)
    if fixture is None:
        fixture = renderer.Fixture(
            function_id=args.function,
            outcome="error",
            reason=f"no fixture for {args.function}",
        )
    if args.outcome:
        fixture = renderer.Fixture(
            function_id=args.function,
            outcome=args.outcome,
            flag=fixture.flag,
            reason=fixture.reason,
            rows=fixture.rows,
            receipt=fixture.receipt,
        )
    print(renderer.render(registry.functions[args.function], fixture))
    return OK


def cmd_frames(args: argparse.Namespace) -> int:
    registry = _load(args.registry)
    packages = renderer.load_fixtures(args.fixtures)
    frames = renderer.render_all(registry, packages)
    if len(frames) != len(registry):
        sys.stderr.write(
            f"control-functions: FAIL — RENDER-COUNT: {len(frames)} frame(s) for "
            f"{len(registry)} declared function(s)\n"
        )
        return NOT_OK
    for function_id in frames:
        print(frames[function_id])
        print()
    print(f"  OK    {len(frames)} of {len(registry)} declared functions rendered headlessly")
    return OK


def cmd_panels(args: argparse.Namespace) -> int:
    registry = _load(args.registry)
    derived = reg.derive()
    for panel in derived.panels:
        print(f"{panel:<16} -> {registry.renders.get(panel, '(undeclared)')}")
    return OK


def cmd_derive(args: argparse.Namespace) -> int:
    derived = reg.derive()
    print(
        json.dumps(
            {
                "routeRoot": derived.route_root,
                "routes": sorted(derived.routes),
                "effectClasses": sorted(derived.effect_classes),
                "capabilities": sorted(derived.capabilities),
                "scopeLevels": sorted(derived.scope_levels),
                "flags": sorted(derived.flags),
                "transportFlag": derived.transport_flag,
                "platformOrg": derived.platform_org,
                "panels": list(derived.panels),
                "streams": {
                    key: {"event": stream.event, "module": stream.module}
                    for key, stream in sorted(derived.streams.items())
                },
                "consumes": derived.consumes,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cockpit-functions", description=__doc__)
    parser.add_argument(
        "--registry", default=None, help="the registry to read (default: the tree's own)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="the check (exit 1 naming every finding)")
    validate.set_defaults(func=cmd_validate)

    listing = sub.add_parser("list", help="every declared function")
    listing.add_argument("--role", default=None, help="recommended for this role")
    listing.set_defaults(func=cmd_list)

    show = sub.add_parser("show", help="one declared function")
    show.add_argument("function")
    show.set_defaults(func=cmd_show)

    call = sub.add_parser("call", help="resolve an operator invocation")
    call.add_argument("function")
    call.add_argument("argument", nargs="*", help="name=value (repeatable)")
    call.set_defaults(func=cmd_call)

    render = sub.add_parser("render", help="render one function against its fixture")
    render.add_argument("function")
    render.add_argument("--outcome", default=None, choices=list(renderer.OUTCOMES))
    render.add_argument("--fixtures", default=None)
    render.set_defaults(func=cmd_render)

    frames = sub.add_parser("frames", help="render every declared function")
    frames.add_argument("--fixtures", default=None)
    frames.set_defaults(func=cmd_frames)

    panels = sub.add_parser("panels", help="the cockpit's rendered panels and their function")
    panels.set_defaults(func=cmd_panels)

    derived = sub.add_parser("derive", help="the consumed authorities, as read")
    derived.set_defaults(func=cmd_derive)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except reg.RegistryError as exc:
        sys.stderr.write(f"control-functions: CANNOT-ASSESS — {exc}\n")
        return CANNOT_ASSESS
    except renderer.RenderError as exc:
        sys.stderr.write(f"control-functions: CANNOT-ASSESS — {exc}\n")
        return CANNOT_ASSESS


if __name__ == "__main__":
    raise SystemExit(main())
