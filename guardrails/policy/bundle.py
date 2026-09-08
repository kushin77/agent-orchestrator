"""PolicyBundle: an ordered, duplicate-free set of policies plus precedence.

A bundle is the unit the gate engine evaluates against.  It is assembled from
validated :class:`Policy` objects (see :mod:`policy.loader` and
:mod:`policy.startup`); the assembler refuses duplicate ids because two
policies with one id would make "which governs?" ambiguous (a silent-shadowing
hazard — AO-GR-4 no-false-green).

Precedence is explicit and documented:

* *Within a bundle* — every applicable rule across every policy is evaluated
  and the strongest decision wins (``BLOCK > WARN > LOG``).  There is no
  hidden ordering among policies; a BLOCK anywhere is a BLOCK.
* *Across bundles* — :meth:`PolicyBundle.merge` overlays bundles in argument
  order so a later bundle's policy *replaces* an earlier one with the same id
  (the base + tenant-overlay pattern used for per-tenant policy inheritance);
  all remaining policies are kept.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from policy.errors import DuplicatePolicyError, UnknownPolicyError
from policy.model import Policy


@dataclass(frozen=True)
class PolicyBundle:
    """An ordered collection of unique policies."""

    policies: tuple[Policy, ...] = ()

    def __post_init__(self) -> None:
        ids = [policy.id for policy in self.policies]
        duplicates = sorted({policy_id for policy_id in ids if ids.count(policy_id) > 1})
        if duplicates:
            raise DuplicatePolicyError(
                f"duplicate policy id(s) in bundle: {', '.join(duplicates)}"
            )

    @property
    def by_id(self) -> dict[str, Policy]:
        return {policy.id: policy for policy in self.policies}

    def get(self, policy_id: str) -> Optional[Policy]:
        return self.by_id.get(policy_id)

    def require(self, policy_id: str) -> Policy:
        policy = self.by_id.get(policy_id)
        if policy is None:
            raise UnknownPolicyError(f"policy {policy_id!r} is not in this bundle")
        return policy

    def __len__(self) -> int:
        return len(self.policies)

    def merge(self, *others: "PolicyBundle") -> "PolicyBundle":
        """Return a new bundle overlaying *others* in argument order.

        When two bundles define the same policy id the *later* bundle's policy
        wins (overlay semantics); unique policies from every bundle are kept.
        """
        merged: dict[str, Policy] = {}
        for bundle in (self, *others):
            for policy in bundle.policies:
                merged[policy.id] = policy
        return PolicyBundle(tuple(merged.values()))


def assemble(policies: Iterable[Policy]) -> PolicyBundle:
    """Assemble an iterable of validated policies into a :class:`PolicyBundle`.

    Raises :class:`DuplicatePolicyError` when two policies share an id.
    """
    return PolicyBundle(tuple(policies))


def policies_from_bundles(bundles: Sequence[PolicyBundle]) -> tuple[Policy, ...]:
    """Flatten many bundles into one tuple (order preserved, no overlay)."""
    policies: list[Policy] = []
    for bundle in bundles:
        policies.extend(bundle.policies)
    return tuple(policies)
