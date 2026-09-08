"""Pytest bootstrap + shared helpers for identity/onboarding tests.

``identity/`` has no ``__init__.py`` (a later identity-phase lane owns adding
one), so ``identity/onboarding`` is reached through the repo-root PEP-420
namespace package and the sibling ``rbac`` package is importable by putting
``identity/`` on ``sys.path`` - mirroring ``identity/rbac/tests/conftest.py``.
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
# identity/onboarding/tests -> identity/onboarding -> identity -> repo root
_onboarding_root = os.path.dirname(_here)
_identity_root = os.path.dirname(_onboarding_root)
_repo_root = os.path.dirname(_identity_root)
sys.path.insert(0, _repo_root)
sys.path.insert(0, _identity_root)

import pytest  # noqa: E402

from rbac.store import InMemoryStore as RbacStore  # noqa: E402

from identity.onboarding.provisioning import (  # noqa: E402
    ProvisionSpec,
    provision,
)
from identity.onboarding.store import InMemoryStore  # noqa: E402


@pytest.fixture()
def stores():
    """Fresh (onboarding store, RBAC store) pair per test."""
    return InMemoryStore(), RbacStore()


@pytest.fixture()
def make_spec():
    """Build a ProvisionSpec with sane defaults and per-test overrides."""

    def _make(**overrides):
        values = dict(
            slug="acme",
            name="Acme Corp",
            tenant_type="platform",
            idp_tenant_id="idp-acme",
            domain="acme.example.com",
            owner_email="admin@acme.example.com",
            owner_name="Acme Admin",
            owner_role="owner",
        )
        values.update(overrides)
        return ProvisionSpec(**values)

    return _make


@pytest.fixture()
def canonical_state():
    """Project a tenant's provisioned end-state to a comparable value.

    Drops timestamps and job records so two runs can be compared for
    idempotency (identical end state) without date noise.
    """

    def _canonical(store, rbac_store, slug):
        tenant = store.get_tenant(slug)
        tenant_view = None
        if tenant is not None:
            defaults = {
                key: (tuple(sorted(value)) if isinstance(value, list) else value)
                for key, value in sorted(tenant.defaults.items())
            }
            tenant_view = (
                tenant.id,
                tenant.name,
                tenant.tenant_type,
                tenant.status,
                tenant.idp_tenant_id,
                tenant.domain,
                tenant.owner_email,
                tenant.owner_name,
                tenant.owner_role,
                tenant.role_admin_permission,
                defaults,
            )
        idp_view = None
        mapping = store.get_idp_mapping(slug)
        if mapping is not None:
            idp_view = (mapping.tenant_id, mapping.idp_tenant_id, mapping.status)
        seeds = tuple(
            (s.kind, s.ref, s.version, s.status, s.source)
            for s in store.seeds_for_tenant(slug)
        )
        roles = tuple(
            sorted(role.key for role in rbac_store.roles_in_org(slug))
        )
        bindings = tuple(
            sorted(
                (
                    binding.subject,
                    rbac_store.role_by_id(binding.role_id).key,
                    binding.team_id,
                )
                for binding in rbac_store.bindings_in_org(slug)
            )
        )
        return (tenant_view, idp_view, seeds, roles, bindings)

    return _canonical


@pytest.fixture()
def run_provision(stores, make_spec):
    """Provision a tenant by direct pipeline call (no job wrapper)."""

    def _run(**overrides):
        store, rbac_store = stores
        return provision(store, rbac_store, make_spec(**overrides))

    return _run
