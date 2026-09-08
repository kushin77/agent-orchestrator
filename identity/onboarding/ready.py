"""Tenant ready-check: verifies provisioning completeness (issue #14).

``ready_check`` produces a report of named checks - the tenant row is active,
the IdP mapping (when requested) is linked, the RBAC Org exists, every preset
role of the tenant-type pack is present, the recorded owner/admin binding
exists, every expected starter seed is recorded (installed, or deferred by
contract while the persona registry is absent - issue #11), and the base
defaults are present. A later phase (#39-#42 portal/health) may surface this
as a health/readiness endpoint; this lane ships the pure completeness check.

A negative test removes one expected element (a seed, a role, the org, the
owner binding) and asserts the report flips to not-ready.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from identity.onboarding import provisioning, registry_assets
from identity.onboarding.model import (
    SEED_STATUS_DEFERRED,
    SEED_STATUS_INSTALLED,
    TENANT_STATUS_ACTIVE,
    TENANT_DEFAULT_KEYS,
)


@dataclass(frozen=True)
class ReadyCheck:
    """One named completeness check of a tenant."""

    name: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class ReadyReport:
    """The full readiness verdict for one tenant."""

    tenant_id: str
    ready: bool
    checks: tuple[ReadyCheck, ...] = field(default_factory=tuple)


def _seed_satisfied(item, kind: str, repo_root: Path) -> tuple[bool, str]:
    """Whether a recorded seed counts as provisioned.

    ``installed`` always counts. ``deferred`` counts only for persona seeds
    whose owning registry (issue #11) is still absent from the checkout - the
    documented deferral-by-contract. Any other deferred state is not ready.
    """
    if item.status == SEED_STATUS_INSTALLED:
        return True, f"{kind} {item.ref} installed"
    if item.status == SEED_STATUS_DEFERRED and kind == "persona":
        status = registry_assets.persona_registry(repo_root)
        if not status.dir_present:
            return True, f"persona {item.ref} deferred (registry absent - issue #11)"
    return False, f"{kind} {item.ref} not provisioned (status={item.status})"


def ready_check(
    store,
    rbac_store,
    tenant_id: str,
    *,
    repo_root: Path | None = None,
    seed_pack: dict | None = None,
) -> ReadyReport:
    """Report whether a tenant is fully provisioned."""
    root = repo_root or registry_assets.default_repo_root()
    checks: list[ReadyCheck] = []

    tenant = store.get_tenant(tenant_id)
    if tenant is None:
        checks.append(
            ReadyCheck("tenant-exists", False, f"no tenant row for {tenant_id!r}")
        )
        return ReadyReport(tenant_id=tenant_id, ready=False, checks=tuple(checks))

    checks.append(ReadyCheck("tenant-exists", True, f"tenant row {tenant_id} present"))

    if tenant.status == TENANT_STATUS_ACTIVE:
        checks.append(ReadyCheck("tenant-active", True, "tenant status is active"))
    else:
        checks.append(
            ReadyCheck("tenant-active", False, f"tenant status is {tenant.status!r}")
        )

    mapping = store.get_idp_mapping(tenant_id)
    if tenant.idp_tenant_id:
        if mapping is not None and mapping.idp_tenant_id == tenant.idp_tenant_id:
            checks.append(
                ReadyCheck(
                    "idp-mapping", True, f"linked to IdP tenant {tenant.idp_tenant_id}"
                )
            )
        else:
            checks.append(
                ReadyCheck(
                    "idp-mapping",
                    False,
                    f"IdP mapping missing for requested IdP tenant {tenant.idp_tenant_id}",
                )
            )
    else:
        checks.append(
            ReadyCheck(
                "idp-mapping", True, "no IdP tenant requested by the operator"
            )
        )

    org = rbac_store.org(tenant_id) if hasattr(rbac_store, "org") else None
    if org is not None:
        checks.append(ReadyCheck("rbac-org", True, f"RBAC org {tenant_id} present"))
    else:
        checks.append(ReadyCheck("rbac-org", False, f"RBAC org {tenant_id} missing"))

    # preset roles
    expected_keys = set()
    try:
        pack = provisioning.resolve_pack_for_tenant(tenant)
        expected_keys = set(pack.role_keys())
    except KeyError:
        expected_keys = set()
    if org is None:
        checks.append(
            ReadyCheck(
                "roles-complete",
                False,
                f"RBAC org {tenant_id} missing - cannot verify preset roles",
            )
        )
    else:
        existing_keys = {role.key for role in rbac_store.roles_in_org(tenant_id)}
        missing_keys = sorted(expected_keys - existing_keys)
        if not missing_keys:
            checks.append(
                ReadyCheck(
                    "roles-complete",
                    True,
                    f"all {len(expected_keys)} preset roles present",
                )
            )
        else:
            checks.append(
                ReadyCheck(
                    "roles-complete",
                    False,
                    f"missing preset role(s): {', '.join(missing_keys)}",
                )
            )

    # owner/admin binding
    if tenant.owner_email:
        bound = _binding_holds(rbac_store, tenant)
        if bound:
            checks.append(
                ReadyCheck(
                    "owner-bound",
                    True,
                    f"owner {tenant.owner_email} holds {tenant.owner_role} org-wide",
                )
            )
        else:
            checks.append(
                ReadyCheck(
                    "owner-bound",
                    False,
                    f"owner binding for {tenant.owner_email} -> {tenant.owner_role} missing",
                )
            )
    else:
        checks.append(
            ReadyCheck(
                "owner-bound",
                True,
                "no owner recorded (tenant provisioned without a first owner)",
            )
        )

    # starter seeds
    expected_entries = _seed_entries_for(seed_pack)
    seed_missing: list[str] = []
    for kind, ref in expected_entries:
        item = store.get_seed(tenant_id, kind, ref)
        if item is None:
            seed_missing.append(f"{kind} {ref}")
            continue
        ok, detail = _seed_satisfied(item, kind, root)
        if not ok:
            seed_missing.append(detail)
    if not seed_missing:
        checks.append(
            ReadyCheck(
                "seeds-complete",
                True,
                f"all {len(expected_entries)} starter seeds provisioned",
            )
        )
    else:
        checks.append(
            ReadyCheck(
                "seeds-complete",
                False,
                "; ".join(seed_missing),
            )
        )

    # defaults
    if tenant.defaults and all(key in tenant.defaults for key in TENANT_DEFAULT_KEYS):
        checks.append(ReadyCheck("defaults-present", True, "base tenant defaults present"))
    else:
        checks.append(ReadyCheck("defaults-present", False, "base tenant defaults missing"))

    return ReadyReport(
        tenant_id=tenant_id,
        ready=all(check.ok for check in checks),
        checks=tuple(checks),
    )


def _binding_holds(rbac_store, tenant) -> bool:
    """True when the tenant's recorded owner holds its owner role org-wide."""
    if not hasattr(rbac_store, "find_role_by_key"):
        return False
    role = rbac_store.find_role_by_key(tenant.id, tenant.owner_role)
    if role is None or not hasattr(rbac_store, "bindings_for_subject"):
        return False
    for binding in rbac_store.bindings_for_subject(tenant.id, tenant.owner_email or ""):
        if binding.role_id == role.id and binding.team_id is None:
            return True
    return False


def _seed_entries_for(seed_pack: dict | None) -> list[tuple[str, str]]:
    return provisioning.seed_entries(seed_pack)
