"""Role-based boundaries for the C-suite seats (issue #638, workbook-7 Pillar 2).

The role model (`model.py`), the two gates (`resolve.py`) and the packs
(`presets/`) answer *what a principal may do*. This module answers the
complementary question the workbook names: *what is outside a role's
boundary*, so an action that leaves it is **refused** rather than merely
un-granted.

A boundary is a **narrowing** declaration, not a grant. It is derived, never
restated: ``load_csuite_boundaries()`` reads the landed persona cards in
``registry/personas/cards/{ceo,cto,coo,cfo,cmo}.yaml`` (issue #632,
read-only consumption per the contract-freeze doctrine) and the org-chart the
cards agree with. The three boundary axes are the card's own fields:

- **owned lanes** (``ownedLanes``) - the work lanes the seat owns;
- **tool allowlist** (``toolAllowlist``) - the tools whose use the seat may
  request;
- **budget cap** (``monthlyBudgetCapUsd`` + ``guardrailPolicyRef``) - the
  monthly ceiling and the policy that enforces it.

Plus the ``capabilitySet`` / ``constraintSet`` the seat declares, so a caller
can refuse a capability or a deliberately-broken constraint the same way.

## Two distinct refusals

The distinction matters for attribution, exactly as it does for the two gates:

- ``"lane"`` - the target is a lane the seat does not own (work outside the
  seat's boundary).
- ``"tool"`` - the seat asked to use a tool outside its allowlist.
- ``"capability"`` - the seat claimed a capability it does not hold.
- ``"budget"`` - the seat's spend would exceed its declared monthly cap.

Every refusal names the offender (lane id / tool id / amount) so a gate can
quote the reason instead of a bare boolean - the same discipline the skills
adapter uses for its refusals and the guard uses for its ``Decision``.


---knowledge---
module_id: identity.rbac.boundaries
system: identity
app: rbac
solution_class: enterprise
patterns: [derived-never-restated, fail-closed, read-only-consumption]
derives_from: null
owner_sme: security-sme
tier: L1
interfaces: [load_csuite_boundaries, RoleBoundary, BoundaryViolation, BOUNDARY_LANES, CSUITE_ROLE_IDS]
invariants: "a boundary is a narrowing declaration, not a grant, and it is derived from the landed persona cards rather than restated"
gotchas: "the C-suite cards are read-only consumption per the contract-freeze doctrine"
related: ["#638", "#632"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise ImportError(f"rbac.boundaries: missing dependency ({exc}); need PyYAML") from exc

# --- locations ---------------------------------------------------------------

_PKG_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PKG_DIR.parent.parent
CARDS_DIR = _REPO_ROOT / "registry" / "personas" / "cards"
ORG_CHART_PATH = _REPO_ROOT / "registry" / "personas" / "org-chart.yaml"

#: The five C-suite seats this module binds (workbook-7 Pillar 2). Ordered by
#: the org-chart reporting line: the root first, then its reports.
CSUITE_ROLE_IDS: tuple[str, ...] = ("ceo", "cto", "coo", "cfo", "cmo")

#: The three boundary axes a refusal can name.
BOUNDARY_LANES = "lanes"
BOUNDARY_TOOLS = "tools"
BOUNDARY_CAPABILITIES = "capabilities"
BOUNDARY_BUDGET = "budget"


class BoundaryError(Exception):
    """Base class for every error this module raises."""


class UnknownRoleBoundaryError(BoundaryError):
    """No boundary is declared for the requested role id."""

    def __init__(self, role_id: str, known: tuple[str, ...]) -> None:
        super().__init__(
            f"no role boundary for {role_id!r}; declared boundaries: "
            f"{', '.join(known) or '(none)'}"
        )
        self.role_id = role_id
        self.known = known


class MissingCardError(BoundaryError):
    """A C-suite persona card is absent or unreadable."""

    def __init__(self, role_id: str, path: Path) -> None:
        super().__init__(f"persona card for {role_id!r} not found at {path}")
        self.role_id = role_id
        self.path = path


class BoundaryViolation(BoundaryError):
    """An action left the role's declared boundary - refused, naming the axis.

    ``axis`` is one of ``"lane"``, ``"tool"``, ``"capability"`` or ``"budget"``
    so the cause is attributable; ``offender`` is the lane/tool/capability id or
    the amount, and ``allowed`` the seat's declared boundary on that axis.
    """

    def __init__(self, role_id: str, axis: str, offender: Any, allowed: Any) -> None:
        super().__init__(
            f"role {role_id!r} is out of boundary on {axis}: "
            f"{offender!r} not in {allowed!r}"
        )
        self.role_id = role_id
        self.axis = axis
        self.offender = offender
        self.allowed = allowed


# --- the boundary declaration -------------------------------------------------


@dataclass(frozen=True)
class RoleBoundary:
    """One C-suite seat's declared boundary - derived from its persona card.

    ``budget_policy_ref`` is the card's ``guardrailPolicyRef`` (the FinOps
    policy that enforces the cap); ``reports_to`` is the org-chart edge target
    (``board`` for the root). ``tenant`` records which card was consumed, so a
    tenant card shadowing the platform one is visible in the boundary.
    """

    role_id: str
    tenant: str
    title: str
    reports_to: str
    owned_lanes: tuple[str, ...]
    tool_allowlist: tuple[str, ...]
    capability_set: tuple[str, ...]
    constraint_set: tuple[str, ...]
    budget_cap_usd: float
    budget_policy_ref: str
    default_model_tier: str

    def owns_lane(self, lane: str) -> bool:
        return lane in self.owned_lanes

    def allows_tool(self, tool: str) -> bool:
        return tool in self.tool_allowlist

    def holds_capability(self, capability: str) -> bool:
        return capability in self.capability_set

    def within_budget(self, spend_usd: float) -> bool:
        return spend_usd <= self.budget_cap_usd

    def as_dict(self) -> dict[str, Any]:
        return {
            "role_id": self.role_id,
            "tenant": self.tenant,
            "title": self.title,
            "reports_to": self.reports_to,
            "owned_lanes": list(self.owned_lanes),
            "tool_allowlist": list(self.tool_allowlist),
            "capability_set": list(self.capability_set),
            "constraint_set": list(self.constraint_set),
            "budget_cap_usd": self.budget_cap_usd,
            "budget_policy_ref": self.budget_policy_ref,
            "default_model_tier": self.default_model_tier,
        }


@dataclass(frozen=True)
class BoundaryPack:
    """The five C-suite boundaries plus the org-chart root they hang from."""

    tenant: str
    root: str
    boundaries: tuple[RoleBoundary, ...]

    def role_ids(self) -> tuple[str, ...]:
        return tuple(b.role_id for b in self.boundaries)

    def find(self, role_id: str) -> RoleBoundary | None:
        for boundary in self.boundaries:
            if boundary.role_id == role_id:
                return boundary
        return None

    def get(self, role_id: str) -> RoleBoundary:
        """The boundary for ``role_id``, refusing (not returning None) if absent."""
        boundary = self.find(role_id)
        if boundary is None:
            raise UnknownRoleBoundaryError(role_id, self.role_ids())
        return boundary


# --- derivation from the persona cards (read-only consumption) ----------------


def _load_card(role_id: str, *, cards_dir: Path = CARDS_DIR) -> dict[str, Any]:
    path = cards_dir / f"{role_id}.yaml"
    if not path.is_file():
        raise MissingCardError(role_id, path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise MissingCardError(role_id, path)
    return data


def _require(card: dict[str, Any], role_id: str, field: str) -> Any:
    if field not in card or card[field] is None:
        raise BoundaryError(
            f"persona card {role_id!r} is missing the boundary field {field!r}"
        )
    return card[field]


def boundary_from_card(card: dict[str, Any]) -> RoleBoundary:
    """Build a ``RoleBoundary`` from a parsed persona card (a narrowing only).

    Refuses a card whose boundary fields are absent, because a boundary that
    cannot be read is a boundary that cannot be enforced - better a loud
    failure at load time than a seat silently unbounded at run time.
    """
    role_id = str(_require(card, card.get("id", "?"), "id"))
    budget = _require(card, role_id, "monthlyBudgetCapUsd")
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) or budget < 0:
        raise BoundaryError(
            f"persona card {role_id!r}: monthlyBudgetCapUsd must be a "
            f"non-negative number (got {budget!r})"
        )
    return RoleBoundary(
        role_id=role_id,
        tenant=str(card.get("tenant", "platform")),
        title=str(card.get("name", role_id)),
        reports_to=str(_require(card, role_id, "reportsTo")),
        owned_lanes=tuple(_require(card, role_id, "ownedLanes")),
        tool_allowlist=tuple(_require(card, role_id, "toolAllowlist")),
        capability_set=tuple(_require(card, role_id, "capabilitySet")),
        constraint_set=tuple(card.get("constraintSet") or ()),
        budget_cap_usd=float(budget),
        budget_policy_ref=str(_require(card, role_id, "guardrailPolicyRef")),
        default_model_tier=str(_require(card, role_id, "defaultModelTier")),
    )


def _load_org_chart(path: Path = ORG_CHART_PATH) -> dict[str, Any]:
    if not path.is_file():
        raise BoundaryError(f"org chart not found at {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BoundaryError(f"org chart at {path} is not a mapping")
    return data


def load_csuite_boundaries(
    *,
    cards_dir: Path = CARDS_DIR,
    org_chart_path: Path = ORG_CHART_PATH,
) -> BoundaryPack:
    """Derive the five C-suite boundaries from the landed persona cards.

    The card is the source of truth for each seat's lanes / tools / budget; the
    org-chart supplies the root and is cross-checked so a chart that has drifted
    from the cards cannot silently bound a seat twice over. Refuses when a card
    is missing, a boundary field is absent, or the chart's root is not one of
    the five seats - an unreadable boundary is never an open boundary.
    """
    chart = _load_org_chart(org_chart_path)
    tenant = str(chart.get("tenant", "platform"))
    root = str(chart.get("root", ""))
    chart_roles = {
        str(node.get("id")): node for node in (chart.get("roles") or []) if node
    }

    boundaries: list[RoleBoundary] = []
    for role_id in CSUITE_ROLE_IDS:
        boundary = boundary_from_card(_load_card(role_id, cards_dir=cards_dir))
        node = chart_roles.get(role_id)
        if node is not None:
            # Chart and card must agree on the reporting edge and the cap, or
            # one of them is stale - refuse rather than adopt the wrong one.
            if node.get("reportsTo") != boundary.reports_to:
                raise BoundaryError(
                    f"org chart role {role_id!r} reportsTo={node.get('reportsTo')!r} "
                    f"but its card declares {boundary.reports_to!r}"
                )
            cap = node.get("monthlyBudgetCapUsd")
            if cap is not None and float(cap) != boundary.budget_cap_usd:
                raise BoundaryError(
                    f"org chart role {role_id!r} cap={cap!r} but its card declares "
                    f"{boundary.budget_cap_usd!r}"
                )
        boundaries.append(boundary)

    if root not in {b.role_id for b in boundaries}:
        raise BoundaryError(
            f"org-chart root {root!r} is not one of the C-suite seats "
            f"({', '.join(CSUITE_ROLE_IDS)})"
        )

    return BoundaryPack(tenant=tenant, root=root, boundaries=tuple(boundaries))


# --- the boundary guard -------------------------------------------------------


def guard_boundary(
    pack: BoundaryPack, role_id: str, action: "BoundaryAction"
) -> "BoundaryDecision":
    """Refuse an action that leaves a role's declared boundary.

    The action is checked against every axis the seat declares and the first
    violated axis refuses, naming the offender. An out-of-boundary action is
    *refused*, never silently allowed: the caller receives
    ``decision.allowed is False`` and ``decision.violation`` carries the axis,
    the offender and the seat's own boundary on that axis.
    """
    boundary = pack.get(role_id)

    if action.lane is not None and not boundary.owns_lane(action.lane):
        return BoundaryDecision.make_refused(
            boundary, action, BOUNDARY_LANES, action.lane, boundary.owned_lanes
        )
    if action.tool is not None and not boundary.allows_tool(action.tool):
        return BoundaryDecision.make_refused(
            boundary, action, BOUNDARY_TOOLS, action.tool, boundary.tool_allowlist
        )
    if (
        action.capability is not None
        and not boundary.holds_capability(action.capability)
    ):
        return BoundaryDecision.make_refused(
            boundary,
            action,
            BOUNDARY_CAPABILITIES,
            action.capability,
            boundary.capability_set,
        )
    if action.spend_usd is not None and not boundary.within_budget(action.spend_usd):
        return BoundaryDecision.make_refused(
            boundary, action, BOUNDARY_BUDGET, action.spend_usd, boundary.budget_cap_usd
        )
    return BoundaryDecision.allowed_for(boundary, action)


@dataclass(frozen=True)
class BoundaryAction:
    """A proposed action, described along one or more boundary axes.

    Every axis is optional: an action that names only a lane is checked against
    the lane boundary alone. Axes are checked in declaration order
    (lane -> tool -> capability -> budget) so the first refusal names the
    outermost violation.
    """

    lane: str | None = None
    tool: str | None = None
    capability: str | None = None
    spend_usd: float | None = None


@dataclass(frozen=True)
class BoundaryDecision:
    """Outcome of a boundary check: allowed, or refused on exactly one axis."""

    allowed: bool
    role_id: str
    boundary: RoleBoundary
    action: BoundaryAction
    violation: BoundaryViolation | None = None

    @property
    def refused(self) -> bool:
        return not self.allowed

    @property
    def axis(self) -> str | None:
        return None if self.violation is None else self.violation.axis

    @classmethod
    def allowed_for(
        cls, boundary: RoleBoundary, action: BoundaryAction
    ) -> "BoundaryDecision":
        return cls(allowed=True, role_id=boundary.role_id, boundary=boundary, action=action)

    @classmethod
    def make_refused(
        cls,
        boundary: RoleBoundary,
        action: BoundaryAction,
        axis: str,
        offender: Any,
        allowed: Any,
    ) -> "BoundaryDecision":
        violation = BoundaryViolation(boundary.role_id, axis, offender, allowed)
        return cls(
            allowed=False,
            role_id=boundary.role_id,
            boundary=boundary,
            action=action,
            violation=violation,
        )
