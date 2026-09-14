"""The OpenAPI document is emitted from the contracts and refuses drift (#413)."""

from __future__ import annotations

from pathlib import Path

from integrations.paperclip.api import company, contracts, openapi, taxonomy


def test_contract_components_come_from_the_frozen_schemas(root: Path) -> None:
    components = contracts.contract_components(root)
    assert sorted(components) == ["Budget", "Heartbeat", "Ticket"]
    ticket = components["Ticket"]
    # The v2 facets/authority fields ride along because they are in the schema.
    assert "facets" in ticket["properties"]
    assert "authority" in ticket["properties"]
    assert "$id" not in ticket and "$schema" not in ticket


def test_adapter_components_are_the_adapters_own_shape() -> None:
    components = contracts.adapter_components()
    assert sorted(components) == ["Activity", "Agent", "Approval"]
    for name, schema in components.items():
        assert schema["x-shape-provenance"] == f"integrations/paperclip/model.py::{name}.to_dict"
        assert schema["required"] == sorted(schema["properties"])


def test_document_publishes_the_surface(root: Path) -> None:
    document = openapi.build_document(root)
    paths = document["paths"]
    assert "/api/health" in paths
    assert "/api/openapi.json" in paths
    for segment in ("agents", "issues", "costs", "activity"):
        assert f"{openapi.COMPANY_PATH}/{segment}" in paths


def test_company_routes_are_scoped_and_carry_every_status(root: Path) -> None:
    document = openapi.build_document(root)
    for segment in ("agents", "issues", "costs", "activity"):
        operation = document["paths"][f"{openapi.COMPANY_PATH}/{segment}"]["get"]
        refs = [param["$ref"] for param in operation["parameters"]]
        assert "#/components/parameters/companyId" in refs
        for status in taxonomy.statuses():
            assert str(status) in operation["responses"]


def test_document_declares_the_single_company_mapping(root: Path) -> None:
    document = openapi.build_document(root)
    assert document["x-company-mapping"] == company.mapping()
    assert document["components"]["parameters"]["companyId"]["x-company-mapping"] == company.MAPPING_ID


def test_validate_document_is_clean_for_a_fresh_build(root: Path) -> None:
    assert openapi.validate_document(openapi.build_document(root), root) == []


def _find(findings: list, needle: str) -> bool:
    return any(needle in finding for finding in findings)


def test_validate_refuses_contract_drift_by_name(root: Path) -> None:
    document = openapi.build_document(root)
    document["components"]["schemas"]["Ticket"]["properties"]["status"]["enum"] = ["open"]
    findings = openapi.validate_document(document, root)
    assert _find(findings, "drifts from the frozen contract") and _find(findings, "ticket.schema.json")


def test_validate_refuses_a_missing_taxonomy_entry_by_name(root: Path) -> None:
    document = openapi.build_document(root)
    del document["components"]["responses"]["422"]
    findings = openapi.validate_document(document, root)
    assert _find(findings, "'422' is missing")


def test_validate_refuses_an_undeclared_company_mapping_by_name(root: Path) -> None:
    document = openapi.build_document(root)
    document.pop("x-company-mapping")
    document["components"]["parameters"]["companyId"].pop("x-company-mapping")
    findings = openapi.validate_document(document, root)
    assert _find(findings, "company mapping is undeclared")


def test_validate_refuses_a_drifted_adapter_shape(root: Path) -> None:
    document = openapi.build_document(root)
    document["components"]["schemas"]["Agent"]["properties"]["agent_id"] = {"type": "integer"}
    findings = openapi.validate_document(document, root)
    assert _find(findings, "no longer matches the adapter's own to_dict shape")


def test_validate_refuses_a_missing_health_path(root: Path) -> None:
    document = openapi.build_document(root)
    document["paths"].pop("/api/health")
    findings = openapi.validate_document(document, root)
    assert _find(findings, "the health path '/api/health' is missing")
