"""Host -> tenant resolution and custom-domain handling (issue #35, AC #1).

Ported from saas-rbac ``tenant-resolution.ts``. Every tenant reaches the
platform on its own hostname - a platform-issued subdomain or a custom domain
it onboarded and verified. The ``Host`` header is the primary tenant selector
and resolution **fails closed**: a host that maps to no tenant is rejected;
there is no "default tenant", no fallback to a tenant named in a token, and no
inference from anything else on the request.

Deliberately ignored: ``X-Forwarded-Host``. It is attacker-controlled on any
request that reaches us, and honouring it would let a caller pick whichever
tenant it liked.
"""

from __future__ import annotations

from typing import Optional

from .errors import UnknownTenantError
from .store import InMemoryStore


def normalize_hostname(host: Optional[str]) -> Optional[str]:
    """Normalize a ``Host`` header into a bare lowercase hostname.

    Strips the port and handles bracketed IPv6 literals; returns None for a
    missing or unusable header (mirrors saas-rbac ``hostnameFromHostHeader``).
    """
    if not host:
        return None
    trimmed = host.strip().lower()
    if not trimmed:
        return None
    if trimmed.startswith("["):  # IPv6 literal "[::1]" / "[::1]:8080"
        end = trimmed.index("]") if "]" in trimmed else -1
        return None if end == -1 else trimmed[: end + 1]
    hostname = trimmed.split(":", 1)[0]
    return hostname if hostname else None


def resolve_tenant_for_host(
    store: InMemoryStore, host_header: str
) -> Optional[str]:
    """Resolve the tenant id for a ``Host`` header (custom-domain aware).

    Returns None when the header is missing or the host belongs to no tenant.
    Callers must treat None as a rejection - never as "no tenant needed".
    """
    host = normalize_hostname(host_header)
    if host is None:
        return None
    return store.route_owner(host)


def require_tenant_for_host(store: InMemoryStore, host_header: str) -> str:
    """Fail-closed host resolution: raises UnknownTenantError when no tenant
    owns the host (the saas-rbac 404 path - no credential would help)."""
    tenant_id = resolve_tenant_for_host(store, host_header)
    if tenant_id is None:
        raise UnknownTenantError(
            f"no tenant mapping for host {host_header!r}"
        )
    return tenant_id


def email_domain(email: str) -> str:
    """Lowercased domain of an email address ('' when unparseable)."""
    if "@" not in email:
        return ""
    return email.rsplit("@", 1)[1].strip().lower()
