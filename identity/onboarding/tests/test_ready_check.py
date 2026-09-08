"""Tenant ready-check: verifies provisioning completeness (issue #14).

A fully provisioned tenant reports READY across all gates; removing any
expected element (a seed, the owner binding, the IdP mapping, the RBAC org)
flips the report to NOT READY. Persona seeds recorded as deferred-by-contract
count as satisfied while the persona registry (issue #11) is absent.
"""

from pathlib import Path

import pytest
import yaml

from identity.onboarding import ready as ready_mod
from identity.onboarding.provisioning import provision

from rbac.store import InMemoryStore as RbacStore  # noqa: E402


@pytest.fixture()
def ready_acme(stores, make_spec):
    store, rbac_store = stores
    provision(store, rbac_store, make_spec())
    return store, rbac_store, "acme"


def test_ready_when_fully_provisioned(ready_acme):
    store, rbac_store, slug = ready_acme
    report = ready_mod.ready_check(store, rbac_store, slug)
    assert report.ready
    names = [check.name for check in report.checks]
    assert all(check.ok for check in report.checks)
    assert {
        "tenant-exists",
        "tenant-active",
        "idp-mapping",
        "rbac-org",
        "roles-complete",
        "owner-bound",
        "seeds-complete",
        "defaults-present",
    } <= set(names)


def test_personas_install_from_merged_registry(ready_acme):
    """On master (persona registry present), persona seeds install."""
    store, rbac_store, slug = ready_acme
    personas = store.seeds_for_tenant(slug, kind="persona")
    assert personas  # starter personas are recorded...
    assert all(s.status == "installed" for s in personas)  # ...and installed
    assert any(s.ref == "orchestrator" for s in personas)
    report = ready_mod.ready_check(store, rbac_store, slug)
    assert report.ready


def test_persona_deferral_counts_as_satisfied_when_registry_absent(
    tmp_path, stores, make_spec
):
    """Without a persona registry, deferred persona seeds still count ready."""
    store, rbac_store = stores
    spec = make_spec()
    seed_pack = {"profiles": [], "personas": ["architecture-sme"], "prompts": []}
    provision(
        store,
        rbac_store,
        spec,
        seed_pack=seed_pack,
        repo_root=Path(tmp_path),
    )
    personas = store.seeds_for_tenant(spec.slug, kind="persona")
    assert personas
    assert personas[0].status == "deferred"
    report = ready_mod.ready_check(
        store,
        rbac_store,
        spec.slug,
        repo_root=Path(tmp_path),
        seed_pack=seed_pack,
    )
    assert report.ready


def test_not_ready_before_provisioning(stores, make_spec):
    store, rbac_store = stores
    report = ready_mod.ready_check(store, rbac_store, "acme")
    assert not report.ready
    check = next(c for c in report.checks if c.name == "tenant-exists")
    assert not check.ok


def test_missing_seed_flips_not_ready(ready_acme):
    store, rbac_store, slug = ready_acme
    removed = store.remove_seed(slug, "profile", "orchestrator@1.0.0")
    assert removed

    report = ready_mod.ready_check(store, rbac_store, slug)
    assert not report.ready
    seed_check = next(c for c in report.checks if c.name == "seeds-complete")
    assert not seed_check.ok
    assert "profile orchestrator@1.0.0" in seed_check.detail
    # Other gates are unaffected by the seed removal.
    owner_check = next(c for c in report.checks if c.name == "owner-bound")
    assert owner_check.ok


def test_revoked_owner_binding_flips_not_ready(ready_acme):
    store, rbac_store, slug = ready_acme
    tenant = store.get_tenant(slug)
    role = rbac_store.find_role_by_key(slug, tenant.owner_role)
    binding = next(
        b
        for b in rbac_store.bindings_for_subject(slug, tenant.owner_email)
        if b.role_id == role.id and b.team_id is None
    )
    rbac_store.delete_binding(binding.id)

    report = ready_mod.ready_check(store, rbac_store, slug)
    assert not report.ready
    owner_check = next(c for c in report.checks if c.name == "owner-bound")
    assert not owner_check.ok


def test_missing_idp_mapping_flips_not_ready(ready_acme):
    store, rbac_store, slug = ready_acme
    store._idp.pop(slug, None)  # noqa: SLF001 - negative-test seam

    report = ready_mod.ready_check(store, rbac_store, slug)
    assert not report.ready
    idp_check = next(c for c in report.checks if c.name == "idp-mapping")
    assert not idp_check.ok


def test_missing_org_flips_not_ready(stores, make_spec):
    store, _rbac = stores
    provision(store, _rbac, make_spec())
    # A different (empty) RBAC store simulates a missing org for the tenant.
    report = ready_mod.ready_check(store, RbacStore(), "acme")
    assert not report.ready
    for name in ("rbac-org", "roles-complete", "owner-bound"):
        check = next(c for c in report.checks if c.name == name)
        assert not check.ok


def test_suspended_tenant_not_ready(ready_acme):
    store, rbac_store, slug = ready_acme
    tenant = store.get_tenant(slug)
    tenant.status = "suspended"
    store.put_tenant(tenant)

    report = ready_mod.ready_check(store, rbac_store, slug)
    assert not report.ready
    active_check = next(c for c in report.checks if c.name == "tenant-active")
    assert not active_check.ok


def test_persona_install_when_registry_present(tmp_path, stores, make_spec):
    """With a persona registry present, personas install (not defer)."""
    store, rbac_store = stores
    spec = make_spec()
    repo_root = tmp_path
    cards = repo_root / "registry" / "personas" / "cards"
    cards.mkdir(parents=True)
    (cards / "security-sme.yaml").write_text(
        yaml.safe_dump({"id": "security-sme", "name": "Security SME"}),
        encoding="utf-8",
    )
    seed_pack = {"profiles": [], "personas": ["security-sme"], "prompts": []}

    result = provision(
        store, rbac_store, spec, seed_pack=seed_pack, repo_root=Path(repo_root)
    )
    persona_seeds = store.seeds_for_tenant(spec.slug, kind="persona")
    assert len(persona_seeds) == 1
    assert persona_seeds[0].status == "installed"
    assert persona_seeds[0].source == "registry/personas/cards/security-sme.yaml"
    assert result.tenant.status == "active"

    report = ready_mod.ready_check(
        store, rbac_store, spec.slug, repo_root=Path(repo_root), seed_pack=seed_pack
    )
    assert report.ready


def test_persona_missing_when_registry_present_fails_closed(tmp_path, stores, make_spec):
    """Persona registry present but expected id absent -> atomic failure."""
    store, rbac_store = stores
    cards = tmp_path / "registry" / "personas" / "cards"
    cards.mkdir(parents=True)
    (cards / "security-sme.yaml").write_text(
        yaml.safe_dump({"id": "security-sme"}), encoding="utf-8"
    )
    seed_pack = {"profiles": [], "personas": ["ghost-sme"], "prompts": []}

    with pytest.raises(Exception):
        provision(
            store,
            rbac_store,
            make_spec(),
            seed_pack=seed_pack,
            repo_root=Path(tmp_path),
        )
    assert store.get_tenant("acme") is None
    assert rbac_store.org("acme") is None
