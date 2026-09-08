"""Loader + declarative tier-table validation tests (issue #17).

Covers:
- the shipped tiers.yaml and budgets.yaml load and validate
- every task class references real ladder tiers (default <= max)
- guarded task classes can reach the security floor; no class sits below it
- each tier's models are cost-ordered cheapest-first with unique ids
- escalation thresholds reference real ladder tiers
- task-class capability ids CONSUME the registry/profiles catalog vocabulary
  (parity gate: nothing is redefined)
- fail-closed negatives: bad tier refs, bad caps, bad thresholds, malformed
  guardrails all raise ValidationError
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import loader
from loader import (
    ValidationError,
    load_tier_table,
    missing_capability_ids,
    parse_tier_table,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CATALOG_PATH = REPO_ROOT / "registry" / "profiles" / "catalog.yaml"


# ------------------------------------------------------------- shipped files
def test_shipped_tiers_yaml_loads(table) -> None:
    assert table.ladder  # non-empty
    assert [t.key for t in table.ladder] == ["L0", "L1", "L2"]
    assert table.security_floor == "L1"
    assert table.task_classes  # non-empty
    assert table.escalation.thresholds  # non-empty


def test_shipped_budgets_yaml_loads(seeded_enforcer) -> None:
    assert seeded_enforcer.budgets  # non-empty
    assert set(seeded_enforcer.budgets) == {"tenant-acme", "tenant-beta", "tenant-gamma"}


def test_shipped_task_class_tier_refs_are_valid(table) -> None:
    keys = table.keys()
    for cls in table.task_classes.values():
        assert cls.default_tier in keys
        assert cls.max_tier in keys
        assert table.rank(cls.default_tier) <= table.rank(cls.max_tier)


def test_shipped_guarded_classes_can_reach_security_floor(table) -> None:
    floor_rank = table.rank(table.security_floor)
    guarded = [c for c in table.task_classes.values() if c.guardrail is not None]
    assert guarded  # the shipped table has guarded classes
    for cls in guarded:
        assert table.rank(cls.max_tier) >= floor_rank, cls.name


def test_shipped_every_tier_models_cost_ordered_unique(table) -> None:
    for tier in table.ladder:
        costs = [m.cost_per_mtok for m in tier.models]
        assert costs == sorted(costs), tier.key  # cheapest-first
        ids = [m.id for m in tier.models]
        assert len(ids) == len(set(ids)), tier.key  # no duplicate model ids


def test_shipped_escalation_thresholds_reference_real_tiers(table) -> None:
    keys = table.keys()
    for tier_key in table.escalation.thresholds:
        assert tier_key in keys


# ------------------------------------------------- catalog capability parity
def test_task_classes_consume_catalog_capability_ids(table) -> None:
    """Every task class references a real registry/profiles catalog capability.

    The chooser consumes ids defined by the registry lane; it must never invent
    new capability vocabulary.
    """
    assert CATALOG_PATH.is_file(), f"catalog not found at {CATALOG_PATH}"
    missing = missing_capability_ids(table, CATALOG_PATH)
    assert missing == [], f"unknown capability ids consumed: {missing}"


# ------------------------------------------------------------ fail-closed
def _raw_tiers() -> dict:
    return yaml.safe_load(loader.TIERS_PATH.read_text(encoding="utf-8"))


def test_unknown_tier_in_task_class_rejected() -> None:
    data = _raw_tiers()
    data["taskClasses"]["bad-class"] = {
        "capability": "code-author",
        "defaultTier": "L9",
        "maxTier": "L2",
    }
    with pytest.raises(ValidationError, match="unknown ladder tier"):
        parse_tier_table(data)


def test_default_above_max_rejected() -> None:
    data = _raw_tiers()
    data["taskClasses"]["bad-class"] = {
        "capability": "code-author",
        "defaultTier": "L2",
        "maxTier": "L0",
    }
    with pytest.raises(ValidationError, match="ranks above"):
        parse_tier_table(data)


def test_guarded_class_max_below_floor_rejected() -> None:
    data = _raw_tiers()
    data["taskClasses"]["bad-class"] = {
        "capability": "security-review",
        "defaultTier": "L0",
        "maxTier": "L0",
        "guardrail": "security",
    }
    with pytest.raises(ValidationError, match="guardrail"):
        parse_tier_table(data)


def test_unknown_escalation_threshold_rejected() -> None:
    data = _raw_tiers()
    data["escalation"]["thresholds"]["L9"] = 10.0
    with pytest.raises(ValidationError, match="unknown ladder tier"):
        parse_tier_table(data)


def test_invalid_guardrail_tag_rejected() -> None:
    data = _raw_tiers()
    data["taskClasses"]["bad-class"] = {
        "capability": "code-author",
        "defaultTier": "L0",
        "maxTier": "L1",
        "guardrail": "safety",
    }
    with pytest.raises(ValidationError, match="guardrail"):
        parse_tier_table(data)


def test_invalid_model_tier_hint_rejected() -> None:
    data = _raw_tiers()
    data["ladder"]["L0"]["modelTierHint"] = "ultra"
    with pytest.raises(ValidationError, match="modelTierHint"):
        parse_tier_table(data)


def test_empty_ladder_rejected() -> None:
    data = _raw_tiers()
    data["ladder"] = {}
    with pytest.raises(ValidationError, match="ladder"):
        parse_tier_table(data)


def test_load_tier_table_parity_is_importable() -> None:
    """The shipped table reloads from disk identically (deterministic)."""
    table_a = load_tier_table()
    table_b = load_tier_table()
    assert [t.key for t in table_a.ladder] == [t.key for t in table_b.ladder]
    assert set(table_a.task_classes) == set(table_b.task_classes)
