"""DELIBERATELY SHARED module-global cache (planted R4).

NEGATIVE-test material: tenant-scoped service methods write records into a
process-global, unscoped dict.  Every tenant in the process shares the same
mutable state keyed by inner id only — the exact "shared mutable state across
tenant namespaces" anti-pattern.  The scanner must report R4 here.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# Module-level unscoped cache: shared by every tenant in the process.
_AGENT_CACHE: Dict[str, Any] = {}


class AgentService:
    """Tenant-aware service that abuses a process-global cache."""

    def cache_agent(self, tenant_id: str, agent: Dict[str, Any]) -> None:
        """PLANTED R4: tenant data written into a global keyed by inner id."""
        _AGENT_CACHE[agent["agent_id"]] = agent

    def read_cached(self, tenant_id: str, agent_id: str) -> Optional[Any]:
        """Reads the same shared global, ignoring tenant_id entirely."""
        return _AGENT_CACHE.get(agent_id)
