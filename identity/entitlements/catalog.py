"""Plan catalog parsing and validation.

The catalog is product configuration (which tier unlocks which features at
which limits), declared as YAML in ``plans/catalog.yaml`` beside this module
and reviewed/versioned/deployed with the code that enforces it (saas-rbac
``entitlements.ts`` doctrine: keeping entitlements in code means adding a
feature flag needs no migration and no per-environment data backfill).

Schema (see ``plans/catalog.yaml`` for a working example)::

    version: 1
    features:                     # the entitlement -> RBAC mapping registry
      - key: budgets              # stable feature key
        kind: feature             # feature | limit
        description: ...
        grants: [budget:read, budget:manage]   # resource:action grants it unlocks
    plans:
      - key: pro                  # commercial tier
        name: Pro
        description: ...
        entitlements:
          - feature: budgets      # must exist in ``features`` above
            enabled: true
            limit: 25             # only for limit-kind features (optional)

Validation is strict and fail-closed: an unknown feature reference, a
duplicate feature key, a duplicate plan key, a malformed permission grant, a
limit on a non-limit feature, or a duplicate plan entry for one feature all
refuse to parse - a catalog that cannot be reasoned about must not load.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from rbac.model import is_permission

from entitlements.model import (
    FEATURE_KIND_FEATURE,
    FEATURE_KIND_LIMIT,
    FEATURE_KINDS,
    FeatureDef,
    Plan,
    PlanCatalog,
    PlanEntitlement,
)

# The catalog shipped with this contract (code-adjacent product config).
_DEFAULT_CATALOG = Path(__file__).resolve().parent / "plans" / "catalog.yaml"

_FEATURE_KEY_PATTERN = "abcdefghijklmnopqrstuvwxyz0123456789_"


def _valid_feature_key(key: str) -> bool:
    return bool(key) and key[0].islower() and all(c in _FEATURE_KEY_PATTERN for c in key)


def parse_catalog(yaml_text: str) -> PlanCatalog:
    """Parse and validate a catalog from YAML text into an immutable PlanCatalog.

    Raises ``ValueError`` on any schema or referential violation (fail closed).
    """
    data = yaml.safe_load(yaml_text)
    if not isinstance(data, dict):
        raise ValueError("catalog YAML must be a mapping with 'features' and 'plans'")

    raw_features = data.get("features")
    if not isinstance(raw_features, list):
        raise ValueError("catalog must declare a 'features' list")

    features: list[FeatureDef] = []
    seen_features: set[str] = set()
    for raw in raw_features:
        if not isinstance(raw, dict):
            raise ValueError("catalog: each feature must be a mapping")
        key = raw.get("key")
        if not isinstance(key, str) or not _valid_feature_key(key):
            raise ValueError(f"catalog: invalid feature key {key!r}")
        if key in seen_features:
            raise ValueError(f"catalog: duplicate feature key {key!r}")
        seen_features.add(key)
        kind = raw.get("kind") or FEATURE_KIND_FEATURE
        if kind not in FEATURE_KINDS:
            raise ValueError(
                f"feature {key!r}: kind must be one of {', '.join(FEATURE_KINDS)}"
            )
        raw_grants = raw.get("grants") or []
        if not isinstance(raw_grants, list):
            raise ValueError(f"feature {key!r}: 'grants' must be a list")
        grants: list[str] = []
        for g in raw_grants:
            if not isinstance(g, str) or not is_permission(g):
                raise ValueError(f"feature {key!r}: invalid grant {g!r}")
            grants.append(g)
        features.append(
            FeatureDef(
                key=key,
                kind=kind,
                description=str(raw.get("description") or ""),
                grants=tuple(grants),
            )
        )
    feature_index = {f.key: f for f in features}

    raw_plans = data.get("plans")
    if not isinstance(raw_plans, list) or not raw_plans:
        raise ValueError("catalog must declare a non-empty 'plans' list")

    plans: list[Plan] = []
    seen_plans: set[str] = set()
    for raw in raw_plans:
        if not isinstance(raw, dict):
            raise ValueError("catalog: each plan must be a mapping")
        key = raw.get("key")
        if not isinstance(key, str) or not key:
            raise ValueError("catalog: each plan needs a non-empty string 'key'")
        if key in seen_plans:
            raise ValueError(f"catalog: duplicate plan key {key!r}")
        seen_plans.add(key)
        raw_entries = raw.get("entitlements")
        if not isinstance(raw_entries, list) or not raw_entries:
            raise ValueError(f"plan {key!r} must declare a non-empty 'entitlements' list")

        entries: list[PlanEntitlement] = []
        seen_in_plan: set[str] = set()
        for entry in raw_entries:
            if not isinstance(entry, dict):
                raise ValueError(f"plan {key!r}: each entitlement must be a mapping")
            feature = entry.get("feature")
            feature_def = feature_index.get(feature)
            if feature_def is None:
                raise ValueError(
                    f"plan {key!r}: entitlement references unknown feature {feature!r}"
                )
            if feature in seen_in_plan:
                raise ValueError(
                    f"plan {key!r}: duplicate entitlement for feature {feature!r}"
                )
            seen_in_plan.add(feature)
            enabled = entry.get("enabled")
            if not isinstance(enabled, bool):
                raise ValueError(
                    f"plan {key!r} feature {feature!r}: 'enabled' must be a boolean"
                )
            limit = entry.get("limit")
            if limit is not None:
                if feature_def.kind != FEATURE_KIND_LIMIT:
                    raise ValueError(
                        f"plan {key!r} feature {feature!r} is {feature_def.kind!r}-kind "
                        "and cannot carry a numeric limit"
                    )
                if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
                    raise ValueError(
                        f"plan {key!r} feature {feature!r}: limit must be a positive "
                        f"integer or null, got {limit!r}"
                    )
            entries.append(
                PlanEntitlement(feature=feature, enabled=enabled, limit=limit)
            )
        plans.append(
            Plan(
                key=key,
                name=str(raw.get("name") or key),
                description=str(raw.get("description") or ""),
                entitlements=tuple(entries),
            )
        )

    return PlanCatalog(
        version=str(data.get("version") or "1"),
        features=tuple(features),
        plans=tuple(plans),
    )


def load_catalog(path: "str | Path") -> PlanCatalog:
    """Load and validate a catalog from a YAML file path."""
    return parse_catalog(Path(path).read_text(encoding="utf-8"))


def load_default_catalog() -> PlanCatalog:
    """Load the catalog shipped beside this module (``plans/catalog.yaml``)."""
    return load_catalog(_DEFAULT_CATALOG)
