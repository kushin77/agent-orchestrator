"""Onboarding stores: the persistence seam for the provisioning controller.

Mirrors the identity/rbac store philosophy - an in-memory store is the offline
default used by tests and embedded use, and later identity phases (#35-#38)
swap in a database adapter behind the same collection accessors without
touching provisioning logic.

Two implementations:

- ``InMemoryStore`` - the canonical seam. Keeps records in dicts keyed by id.
  Exposes ``resource_snapshot()`` / ``restore_resources()`` so the provision
  pipeline can be all-or-nothing across the *tenant resource* collections
  while leaving provisioning-job records (attempt/audit history) durable.
- ``FileStore`` - an operator/CLI persistence layer that serializes the same
  records to a JSON file (deterministic ordering), so a provisioning run in
  one process can be inspected/verified by ``ready`` in a later process.
"""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from identity.onboarding.model import (
    AuditEvent,
    IdpTenantMapping,
    ProvisioningJob,
    SeedItem,
    Tenant,
    TenantCustomization,
)

# The tenant-resource collections the provision pipeline may mutate and must be
# able to roll back atomically. Provisioning-job records are deliberately NOT
# included: a job is an audit/attempt record, not a tenant resource, so it
# survives a rollback (mirrors a job row committing outside the resource
# transaction in the harvested capital-underwriting model).
_RESOURCE_ATTRS = ("_tenants", "_idp", "_seeds")


@dataclass
class InMemoryStore:
    """Thread-unsafe in-memory onboarding store (tests / embedded use)."""

    _tenants: dict[str, Tenant] = field(default_factory=dict)
    _idp: dict[str, IdpTenantMapping] = field(default_factory=dict)
    _seeds: dict[tuple[str, str, str], SeedItem] = field(default_factory=dict)
    _customizations: dict[str, TenantCustomization] = field(default_factory=dict)
    _jobs: dict[str, ProvisioningJob] = field(default_factory=dict)
    _next_id: int = 0

    def _new_id(self, prefix: str) -> str:
        self._next_id += 1
        return f"{prefix}_{self._next_id}"

    def next_id(self, prefix: str) -> str:
        """Public id generator for job records (internal seam, shared)."""
        return self._new_id(prefix)

    # --- tenants -------------------------------------------------------------

    def put_tenant(self, tenant: Tenant) -> Tenant:
        self._tenants[tenant.id] = tenant
        return tenant

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        return self._tenants.get(tenant_id)

    def list_tenants(self) -> list[Tenant]:
        return list(self._tenants.values())

    def delete_tenant(self, tenant_id: str) -> bool:
        """Delete a tenant and its dependent records. Returns True if it existed."""
        existed = self._tenants.pop(tenant_id, None) is not None
        self._idp.pop(tenant_id, None)
        self._customizations.pop(tenant_id, None)
        for key in [k for k in self._seeds if k[0] == tenant_id]:
            del self._seeds[key]
        return existed

    # --- idp tenant mappings -------------------------------------------------

    def put_idp_mapping(self, mapping: IdpTenantMapping) -> IdpTenantMapping:
        self._idp[mapping.tenant_id] = mapping
        return mapping

    def get_idp_mapping(self, tenant_id: str) -> IdpTenantMapping | None:
        return self._idp.get(tenant_id)

    def list_idp_mappings(self) -> list[IdpTenantMapping]:
        return list(self._idp.values())

    # --- seed packs ----------------------------------------------------------

    def add_seed(self, item: SeedItem) -> bool:
        """Record a seed; returns False when it is already present (no-op)."""
        key = (item.tenant_id, item.kind, item.ref)
        if key in self._seeds:
            return False
        self._seeds[key] = item
        return True

    def get_seed(self, tenant_id: str, kind: str, ref: str) -> SeedItem | None:
        return self._seeds.get((tenant_id, kind, ref))

    def remove_seed(self, tenant_id: str, kind: str, ref: str) -> bool:
        """Remove one seed record; returns True if it existed (used in tests)."""
        return self._seeds.pop((tenant_id, kind, ref), None) is not None

    def seeds_for_tenant(self, tenant_id: str, kind: str | None = None) -> list[SeedItem]:
        items = [s for s in self._seeds.values() if s.tenant_id == tenant_id]
        if kind is not None:
            items = [s for s in items if s.kind == kind]
        return sorted(items, key=lambda s: (s.kind, s.ref))

    # --- customization overlays ----------------------------------------------

    def put_customization(self, customization: TenantCustomization) -> TenantCustomization:
        self._customizations[customization.tenant_id] = customization
        return customization

    def get_customization(self, tenant_id: str) -> TenantCustomization | None:
        return self._customizations.get(tenant_id)

    # --- provisioning jobs ---------------------------------------------------

    def put_job(self, job: ProvisioningJob) -> ProvisioningJob:
        self._jobs[job.id] = job
        return job

    def get_job(self, job_id: str) -> ProvisioningJob | None:
        return self._jobs.get(job_id)

    def jobs_for_tenant(self, tenant_id: str) -> list[ProvisioningJob]:
        return sorted(
            (j for j in self._jobs.values() if j.tenant_id == tenant_id),
            key=lambda j: j.created_at,
        )

    def all_jobs(self) -> list[ProvisioningJob]:
        return list(self._jobs.values())

    # --- atomicity -----------------------------------------------------------

    def resource_snapshot(self) -> dict[str, Any]:
        """Deep snapshot of the tenant-resource collections (not jobs)."""
        return {attr: copy.deepcopy(getattr(self, attr)) for attr in _RESOURCE_ATTRS}

    def restore_resources(self, snapshot: dict[str, Any]) -> None:
        """Restore tenant-resource collections from a snapshot (all-or-nothing)."""
        for attr in _RESOURCE_ATTRS:
            setattr(self, attr, snapshot[attr])

    # --- serialization (FileStore / operator persistence) --------------------

    def dump(self) -> dict[str, Any]:
        """JSON-serializable, deterministic dump of every collection."""
        return {
            "next_id": self._next_id,
            "tenants": {
                tid: _entity_to_dict(t)
                for tid, t in sorted(self._tenants.items())
            },
            "idp": {
                tid: _entity_to_dict(m)
                for tid, m in sorted(self._idp.items())
            },
            "seeds": sorted(
                (_entity_to_dict(s) for s in self._seeds.values()),
                key=lambda d: (d["tenant_id"], d["kind"], d["ref"]),
            ),
            "customizations": {
                tid: _entity_to_dict(c)
                for tid, c in sorted(self._customizations.items())
            },
            "jobs": {
                jid: _entity_to_dict(j)
                for jid, j in sorted(self._jobs.items())
            },
        }

    def load(self, data: dict[str, Any]) -> None:
        """Replace contents from a ``dump()`` payload."""
        self._next_id = int(data.get("next_id", 0))
        self._tenants = {
            tid: _entity_from_dict("tenant", d)
            for tid, d in data.get("tenants", {}).items()
        }
        self._idp = {
            tid: _entity_from_dict("idp", d)
            for tid, d in data.get("idp", {}).items()
        }
        self._seeds = {
            (d["tenant_id"], d["kind"], d["ref"]): _entity_from_dict("seed", d)
            for d in data.get("seeds", [])
        }
        self._customizations = {
            tid: _entity_from_dict("customization", d)
            for tid, d in data.get("customizations", {}).items()
        }
        self._jobs = {
            jid: _entity_from_dict("job", d)
            for jid, d in data.get("jobs", {}).items()
        }


class FileStore(InMemoryStore):
    """Operator persistence: an InMemoryStore backed by a JSON file.

    The file is the source of onboarding records between operator invocations;
    the RBAC view for a loaded store is rebuilt from those records (see
    ``identity.onboarding.provisioning.materialize_rbac``).
    """

    def __init__(self, path: str | Path):
        super().__init__()
        self.path = Path(path)

    def load_file(self) -> "FileStore":
        if self.path.is_file():
            with self.path.open(encoding="utf-8") as handle:
                self.load(json.load(handle))
        return self

    def save_file(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump(self.dump(), handle, indent=2, sort_keys=True)
            handle.write("\n")


# --- serde helpers -----------------------------------------------------------


def _entity_to_dict(entity: Any) -> dict[str, Any]:
    """dataclasses.asdict with deterministic key order for nested objects."""
    data = asdict(entity)
    return _sort_nested(data)


def _sort_nested(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _sort_nested(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_sort_nested(v) for v in value]
    return value


def _entity_from_dict(kind: str, data: dict[str, Any]) -> Any:
    if kind == "tenant":
        return Tenant(**data)
    if kind == "idp":
        return IdpTenantMapping(**data)
    if kind == "seed":
        return SeedItem(**data)
    if kind == "customization":
        return TenantCustomization(**data)
    if kind == "job":
        audit = [
            AuditEvent(**entry) for entry in data.get("audit_log", [])
        ]
        return ProvisioningJob(**{**data, "audit_log": audit})
    raise ValueError(f"unknown entity kind: {kind}")
