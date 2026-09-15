#!/usr/bin/env python3
"""Compare the control-plane ROUTER's routes against its OpenAPI SPEC (issue #816).

The spec is a published contract — `identity/cpapi/openapi.yaml` describes itself
as "the contract the generated clients in `clients/` are built against". A
contract that has silently drifted from the code is worse than no contract,
because a consumer trusts it. Measured 2026-09-15 they agreed (29 routes / 29
paths, zero drift in both directions) — and **nothing kept them agreeing**: no
gate compared the two, so a route added, renamed or removed in code would
invalidate the contract without a single check firing.

This module is the comparator. It lives in its own file, and takes both artifacts
as ARGUMENTS, so `scripts/check-cpapi-spec-drift.sh` can drive it against planted
fixtures -- proving the comparator works without touching the real files. A proof
that drives a copy of the logic proves nothing about the logic that runs.

    cpapi-spec-drift.py <router.py> <openapi.yaml>

Exit codes follow the repo tri-state: 0 agree / 1 drift / 2 CANNOT-ASSESS.

THE NORMALISATION, STATED EXPLICITLY
The router declares versioned paths (`/v1/tenants/{tenantId}`); the spec declares
unversioned ones (`/tenants/{tenantId}`) because the version lives in the spec's
`servers` URL (`https://.../v1`). So the comparator strips a leading `/v<N>` from
router paths before comparing. That mapping is a real assumption, not a detail:
it is named here and reported by `--explain`, because a future reader who does not
know it will mistake the prefix for drift and "fix" the comparator into agreeing
with a broken spec.
"""
from __future__ import annotations

import re
import sys

# The YAML keys that are HTTP methods rather than path-item metadata
# (`parameters`, `summary`, `servers`, `$ref`, ...).
HTTP_METHODS = frozenset({"get", "post", "put", "delete", "patch", "head", "options", "trace"})

# `Route("GET", "/v1/tenants/{tenantId}", "org:read", "tenant.get")` — the
# declarative table in identity/cpapi/control.py. Method and path are positional
# and first, which is what makes them safely extractable.
_ROUTE_RE = re.compile(r'Route\(\s*"([A-Z]+)"\s*,\s*"([^"]+)"')
_VERSION_RE = re.compile(r"^/v\d+")


def normalise(path: str) -> str:
    """Strip the version prefix the router carries and the spec carries in `servers`."""
    return _VERSION_RE.sub("", path)


def router_routes(text: str) -> set[tuple[str, str]]:
    """(METHOD, path) pairs declared in the router source."""
    return {(m.group(1), normalise(m.group(2))) for m in _ROUTE_RE.finditer(text)}


def spec_routes(document: dict) -> set[tuple[str, str]]:
    """(METHOD, path) pairs declared in a parsed OpenAPI document."""
    found: set[tuple[str, str]] = set()
    for path, item in (document.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for key in item:
            if key.lower() in HTTP_METHODS:
                found.add((key.upper(), path))
    return found


def compare(router_text: str, spec_document: dict) -> dict:
    """Return the drift in both directions, plus the counts that produced it."""
    router = router_routes(router_text)
    spec = spec_routes(spec_document)
    return {
        "router_count": len(router),
        "spec_count": len(spec),
        "router_only": sorted(router - spec),
        "spec_only": sorted(spec - router),
    }


def explain() -> str:
    return (
        "normalisation: a leading /v<N> is stripped from ROUTER paths, because the\n"
        "  router declares /v1/tenants/{tenantId} while the spec declares\n"
        "  /tenants/{tenantId} and carries the version in its `servers` URL.\n"
        "  Without this the comparator would report all routes as drift."
    )


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] == "--explain":
        print(explain())
        return 0
    if len(argv) != 3:
        print("usage: cpapi-spec-drift.py <router.py> <openapi.yaml> | --explain", file=sys.stderr)
        return 2

    try:
        import yaml
    except ImportError:
        print("cpapi-spec-drift: CANNOT-ASSESS — PyYAML is unavailable", file=sys.stderr)
        return 2

    router_path, spec_path = argv[1], argv[2]
    try:
        router_text = open(router_path, encoding="utf-8").read()
    except OSError as exc:
        print(f"cpapi-spec-drift: CANNOT-ASSESS — cannot read the router: {exc}", file=sys.stderr)
        return 2
    try:
        spec_document = yaml.safe_load(open(spec_path, encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        print(f"cpapi-spec-drift: CANNOT-ASSESS — cannot read the spec: {exc}", file=sys.stderr)
        return 2

    if not isinstance(spec_document, dict):
        print("cpapi-spec-drift: CANNOT-ASSESS — the spec did not parse to a mapping", file=sys.stderr)
        return 2
    if not (spec_document.get("paths") or {}):
        # A spec with no `paths` is not "no drift" -- it is an unreadable spec, and
        # reporting agreement would be the fail-open this repo keeps removing.
        print("cpapi-spec-drift: CANNOT-ASSESS — the spec declares no paths", file=sys.stderr)
        return 2

    result = compare(router_text, spec_document)

    if result["router_count"] == 0:
        # Symmetrically: a router the extractor cannot read must never read as
        # "the spec is fine". Zero routes means the extraction failed.
        print(
            "cpapi-spec-drift: CANNOT-ASSESS — extracted ZERO routes from the router; "
            "the Route(...) table is missing or spelled differently",
            file=sys.stderr,
        )
        return 2

    print(f"  router routes: {result['router_count']}")
    print(f"  spec routes  : {result['spec_count']}")

    drift = False
    if result["router_only"]:
        drift = True
        print(f"\n  in ROUTER, missing from SPEC: {len(result['router_only'])}")
        for method, path in result["router_only"]:
            print(f"     {method:5} {path}")
    if result["spec_only"]:
        drift = True
        print(f"\n  in SPEC, missing from ROUTER: {len(result['spec_only'])}")
        for method, path in result["spec_only"]:
            print(f"     {method:5} {path}")

    if drift:
        print("\n  ==> DRIFT — the published contract does not match the code", file=sys.stderr)
        return 1
    print("\n  ==> AGREE")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
