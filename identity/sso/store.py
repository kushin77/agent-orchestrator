"""SSO persistence seam - mirrors the identity/onboarding + rbac store
philosophy: an in-memory store is the offline default used by tests and
embedded use; a file-backed store serializes the same collections so an
operator path can persist a tenant's SSO config and route table. A later
identity phase swaps in a database adapter behind the same accessors.

Collections (all tenant-scoped, fail closed):

- per-tenant ``TenantSsoConfig`` (config.py validates + registers),
- host->tenant domain route table (primary domain + aliases),
- the one-to-one IdP-tenant -> platform-tenant index,
- revoked session ``jti`` set (logout / impersonation revoke),
- ``ImpersonationGrant`` records,
- the append-only SSO audit log (with impersonation operator stamps).
"""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .model import (
    ImpersonationGrant,
    SsoAuditEvent,
    TenantSsoConfig,
)


@dataclass
class InMemoryStore:
    """Thread-unsafe in-memory SSO store (tests / embedded use)."""

    _configs: dict[str, TenantSsoConfig] = field(default_factory=dict)
    _domain_owner: dict[str, str] = field(default_factory=dict)
    _tenant_domains: dict[str, tuple[str, tuple[str, ...]]] = field(
        default_factory=dict
    )
    _idp_owner: dict[str, str] = field(default_factory=dict)
    _revoked: dict[str, int] = field(default_factory=dict)
    _grants: dict[str, ImpersonationGrant] = field(default_factory=dict)
    _audit: list[SsoAuditEvent] = field(default_factory=list)

    # --- tenant SSO config --------------------------------------------------

    def put_config(self, config: TenantSsoConfig) -> TenantSsoConfig:
        self._configs[config.tenant_id] = config
        if config.idp_tenant_id:
            self._idp_owner[config.idp_tenant_id] = config.tenant_id
        return config

    def config_for(self, tenant_id: str) -> TenantSsoConfig | None:
        return self._configs.get(tenant_id)

    def configs(self) -> tuple[TenantSsoConfig, ...]:
        return tuple(self._configs.values())

    def idp_owner(self, idp_tenant_id: str) -> str | None:
        """Which platform tenant an IdP tenant is bound to (one-to-one)."""
        return self._idp_owner.get(idp_tenant_id)

    # --- domain routes -------------------------------------------------------

    def put_route(self, tenant_id: str, primary: str, aliases: tuple[str, ...]) -> None:
        primary_n = primary.lower()
        alias_n = tuple(a.lower() for a in aliases)
        # Drop stale host keys so a renamed/removed domain stops resolving.
        previous = self._tenant_domains.get(tenant_id)
        if previous is not None:
            old_primary, old_aliases = previous
            for host in (old_primary, *old_aliases):
                self._domain_owner.pop(host, None)
        self._tenant_domains[tenant_id] = (primary_n, alias_n)
        self._domain_owner[primary_n] = tenant_id
        for host in alias_n:
            self._domain_owner[host] = tenant_id

    def route_owner(self, host: str) -> str | None:
        return self._domain_owner.get(host.lower())

    def tenant_domains(self, tenant_id: str) -> tuple[str, tuple[str, ...]] | None:
        """The (primary, aliases) currently claimed by a tenant."""
        return self._tenant_domains.get(tenant_id)

    # --- revocation (logout / impersonation revoke) --------------------------

    def revoke_jti(self, jti: str, revoked_at: int) -> None:
        self._revoked[jti] = revoked_at

    def is_revoked(self, jti: str) -> bool:
        return jti in self._revoked

    # --- impersonation grants ------------------------------------------------

    def put_grant(self, grant: ImpersonationGrant) -> ImpersonationGrant:
        self._grants[grant.grant_id] = grant
        return grant

    def get_grant(self, grant_id: str) -> ImpersonationGrant | None:
        return self._grants.get(grant_id)

    # --- audit ----------------------------------------------------------------

    def add_audit(self, event: SsoAuditEvent) -> SsoAuditEvent:
        self._audit.append(event)
        return event

    def audit_log(self) -> list[SsoAuditEvent]:
        return list(self._audit)

    def resource_snapshot(self) -> dict[str, Any]:
        """Deep-copied resource state (config/routes/grants/revoked), for the
        all-or-nothing registration seam."""
        return {
            "_configs": dict(self._configs),
            "_domain_owner": dict(self._domain_owner),
            "_tenant_domains": dict(self._tenant_domains),
            "_idp_owner": dict(self._idp_owner),
            "_revoked": dict(self._revoked),
            "_grants": dict(self._grants),
        }

    def restore_resources(self, snapshot: dict[str, Any]) -> None:
        for key, value in snapshot.items():
            setattr(self, key, value)


class FileStore(InMemoryStore):
    """JSON-file persistence for the operator path (config + route + grants).

    Serializes the durable collections deterministically so a registration in
    one process can be loaded by a later process. Session ``jti`` revocation
    and audit events persist too (append/revoke history survives restarts).
    """

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self._path = Path(path)
        if self._path.exists():
            self._load(self._path)

    # --- persistence ----------------------------------------------------------

    def _load(self, path: Path) -> None:
        raw = json.loads(path.read_text(encoding="utf-8"))
        for item in raw.get("configs", []):
            config = TenantSsoConfig(**item)
            self._configs[config.tenant_id] = config
            if config.idp_tenant_id:
                self._idp_owner[config.idp_tenant_id] = config.tenant_id
        for tenant, (primary, aliases) in raw.get("routes", {}).items():
            self.put_route(tenant, primary, tuple(aliases))
        self._revoked = {jti: at for jti, at in raw.get("revoked", {}).items()}
        for item in raw.get("grants", []):
            self._grants[item["grant_id"]] = ImpersonationGrant(**item)

    def save(self) -> None:
        payload = {
            "configs": [
                asdict(c)
                for c in sorted(
                    self._configs.values(), key=lambda c: (c.tenant_id, c.protocol)
                )
            ],
            "routes": {
                tenant: [primary, list(aliases)]
                for tenant, (primary, aliases) in self._tenant_domains.items()
            },
            "revoked": dict(self._revoked),
            "grants": [asdict(g) for g in self._grants.values()],
        }
        self._path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def clone_store(store: InMemoryStore) -> InMemoryStore:
    """Deep copy a store (test helper: snapshot/compare states)."""
    return copy.deepcopy(store)
