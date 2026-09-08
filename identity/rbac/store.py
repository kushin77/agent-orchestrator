"""In-memory RBAC store.

The persistence seam for the RBAC contract. `resolve.py`, `bindings.py` and
`guard.py` are written against the handful of accessors this module provides,
so a later phase (#35-#38 identity) can back them with a real database adapter
without touching the enforcement logic - the same injected data-access seam the
cannibalized saas-rbac module uses (mirror of its ``RbacDataAccess`` /
``BindingStore`` interfaces, adapted to Python).

The in-memory implementation here is the offline default used by tests and by
single-tenant embedded use. It keeps entities in dicts keyed by id and hands
out small monotonically increasing ids.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rbac.model import (
    DEFAULT_ROLE_ADMIN_PERMISSION,
    ROLE_LEVEL_TEAM,
    Agent,
    Binding,
    Org,
    Role,
    Team,
)


@dataclass
class InMemoryStore:
    """Thread-unsafe in-memory RBAC store (tests / embedded single-tenant use)."""

    _orgs: dict[str, Org] = field(default_factory=dict)
    _teams: dict[str, Team] = field(default_factory=dict)
    _agents: dict[str, Agent] = field(default_factory=dict)
    _roles: dict[str, Role] = field(default_factory=dict)
    _bindings: dict[str, Binding] = field(default_factory=dict)
    _next_id: int = 0

    # --- id generation ------------------------------------------------------

    def _new_id(self, prefix: str) -> str:
        self._next_id += 1
        return f"{prefix}_{self._next_id}"

    # --- org / team / agent -------------------------------------------------

    def add_org(
        self,
        org_id: str,
        name: str,
        tenant_type: str = "platform",
        role_admin_permission: str = DEFAULT_ROLE_ADMIN_PERMISSION,
    ) -> Org:
        if org_id in self._orgs:
            raise ValueError(f"org already exists: {org_id}")
        org = Org(
            id=org_id,
            name=name,
            tenant_type=tenant_type,
            role_admin_permission=role_admin_permission,
        )
        self._orgs[org_id] = org
        return org

    def add_team(self, org_id: str, name: str, *, team_id: str | None = None) -> Team:
        if org_id not in self._orgs:
            raise ValueError(f"unknown org: {org_id}")
        team = Team(id=team_id or self._new_id("team"), org_id=org_id, name=name)
        if team.id in self._teams:
            raise ValueError(f"team already exists: {team.id}")
        self._teams[team.id] = team
        return team

    def add_agent(
        self,
        org_id: str,
        team_id: str,
        name: str,
        *,
        agent_id: str | None = None,
    ) -> Agent:
        if org_id not in self._orgs:
            raise ValueError(f"unknown org: {org_id}")
        team = self._teams.get(team_id)
        if team is None or team.org_id != org_id:
            raise ValueError(f"unknown team {team_id!r} in org {org_id}")
        agent = Agent(
            id=agent_id or self._new_id("agent"),
            org_id=org_id,
            team_id=team_id,
            name=name,
        )
        if agent.id in self._agents:
            raise ValueError(f"agent already exists: {agent.id}")
        self._agents[agent.id] = agent
        return agent

    def org(self, org_id: str) -> Org | None:
        return self._orgs.get(org_id)

    def team(self, team_id: str) -> Team | None:
        return self._teams.get(team_id)

    def agent(self, agent_id: str) -> Agent | None:
        return self._agents.get(agent_id)

    # --- roles ---------------------------------------------------------------

    def create_role(
        self,
        org_id: str,
        key: str,
        name: str,
        description: str = "",
        permissions: tuple[str, ...] = (),
        level: str = "org",
        is_system: bool = False,
        role_id: str | None = None,
    ) -> Role:
        if org_id not in self._orgs:
            raise ValueError(f"unknown org: {org_id}")
        if self.find_role_by_key(org_id, key) is not None:
            raise ValueError(f"role key {key!r} already exists in org {org_id}")
        role = Role(
            id=role_id or self._new_id("role"),
            org_id=org_id,
            key=key,
            name=name,
            description=description,
            permissions=tuple(permissions),
            level=level,
            is_system=is_system,
        )
        self._roles[role.id] = role
        return role

    def find_role_by_key(self, org_id: str, key: str) -> Role | None:
        for role in self._roles.values():
            if role.org_id == org_id and role.key == key:
                return role
        return None

    def role_by_id(self, role_id: str) -> Role | None:
        return self._roles.get(role_id)

    def roles_in_org(self, org_id: str) -> list[Role]:
        return [r for r in self._roles.values() if r.org_id == org_id]

    # --- bindings ------------------------------------------------------------

    def add_binding(
        self,
        org_id: str,
        subject: str,
        subject_type: str,
        role_id: str,
        team_id: str | None = None,
        binding_id: str | None = None,
    ) -> Binding:
        if org_id not in self._orgs:
            raise ValueError(f"unknown org: {org_id}")
        role = self._roles.get(role_id)
        if role is None or role.org_id != org_id:
            raise ValueError(f"unknown role {role_id!r} in org {org_id}")
        if team_id is not None:
            team = self._teams.get(team_id)
            if team is None or team.org_id != org_id:
                raise ValueError(f"unknown team {team_id!r} in org {org_id}")
        # A team-level role (e.g. team-admin / agent-operator) is only ever
        # granted inside a team - level is advisory for org-level roles, which
        # may still be granted to a single team, but never the reverse.
        if role.level == ROLE_LEVEL_TEAM and team_id is None:
            raise ValueError(
                f"role {role.key!r} is team-level and cannot be granted org-wide"
            )
        binding = Binding(
            id=binding_id or self._new_id("binding"),
            org_id=org_id,
            subject=subject,
            subject_type=subject_type,
            role_id=role_id,
            team_id=team_id,
        )
        self._bindings[binding.id] = binding
        return binding

    def delete_binding(self, binding_id: str) -> bool:
        return self._bindings.pop(binding_id, None) is not None

    def bindings_for_subject(self, org_id: str, subject: str) -> list[Binding]:
        return [
            b
            for b in self._bindings.values()
            if b.org_id == org_id and b.subject == subject
        ]

    def bindings_in_org(self, org_id: str) -> list[Binding]:
        return [b for b in self._bindings.values() if b.org_id == org_id]
