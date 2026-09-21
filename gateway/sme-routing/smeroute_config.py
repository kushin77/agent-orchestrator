"""Load, schema-validate and semantically validate the SME-routing policies.

Two validation layers, deliberately separated (GR-12, no-false-green):

* **Schema** (``schema.yaml`` via ``jsonschema_lite``) checks the SHAPE of each
  declared document. A shape violation means the policy cannot be used to
  evaluate a task at all -- ``PolicyMalformed``.
* **Semantics** checks the invariants JSON Schema cannot express: an agent
  named in a chain must be a declared agent, a route's worker fleet and tier
  must exist, the escalation ladder must be acyclic / strictly increasing /
  terminate at a null fallback, and the unknown-task fail-safe must stay
  ``deep``. A violated invariant means the policy is evaluable but WRONG --
  ``PolicyInvariantViolated``.

Exit-code mapping (tri-state, see ``cli.py``):

* ``PolicyUnavailable`` / ``PolicyMalformed``  -> CANNOT-ASSESS (2)
* ``PolicyInvariantViolated``                  -> NOT-OK        (1)
* loaded cleanly                               -> OK            (0)

Nothing here touches the network; the dependency surface is stdlib + PyYAML.

---knowledge---
module_id: gateway.sme-routing.smeroute_config
system: gateway
app: sme-routing
solution_class: enterprise
patterns: [two-layer-validation, schema-then-semantics, fail-closed]
derives_from: null
owner_sme: orchestrator
tier: L1
interfaces: [load_bundle, PolicyBundle, PolicyError, PolicyUnavailable, PolicyMalformed, PolicyInvariantViolated, normalise_task_type]
invariants: "a shape violation is CANNOT-ASSESS (rc 2) and a broken declared invariant is NOT-OK (rc 1); the two are never collapsed"
gotchas: "a violated invariant means the policy is evaluable but WRONG, which is a different verdict from unusable"
related: ["#149"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import datetime
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

try:  # PyYAML is the platform standard stack; absence is CANNOT-ASSESS, not a crash.
    import yaml
except ImportError:  # pragma: no cover - exercised only in a PyYAML-less env
    yaml = None  # type: ignore[assignment]

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import jsonschema_lite  # noqa: E402  (path arranged above)

__all__ = [
    "PolicyError",
    "PolicyUnavailable",
    "PolicyMalformed",
    "PolicyInvariantViolated",
    "AgentRole",
    "WorkerFleet",
    "SmeDomain",
    "Squad",
    "ModuleAuthority",
    "CapabilityRegistry",
    "Thresholds",
    "RouteSpec",
    "RoutePolicy",
    "TierSpec",
    "TierPolicy",
    "PolicyBundle",
    "normalise_task_type",
    "module_dir",
    "schema_path",
    "default_policies_dir",
    "load_bundle",
]

POLICY_FILES: Tuple[Tuple[str, str], ...] = (
    ("capability-registry.yaml", "capability_registry"),
    ("route-policy.yaml", "route_policy"),
    ("tier-policy.yaml", "tier_policy"),
)

_RANGE_RE = re.compile(r"^(\d+)-(\d+)$")


class PolicyError(Exception):
    """Base class for every policy-loading failure."""


class PolicyUnavailable(PolicyError):
    """The policy set could not be read at all -- CANNOT-ASSESS (rc 2)."""


class PolicyMalformed(PolicyError):
    """The policy is unusable to evaluate a task -- CANNOT-ASSESS (rc 2).

    Covers an unparseable file, a schema violation, and a non-total
    complexity map (a task can fall outside every declared range, so the
    router genuinely cannot answer).
    """


class PolicyInvariantViolated(PolicyError):
    """The policy parses but a declared invariant is broken -- NOT-OK (rc 1)."""


def normalise_task_type(value: str) -> str:
    """Canonical task-type key: lower-cased, ``_`` folded to ``-``.

    The harvest spells route-policy keys with underscores and tier-policy
    overrides with hyphens; normalising both sides means a declared override
    can never be skipped because of a spelling mismatch.
    """
    return str(value).strip().lower().replace("_", "-")


def module_dir() -> Path:
    return Path(_HERE)


def schema_path() -> Path:
    return Path(_HERE) / "schema.yaml"


def default_policies_dir() -> Path:
    return Path(_HERE) / "policies"


# --- typed policy views -----------------------------------------------------
@dataclass(frozen=True)
class AgentRole:
    id: str
    owner: str
    capabilities: Tuple[str, ...]
    tools_allowed: Tuple[str, ...]
    latency_tier: str
    cost_tier: str
    fallback: Tuple[str, ...]
    worker_types: Tuple[str, ...]


@dataclass(frozen=True)
class WorkerFleet:
    name: str
    description: str
    access_level: str
    agent_roles: Tuple[str, ...]
    default_model: str


@dataclass(frozen=True)
class SmeDomain:
    name: str
    description: str
    labels: Tuple[str, ...]
    sme: str
    module: str


@dataclass(frozen=True)
class Squad:
    name: str
    lens: str
    keywords: Tuple[str, ...]


@dataclass(frozen=True)
class ModuleAuthority:
    domain: str
    module: str
    owns: Tuple[str, ...]
    policy: str


@dataclass(frozen=True)
class CapabilityRegistry:
    version: str
    updated_at: str
    source: str
    description: str
    agents: Tuple[AgentRole, ...]
    worker_fleet: Mapping[str, WorkerFleet]
    dispatch_routing: Mapping[str, Tuple[str, ...]]
    sme_domains: Mapping[str, SmeDomain]
    squads: Mapping[str, Squad]
    squad_default: str
    sme_default: str
    module_authority: Tuple[ModuleAuthority, ...]

    @property
    def agent_ids(self) -> frozenset:
        return frozenset(agent.id for agent in self.agents)

    @property
    def worker_fleet_names(self) -> frozenset:
        return frozenset(self.worker_fleet)

    @property
    def module_ids(self) -> frozenset:
        return frozenset(entry.module for entry in self.module_authority)


@dataclass(frozen=True)
class Thresholds:
    fast_path_max_tokens: int
    deep_path_min_tokens: int
    risk_high_keywords: Tuple[str, ...]
    complexity_keywords: Tuple[str, ...]


@dataclass(frozen=True)
class RouteSpec:
    name: str
    path_mode: str
    description: str
    agents: Tuple[str, ...]
    model_tier: str
    worker_types: Tuple[str, ...]


@dataclass(frozen=True)
class RoutePolicy:
    version: str
    updated_at: str
    source: str
    description: str
    thresholds: Thresholds
    routes: Mapping[str, RouteSpec]
    dispatch_defaults: Mapping[str, str]
    normalised_defaults: Mapping[str, str]


@dataclass(frozen=True)
class TierSpec:
    name: str
    models: Tuple[str, ...]
    timeout_seconds: int
    max_tokens: int
    fallback: Optional[str]
    description: str


@dataclass(frozen=True)
class TierPolicy:
    version: str
    updated_at: str
    source: str
    description: str
    tiers: Mapping[str, TierSpec]
    tier_order: Tuple[str, ...]
    complexity_ranges: Tuple[Tuple[int, int, str], ...]
    task_overrides: Mapping[str, str]
    normalised_overrides: Mapping[str, str]

    def rank(self, tier_name: str) -> int:
        """Position of ``tier_name`` in the escalation order (cheap -> costly)."""
        return self.tier_order.index(tier_name)

    def tier(self, tier_name: str) -> TierSpec:
        return self.tiers[tier_name]

    @property
    def terminal_tier(self) -> str:
        return self.tier_order[-1]

    def tier_for_complexity(self, score: int) -> str:
        for low, high, tier_name in self.complexity_ranges:
            if low <= score <= high:
                return tier_name
        raise PolicyMalformed(
            f"complexity {score} falls outside every declared range: cannot assess"
        )


@dataclass(frozen=True)
class PolicyBundle:
    capability_registry: CapabilityRegistry
    route_policy: RoutePolicy
    tier_policy: TierPolicy
    policies_dir: Path


# --- loading ----------------------------------------------------------------
def _read_yaml(path: Path, label: str) -> Any:
    if yaml is None:
        raise PolicyUnavailable(
            f"{label}: PyYAML is not installed; {path} cannot be parsed"
        )
    if not path.is_file():
        raise PolicyUnavailable(f"{label}: policy file not found at {path}")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except OSError as exc:
        raise PolicyUnavailable(f"{label}: cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:  # type: ignore[union-attr]
        raise PolicyMalformed(f"{label}: {path} is not valid YAML: {exc}") from exc


def _normalise_dates(value: Any) -> Any:
    """Fold PyYAML's ``date``/``datetime`` coercion back to ISO strings.

    PyYAML turns an unquoted ``2026-07-24`` into ``datetime.date``, which then
    fails a JSON-Schema ``type: string`` on a date a human wrote correctly.
    Normalising at the loader boundary keeps the schema honest without
    silently coercing anything else.
    """
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {key: _normalise_dates(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalise_dates(item) for item in value]
    return value


def load_schema(path: Optional[Path] = None) -> Dict[str, Any]:
    schema_file = Path(path) if path else schema_path()
    raw = _read_yaml(schema_file, "schema")
    if not isinstance(raw, Mapping):
        raise PolicyMalformed(f"schema: {schema_file} must be a mapping")
    return dict(raw)


def _validate_document(document: Any, schema: Mapping[str, Any], definition: str,
                       label: str) -> None:
    try:
        subschema = jsonschema_lite.subschema(schema, definition)
    except jsonschema_lite.SchemaValidationError as exc:
        raise PolicyMalformed(f"schema: {exc}") from exc
    try:
        jsonschema_lite.validate(document, subschema, root=schema)
    except jsonschema_lite.SchemaValidationError as exc:
        raise PolicyMalformed(
            f"{label}: does not satisfy #/definitions/{definition}\n{exc}"
        ) from exc


def _collision_free(raw_keys: Sequence[str], label: str) -> Dict[str, str]:
    """Map declared keys -> normalised keys, refusing an ambiguous collision."""
    seen: Dict[str, str] = {}
    for key in raw_keys:
        normalised = normalise_task_type(key)
        if normalised in seen:
            raise PolicyInvariantViolated(
                f"{label}: {key!r} collides with {seen[normalised]!r} after "
                f"normalisation ({normalised!r}) -- the rule is ambiguous"
            )
        seen[normalised] = key
    return seen


def _parse_complexity_ranges(mapping: Mapping[str, str]) -> Tuple[Tuple[int, int, str], ...]:
    ranges: List[Tuple[int, int, str]] = []
    for key, tier_name in mapping.items():
        match = _RANGE_RE.match(str(key))
        if not match:
            raise PolicyMalformed(
                f"tier-policy: complexity_to_tier key {key!r} is not a 'low-high' range"
            )
        low, high = int(match.group(1)), int(match.group(2))
        if low > high:
            raise PolicyMalformed(
                f"tier-policy: complexity range {key!r} is inverted"
            )
        ranges.append((low, high, str(tier_name)))
    ranges.sort()

    cursor = 0
    for low, high, _ in ranges:
        if low != cursor:
            raise PolicyMalformed(
                "tier-policy: complexity ranges are not contiguous from 0: "
                f"expected {cursor}-..., found {low}-{high}; a task complexity "
                "in the gap cannot be assessed"
            )
        cursor = high + 1
    if cursor != 101:
        raise PolicyMalformed(
            "tier-policy: complexity ranges must cover 0..100, "
            f"they stop at {cursor - 1}"
        )
    if len({tier for _, _, tier in ranges}) != len(ranges):
        raise PolicyMalformed(
            "tier-policy: two complexity ranges map onto the same tier -- "
            "the tier order is ambiguous"
        )
    return tuple(ranges)


def _build_registry(raw: Mapping[str, Any]) -> CapabilityRegistry:
    agents = tuple(
        AgentRole(
            id=entry["id"],
            owner=entry["owner"],
            capabilities=tuple(entry["capabilities"]),
            tools_allowed=tuple(entry["tools_allowed"]),
            latency_tier=entry["latency_tier"],
            cost_tier=entry["cost_tier"],
            fallback=tuple(entry["fallback"]),
            worker_types=tuple(entry["worker_types"]),
        )
        for entry in raw["agents"]
    )
    fleets = {
        name: WorkerFleet(
            name=name,
            description=entry["description"],
            access_level=entry["access_level"],
            agent_roles=tuple(entry["agent_roles"]),
            default_model=entry["default_model"],
        )
        for name, entry in raw["worker_fleet"].items()
    }
    domains = {
        name: SmeDomain(
            name=name,
            description=entry["description"],
            labels=tuple(entry["labels"]),
            sme=entry["sme"],
            module=entry["module"],
        )
        for name, entry in raw["sme_domains"].items()
    }
    squads = {
        name: Squad(
            name=name,
            lens=entry["lens"],
            keywords=tuple(entry["keywords"]),
        )
        for name, entry in raw["squads"].items()
    }
    authority = tuple(
        ModuleAuthority(
            domain=entry["domain"],
            module=entry["module"],
            owns=tuple(entry["owns"]),
            policy=entry["policy"],
        )
        for entry in raw["module_authority"]
    )
    return CapabilityRegistry(
        version=raw["version"],
        updated_at=raw["updated_at"],
        source=raw["source"],
        description=raw["description"],
        agents=agents,
        worker_fleet=fleets,
        dispatch_routing={
            name: tuple(chain) for name, chain in raw["dispatch_routing"].items()
        },
        sme_domains=domains,
        squads=squads,
        squad_default=raw["squad_default"],
        sme_default=raw["sme_default"],
        module_authority=authority,
    )


def _build_route_policy(raw: Mapping[str, Any]) -> RoutePolicy:
    thresholds_raw = raw["thresholds"]
    routes = {
        name: RouteSpec(
            name=name,
            path_mode=entry["path_mode"],
            description=entry["description"],
            agents=tuple(entry["agents"]),
            model_tier=entry["model_tier"],
            worker_types=tuple(entry["worker_types"]),
        )
        for name, entry in raw["routes"].items()
    }
    defaults = {str(key): str(value) for key, value in raw["dispatch_defaults"].items()}
    _collision_free(tuple(defaults), "route-policy: dispatch_defaults")
    return RoutePolicy(
        version=raw["version"],
        updated_at=raw["updated_at"],
        source=raw["source"],
        description=raw["description"],
        thresholds=Thresholds(
            fast_path_max_tokens=thresholds_raw["fast_path_max_tokens"],
            deep_path_min_tokens=thresholds_raw["deep_path_min_tokens"],
            risk_high_keywords=tuple(thresholds_raw["risk_high_keywords"]),
            complexity_keywords=tuple(thresholds_raw["complexity_keywords"]),
        ),
        routes=routes,
        dispatch_defaults=defaults,
        normalised_defaults={
            normalise_task_type(key): value for key, value in defaults.items()
        },
    )


def _build_tier_policy(raw: Mapping[str, Any]) -> TierPolicy:
    tiers = {
        name: TierSpec(
            name=name,
            models=tuple(entry["models"]),
            timeout_seconds=entry["timeout_seconds"],
            max_tokens=entry["max_tokens"],
            fallback=entry["fallback"],
            description=entry["description"],
        )
        for name, entry in raw["tiers"].items()
    }
    ranges = _parse_complexity_ranges(raw["complexity_to_tier"])
    overrides = {str(key): str(value) for key, value in raw["task_overrides"].items()}
    _collision_free(tuple(overrides), "tier-policy: task_overrides")
    return TierPolicy(
        version=raw["version"],
        updated_at=raw["updated_at"],
        source=raw["source"],
        description=raw["description"],
        tiers=tiers,
        tier_order=tuple(tier for _, _, tier in ranges),
        complexity_ranges=ranges,
        task_overrides=overrides,
        normalised_overrides={
            normalise_task_type(key): value for key, value in overrides.items()
        },
    )


def _check_invariants(registry: CapabilityRegistry, routes: RoutePolicy,
                      tiers: TierPolicy) -> None:
    agent_ids = registry.agent_ids
    fleet_names = registry.worker_fleet_names

    for agent in registry.agents:
        for worker in agent.worker_types:
            if worker not in fleet_names:
                raise PolicyInvariantViolated(
                    f"capability-registry: agent {agent.id!r} names worker fleet "
                    f"{worker!r}, which is not declared"
                )
        for downstream in agent.fallback:
            if downstream not in agent_ids:
                raise PolicyInvariantViolated(
                    f"capability-registry: agent {agent.id!r} falls back to "
                    f"{downstream!r}, which is not declared"
                )
    for fleet in registry.worker_fleet.values():
        for role in fleet.agent_roles:
            if role not in agent_ids:
                raise PolicyInvariantViolated(
                    f"capability-registry: worker fleet {fleet.name!r} names agent "
                    f"role {role!r}, which is not declared"
                )
    for chain_name, chain in registry.dispatch_routing.items():
        for role in chain:
            if role not in agent_ids:
                raise PolicyInvariantViolated(
                    f"capability-registry: dispatch chain {chain_name!r} names agent "
                    f"{role!r}, which is not declared"
                )

    for name, domain in registry.sme_domains.items():
        if name != "general" and not domain.labels:
            raise PolicyInvariantViolated(
                f"capability-registry: SME domain {name!r} declares no label, so no "
                "text can ever select it"
            )
        if domain.module and domain.module not in registry.module_ids:
            raise PolicyInvariantViolated(
                f"capability-registry: SME domain {name!r} names module "
                f"{domain.module!r}, which is not in the authority matrix"
            )
    if registry.sme_default != registry.sme_domains["general"].sme:
        raise PolicyInvariantViolated(
            f"capability-registry: sme_default {registry.sme_default!r} does not match "
            f"the general domain's SME {registry.sme_domains['general'].sme!r}"
        )
    if registry.squad_default not in registry.squads:
        raise PolicyInvariantViolated(
            f"capability-registry: squad_default {registry.squad_default!r} is not a "
            "declared squad"
        )
    if registry.squads[registry.squad_default].keywords:
        raise PolicyInvariantViolated(
            f"capability-registry: squad_default {registry.squad_default!r} declares "
            "keywords, so it is not the keyword-less fallthrough arm"
        )
    keywordless = [name for name, squad in registry.squads.items() if not squad.keywords]
    if keywordless != [registry.squad_default]:
        raise PolicyInvariantViolated(
            "capability-registry: exactly one squad must be the keyword-less default, "
            f"found {sorted(keywordless)}"
        )

    if routes.thresholds.fast_path_max_tokens >= routes.thresholds.deep_path_min_tokens:
        raise PolicyInvariantViolated(
            "route-policy: fast_path_max_tokens "
            f"({routes.thresholds.fast_path_max_tokens}) must be below "
            f"deep_path_min_tokens ({routes.thresholds.deep_path_min_tokens}), "
            "otherwise the size heuristics overlap and the route is ambiguous"
        )
    fail_safe = routes.normalised_defaults.get("unknown-task-type")
    if fail_safe != "deep":
        raise PolicyInvariantViolated(
            "route-policy: dispatch_defaults.unknown_task_type must be 'deep' "
            f"(the declared fail-safe), found {fail_safe!r}"
        )
    for name, route in routes.routes.items():
        if route.name != name:
            raise PolicyInvariantViolated(
                f"route-policy: route {name!r} carries name {route.name!r}"
            )
        for role in route.agents:
            if role not in agent_ids:
                raise PolicyInvariantViolated(
                    f"route-policy: route {name!r} names agent {role!r}, which is not "
                    "declared in the capability registry"
                )
        for worker in route.worker_types:
            if worker not in fleet_names:
                raise PolicyInvariantViolated(
                    f"route-policy: route {name!r} names worker fleet {worker!r}, "
                    "which is not declared in the capability registry"
                )
        if route.model_tier not in tiers.tiers:
            raise PolicyInvariantViolated(
                f"route-policy: route {name!r} names tier {route.model_tier!r}, which "
                "is not declared in the tier policy"
            )
    for key, value in routes.dispatch_defaults.items():
        if value not in routes.routes:
            raise PolicyInvariantViolated(
                f"route-policy: dispatch_defaults[{key!r}] names route {value!r}, "
                "which is not declared"
            )

    for name, tier in tiers.tiers.items():
        if tier.fallback is not None and tier.fallback not in tiers.tiers:
            raise PolicyInvariantViolated(
                f"tier-policy: tier {name!r} falls back to {tier.fallback!r}, which is "
                "not declared"
            )
    for name in tiers.tier_order:
        if name not in tiers.tiers:
            raise PolicyInvariantViolated(
                f"tier-policy: complexity_to_tier names tier {name!r}, which is not "
                "declared in tiers"
            )
    if len(tiers.tier_order) != len(tiers.tiers):
        raise PolicyInvariantViolated(
            "tier-policy: tier_order covers "
            f"{list(tiers.tier_order)} but tiers declares {sorted(tiers.tiers)}"
        )

    # The escalation ladder must terminate: acyclic, strictly upward, and the
    # top tier must declare the null (human/advisor) terminal.
    terminal = tiers.terminal_tier
    if tiers.tier(terminal).fallback is not None:
        raise PolicyInvariantViolated(
            f"tier-policy: the top tier {terminal!r} declares fallback "
            f"{tiers.tier(terminal).fallback!r}; the ladder must terminate at null "
            "(the human/advisor hand-off)"
        )
    for name in tiers.tier_order:
        seen: List[str] = []
        current: Optional[str] = name
        while current is not None:
            if current in seen:
                raise PolicyInvariantViolated(
                    f"tier-policy: escalation ladder from {name!r} cycles through "
                    f"{current!r}"
                )
            seen.append(current)
            current = tiers.tier(current).fallback
        if seen != sorted(seen, key=tiers.rank):
            raise PolicyInvariantViolated(
                f"tier-policy: escalation ladder from {name!r} is not strictly "
                f"increasing in tier order: {seen}"
            )

    # Escalating must BUY capacity, or the ladder is decorative: each rung has to
    # offer strictly more tokens and time than the tier it climbs from.
    for name in tiers.tier_order:
        spec = tiers.tier(name)
        if spec.fallback is None:
            continue
        higher = tiers.tier(spec.fallback)
        if (
            higher.max_tokens <= spec.max_tokens
            or higher.timeout_seconds <= spec.timeout_seconds
        ):
            raise PolicyInvariantViolated(
                f"tier-policy: escalation from {name!r} to {spec.fallback!r} buys no "
                f"more capacity (max_tokens {spec.max_tokens} -> {higher.max_tokens}, "
                f"timeout {spec.timeout_seconds}s -> {higher.timeout_seconds}s): the "
                "ladder would be decorative"
            )

    for key, value in tiers.task_overrides.items():
        if value not in tiers.tiers:
            raise PolicyInvariantViolated(
                f"tier-policy: task_overrides[{key!r}] names tier {value!r}, which is "
                "not declared"
            )


def load_bundle(policies_dir: Optional[Any] = None) -> PolicyBundle:
    """Load and validate the three policy documents.

    Raises ``PolicyUnavailable`` / ``PolicyMalformed`` (CANNOT-ASSESS) or
    ``PolicyInvariantViolated`` (NOT-OK). Never returns a partially valid
    bundle.
    """
    if yaml is None:
        raise PolicyUnavailable(
            "PyYAML is not installed; the declared policies cannot be parsed"
        )
    directory = Path(policies_dir) if policies_dir else default_policies_dir()
    if not directory.is_dir():
        raise PolicyUnavailable(f"policy directory not found at {directory}")

    schema = load_schema()
    documents: Dict[str, Any] = {}
    for filename, definition in POLICY_FILES:
        label = filename.replace(".yaml", "")
        raw = _read_yaml(directory / filename, label)
        if not isinstance(raw, Mapping):
            raise PolicyMalformed(f"{label}: top level must be a mapping")
        raw = _normalise_dates(dict(raw))
        _validate_document(raw, schema, definition, label)
        documents[definition] = raw

    registry = _build_registry(documents["capability_registry"])
    route_policy = _build_route_policy(documents["route_policy"])
    tier_policy = _build_tier_policy(documents["tier_policy"])
    _check_invariants(registry, route_policy, tier_policy)
    return PolicyBundle(
        capability_registry=registry,
        route_policy=route_policy,
        tier_policy=tier_policy,
        policies_dir=directory,
    )
