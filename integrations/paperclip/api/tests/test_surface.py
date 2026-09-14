"""The route surface is read from the adapter's own client (issue #413)."""

from __future__ import annotations

from pathlib import Path

from integrations.paperclip.api import openapi, surface


def test_routes_are_read_from_the_client() -> None:
    routes = surface.client_routes()
    paths = {(route.method, route.path) for route in routes}
    assert ("GET", "/api/health") in paths
    assert ("GET", "/api/openapi.json") in paths
    assert ("GET", "/api/companies/{companyId}/agents") in paths
    assert ("GET", "/api/companies/{companyId}/issues") in paths


def test_mutations_are_not_part_of_the_read_surface() -> None:
    """A method that needs arguments (a mutation) is never read as a route."""
    paths = {route.path for route in surface.client_routes()}
    assert "/api/issues/{issueId}" not in paths
    assert all(
        route.operation_id not in {"create_issue", "update_issue", "request_topup"}
        for route in surface.client_routes()
    )


def test_document_describes_every_client_route(root: Path) -> None:
    document = openapi.build_document(root)
    for route in surface.client_routes():
        assert route.path in document["paths"], f"{route.path} missing from the document"
        assert route.method.lower() in document["paths"][route.path]


def test_provenance_records_the_client_routes(root: Path) -> None:
    document = openapi.build_document(root)
    recorded = {(entry["method"], entry["path"]) for entry in document["x-client-routes"]}
    assert recorded == {(r.method, r.path) for r in surface.client_routes()}


def test_an_invented_route_is_refused_by_name(root: Path) -> None:
    document = openapi.build_document(root)
    document["paths"]["/api/companies/{companyId}/made-up"] = {
        "get": {"operationId": "listMadeUp", "responses": {}}
    }
    findings = openapi.validate_document(document, root)
    assert any("the client does not emit" in finding for finding in findings)


def test_dashboard_is_declared_unshaped_not_invented(root: Path) -> None:
    document = openapi.build_document(root)
    dashboard = document["components"]["schemas"]["Dashboard"]
    assert "not introspectable" in dashboard["x-shape-provenance"]
