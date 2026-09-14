"""The company scope is one declared mapping onto the fleet's own tenancy (#413)."""

from __future__ import annotations

from pathlib import Path

import pytest

from integrations.paperclip.api import company
from integrations.paperclip.api.errors import AuthError


def test_mapping_declares_the_identity_relation() -> None:
    declared = company.mapping()
    assert declared["id"] == company.MAPPING_ID
    assert declared["upstream"] == "companyId"
    assert declared["relation"] == "identity"
    assert declared["target_authority"].startswith("identity/rbac.Org.id")
    assert declared["declared_source"] == company.DECLARED_SOURCE.as_posix()


def test_declared_companies_reads_the_fleet_source(tree: Path) -> None:
    assert company.declared_companies(tree) == ("acme", "globex")


def test_declared_companies_fails_closed_without_the_source(tmp_path: Path) -> None:
    assert company.declared_companies(tmp_path) == ()


def test_resolve_is_an_identity_onto_the_fleet_tenant(tree: Path) -> None:
    assert company.resolve(tree, "acme") == "acme"
    assert company.tenant_for("acme") == "acme"


def test_resolve_refuses_an_unknown_company_with_404(tree: Path) -> None:
    with pytest.raises(AuthError) as caught:
        company.resolve(tree, "not-declared")
    assert caught.value.status == 404
    assert caught.value.code == "not_found"


def test_resolve_refuses_a_cross_company_read_with_403(tree: Path) -> None:
    with pytest.raises(AuthError) as caught:
        company.resolve(tree, "globex", caller_company="acme")
    assert caught.value.status == 403
    assert caught.value.code == "cross_tenant"


def test_resolve_refuses_an_empty_company_with_400(tree: Path) -> None:
    with pytest.raises(AuthError) as caught:
        company.resolve(tree, "  ")
    assert caught.value.status == 400
    assert caught.value.code == "validation_error"


def test_resolve_allows_the_callers_own_company(tree: Path) -> None:
    assert company.resolve(tree, "acme", caller_company="acme") == "acme"
