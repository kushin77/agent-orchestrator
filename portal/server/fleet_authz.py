"""portal.server.fleet_authz — access control + tenant scoping for the streamed
fleet dashboard (issue #333).


WHY this exists: ``portal/server/fleet.py`` (issue #331) exposes the fleet's
live projection over HTTP. The terminal dashboard it mirrors needs no access
control because it is a local tmux pane; a **browser** surface changes the
threat model completely — one tenant must never see another tenant's org,
lanes or claims, and the cross-org view must be an explicit administrative
capability rather than the default. This module is the middleware that scopes
``/api/fleet/*`` to the caller.

Consume, do not duplicate (the issue's cannibalize rule):

* **``identity/rbac``** — the ``resource:action`` permission language, the
  platform preset pack (roles ``owner``/``admin``/``team-admin``/
  ``agent-operator``/``member``/``viewer``), and ``guard``'s two gates (scope
  then permission). No second policy language is invented here; the SPoG only
  declares *which action* its own rows need (the issue's "SPoG-specific policy
  bindings").
* **``identity/entitlements``** — the subscription/plan/entitlement gates
  (issue #36) applied to the platform roll-up: a plan is never implied and an
  unknown plan never silently grants.
* **``identity/cpapi``** — the denial vocabulary (``scope_denied`` /
  ``permission_denied`` / ``unauthorized``), so every refusal on this surface
  carries the control plane's own machine codes.

Row ownership (what a principal may see)
----------------------------------------

The projection is a set of *rows* (rungs, orders, dispatches, claims, waves,
closed issues, events, watchdog lines) plus the response's own identity
(``repo``, ``head``, ``now``). Each row is attributed to the ``(org, team)``
that owns it, by a declared ownership index (``OrgIndex``) seeded from:

* the console's **org directory** — ``state.agents[org]`` names each org's
  agents and their team (so a row naming an agent belongs to that agent's org);
* the **persona cards** — ``registry/personas/cards/*.yaml`` declares each
  persona's ``tenant`` and the ``ownedLanes`` it may consume (so a row naming a
  lane belongs to the org the lane's persona declares).

A row that names none of these is **platform-owned**: it belongs to the
platform runtime itself and is therefore visible only to a principal in scope
for the platform org. Attribution is never guessed — an unknown org, team,
agent or lane leaves the row platform-owned rather than leaking it into a
tenant that merely resembles it.

A principal may read a row only when ``rbac.guard`` allows here — that is, when
the caller holds ``fleet:read`` **and** is in scope at the row's own node. A
scoped principal (a tenant owner, an org admin, a team operator) therefore
receives exactly its own org's rows and nothing else; the platform runtime's
rows are visible to the platform org. Fail-closed by construction: an
unattributable row, a malformed permission or an unknown scope is a denial, not
a fallback.

``/api/fleet/rollup`` is the cross-org administrative view. It runs the full
``identity/entitlements`` chain — subscription, then scope, then permission,
then entitlement — at the platform org, so it is refused not only for a
principal that lacks ``fleet:rollup`` but also for one that holds the
permission without platform scope (the two-gate doctrine: a permission is never
usable outside a resolved scope), and for a platform org whose assigned plan
does not entitle the roll-up surface.

Measured finding recorded by this lane (not fixed here)
------------------------------------------------------

``identity/entitlements/plans/catalog.yaml`` defines the plans ``free``,
``pro`` and ``enterprise``, while ``telemetry/budgets/config/quotas.yaml``
assigns the plan keys ``free``, ``standard``, ``premium`` and ``enterprise``.
The two vocabularies disagree, so applying the entitlement gate to *customer*
orgs would deny a working tenant for a reason that is not about this surface at
all (``unknown_plan`` for a ``standard`` tenant, ``no_plan`` for a tenant the
quota config does not mention). Customer row reads are therefore gated by RBAC
(scope + permission), and the entitlement chain is applied at the platform
boundary, where the plan key is this module's own declaration. Reconciling the
two vocabularies belongs to the entitlements lane, not to issue #333.

Token discipline
----------------

Nothing on this path renders or logs a credential: denials carry the control
plane's machine codes and never echo the presented cookie, and the transport
(``portal/server/httpd.py``) keeps its request log silent. The suite proves
both with a provoked negative control rather than by inspection
(``portal/tests/test_fleet_access_control.py``).


---knowledge---
module_id: portal.server.fleet_authz
system: portal
app: server
solution_class: enterprise
patterns: [row-ownership, scope-then-permission, no-cross-tenant-default]
derives_from: null
owner_sme: security-sme
tier: L1
interfaces: [FleetAuthorizer, OrgIndex, FleetRow, RowOwner, FleetDenied]
invariants: "one tenant never sees another's rows; the cross-org roll-up is an explicit administrative capability, never the default"
gotchas: "a row naming no org/team/agent/lane is platform-owned and visible only to an in-scope principal"
related: ["#333", "#331"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
# ``identity/`` is a PEP-420 namespace whose modules import their lane siblings
# by bare name (``rbac.model``, ``entitlements.engine``); putting it on the path
# is the same bootstrap ``identity/rbac/tests/conftest.py`` and
# ``portal/server/sso.py`` (repo root, for ``identity.sso``) already perform.
IDENTITY_ROOT = REPO_ROOT / "identity"
if str(IDENTITY_ROOT) not in sys.path:
    sys.path.insert(0, str(IDENTITY_ROOT))

from cpapi import errors as cpapi_errors  # noqa: E402
from entitlements.catalog import load_default_catalog  # noqa: E402
from entitlements.engine import assign_plan, evaluate_permission  # noqa: E402
# The denial-reason vocabulary is shared by both gates: ``rbac.guard`` returns
# "scope"/"permission" and ``entitlements`` extends it with the two plan gates,
# so one set of constants describes every refusal on this path.
from entitlements.model import (  # noqa: E402
    REASON_ENTITLEMENT,
    REASON_PERMISSION,
    REASON_SCOPE,
    REASON_SUBSCRIPTION,
)
from entitlements.store import InMemoryStore as EntitlementStore  # noqa: E402
from rbac.guard import guard  # noqa: E402
from rbac.model import SUBJECT_USER, ScopeNode  # noqa: E402
from rbac.presets import resolve_pack  # noqa: E402
from rbac.store import InMemoryStore  # noqa: E402

# --- the SPoG's policy declarations (the bindings this issue builds) ---------

#: The SPoG's resource in the platform ``resource:action`` vocabulary. The pack
#: owns the roles; the SPoG owns which actions of its own resource they carry.
#: Both action strings are derived from it, so the resource name cannot drift
#: away from the permissions and the entitlement catalog entry that grants them.
FLEET_RESOURCE = "fleet"
#: Read the caller's own org's projection rows.
READ_PERMISSION = f"{FLEET_RESOURCE}:read"
#: Read the cross-org administrative roll-up (platform scope only).
ROLLUP_PERMISSION = f"{FLEET_RESOURCE}:rollup"

#: The org owning the platform runtime's own rows (rungs, steering queue,
#: watchdog, closed-issue ledger, and every row no tenant reference attributes).
PLATFORM_ORG = "platform"
PLATFORM_ORG_NAME = "Platform"
#: The preset pack every org of this console is seeded from (identity/rbac).
PLATFORM_TENANT_TYPE = "platform"
# The console's tenant id *is* the rbac org id, so no translation table exists.

#: Which projection-row actions each platform-preset role carries. This is the
#: SPoG's binding layer on top of the frozen pack — adding an action here grants
#: it to that role in every org, and removing it revokes it everywhere.
SPOG_ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "owner": (READ_PERMISSION, ROLLUP_PERMISSION),
    "admin": (READ_PERMISSION,),
    "team-admin": (READ_PERMISSION,),
    "agent-operator": (READ_PERMISSION,),
    "member": (READ_PERMISSION,),
    "viewer": (READ_PERMISSION,),
}

#: The entitlement feature (identity/entitlements/plans/catalog.yaml) that
#: unlocks the roll-up surface, and the plan assigned to the platform org at
#: boot through the audited ``assign_plan`` API (never a silent default).
ROLLUP_FEATURE = "fleet_dashboard"
PLATFORM_PLAN = "enterprise"

#: How much of the fleet's event tail one scoped read inspects. The caller's
#: own last-N window is taken from within this bound, so the surface stays a
#: bounded read (``fleet/console.events_snapshot`` is itself a bounded tail
#: read) rather than scanning the whole multi-megabyte slog.
EVENTS_SCAN_LIMIT = 500

# --- row ownership ----------------------------------------------------------

#: The response's own identity: the build, commit and moment of *this* response.
#: Present in every scoped view — it describes the surface, never a tenant.
OWNER_SURFACE = "surface"
#: Rows of the platform runtime itself.
OWNER_PLATFORM = PLATFORM_ORG
#: Rows attributed by their own references through the ownership index.
OWNER_ROW = "row"

#: Reference fields, most specific first. A row may carry them nested (a
#: steering message keeps its lane under ``task``), so the scan is recursive.
ORG_REFERENCE_KEYS = ("orgId", "org", "org_id", "tenantId", "tenant", "tenant_id")
TEAM_REFERENCE_KEYS = ("teamId", "team_id", "team")
AGENT_REFERENCE_KEYS = ("agentId", "agent_id", "agent")
LANE_REFERENCE_KEYS = ("lane",)

#: A rendered line ("… lane knowledge-crossref …") is not structured, so the
#: lane it names is read with a bounded pattern. Nothing else in a log line is
#: treated as an ownership reference.
_LINE_LANE_RE = re.compile(r"(?:^|[\s(\[,;])lane[=:\s]+([A-Za-z0-9][A-Za-z0-9._/-]*)")

#: Recursion bounds for the reference scan: deep enough for the message schema
#: (``task.lane``), narrow enough that a hostile payload cannot make the walk
#: quadratic.
_MAX_SCAN_DEPTH = 4
_MAX_SCAN_NODES = 256


def team_node_id(org_id: str, team: str) -> str:
    """The globally unique id of a team node.

    A team *name* (``platform``, ``research``) repeats across orgs, while the
    rbac store keys teams by id and every row owner is a scope node — so the
    name is namespaced once, here, and both the ownership index and the store
    use the result. A row that names a bare team name is therefore
    unattributed rather than attributed to a same-named team in another org.
    """
    return f"{org_id}/{team}"


@dataclass(frozen=True)
class RowOwner:
    """The ``(org, team)`` a projection row belongs to.

    ``team`` is None for an org-level row (the whole tenant) and set for a row
    the ownership index attributed to one team — a team-scoped binding reaches
    the second but never the first, exactly as ``rbac.resolve`` defines it.
    """

    org_id: str
    team_id: Optional[str] = None

    @property
    def node(self) -> ScopeNode:
        return ScopeNode(org_id=self.org_id, team_id=self.team_id)

    def as_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"orgId": self.org_id}
        if self.team_id is not None:
            payload["teamId"] = self.team_id
        return payload


@dataclass(frozen=True)
class FleetRow:
    """One attributed projection row: its section, key, value and owner."""

    section: str
    key: str
    value: Any
    owner: RowOwner


@dataclass(frozen=True)
class RowSpec:
    """How one projection section is flattened into ownable rows."""

    name: str
    kind: str  # "scalar" | "mapping" | "sequence"
    owner: str  # OWNER_SURFACE | OWNER_PLATFORM | OWNER_ROW


#: The projection's documented contract, section by section. The order matches
#: ``fleet/console.py snapshot()`` so a scoped view keeps the dashboard's shape.
ROW_SPECS: tuple[RowSpec, ...] = (
    RowSpec("repo", "scalar", OWNER_SURFACE),
    RowSpec("head", "scalar", OWNER_SURFACE),
    RowSpec("now", "scalar", OWNER_SURFACE),
    RowSpec("uptime", "scalar", OWNER_PLATFORM),
    RowSpec("rungs", "mapping", OWNER_PLATFORM),
    RowSpec("orders", "mapping", OWNER_PLATFORM),
    RowSpec("dispatches", "sequence", OWNER_ROW),
    RowSpec("claims", "sequence", OWNER_ROW),
    RowSpec("waves", "sequence", OWNER_ROW),
    RowSpec("closed", "sequence", OWNER_PLATFORM),
    RowSpec("events", "sequence", OWNER_ROW),
    RowSpec("watchdog", "sequence", OWNER_PLATFORM),
)

#: The sections a scoped response always carries, so a tenant's view has the
#: dashboard's exact shape — empty where every row was filtered out — rather
#: than a shape that varies with the caller.
SNAPSHOT_SECTIONS: tuple[str, ...] = tuple(spec.name for spec in ROW_SPECS)


def _walk_references(value: Any) -> list[tuple[str, Any]]:
    """Every ``(key, value)`` pair in a row, depth- and size-bounded."""
    pairs: list[tuple[str, Any]] = []
    stack: list[tuple[int, Any]] = [(0, value)]
    while stack and len(pairs) < _MAX_SCAN_NODES:
        depth, node = stack.pop()
        if depth >= _MAX_SCAN_DEPTH:
            continue
        if isinstance(node, Mapping):
            for key, item in node.items():
                if not isinstance(key, str):
                    continue
                pairs.append((key, item))
                stack.append((depth + 1, item))
        elif isinstance(node, (list, tuple)):
            for item in node:
                stack.append((depth + 1, item))
    return pairs


class OrgIndex:
    """Which org (and team) owns an agent, a team or a lane.

    The declaration seam for the SPoG's row ownership. It is seeded from the
    live sources that already declare ownership — the console org directory's
    agent roster and the registry's persona cards — and ``declare`` is the
    explicit binding a deployment adds when a lane is not (yet) described by a
    persona card. Nothing is inferred from a name's resemblance to an org id.
    """

    def __init__(self, *, known_orgs: Iterable[str] = ()) -> None:
        self._orgs: set[str] = set(known_orgs)
        self._teams: dict[str, RowOwner] = {}
        self._agents: dict[str, RowOwner] = {}
        self._lanes: dict[str, RowOwner] = {}

    @property
    def orgs(self) -> tuple[str, ...]:
        return tuple(sorted(self._orgs))

    def add_org(self, org_id: str) -> None:
        if org_id:
            self._orgs.add(org_id)

    def declare(
        self,
        org_id: str,
        *,
        team: Optional[str] = None,
        teams: Iterable[str] = (),
        agents: Iterable[str] = (),
        lanes: Iterable[str] = (),
    ) -> None:
        """Bind agents/teams/lanes of ``org_id`` to that org.

        ``team`` names the team the ``agents`` belong to; a call with ``agents``
        but no ``team`` binds them org-wide (they reach the org node, never a
        team node). Re-declaring an entity to the *same* owner is idempotent;
        re-declaring it to a different owner is refused, so a later declaration
        can never silently steal another tenant's rows.
        """
        self.add_org(org_id)
        owner = RowOwner(org_id, team_node_id(org_id, team) if team else None)
        for name in teams:
            node_id = team_node_id(org_id, name)
            self._bind(self._teams, node_id, RowOwner(org_id, node_id), "team")
        for agent_id in agents:
            self._bind(self._agents, agent_id, owner, "agent")
        for lane in lanes:
            self._bind(self._lanes, lane, RowOwner(org_id, owner.team_id), "lane")

    def _bind(
        self,
        table: dict[str, RowOwner],
        name: str,
        owner: RowOwner,
        kind: str,
    ) -> None:
        existing = table.get(name)
        if existing is not None:
            if existing != owner:
                raise ValueError(
                    f"{kind} {name!r} is already owned by {existing.org_id!r}; "
                    f"refusing to reassign it to {owner.org_id!r}"
                )
            return
        table[name] = owner

    # -- resolution (never raises; an unknown name is simply not owned) -------
    def owner_of_agent(self, agent_id: str) -> Optional[RowOwner]:
        return self._agents.get(agent_id)

    def owner_of_team(self, team_id: str) -> Optional[RowOwner]:
        return self._teams.get(team_id)

    def owner_of_lane(self, lane: str) -> Optional[RowOwner]:
        return self._lanes.get(lane) or self._lanes.get(lane.lower())

    def attribute(self, value: Any) -> Optional[RowOwner]:
        """The owner a projection row declares, or None when it declares none.

        Priority follows specificity: an explicit org reference outranks a team,
        a team outranks an agent, and an agent outranks a lane — a row naming
        both its lane and its agent belongs to the agent's team, not to whatever
        org happens to own the lane.
        """
        pairs = _walk_references(value)
        for keys, resolve in (
            (ORG_REFERENCE_KEYS, self._known_org),
            (TEAM_REFERENCE_KEYS, self.owner_of_team),
            (AGENT_REFERENCE_KEYS, self.owner_of_agent),
            (LANE_REFERENCE_KEYS, self.owner_of_lane),
        ):
            for key, item in pairs:
                if key in keys and isinstance(item, str) and item:
                    owner = resolve(item)
                    if owner is not None:
                        return owner
        if isinstance(value, str):
            match = _LINE_LANE_RE.search(value)
            if match is not None:
                return self.owner_of_lane(match.group(1))
        return None

    def _known_org(self, org_id: str) -> Optional[RowOwner]:
        return RowOwner(org_id) if org_id in self._orgs else None

    # -- seeding from live sources ------------------------------------------
    @classmethod
    def from_state(cls, state: Any, *, known_orgs: Iterable[str] = ()) -> "OrgIndex":
        """Seed from the console's org directory (agents + their teams)."""
        index = cls(known_orgs=known_orgs)
        for org_id, agents in getattr(state, "agents", {}).items():
            for agent in agents:
                index.declare(org_id, team=agent.team, agents=[agent.id])
        return index

    def seed_persona_cards(self, cards_dir: Path | str) -> None:
        """Bind each persona card's ``ownedLanes`` to its declared ``tenant``.

        A card whose declared tenant is not an org of this console is skipped:
        the lane stays unattributed (platform-owned) rather than being guessed
        into an org that merely resembles it.
        """
        import yaml

        directory = Path(cards_dir)
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*.yaml")):
            try:
                document = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(document, Mapping):
                continue
            tenant = str(document.get("tenant") or "")
            if tenant not in self._orgs:
                continue
            lanes = [str(lane) for lane in (document.get("ownedLanes") or []) if lane]
            if lanes:
                self.declare(tenant, lanes=lanes)


class FleetAuthorizer:
    """The middleware: scopes the fleet projection to one authenticated caller.

    Constructed once per console and consulted by every ``/api/fleet/*`` read
    (``portal/server/app.py``). It owns no transport concern — it returns plain
    data and raises :class:`FleetDenied`, which the app maps onto its own error
    envelope.
    """

    def __init__(
        self,
        *,
        state: Any,
        repo_root: Path | str = REPO_ROOT,
        index: Optional[OrgIndex] = None,
        index_known_orgs: Optional[Iterable[str]] = None,
        persona_cards_dir: Optional[Path | str] = None,
        platform_plan: str = PLATFORM_PLAN,
    ) -> None:
        self.state = state
        self.repo_root = Path(repo_root)
        self.platform_plan = platform_plan
        self.index = (
            index
            if index is not None
            else OrgIndex.from_state(state, known_orgs=self._org_ids())
        )
        for org_id in self._org_ids():
            self.index.add_org(org_id)
        cards_dir = (
            Path(persona_cards_dir)
            if persona_cards_dir is not None
            else self.repo_root / "registry" / "personas" / "cards"
        )
        self.index.seed_persona_cards(cards_dir)
        self.store = self._build_store()
        self.catalog = load_default_catalog()
        self.entitlements = self._build_entitlements()

    # -- the rbac store (platform pack + the SPoG's action layer) -----------
    def _org_ids(self) -> list[str]:
        """The platform org plus every console tenant, in a stable order."""
        tenants = []
        if hasattr(self.state, "tenant_ids"):
            tenants = list(self.state.tenant_ids())
        elif hasattr(self.state, "tenants"):
            tenants = sorted(getattr(self.state, "tenants"))
        return [PLATFORM_ORG, *tenants]

    def _build_store(self) -> InMemoryStore:
        """Seed orgs, teams, roles and bindings from the console org directory.

        Roles come from the platform preset pack (``resolve_pack``), each
        extended by :data:`SPOG_ROLE_PERMISSIONS` — the pack stays the single
        vocabulary, and the SPoG's own row actions are a declared layer on it.
        """
        store = InMemoryStore()
        pack = resolve_pack(PLATFORM_TENANT_TYPE)
        for org_id in self._org_ids():
            name = (
                PLATFORM_ORG_NAME
                if org_id == PLATFORM_ORG
                else self._tenant_name(org_id)
            )
            org = store.add_org(org_id, name, tenant_type=PLATFORM_TENANT_TYPE)
            for preset in pack.roles:
                extra = SPOG_ROLE_PERMISSIONS.get(preset.key, ())
                store.create_role(
                    org.id,
                    preset.key,
                    preset.name,
                    preset.description,
                    permissions=tuple(preset.permissions) + extra,
                    level=preset.level,
                    is_system=preset.is_system,
                )
        self._seed_teams_and_bindings(store)
        return store

    def _tenant_name(self, org_id: str) -> str:
        tenant = getattr(self.state, "tenants", {}).get(org_id)
        return str(getattr(tenant, "name", "") or org_id)

    def _team_id(self, org_id: str, team: str) -> str:
        return team_node_id(org_id, team)

    def _seed_teams_and_bindings(self, store: InMemoryStore) -> None:
        for org_id, agents in getattr(self.state, "agents", {}).items():
            if store.org(org_id) is None:
                continue
            for agent in agents:
                team_id = self._team_id(org_id, agent.team)
                if store.team(team_id) is None:
                    store.add_team(org_id, agent.team, team_id=team_id)

        subjects: dict[str, list[tuple[str, str, Optional[str]]]] = {}
        for binding in getattr(self.state, "bindings", ()) or ():
            subjects.setdefault(binding.email, []).append(
                (binding.tenant_id, binding.role, None)
            )
        for org_id in self._org_ids():
            for email, bindings in subjects.items():
                for tenant_id, role, _ in bindings:
                    if tenant_id != org_id:
                        continue
                    role_id = self._role_id(store, org_id, role)
                    if role_id is None:
                        continue
                    store.add_binding(
                        org_id,
                        email,
                        SUBJECT_USER,
                        role_id,
                        team_id=self._governing_team(store, org_id, role),
                    )

    def _role_id(self, store: InMemoryStore, org_id: str, key: str) -> Optional[str]:
        role = store.find_role_by_key(org_id, key)
        return None if role is None else role.id

    def _governing_team(
        self, store: InMemoryStore, org_id: str, role_key: str
    ) -> Optional[str]:
        """The team a team-level role is bound inside.

        ``team-admin`` and ``agent-operator`` are team-level in the platform
        pack, and the rbac store refuses to grant a team-level role org-wide
        (an unadministrable org is the failure that guard prevents). The console
        directory binds them to the org's first declared team — a team-scoped
        principal reaches that team's rows, never the whole org's.
        """
        role = store.find_role_by_key(org_id, role_key)
        if role is None or role.level != "team":
            return None
        for agent in getattr(self.state, "agents", {}).get(org_id, ()):
            return self._team_id(org_id, agent.team)
        return None

    def _build_entitlements(self) -> EntitlementStore:
        """Assign the platform org its plan through the audited API.

        The platform org is the SPoG's own tenant, so its plan is this module's
        declaration (not an inference about a customer). ``assign_plan`` records
        an audit event, so the assignment is visible in the entitlements ledger
        rather than being a hidden constant.
        """
        store = EntitlementStore()
        assign_plan(
            store,
            self.catalog,
            PLATFORM_ORG,
            self.platform_plan,
            actor="system:portal-fleet-authz",
            subscription_status="active",
            note="platform operator org for the fleet single-pane-of-glass (#333)",
        )
        return store

    # -- principal -> rbac subject ------------------------------------------
    def subject(self, principal: Any) -> str:
        """The rbac subject behind a console principal (its verified email).

        The platform's role bindings are email-keyed (``state.bindings``), and
        the console principal's identity is the auth-gate token subject — never
        a token's own role claim.
        """
        return str(getattr(principal, "email", "") or "").strip().lower()

    def _prepare(self, principal: Any) -> str:
        """Bind a super-admin where it acts, and return its rbac subject."""
        self._bind_super_admin(principal)
        return self.subject(principal)

    def _bind_super_admin(self, principal: Any) -> None:
        """Express a super-admin as an org-wide ``owner`` binding, per org.

        The console's own doctrine is that the local allowlist — never a token
        claim — makes a principal a super-admin who can act in every tenant
        (``portal/server/authz.py``), so the platform operator sees the whole
        board through the scoped view and reaches the platform org the roll-up
        is gated on. Rbac has no global principal, so the power is expressed as
        the binding an ordinary operator would need — one owner binding per org
        — and disappears the moment the principal is not a super-admin.
        """
        if not getattr(principal, "super_admin", False):
            return
        subject = self.subject(principal)
        for org_id in self._org_ids():
            role_id = self._role_id(self.store, org_id, "owner")
            if role_id is None:
                continue
            held = {
                binding.role_id
                for binding in self.store.bindings_for_subject(org_id, subject)
            }
            if role_id not in held:
                self.store.add_binding(org_id, subject, SUBJECT_USER, role_id)

    # -- decisions ----------------------------------------------------------
    def row_decision(self, principal: Any, owner: RowOwner):
        """The two-gate decision for reading a row owned by ``owner``."""
        return guard(self.store, self._prepare(principal), owner.node, READ_PERMISSION)

    def rollup_decision(self, principal: Any):
        """The four-gate decision for the cross-org roll-up.

        Subscription, then scope at the platform org, then ``fleet:rollup``,
        then the entitlement the roll-up feature unlocks — the entitlements
        lane's own order, so each refusal is attributable to one gate.
        """
        return evaluate_permission(
            self.entitlements,
            self.store,
            self.catalog,
            PLATFORM_ORG,
            self._prepare(principal),
            ScopeNode(org_id=PLATFORM_ORG),
            ROLLUP_PERMISSION,
        )

    def may_read(self, principal: Any, owner: RowOwner) -> bool:
        return self.row_decision(principal, owner).allowed

    # -- the surface gate ---------------------------------------------------
    def scope_nodes(self) -> list[ScopeNode]:
        """Every node the console's org directory declares.

        One org-level node per org plus one node per declared team, so a
        team-scoped principal (``team-admin``, ``agent-operator`` — team-level
        roles the rbac store refuses to grant org-wide) is still recognised as
        a legitimate reader of its own team's rows.
        """
        nodes = [ScopeNode(org_id=org_id) for org_id in self._org_ids()]
        for org_id, agents in getattr(self.state, "agents", {}).items():
            for agent in agents:
                nodes.append(
                    ScopeNode(org_id=org_id, team_id=self._team_id(org_id, agent.team))
                )
        return nodes

    def surface_refusal(self, principal: Any):
        """The refusal to raise when the caller may read no fleet row at all.

        A caller with no reachable node is out of scope; a caller in scope
        somewhere but without ``fleet:read`` is short a permission. The two are
        reported separately because the fix for one is not the fix for the
        other (rbac's denial doctrine). ``None`` means the surface is reachable.
        """
        subject = self._prepare(principal)
        reached_permission_gate = False
        for node in self.scope_nodes():
            decision = guard(self.store, subject, node, READ_PERMISSION)
            if decision.allowed:
                return None
            if decision.reason == REASON_PERMISSION:
                reached_permission_gate = True
        if reached_permission_gate:
            return cpapi_errors.permission_denied(READ_PERMISSION)
        return cpapi_errors.scope_denied("out_of_scope")

    def _require_surface(self, principal: Any) -> None:
        refusal = self.surface_refusal(principal)
        if refusal is not None:
            raise FleetDenied(
                refusal.status, refusal.code, refusal.message, refusal.details
            )

    # -- row flattening -----------------------------------------------------
    def _row_owner(self, spec: RowSpec, value: Any) -> RowOwner:
        if spec.owner == OWNER_ROW:
            attributed = self.index.attribute(value)
            if attributed is not None:
                return attributed
        return RowOwner(OWNER_PLATFORM)

    def rows(self, snapshot: Mapping[str, Any]) -> list[FleetRow]:
        """Every projection row, attributed. The roll-up's unit of account."""
        rows: list[FleetRow] = []
        for spec in ROW_SPECS:
            if spec.kind not in ("mapping", "sequence"):
                continue
            value = snapshot.get(spec.name)
            if spec.kind == "mapping" and isinstance(value, Mapping):
                for key, item in value.items():
                    rows.append(
                        FleetRow(spec.name, str(key), item, self._row_owner(spec, item))
                    )
            elif spec.kind == "sequence" and isinstance(value, (list, tuple)):
                for position, item in enumerate(value):
                    rows.append(
                        FleetRow(
                            spec.name, f"{spec.name}[{position}]", item,
                            self._row_owner(spec, item),
                        )
                    )
        return rows

    # -- scoped reads -------------------------------------------------------
    def _section(self, spec: RowSpec, value: Any, principal: Any) -> Any:
        if spec.owner == OWNER_SURFACE:
            return value
        if spec.kind == "scalar":
            owner = RowOwner(OWNER_PLATFORM)
            return value if self.may_read(principal, owner) else None
        if spec.kind == "mapping":
            if not isinstance(value, Mapping):
                return value
            return {
                key: item
                for key, item in value.items()
                if self.may_read(principal, self._row_owner(spec, item))
            }
        if isinstance(value, (list, tuple)):
            return [
                item
                for item in value
                if self.may_read(principal, self._row_owner(spec, item))
            ]
        return value

    def scoped_snapshot(self, principal: Any, projection: Any) -> dict[str, Any]:
        """``projection.snapshot()`` restricted to the caller's own rows.

        Every documented section is always present so the dashboard's shape does
        not vary with the caller; a section is empty when no row in it belongs
        to the caller's org.
        """
        self._require_surface(principal)
        return self._scoped_view(principal, projection)

    def _scoped_view(self, principal: Any, projection: Any) -> dict[str, Any]:
        """The caller's scoped view, without re-running the surface gate."""
        full = projection.snapshot()
        return {
            spec.name: self._section(spec, full.get(spec.name), principal)
            for spec in ROW_SPECS
        }

    def scoped_events(self, principal: Any, projection: Any, limit: int) -> list[dict]:
        """The caller's own event history, its last ``limit`` records.

        The window is applied to the caller's records, not to the fleet's tail:
        a tenant asking for its last 8 events must not receive 8 records of
        which 6 are another org's. The fleet tail is read once, bounded by
        :data:`EVENTS_SCAN_LIMIT` — so the surface stays a bounded read, never a
        whole-file scan — and then filtered to the caller.
        """
        self._require_surface(principal)
        spec = next(spec for spec in ROW_SPECS if spec.name == "events")
        records = projection.events(EVENTS_SCAN_LIMIT)
        scoped = [
            record
            for record in records
            if self.may_read(principal, self._row_owner(spec, record))
        ]
        return scoped[-int(limit):] if limit > 0 else []

    def scoped_stream(self, principal: Any, projection: Any):
        """The push channel, carrying only frames the caller may receive.

        The surface gate runs here, eagerly, so a refused caller receives the
        control plane's JSON error rather than an empty event stream. Each frame
        carries the caller's own scoped snapshot, so a tenant can never receive
        another org's row even transiently; a frame whose scoped view has not
        changed is not re-sent, so scoping does not turn the deliberately quiet
        channel into a busy one.
        """
        self._require_surface(principal)
        return self._scoped_frames(principal, projection)

    def _scoped_frames(self, principal: Any, projection: Any):
        import json

        previous: Optional[str] = None
        for _frame in projection.stream():
            payload = json.dumps(
                self._scoped_view(principal, projection), separators=(",", ":")
            )
            if payload == previous:
                continue
            previous = payload
            yield f"event: snapshot\ndata: {payload}\n\n"

    # -- the cross-org administrative view ----------------------------------
    def rollup(self, principal: Any, projection: Any) -> dict[str, Any]:
        """The full projection plus its per-org accounting (admin only).

        Refused with the entitlements lane's own reason when the caller fails
        any gate; the refusal names the gate so an operator can tell a scope
        problem from a permission or a plan problem.
        """
        decision = self.rollup_decision(principal)
        if decision.denied:
            raise self._rollup_denial(decision)
        full = projection.snapshot()
        rows = self.rows(full)
        counts: dict[str, int] = {}
        detail: dict[str, dict[str, int]] = {}
        for row in rows:
            counts[row.owner.org_id] = counts.get(row.owner.org_id, 0) + 1
            sections = detail.setdefault(row.owner.org_id, {})
            sections[row.section] = sections.get(row.section, 0) + 1
        orgs = [
            {"orgId": org_id, "rows": counts[org_id], "sections": detail[org_id]}
            for org_id in sorted(counts)
        ]
        return {
            "snapshot": full,
            "orgs": orgs,
            "totals": {"orgs": len(orgs), "rows": len(rows)},
        }

    def _rollup_denial(self, decision: Any) -> "FleetDenied":
        reason = getattr(decision, "reason", None)
        code = str(getattr(decision, "code", "") or "")
        if reason == REASON_SCOPE:
            error = cpapi_errors.scope_denied(code or "out_of_scope")
        elif reason == REASON_PERMISSION:
            error = cpapi_errors.permission_denied(ROLLUP_PERMISSION)
        elif reason in (REASON_SUBSCRIPTION, REASON_ENTITLEMENT):
            error = cpapi_errors.ApiError(
                403,
                "not_entitled",
                "the platform org is not entitled to the fleet roll-up surface",
                {"reason": reason, "entitlementCode": code},
            )
        else:
            error = cpapi_errors.forbidden("the fleet roll-up is refused")
        return FleetDenied(error.status, error.code, error.message, error.details)


class FleetDenied(Exception):
    """A refusal on the fleet surface, in the control plane's vocabulary.

    Carries the same ``status``/``code``/``message``/``details`` an
    ``identity/cpapi`` error does, so the console maps it onto its own error
    envelope with no second taxonomy. The message never carries a credential.
    """

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.details = dict(details) if details else None


__all__ = [
    "EVENTS_SCAN_LIMIT",
    "FLEET_RESOURCE",
    "PLATFORM_ORG",
    "PLATFORM_PLAN",
    "READ_PERMISSION",
    "ROLLUP_FEATURE",
    "ROLLUP_PERMISSION",
    "ROW_SPECS",
    "SNAPSHOT_SECTIONS",
    "SPOG_ROLE_PERMISSIONS",
    "FleetAuthorizer",
    "FleetDenied",
    "FleetRow",
    "OrgIndex",
    "RowOwner",
    "RowSpec",
]
