"""Policy document model: :class:`Policy` and :class:`PolicyRule`.

Policies are authored in YAML (one policy per file, or a ``policies:``
container), validated against ``schema/policy.schema.json`` plus semantic
checks in the startup gate, and turned into these immutable objects by the
loader.  Rules are the executable unit of the gate engine: an
action/subject/tenant scope plus an optional condition that decides
BLOCK/WARN/LOG for the action when the scope and condition hold.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from policy.decision import DecisionLevel

#: Level applied by the engine when a policy governs the action but no rule
#: fires and the policy does not declare its own ``default``.
DEFAULT_POLICY_DECISION = DecisionLevel.LOG


@dataclass(frozen=True)
class PolicyRule:
    """One declarative rule inside a policy.

    ``actions`` is a non-empty list of glob patterns matched against the
    action name (``fnmatch`` semantics, e.g. ``model.call``, ``tool.use``,
    ``*``).  ``subjects``/``tenants`` are optional glob lists that further
    scope the rule; an empty list means "any".  ``condition`` is an optional
    predicate tree consumed by :mod:`policy.conditions`.  ``reason`` is a
    human/audit explanation; a BLOCK rule must carry one (unsafe otherwise).
    """

    id: str
    actions: tuple[str, ...]
    decision: DecisionLevel
    reason: str
    condition: Optional[Mapping[str, Any]] = None
    subjects: tuple[str, ...] = ()
    tenants: tuple[str, ...] = ()


@dataclass(frozen=True)
class Policy:
    """An immutable, validated policy.

    ``controls`` lists the registry control ids that must all be active for
    the policy to be enforced (flag-gated rollout, AO-GR-6); an empty tuple
    means the policy is enforced whenever ``enabled`` is true.  ``default``
    is the decision contributed when the policy governs the action but no
    rule fires (deny-list policies default to ``log``; allow-list policies
    default to ``block``).  ``source`` records the file it was loaded from
    for audit/provenance.
    """

    id: str
    version: int
    name: str
    description: str
    default: DecisionLevel
    enabled: bool
    controls: tuple[str, ...]
    rules: tuple[PolicyRule, ...]
    source: str = ""
