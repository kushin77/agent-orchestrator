"""Gate engine — evaluate an action against the active policy bundle.

Issue #26 acceptance #2: :meth:`PolicyEngine.evaluate` turns
``(action, subject, tenant, context)`` into an honest
``BLOCK / WARN / LOG`` :class:`DecisionResult` with structured evidence, and
every decision is audit-logged (a BLOCK always is).

Decision contract (documented, fail-closed by default — AO-GR-19):

1. **Uncovered action.**  When no *active* policy governs the action, the
   engine returns the configured ``uncovered_decision`` — ``BLOCK`` by
   default (deny-by-default: an ungoverned action must not sail through).
   A deployment that wants to observe while it authors policies sets
   ``uncovered_decision=log`` explicitly — "unless configured otherwise".

2. **Covered action.**  Every applicable rule across every active policy is
   evaluated in declaration order.  A rule that fires contributes its
   decision; the **strongest** decision wins (``BLOCK > WARN > LOG``).  When
   a policy governs the action but none of its rules fire, the policy's
   ``default`` (``log`` unless declared) is contributed.  A BLOCK anywhere is
   a BLOCK.

3. **Fail-closed on evaluation error (no-false-green, AO-GR-4).**  An
   unparseable/unusable rule — an unknown operator, an invalid regex, a
   required context path that is absent — raises inside evaluation and the
   engine converts it into a BLOCK with the error attached to the evidence.
   Absence of a required attribute is never a silent pass.

*Active policy* means ``policy.enabled`` is true and every control id listed
in ``policy.controls`` is registered AND enabled in the
:class:`~policy.controls.ControlRegistry` (default OFF, AO-GR-6).  A control
that is not registered makes its policy inactive (and the startup gate rejects
that bundle/registry pairing so it cannot ship).
"""

from __future__ import annotations

import fnmatch
import re
from typing import Any, Mapping, Optional, Sequence

from policy.audit import AuditLog, AuditRecord, InMemoryAuditLog, utc_now_iso
from policy.bundle import PolicyBundle
from policy.conditions import ConditionError, evaluate_condition, resolve_path
from policy.controls import ControlRegistry
from policy.decision import DecisionLevel, DecisionResult, RuleHit, strongest
from policy.model import Policy, PolicyRule

_TOKEN_RE = re.compile(r"\{([^{}]+)\}")


def _scope_matches(value: Optional[str], patterns: Sequence[str]) -> bool:
    """True when *patterns* is empty or *value* matches one of the globs."""
    if not patterns:
        return True
    if value is None:
        return False
    return any(fnmatch.fnmatch(value, pattern) for pattern in patterns)


def _rule_scope_match(rule: PolicyRule, action: str, subject: Optional[str], tenant: Optional[str]) -> bool:
    """Does this rule's declared scope cover the action/subject/tenant?"""
    if not any(fnmatch.fnmatch(action, pattern) for pattern in rule.actions):
        return False
    if not _scope_matches(subject, rule.subjects):
        return False
    if not _scope_matches(tenant, rule.tenants):
        return False
    return True


def _policy_governs(policy: Policy, action: str, subject: Optional[str], tenant: Optional[str]) -> bool:
    """A policy governs the action when at least one rule's scope covers it."""
    return any(_rule_scope_match(rule, action, subject, tenant) for rule in policy.rules)


class PolicyEngine:
    """Deterministic, offline gate engine over a :class:`PolicyBundle`."""

    def __init__(
        self,
        bundle: PolicyBundle,
        controls: Optional[ControlRegistry] = None,
        uncovered_decision: Any = DecisionLevel.BLOCK,
        audit_log: Optional[AuditLog] = None,
    ) -> None:
        self.bundle = bundle
        self.controls = controls if controls is not None else ControlRegistry()
        self.uncovered_decision = DecisionLevel.from_token(uncovered_decision)
        # Every engine audits by default so a BLOCK is never silently dropped.
        self.audit_log: AuditLog = audit_log if audit_log is not None else InMemoryAuditLog()
        self._sequence = 0

    # ------------------------------------------------------------------ #
    # policy activation
    # ------------------------------------------------------------------ #
    def active_policies(self) -> tuple[Policy, ...]:
        """Enabled policies whose controls are all registered and active."""
        active: list[Policy] = []
        for policy in self.bundle.policies:
            if not policy.enabled:
                continue
            if any(not self.controls.is_active(control_id) for control_id in policy.controls):
                continue
            active.append(policy)
        return tuple(active)

    # ------------------------------------------------------------------ #
    # evaluation
    # ------------------------------------------------------------------ #
    def evaluate(
        self,
        action: str,
        subject: Optional[str] = None,
        tenant: Optional[str] = None,
        context: Optional[Mapping[str, Any]] = None,
    ) -> DecisionResult:
        """Evaluate one action and return a decision with structured evidence.

        The returned decision is also written to the engine's audit log.
        """
        context = dict(context or {})
        applicable = [
            policy
            for policy in self.active_policies()
            if _policy_governs(policy, action, subject, tenant)
        ]

        if not applicable:
            result = DecisionResult(
                decision=self.uncovered_decision,
                action=action,
                subject=subject,
                tenant=tenant,
                uncovered=True,
            )
        else:
            result = self._evaluate_applicable(action, subject, tenant, context, applicable)

        self._audit(result)
        return result

    def _evaluate_applicable(
        self,
        action: str,
        subject: Optional[str],
        tenant: Optional[str],
        context: dict[str, Any],
        applicable: Sequence[Policy],
    ) -> DecisionResult:
        interp_ctx = {"action": action, "subject": subject, "tenant": tenant, **context}

        hits: list[RuleHit] = []
        consulted: list[str] = []
        fired_policies: set[str] = set()
        error: Optional[str] = None

        for policy in applicable:
            consulted.append(policy.id)
            for rule in policy.rules:
                if not _rule_scope_match(rule, action, subject, tenant):
                    continue
                try:
                    fires = rule.condition is None or evaluate_condition(rule.condition, context)
                except ConditionError as exc:
                    # Fail closed: we could not evaluate this rule, so we
                    # cannot honestly allow the action.
                    if error is None:
                        error = f"{policy.id}/{rule.id}: {exc}"
                    continue
                if fires:
                    fired_policies.add(policy.id)
                    hits.append(
                        RuleHit(
                            policy_id=policy.id,
                            policy_version=policy.version,
                            rule_id=rule.id,
                            decision=rule.decision,
                            reason=_interpolate(rule.reason, interp_ctx),
                            action=action,
                            subject=subject,
                            tenant=tenant,
                        )
                    )

        if error is not None:
            decision = DecisionLevel.BLOCK
        else:
            candidates = [hit.decision for hit in hits]
            for policy in applicable:
                if policy.id not in fired_policies:
                    candidates.append(policy.default)
            decision = strongest(candidates) or DecisionLevel.LOG

        return DecisionResult(
            decision=decision,
            action=action,
            subject=subject,
            tenant=tenant,
            uncovered=False,
            error=error,
            matched_rules=tuple(hits),
            policies_consulted=tuple(consulted),
        )

    # ------------------------------------------------------------------ #
    # audit
    # ------------------------------------------------------------------ #
    def _audit(self, result: DecisionResult) -> None:
        self._sequence += 1
        record = AuditRecord(
            sequence=self._sequence,
            timestamp=utc_now_iso(),
            decision=result.decision.value,
            action=result.action,
            outcome="blocked" if result.blocked else "allowed",
            subject=result.subject,
            tenant=result.tenant,
            policy_ids=tuple(hit.policy_id for hit in result.matched_rules)
            or result.policies_consulted,
            rule_ids=tuple(hit.rule_id for hit in result.matched_rules),
            reason=result.matched_rules[0].reason if result.matched_rules else None,
            error=result.error,
            evidence=result.to_dict(),
        )
        self.audit_log.append(record)


def _interpolate(template: str, context: Mapping[str, Any]) -> str:
    """Substitute ``{path}`` tokens in a reason from the merged context.

    Present paths are substituted; a token whose path is absent is left as-is
    (a block that reached evaluation always has its required paths present —
    absence fails closed before a hit is recorded).
    """

    def replace(match: re.Match[str]) -> str:
        path = match.group(1)
        present, value = resolve_path(context, path)
        if not present:
            return match.group(0)
        if isinstance(value, str):
            return value
        return str(value)

    return _TOKEN_RE.sub(replace, template)
