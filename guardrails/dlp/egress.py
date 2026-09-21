"""guardrails.dlp.egress — the egress guard (default-deny allowlist proxy).

Only allowlisted providers/endpoints are reachable from a tenant context.
Every outbound commercial-model call must pass :meth:`EgressGuard.allow`
before the (scrubbed, injection-checked) payload may be dispatched; anything
else is denied and audited.

Model:

* the guard is configured with a **per-tenant allowlist** — a mapping of
  ``tenant_id`` to a sequence of :class:`AllowedTarget` entries;
* a target names a ``provider``, an allowed ``host`` and an optional
  ``path_prefix``;
* a ``host`` of ``"api.openai.com"`` matches only that exact host; a host of
  ``".openai.com"`` (leading dot) matches ``api.openai.com`` and any other
  subdomain under ``openai.com`` — never a suffix-squatting name such as
  ``api.openai.com.evil.example``;
* **default deny**: an empty or missing allowlist denies everything, an
  unlisted tenant is denied, and an unlisted provider/host/path is denied.
  There is no implicit default provider set — allowlisting is explicit.


---knowledge---
module_id: guardrails.dlp.egress
system: guardrails
app: dlp
solution_class: enterprise
patterns: [allowlist-not-denylist, fail-closed, tenant-scoped]
derives_from: null
owner_sme: security-sme
tier: L1
interfaces: [AllowedTarget, EgressDecision, EgressGuard]
invariants: "only allowlisted providers and endpoints are reachable from a tenant context; anything else is denied and audited"
gotchas: "a host of api.openai.com matches only that exact host, not a suffix match"
related: ["#27"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Sequence
from urllib.parse import urlsplit


@dataclass(frozen=True)
class AllowedTarget:
    """One allowlisted egress target for a tenant."""

    provider: str
    host: str  # exact host, or ".domain" to allow subdomains
    path_prefix: str = ""


@dataclass(frozen=True)
class EgressDecision:
    """Outcome of an egress guard check."""

    verdict: str  # "allow" | "deny"
    reason: str

    @property
    def allowed(self) -> bool:
        return self.verdict == "allow"


def _host_matches(host: str, allowed_host: str) -> bool:
    """Exact match, or subdomain match when ``allowed_host`` starts with '.'."""
    host = host.lower()
    allowed_host = allowed_host.lower()
    if allowed_host.startswith("."):
        suffix = allowed_host[1:]
        if not suffix:
            return False
        return host.endswith("." + suffix) or host == suffix
    return host == allowed_host


def _split_endpoint(endpoint: str) -> tuple:
    """Return (host, path) for an endpoint URL; raises ValueError if invalid."""
    parts = urlsplit(endpoint)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise ValueError(f"invalid endpoint URL: {endpoint!r}")
    return parts.hostname.lower(), parts.path or "/"


class EgressGuard:
    """Default-deny egress allowlist keyed by tenant id."""

    def __init__(
        self,
        allowlist: Optional[Dict[str, Sequence[AllowedTarget]]] = None,
    ) -> None:
        # Normalize: tenant -> tuple of AllowedTarget (dedup, stable order).
        self._allowlist: Dict[str, tuple] = {}
        for tenant, targets in (allowlist or {}).items():
            ordered = tuple(dict.fromkeys(targets))
            self._allowlist[tenant] = ordered

    def targets_for(self, tenant_id: str) -> tuple:
        return self._allowlist.get(tenant_id, ())

    def allow(self, *, tenant_id: str, provider: str, endpoint: str) -> EgressDecision:
        """Decide whether ``endpoint`` is reachable for the provider/tenant."""
        targets = self._allowlist.get(tenant_id)
        if not targets:
            return EgressDecision("deny", f"tenant {tenant_id!r} has no egress allowlist")

        try:
            host, path = _split_endpoint(endpoint)
        except ValueError as exc:
            return EgressDecision("deny", str(exc))

        provider_targets = [t for t in targets if t.provider == provider]
        if not provider_targets:
            allowed_providers = sorted({t.provider for t in targets})
            return EgressDecision(
                "deny",
                f"provider {provider!r} not allowlisted for tenant "
                f"{tenant_id!r} (allowed: {', '.join(allowed_providers) or 'none'})",
            )

        for target in provider_targets:
            if not _host_matches(host, target.host):
                continue
            if target.path_prefix and not path.startswith(target.path_prefix):
                continue
            return EgressDecision("allow", "endpoint is allowlisted")

        return EgressDecision(
            "deny",
            f"endpoint {endpoint!r} not allowlisted for provider {provider!r} "
            f"under tenant {tenant_id!r}",
        )
