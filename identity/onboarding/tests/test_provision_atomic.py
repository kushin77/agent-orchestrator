"""All-or-nothing atomicity + fail-fast validation of provisioning
(issue #14, work item 10).

When any pipeline step fails, both stores are rolled back to their pre-run
state - no partial tenant (no orphan org, roles, bindings, IdP mapping or
seeds) is ever left behind, and pre-existing tenants are untouched. Invalid
input is rejected before any write.
"""

import pytest

from identity.onboarding.model import ProvisionError, ProvisionValidationError
from identity.onboarding.provisioning import provision

# A seed pack entry that cannot resolve in the platform registry aborts the
# seeds step - after the tenant row, org, roles and owner were already written,
# which is exactly the point where all-or-nothing must roll back.
_BAD_SEED_PACK = {
    "profiles": ["ghost@9.9.9"],
    "personas": [],
    "prompts": [],
}


def test_failure_rolls_back_everything(stores, make_spec, canonical_state):
    store, rbac_store = stores
    spec = make_spec()

    with pytest.raises(ProvisionError):
        provision(store, rbac_store, spec, seed_pack=_BAD_SEED_PACK)

    assert store.get_tenant(spec.slug) is None
    assert store.get_idp_mapping(spec.slug) is None
    assert store.seeds_for_tenant(spec.slug) == []
    assert rbac_store.org(spec.slug) is None
    assert rbac_store.roles_in_org(spec.slug) == []
    assert rbac_store.bindings_in_org(spec.slug) == []


def test_failed_run_leaves_no_partial_tenant(stores, make_spec):
    store, rbac_store = stores
    spec = make_spec()

    with pytest.raises(ProvisionError):
        provision(store, rbac_store, spec, seed_pack=_BAD_SEED_PACK)

    # The tenant row must be entirely absent - "no partial tenant".
    assert store.dump()["tenants"] == {}
    assert store.dump()["idp"] == {}
    assert store.dump()["seeds"] == []


def test_failure_preserves_existing_tenants(stores, make_spec, canonical_state):
    store, rbac_store = stores
    other = make_spec(
        slug="survivor",
        name="Survivor Co",
        idp_tenant_id="idp-survivor",
        domain="survivor.example.com",
        owner_email="a@survivor.example",
    )
    provision(store, rbac_store, other)
    before = canonical_state(store, rbac_store, other.slug)

    with pytest.raises(ProvisionError):
        provision(
            store,
            rbac_store,
            make_spec(),
            seed_pack=_BAD_SEED_PACK,
        )

    assert canonical_state(store, rbac_store, other.slug) == before
    assert store.get_tenant("acme") is None


def test_unknown_tenant_type_fails_before_any_write(stores, make_spec):
    store, rbac_store = stores
    spec = make_spec(tenant_type="not-a-real-pack")

    with pytest.raises(ProvisionValidationError):
        provision(store, rbac_store, spec)

    assert store.get_tenant(spec.slug) is None
    assert rbac_store.org(spec.slug) is None


def test_invalid_slug_rejected_before_any_write(stores, make_spec):
    store, rbac_store = stores
    spec = make_spec(slug="Not A Slug!")

    with pytest.raises(ProvisionValidationError):
        provision(store, rbac_store, spec)

    assert store.get_tenant("Not A Slug!") is None
    assert rbac_store.bindings_in_org("Not A Slug!") == []


def test_conflicting_rerun_rejected(stores, make_spec):
    store, rbac_store = stores
    spec = make_spec()
    provision(store, rbac_store, spec)

    # Same slug but a different name must not silently overwrite.
    with pytest.raises(ProvisionValidationError):
        provision(store, rbac_store, make_spec(name="Renamed Corp"))

    # A different first owner on an existing tenant must be refused.
    with pytest.raises(ProvisionValidationError):
        provision(
            store,
            rbac_store,
            make_spec(owner_email="someone-else@acme.example.com"),
        )

    tenant = store.get_tenant(spec.slug)
    assert tenant is not None
    assert tenant.name == "Acme Corp"
    assert tenant.owner_email == "admin@acme.example.com"


def test_unknown_owner_role_rejected_before_write(stores, make_spec):
    store, rbac_store = stores
    spec = make_spec(owner_role="superuser")

    with pytest.raises(ProvisionValidationError):
        provision(store, rbac_store, spec)

    assert store.get_tenant(spec.slug) is None
    assert rbac_store.org(spec.slug) is None


def test_dry_run_writes_nothing(stores, make_spec):
    store, rbac_store = stores
    spec = make_spec()

    result = provision(store, rbac_store, spec, dry_run=True)

    assert result.tenant.status == "active"  # preview reflects what WOULD happen
    assert store.get_tenant(spec.slug) is None
    assert rbac_store.org(spec.slug) is None
    assert store.seeds_for_tenant(spec.slug) == []
