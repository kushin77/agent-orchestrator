"""Tenant provisioning pipeline - the write path that makes a tenant exist
(issue #14, work item 10).

Given a ``ProvisionSpec`` it creates, in order: the onboarding tenant row, the
IdP tenant mapping, the RBAC Org (Org.id == tenant slug), the tenant-type
preset roles, the first owner/admin binding, the starter seed packs
(profile/prompt/persona assets installed from the platform registries), and
the tenant's base defaults. It then flips the tenant to ``active``.

Why this is not an HTTP endpoint
--------------------------------
Provisioning a tenant is an operator action, not something an end user does.
Exposing it anonymously would mean an unauthenticated write reachable by
anyone, or a permission check inside a tenant that has no roles and no
bindings yet (nobody can hold ``roles:manage`` in a tenant that does not
exist). The entry point is the operator CLI (``identity.onboarding.cli``), run
by a human or an automation with control-plane access. Later identity phases
(#35-#38) may expose an authenticated control-plane surface; this lane ships
the controller core only.

All-or-nothing
--------------
The pipeline runs against resource snapshots of both stores. If any step
fails, the onboarding tenant-resource collections and the RBAC store are
restored to their pre-run state - no partial tenant is ever left behind.
Provisioning-job records (attempt/audit history) live outside that resource
scope and survive a rollback, mirroring a job row committing outside the
resource transaction.

Idempotent and re-run safe
--------------------------
Every step is "create if missing, otherwise use/return what is there". A run
that fails partway (or a re-run over an already-provisioned tenant) converges:
re-running produces no duplicate roles, bindings or seeds and leaves an
identical end state.
"""

from __future__ import annotations

import copy
import sys
from dataclasses import dataclass
from pathlib import Path

# Make the sibling ``rbac`` package importable (identity/ has no __init__.py;
# the rbac package is consumed as a top-level ``rbac`` - see
# identity/rbac/README.md and identity/rbac/tests/conftest.py).
_IDENTITY_DIR = str(Path(__file__).resolve().parents[1])
if _IDENTITY_DIR not in sys.path:
    sys.path.insert(0, _IDENTITY_DIR)

import yaml

from rbac.bindings import grant_role
from rbac.presets import resolve_pack, seed_org
from rbac.store import InMemoryStore as RbacInMemoryStore

from identity.onboarding import registry_assets
from identity.onboarding.model import (
    BUILTIN_TENANT_TYPES,
    DEFAULT_OWNER_ROLE_KEY,
    DEFAULT_ROLE_ADMIN_PERMISSION,
    SEED_KIND_PERSONA,
    SEED_KIND_PROFILE,
    SEED_KIND_PROMPT,
    SEED_STATUS_DEFERRED,
    SEED_STATUS_INSTALLED,
    SLUG_PATTERN,
    TENANT_STATUS_ACTIVE,
    TENANT_STATUS_PROVISIONING,
    TENANT_DEFAULT_KEYS,
    AuditEvent,
    IdpTenantMapping,
    ProvisionError,
    ProvisionResult,
    ProvisionValidationError,
    SeedItem,
    Tenant,
    default_tenant_defaults,
    is_valid_email,
    utcnow_iso,
)

_SEEDS_DIR = Path(__file__).resolve().parent / "seeds"
_DEFAULT_SEED_PACK_FILE = _SEEDS_DIR / "tenant-starter.yaml"

_SEED_KIND_BY_COLLECTION = {
    "profiles": SEED_KIND_PROFILE,
    "personas": SEED_KIND_PERSONA,
    "prompts": SEED_KIND_PROMPT,
}


@dataclass(frozen=True)
class ProvisionSpec:
    """What provisioning should make exist (operator input, validated early)."""

    slug: str
    name: str
    tenant_type: str = "platform"
    idp_tenant_id: str | None = None
    domain: str | None = None
    owner_email: str | None = None
    owner_name: str | None = None
    owner_role: str = DEFAULT_OWNER_ROLE_KEY
    role_admin_permission: str = DEFAULT_ROLE_ADMIN_PERMISSION
    max_attempts: int = 3  # used by the job runner, not the pipeline itself


def load_default_seed_pack() -> dict:
    """Load the default tenant-starter seed pack (identity/onboarding/seeds/)."""
    data = yaml.safe_load(_DEFAULT_SEED_PACK_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ProvisionError("default seed pack is malformed (not a mapping)")
    return data


def seed_entries(seed_pack: dict | None) -> list[tuple[str, str]]:
    """Flatten a seed pack into deterministic (kind, ref) entries."""
    pack = load_default_seed_pack() if seed_pack is None else seed_pack
    if not isinstance(pack, dict):
        raise ProvisionError("seed pack must be a mapping of kind -> [ref, ...]")
    entries: list[tuple[str, str]] = []
    for collection in ("profiles", "personas", "prompts"):
        kind = _SEED_KIND_BY_COLLECTION[collection]
        refs = pack.get(collection) or []
        if not isinstance(refs, list):
            raise ProvisionError(f"seed pack {collection!r} must be a list")
        for ref in refs:
            entries.append((kind, str(ref)))
    return entries


def resolve_pack_for_tenant(tenant):
    """Resolve the preset pack for an existing tenant record (ready-check)."""
    return resolve_pack(tenant.tenant_type)


def _normalize_host(domain: str) -> str:
    """Lowercase, no port, no trailing dot (mirrors saas-rbac normalizeHost)."""
    return domain.strip().lower().split(":")[0].rstrip(".")


def _load_pack_for(spec: ProvisionSpec):
    """Resolve + sanity-check the RBAC preset pack for a spec (no writes)."""
    if not isinstance(spec.slug, str) or not SLUG_PATTERN.match(spec.slug):
        raise ProvisionValidationError(
            f"tenant slug {spec.slug!r} must be lowercase alphanumeric with "
            "single hyphens between segments (e.g. 'capital-underwriting')"
        )
    if not spec.name or not spec.name.strip():
        raise ProvisionValidationError("tenant name must be non-empty")
    try:
        pack = resolve_pack(spec.tenant_type)
    except KeyError as exc:
        raise ProvisionValidationError(
            f"no role pack for tenant type {spec.tenant_type!r}; use a built-in "
            f"({', '.join(BUILTIN_TENANT_TYPES)}) or register a custom pack"
        ) from exc
    if not pack.grants_permission(spec.role_admin_permission):
        raise ProvisionValidationError(
            f"pack {pack.key!r} does not grant the org role-admin permission "
            f"{spec.role_admin_permission!r}; refusing to seed an unadministrable org"
        )
    if spec.owner_email is not None:
        if not is_valid_email(spec.owner_email):
            raise ProvisionValidationError(
                f"owner email {spec.owner_email!r} does not look like an email"
            )
        if spec.owner_role not in pack.role_keys():
            raise ProvisionValidationError(
                f"owner role {spec.owner_role!r} is not in the preset pack "
                f"(have: {', '.join(pack.role_keys())})"
            )
    return pack


def _validate_existing(spec: ProvisionSpec, store) -> None:
    """Refuse a re-run that would silently change or steal an existing record."""
    existing = store.get_tenant(spec.slug)
    if existing is not None:
        if existing.name != spec.name or existing.tenant_type != spec.tenant_type:
            raise ProvisionValidationError(
                f"tenant {spec.slug!r} already exists with a different "
                "name/tenant_type; re-running must pass the same name and "
                "tenant_type to converge"
            )
        if (
            existing.owner_email
            and spec.owner_email
            and existing.owner_email != spec.owner_email
        ):
            raise ProvisionValidationError(
                f"tenant {spec.slug!r} already has owner {existing.owner_email!r}; "
                "refusing to bind a different first owner"
            )
    if spec.idp_tenant_id:
        for mapping in store.list_idp_mappings():
            if mapping.idp_tenant_id == spec.idp_tenant_id and mapping.tenant_id != spec.slug:
                raise ProvisionValidationError(
                    f"IdP tenant {spec.idp_tenant_id!r} is already mapped to tenant "
                    f"{mapping.tenant_id!r}; an IdP tenant maps to exactly one tenant"
                )
    if spec.domain:
        domain = _normalize_host(spec.domain)
        for other in store.list_tenants():
            if other.id != spec.slug and other.domain == domain:
                raise ProvisionValidationError(
                    f"domain {domain!r} is already registered to tenant {other.id!r}"
                )


def provision(
    store,
    rbac_store,
    spec: ProvisionSpec,
    *,
    seed_pack: dict | None = None,
    repo_root: Path | None = None,
    job=None,
    dry_run: bool = False,
) -> ProvisionResult:
    """Create (or complete) a tenant, atomically and idempotently.

    ``job``, when given, receives step-level audit events and current-step
    fields so the job runner (``identity.onboarding.jobs``) can persist a
    retry/audit record without coupling the pipeline to the store.
    """
    pack = _load_pack_for(spec)
    _validate_existing(spec, store)

    snapshot = store.resource_snapshot()
    rbac_snapshot = copy.deepcopy(rbac_store.__dict__)
    steps: list[str] = []
    repo_root = repo_root or registry_assets.default_repo_root()

    def emit(step: str, status: str, detail: str = "") -> None:
        steps.append(step)
        if job is not None:
            job.step_name = step
            job.step_status = status
            job.audit_log.append(AuditEvent(utcnow_iso(), step, status, detail))

    def rollback() -> None:
        store.restore_resources(snapshot)
        rbac_store.__dict__.clear()
        rbac_store.__dict__.update(rbac_snapshot)

    converged = False
    try:
        existing = store.get_tenant(spec.slug)
        converged = existing is not None and existing.status == TENANT_STATUS_ACTIVE
        emit("validate", "ok", f"pack {pack.key!r} for tenant type {spec.tenant_type!r}")

        # 1. tenant row
        tenant = existing
        if tenant is None:
            tenant = Tenant(
                id=spec.slug,
                name=spec.name,
                tenant_type=spec.tenant_type,
                status=TENANT_STATUS_PROVISIONING,
                idp_tenant_id=spec.idp_tenant_id,
                domain=(_normalize_host(spec.domain) if spec.domain else None),
                owner_email=spec.owner_email,
                owner_name=spec.owner_name,
                owner_role=spec.owner_role,
                role_admin_permission=spec.role_admin_permission,
                defaults=default_tenant_defaults(),
                created_at=utcnow_iso(),
                updated_at=utcnow_iso(),
            )
            store.put_tenant(tenant)
            emit("tenant", "ok", f"created tenant row {spec.slug}")
        else:
            emit("tenant", "ok", f"tenant row {spec.slug} already exists")

        # 2. IdP tenant mapping
        if spec.idp_tenant_id:
            mapping = store.get_idp_mapping(spec.slug)
            if mapping is None:
                store.put_idp_mapping(
                    IdpTenantMapping(
                        tenant_id=spec.slug,
                        idp_tenant_id=spec.idp_tenant_id,
                        created_at=utcnow_iso(),
                    )
                )
                emit("idp", "ok", f"linked IdP tenant {spec.idp_tenant_id}")
            else:
                emit("idp", "ok", "IdP mapping already present")
        else:
            emit("idp", "ok", "no IdP tenant requested")

        # 3. RBAC org (Org.id == tenant slug)
        org = rbac_store.org(spec.slug)
        if org is None:
            org = rbac_store.add_org(
                org_id=spec.slug,
                name=spec.name,
                tenant_type=spec.tenant_type,
                role_admin_permission=spec.role_admin_permission,
            )
            emit("org", "ok", f"created RBAC org {spec.slug}")
        else:
            emit("org", "ok", f"RBAC org {spec.slug} already exists")

        # 4. preset roles (from the tenant-type pack)
        existing_keys = {role.key for role in rbac_store.roles_in_org(spec.slug)}
        expected_keys = set(pack.role_keys())
        if not existing_keys:
            seed_org(rbac_store, org, pack)
            emit("roles", "ok", f"seeded {len(expected_keys)} preset roles from {pack.key}")
        elif existing_keys == expected_keys:
            emit("roles", "ok", "preset roles already seeded")
        elif existing_keys < expected_keys:
            for key in sorted(expected_keys - existing_keys):
                preset = pack.find(key)
                if preset is None:
                    raise ProvisionError(f"pack {pack.key!r} lost preset {key!r}")
                rbac_store.create_role(
                    org_id=spec.slug,
                    key=key,
                    name=preset.name,
                    description=preset.description,
                    permissions=preset.permissions,
                    level=preset.level,
                    is_system=preset.is_system,
                )
            emit("roles", "ok", f"seeded {len(expected_keys - existing_keys)} missing role(s)")
        else:
            raise ProvisionValidationError(
                f"org {spec.slug!r} already has roles from a different pack: "
                f"unexpected keys {sorted(existing_keys - expected_keys)}"
            )

        # 5. first owner/admin binding
        owner_role = None
        if spec.owner_email:
            owner_role = spec.owner_role or DEFAULT_OWNER_ROLE_KEY
            grant_role(
                rbac_store,
                spec.slug,
                spec.owner_email,
                owner_role,
                subject_type="user",
            )
            if tenant.owner_email is None:
                tenant.owner_email = spec.owner_email
                tenant.owner_name = spec.owner_name
                tenant.owner_role = owner_role
                store.put_tenant(tenant)
            emit("owner", "ok", f"{spec.owner_email} -> {owner_role} (org-wide)")
        else:
            emit("owner", "ok", "no owner requested (tenant inert until one is bound)")

        # 6. starter seed packs
        seeded: list[SeedItem] = []
        for kind, ref in seed_entries(seed_pack):
            item = store.get_seed(spec.slug, kind, ref)
            if item is not None:
                seeded.append(item)
                continue
            resolved = registry_assets.resolve(kind, ref, repo_root)
            if kind == SEED_KIND_PERSONA and resolved.deferrable:
                item = SeedItem(
                    tenant_id=spec.slug,
                    kind=kind,
                    ref=ref,
                    status=SEED_STATUS_DEFERRED,
                    detail=resolved.detail,
                )
                store.add_seed(item)
                seeded.append(item)
            elif not resolved.present:
                raise ProvisionError(
                    f"registry seed not found: {kind} {ref}: {resolved.detail}"
                )
            else:
                item = SeedItem(
                    tenant_id=spec.slug,
                    kind=kind,
                    ref=ref,
                    version=resolved.version,
                    status=SEED_STATUS_INSTALLED,
                    source=resolved.source,
                )
                store.add_seed(item)
                seeded.append(item)
        emit("seeds", "ok", f"{len(seeded)} starter seed(s) recorded")

        # 7. base defaults
        if not tenant.defaults or not all(
            key in tenant.defaults for key in TENANT_DEFAULT_KEYS
        ):
            tenant.defaults = default_tenant_defaults()
            store.put_tenant(tenant)
        emit("defaults", "ok", "base tenant defaults present")

        # 8. activate
        tenant.status = TENANT_STATUS_ACTIVE
        tenant.provisioned_at = tenant.provisioned_at or utcnow_iso()
        tenant.updated_at = utcnow_iso()
        store.put_tenant(tenant)
        emit("finalize", "ok", f"tenant {spec.slug} active")

        roles = sorted(role.key for role in rbac_store.roles_in_org(spec.slug))
        result = ProvisionResult(
            tenant=tenant,
            roles=roles,
            owner_role=owner_role,
            seeds=store.seeds_for_tenant(spec.slug),
            steps=list(dict.fromkeys(steps)),
            converged=converged,
        )
        if dry_run:
            rollback()
        return result
    except Exception:
        rollback()
        raise


def materialize_rbac(store) -> RbacInMemoryStore:
    """Rebuild the RBAC store from persisted onboarding tenant records.

    The RBAC Org for a tenant is fully derivable from its onboarding row
    (org id == slug, tenant_type, role_admin_permission, owner binding), so a
    later process loading a ``FileStore`` can reconstruct the RBAC view without
    a separate RBAC persistence layer. Used by the operator CLI for
    ``status`` / ``ready`` across invocations.
    """
    rbac_store = RbacInMemoryStore()
    for tenant in sorted(store.list_tenants(), key=lambda t: t.id):
        if rbac_store.org(tenant.id) is None:
            org = rbac_store.add_org(
                org_id=tenant.id,
                name=tenant.name,
                tenant_type=tenant.tenant_type,
                role_admin_permission=(
                    tenant.role_admin_permission or DEFAULT_ROLE_ADMIN_PERMISSION
                ),
            )
            pack = resolve_pack(tenant.tenant_type)
            if not rbac_store.roles_in_org(tenant.id):
                seed_org(rbac_store, org, pack)
        if tenant.owner_email:
            role_key = tenant.owner_role or DEFAULT_OWNER_ROLE_KEY
            grant_role(
                rbac_store,
                tenant.id,
                tenant.owner_email,
                role_key,
                subject_type="user",
            )
    return rbac_store
