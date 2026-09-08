"""Tenant-scoped in-memory registry store (issue #10).

Persistence seam: agents are keyed by ``(tenant_id, agent_id)`` and every lookup
is strictly tenant-scoped - asking for an agent that exists only in another
tenant returns None / raises, never a cross-tenant hit. There is no delete:
the registry is append-only and an agent leaves service via ``retire`` (a
terminal status), never by removal. A later phase may back this seam with a
database adapter.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, List, Optional

from .errors import UnknownAgentError
from .model import Agent


class RegistryStore:
    """In-memory, tenant-keyed agent store."""

    def __init__(self) -> None:
        # tenant_id -> {agent_id -> Agent}
        self._agents: Dict[str, Dict[str, Agent]] = {}

    def put_agent(self, agent: Agent) -> None:
        """Insert or replace an agent row under its (tenant, id) key."""
        self._agents.setdefault(agent.tenant_id, {})[agent.agent_id] = agent

    def has_agent(self, tenant_id: str, agent_id: str) -> bool:
        """True if the agent exists in exactly this tenant."""
        return agent_id in self._agents.get(tenant_id, {})

    def get_agent(self, tenant_id: str, agent_id: str) -> Optional[Agent]:
        """Return the agent, but only if it exists in this tenant.

        An agent with the same id registered in a different tenant is never
        visible here (no cross-tenant fallback).
        """
        return self._agents.get(tenant_id, {}).get(agent_id)

    def require_agent(self, tenant_id: str, agent_id: str) -> Agent:
        """Like ``get_agent`` but raise ``UnknownAgentError`` when absent."""
        agent = self.get_agent(tenant_id, agent_id)
        if agent is None:
            raise UnknownAgentError(tenant_id, agent_id)
        return agent

    def list_agents(self, tenant_id: str) -> List[Agent]:
        """All agents of one tenant, in deterministic id order."""
        return sorted(
            self._agents.get(tenant_id, {}).values(), key=lambda agent: agent.agent_id
        )

    def tenant_ids(self) -> FrozenSet[str]:
        """Every tenant that has at least one registered agent."""
        return frozenset(self._agents.keys())
