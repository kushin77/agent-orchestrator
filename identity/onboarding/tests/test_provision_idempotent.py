"""Idempotency: provisioning the same tenant twice converges to an identical
end state - no duplicate roles, bindings or seeds (issue #14, work item 10).

The acceptance criterion is a negative test: run twice, compare the projected
end state, assert it is identical.
"""

from identity.onboarding.provisioning import provision


def test_second_run_leaves_identical_end_state(stores, make_spec, canonical_state):
    store, rbac_store = stores
    spec = make_spec()

    result_one = provision(store, rbac_store, spec)
    before = canonical_state(store, rbac_store, spec.slug)

    assert result_one.tenant.status == "active"
    assert not result_one.converged

    result_two = provision(store, rbac_store, spec)
    after = canonical_state(store, rbac_store, spec.slug)

    assert before == after
    assert result_two.converged
    assert result_two.tenant.status == "active"


def test_no_duplicate_roles_bindings_or_seeds(stores, make_spec):
    store, rbac_store = stores
    spec = make_spec()

    provision(store, rbac_store, spec)
    first_roles = len(rbac_store.roles_in_org(spec.slug))
    first_bindings = len(rbac_store.bindings_in_org(spec.slug))
    first_seeds = len(store.seeds_for_tenant(spec.slug))
    first_tenants = len(store.list_tenants())
    first_idp = len(store.list_idp_mappings())

    provision(store, rbac_store, spec)

    assert len(rbac_store.roles_in_org(spec.slug)) == first_roles
    assert len(rbac_store.bindings_in_org(spec.slug)) == first_bindings
    assert len(store.seeds_for_tenant(spec.slug)) == first_seeds
    assert len(store.list_tenants()) == first_tenants
    assert len(store.list_idp_mappings()) == first_idp


def test_run_is_a_noop_when_already_provisioned(stores, make_spec):
    """A re-run over a converged tenant adds no new job-scoped writes."""
    store, rbac_store = stores
    spec = make_spec()
    provision(store, rbac_store, spec)
    provision(store, rbac_store, spec)

    tenant = store.get_tenant(spec.slug)
    assert tenant is not None
    assert tenant.status == "active"
    # Every expected starter seed is recorded exactly once (no duplicate refs).
    refs = [seed.ref for seed in store.seeds_for_tenant(spec.slug)]
    assert len(refs) == len(set(refs))
    assert "orchestrator@1.0.0" in refs  # a starter profile is present


def test_rerun_with_same_identity_converges_across_types(
    stores, make_spec, canonical_state
):
    """startup tenants converge like platform tenants (pack-driven seeding)."""
    store, rbac_store = stores
    spec = make_spec(tenant_type="startup")
    provision(store, rbac_store, spec)
    before = canonical_state(store, rbac_store, spec.slug)
    provision(store, rbac_store, spec)
    assert before == canonical_state(store, rbac_store, spec.slug)
