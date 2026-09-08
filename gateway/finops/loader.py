#!/usr/bin/env python3
"""FinOps model-tier table loader and data model (issue #17).

Loads ``gateway/finops/tiers.yaml`` into a validated ``TierTable``. The table
is the declarative contract behind the model chooser (``chooser.py``): the
L0/L1/L2 cheapest-capable ladder, per-task-class default tier and per-task-type
escalation cap, the security floor, and the difficulty thresholds that drive
escalation.

The table CONSUMES ids already defined by earlier lanes and never redefines
them:

- ``modelTierHint`` low|med|high (registry/prompts contract) and the
  ``registryTier`` LOW/MED/HIGH registry profile tiers (catalog.yaml) are
  carried per ladder tier.
- each task-class ``capability`` references a registry/profiles catalog
  capability id.

Validation is fail-closed: an unknown ladder tier, an unknown task-class tier
reference, a ``defaultTier`` ranked above its ``maxTier``, or a guarded task
class whose ``maxTier`` sits below the security floor all raise
``ValidationError`` instead of being silently accepted.

Standalone module: no cross-package imports; the pytest bootstrap
(tests/conftest.py) inserts this directory on ``sys.path`` so modules import
plainly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml  # type: ignore

PKG_DIR = Path(__file__).resolve().parent
TIERS_PATH = PKG_DIR / "tiers.yaml"

# Guardrail tags that force a task class to the security floor (or above).
GUARDRAIL_TAGS = ("security", "iac", "governance")
_MODEL_ROLE_DEFAULT = "fallback"
_MODEL_HINTS = ("low", "med", "high")


class ValidationError(Exception):
    """Raised when a tier-table definition is invalid (fail closed)."""


@dataclass(frozen=True)
class ModelSpec:
    """A concrete commercial model that can serve a ladder tier."""

    id: str
    provider: str
    cost_per_mtok: float
    role: str  # primary | fallback (informational; selection is cost-ordered)


@dataclass(frozen=True)
class LadderTier:
    """One rung of the cheapest-capable ladder (L0/L1/L2)."""

    key: str  # L0/L1/L2
    label: str
    model_tier_hint: str  # low|med|high (registry/prompts vocabulary)
    registry_tier: str  # LOW/MED/HIGH (registry/profiles vocabulary)
    description: str
    models: Tuple[ModelSpec, ...]  # ordered cheapest-first

    @property
    def cheapest(self) -> ModelSpec:
        """Cheapest candidate (head of the cost-ordered list)."""
        return self.models[0]


@dataclass(frozen=True)
class TaskClass:
    """A task class and its FinOps routing policy."""

    name: str
    capability: str  # registry/profiles catalog capability id (consumed)
    default_tier: str  # cheapest capable tier for a typical task
    max_tier: str  # per-task-type escalation cap
    guardrail: Optional[str] = None  # security|iac|governance


@dataclass(frozen=True)
class EscalationConfig:
    """Difficulty thresholds that drive tier escalation."""

    thresholds: Dict[str, float]  # ladder key -> score at which it is exceeded


def _rank_of(keys: Tuple[str, ...], key: str) -> int:
    """Index of ``key`` in an ordered tier-key tuple (0 = cheapest)."""
    return keys.index(key)


@dataclass
class TierTable:
    """Validated FinOps model-tier table."""

    ladder: Tuple[LadderTier, ...]  # ascending by cost/rank
    task_classes: Dict[str, TaskClass]
    security_floor: str
    escalation: EscalationConfig
    schema_version: Optional[int] = None

    # caches -----------------------------------------------------------------
    _by_key: Dict[str, LadderTier] = field(default_factory=dict, repr=False)
    _index: Dict[str, int] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._by_key = {t.key: t for t in self.ladder}
        self._index = {t.key: i for i, t in enumerate(self.ladder)}

    # -- lookups -------------------------------------------------------------
    def tier(self, key: str) -> LadderTier:
        if key not in self._by_key:
            raise ValidationError(f"unknown ladder tier: {key!r}")
        return self._by_key[key]

    def rank(self, key: str) -> int:
        if key not in self._index:
            raise ValidationError(f"unknown ladder tier: {key!r}")
        return self._index[key]

    def keys(self) -> Tuple[str, ...]:
        return tuple(t.key for t in self.ladder)

    def lowest_tier(self) -> str:
        return self.ladder[0].key

    def highest_tier(self) -> str:
        return self.ladder[-1].key

    def task_class(self, name: str) -> TaskClass:
        if name not in self.task_classes:
            raise ValidationError(f"unknown task class: {name!r}")
        return self.task_classes[name]

    # -- ordering helpers -----------------------------------------------------
    def next_tier(self, key: str) -> Optional[str]:
        """The tier immediately above ``key``, or None if already at the top."""
        idx = self.rank(key)
        if idx + 1 >= len(self.ladder):
            return None
        return self.ladder[idx + 1].key

    def prev_tier(self, key: str) -> Optional[str]:
        """The tier immediately below ``key``, or None if already at the bottom."""
        idx = self.rank(key)
        if idx <= 0:
            return None
        return self.ladder[idx - 1].key

    def higher(self, a: str, b: str) -> str:
        """The higher-ranked of two tier keys (used for floors/caps)."""
        return a if self.rank(a) >= self.rank(b) else b

    def threshold(self, key: str) -> float:
        """Difficulty score at/above which ``key`` is no longer sufficient."""
        return self.escalation.thresholds.get(key, float("inf"))

    # -- model selection helpers ---------------------------------------------
    def healthy_models(
        self, key: str, is_healthy: Callable[[str], bool]
    ) -> Tuple[ModelSpec, ...]:
        """Candidates of a tier (cheapest-first) filtered by health signal."""
        tier = self.tier(key)
        if is_healthy is None:
            return tier.models
        return tuple(m for m in tier.models if is_healthy(m.id))

    def cheapest_model(self, key: str) -> ModelSpec:
        """Cheapest model that can serve a tier (ignores health)."""
        return self.tier(key).cheapest

    def estimate_cost(self, model: ModelSpec, tokens: int) -> float:
        """Estimated USD cost of one call to ``model`` at ``tokens`` tokens."""
        if tokens < 0:
            raise ValidationError(f"tokens must be >= 0, got {tokens}")
        return round(model.cost_per_mtok * tokens / 1_000_000.0, 6)


# --------------------------------------------------------------------------- #
# Parsing / validation
# --------------------------------------------------------------------------- #
def _require(mapping: Dict[str, Any], section: str, field_name: str) -> Any:
    if field_name not in mapping:
        raise ValidationError(f"{section}: missing required field {field_name!r}")
    return mapping[field_name]


def parse_tier_table(data: Dict[str, Any]) -> TierTable:
    """Parse and validate a tier-table mapping (from tiers.yaml or a test).

    Raises ``ValidationError`` on any structural problem — fail closed.
    """
    if not isinstance(data, dict):
        raise ValidationError("tier table must be a mapping")

    raw_ladder = _require(data, "table", "ladder")
    raw_classes = _require(data, "table", "taskClasses")
    raw_security = _require(data, "table", "security")
    raw_escalation = _require(data, "table", "escalation")

    # -- ladder ---------------------------------------------------------------
    if not isinstance(raw_ladder, dict) or not raw_ladder:
        raise ValidationError("ladder must be a non-empty mapping of tiers")
    ladder: List[LadderTier] = []
    for key, cfg in raw_ladder.items():
        if not isinstance(cfg, dict):
            raise ValidationError(f"ladder.{key}: tier config must be a mapping")
        models_raw = _require(cfg, f"ladder.{key}", "models")
        if not isinstance(models_raw, list) or not models_raw:
            raise ValidationError(f"ladder.{key}.models: must be a non-empty list")
        models: List[ModelSpec] = []
        seen_ids = set()
        for m in models_raw:
            if not isinstance(m, dict):
                raise ValidationError(f"ladder.{key}.models: entries must be mappings")
            mid = _require(m, f"ladder.{key}.models", "id")
            provider = _require(m, f"ladder.{key}.models", "provider")
            cost = _require(m, f"ladder.{key}.models", "costPerMTok")
            role = m.get("role", _MODEL_ROLE_DEFAULT)
            if not isinstance(cost, (int, float)) or cost < 0:
                raise ValidationError(
                    f"ladder.{key}.models[{mid!r}].costPerMTok must be a number >= 0"
                )
            if not isinstance(mid, str) or not isinstance(provider, str):
                raise ValidationError(
                    f"ladder.{key}.models: id and provider must be strings"
                )
            if mid in seen_ids:
                raise ValidationError(f"ladder.{key}: duplicate model id {mid!r}")
            seen_ids.add(mid)
            models.append(
                ModelSpec(id=mid, provider=provider, cost_per_mtok=float(cost), role=role)
            )
        models.sort(key=lambda m: m.cost_per_mtok)  # cheapest-first: the contract
        hint = _require(cfg, f"ladder.{key}", "modelTierHint")
        if hint not in _MODEL_HINTS:
            raise ValidationError(
                f"ladder.{key}.modelTierHint must be one of {_MODEL_HINTS}, got {hint!r}"
            )
        ladder.append(
            LadderTier(
                key=key,
                label=_require(cfg, f"ladder.{key}", "label"),
                model_tier_hint=hint,
                registry_tier=_require(cfg, f"ladder.{key}", "registryTier"),
                description=cfg.get("description", ""),
                models=tuple(models),
            )
        )

    keys = tuple(t.key for t in ladder)

    # -- security floor ---------------------------------------------------------
    if not isinstance(raw_security, dict):
        raise ValidationError("security must be a mapping")
    floor = _require(raw_security, "security", "floorTier")
    if floor not in keys:
        raise ValidationError(f"security.floorTier must be a ladder tier, got {floor!r}")

    # -- escalation thresholds ---------------------------------------------------
    if not isinstance(raw_escalation, dict):
        raise ValidationError("escalation must be a mapping")
    raw_thresholds = _require(raw_escalation, "escalation", "thresholds")
    if not isinstance(raw_thresholds, dict):
        raise ValidationError("escalation.thresholds must be a mapping")
    thresholds: Dict[str, float] = {}
    for tkey, tval in raw_thresholds.items():
        if tkey not in keys:
            raise ValidationError(f"escalation.thresholds: unknown ladder tier {tkey!r}")
        if not isinstance(tval, (int, float)) or tval < 0 or tval > 100:
            raise ValidationError(
                f"escalation.thresholds.{tkey}: must be a score in [0, 100]"
            )
        thresholds[tkey] = float(tval)

    # -- task classes --------------------------------------------------------------
    if not isinstance(raw_classes, dict) or not raw_classes:
        raise ValidationError("taskClasses must be a non-empty mapping")
    classes: Dict[str, TaskClass] = {}
    for name, cfg in raw_classes.items():
        if not isinstance(cfg, dict):
            raise ValidationError(f"taskClasses.{name}: config must be a mapping")
        default_tier = _require(cfg, f"taskClasses.{name}", "defaultTier")
        max_tier = _require(cfg, f"taskClasses.{name}", "maxTier")
        guardrail = cfg.get("guardrail")
        if guardrail is not None and guardrail not in GUARDRAIL_TAGS:
            raise ValidationError(
                f"taskClasses.{name}.guardrail must be one of {GUARDRAIL_TAGS}, "
                f"got {guardrail!r}"
            )
        for ref, fname in ((default_tier, "defaultTier"), (max_tier, "maxTier")):
            if ref not in keys:
                raise ValidationError(
                    f"taskClasses.{name}.{fname}: unknown ladder tier {ref!r}"
                )
        if _rank_of(keys, default_tier) > _rank_of(keys, max_tier):
            raise ValidationError(
                f"taskClasses.{name}: defaultTier {default_tier!r} ranks above "
                f"maxTier {max_tier!r}"
            )
        # A guarded class must be able to reach at least the security floor.
        if guardrail is not None and _rank_of(keys, max_tier) < _rank_of(keys, floor):
            raise ValidationError(
                f"taskClasses.{name}: guardrail {guardrail!r} requires maxTier to "
                f"be at or above security.floorTier {floor!r}"
            )
        classes[name] = TaskClass(
            name=name,
            capability=_require(cfg, f"taskClasses.{name}", "capability"),
            default_tier=default_tier,
            max_tier=max_tier,
            guardrail=guardrail,
        )

    return TierTable(
        ladder=tuple(ladder),
        task_classes=classes,
        security_floor=floor,
        escalation=EscalationConfig(thresholds=thresholds),
        schema_version=data.get("schemaVersion"),
    )


def load_tier_table(path: Path = TIERS_PATH) -> TierTable:
    """Load and validate the tier table from a YAML file."""
    if not path.is_file():
        raise ValidationError(f"tier table not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValidationError(f"{path}: top-level YAML must be a mapping")
    return parse_tier_table(data)


# --------------------------------------------------------------------------- #
# Contract parity with the registry/profiles catalog (read-only consumption)
# --------------------------------------------------------------------------- #
def catalog_capability_ids(catalog_path: Path) -> List[str]:
    """Read the capability ids declared in a registry/profiles catalog.yaml.

    The catalog file is owned by the registry lane; this helper only READS it
    (the file exists on master) so tests can assert the chooser's task classes
    consume real capability ids rather than inventing new ones.
    """
    with open(catalog_path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict) or not isinstance(data.get("capabilities"), dict):
        raise ValidationError(f"{catalog_path}: no capabilities mapping found")
    return sorted(data["capabilities"].keys())


def missing_capability_ids(table: TierTable, catalog_path: Path) -> List[str]:
    """Task-class capability ids not present in the registry catalog."""
    known = set(catalog_capability_ids(catalog_path))
    return sorted(
        cls.capability for cls in table.task_classes.values() if cls.capability not in known
    )
