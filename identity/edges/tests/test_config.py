"""Declarative allowlist config - YAML/code parity + validation (fail closed).

The committed ``config/public-routes.yaml`` is the operator-editable
declarative view of the public front door.  It must stay in lockstep with the
code-level single source of truth (``allowlist.DEFAULT_ROUTE_SPECS``), load
through the validator, and reject malformed/ambiguous tables.
"""

from __future__ import annotations

import pytest

from identity.edges.allowlist import (
    AllowlistError,
    default_config_path,
    default_public_routes,
    load_public_routes_yaml,
    route_from_spec,
)


def _default_specs_set(routes) -> set[tuple]:
    """A comparable view of a PublicRoutes table (route_id, method, paths...)."""
    return {
        (
            r.route_id,
            r.method,
            r.api_path,
            r.backend_path,
            r.authenticated,
            r.expected_tenant,
        )
        for r in routes.routes
    }


def test_committed_yaml_is_in_parity_with_code_default():
    code_routes = default_public_routes()
    yaml_routes = load_public_routes_yaml(open(default_config_path(), encoding="utf-8"))
    assert _default_specs_set(yaml_routes) == _default_specs_set(code_routes)


def test_yaml_allowlist_round_trip_matches_code():
    code_routes = default_public_routes()
    yaml_routes = load_public_routes_yaml(open(default_config_path(), encoding="utf-8"))
    assert yaml_routes.route_ids() == code_routes.route_ids()
    assert len(yaml_routes.routes) == 6


def test_malformed_yaml_is_rejected():
    import io

    bad = io.StringIO("routes: [not-a-mapping")
    with pytest.raises(AllowlistError):
        load_public_routes_yaml(bad)


def test_yaml_missing_routes_key_is_rejected():
    import io

    bad = io.StringIO("apiVersion: v1\nroutes: []\n")
    with pytest.raises(AllowlistError):
        load_public_routes_yaml(bad)


def test_yaml_unsupported_api_version_is_rejected():
    import io

    bad = io.StringIO("apiVersion: v2\nroutes: [{route_id: a, method: GET, api_path: /a, backend_path: /a}]\n")
    with pytest.raises(AllowlistError):
        load_public_routes_yaml(bad)


def test_yaml_duplicate_route_is_rejected():
    import io

    text = (
        "apiVersion: v1\n"
        "routes:\n"
        "  - {route_id: a, method: GET, api_path: /v1/a, backend_path: /v1/a}\n"
        "  - {route_id: b, method: GET, api_path: /v1/a, backend_path: /v1/a}\n"
    )
    with pytest.raises(AllowlistError):
        load_public_routes_yaml(io.StringIO(text))


def test_route_from_spec_validation():
    with pytest.raises(AllowlistError):
        route_from_spec({"method": "GET", "api_path": "/v1/x", "backend_path": "/v1/x"})
    with pytest.raises(AllowlistError):
        route_from_spec({"route_id": "x", "api_path": "/v1/x", "backend_path": "/v1/x"})
    with pytest.raises(AllowlistError):
        route_from_spec({"route_id": "x", "method": "GET", "backend_path": "/v1/x"})
    with pytest.raises(AllowlistError):
        route_from_spec(
            {"route_id": "x", "method": "GET", "api_path": "/v1/x",
             "backend_path": "/v1/x", "authenticated": "yes"}  # non-bool
        )


def test_expected_tenant_is_optional():
    route = route_from_spec(
        {"route_id": "x", "method": "GET", "api_path": "/v1/x",
         "backend_path": "/v1/x", "expected_tenant": None}
    )
    assert route.expected_tenant is None
    route2 = route_from_spec(
        {"route_id": "y", "method": "GET", "api_path": "/v1/y",
         "backend_path": "/v1/y", "expected_tenant": "acme"}
    )
    assert route2.expected_tenant == "acme"
