"""Rollout domain model (issue #45) - stages, promotion rules, audiences.

Pure, side-effect-free decision logic for the flag-gated rollout and
deployment pipeline. The stateful engine (``engine.py``) and the honest
offline gate (``checks/check_rollout.py``) build on these primitives; the
declarative rules live in ``stage-model.yaml`` and are loaded through
``StageModel`` here - never hard-coded twice.

The model guarantees (each is tested):

* **Closed stage vocabulary** - only ``off`` / ``canary`` / ``gradual`` /
  ``full`` exist, in a strict order; an unknown stage is a hard error.
* **OFF by default** - every flag is born OFF (AO-GR-6); a flag that
  declares a default of anything else is rejected.
* **Strict-forward promotion** - a flag moves OFF -> CANARY -> GRADUAL ->
  FULL one adjacent step at a time; jumps are rejected.
* **Gated promotion** - every promotion requires green verification evidence
  plus an approval-as-code id; ramping and full promotion add canary-health
  and gradual-complete signals (mirroring the issue #43 ``merge_verdict``
  gate shape, applied to rollout).
* **Deterministic audiences** - a subject is either explicitly targeted or
  consistently hashed into a percentage bucket, so a canary slice is stable
  across calls.
* **Rollback-to-OFF** - a failed canary/gradual health check must revert the
  flag to OFF; the model never returns "stay on" for a failed health signal.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

# Canonical stage vocabulary in promotion order (index == stage order).
STAGE_ORDER_KEYS: Tuple[str, ...] = ("off", "canary", "gradual", "full")


class RolloutStage(str, Enum):
    """The closed vocabulary of rollout stages, ordered by promotion depth."""

    OFF = "off"
    CANARY = "canary"
    GRADUAL = "gradual"
    FULL = "full"

    @property
    def order(self) -> int:
        return STAGE_ORDER_KEYS.index(self.value)

    @property
    def exposed(self) -> bool:
        """True when the surface is visible to any subject."""
        return self.order > 0

    def can_promote_to(self, target: "RolloutStage", *, jump_allowed: bool = False) -> bool:
        """True only for an adjacent forward step (or any jump if allowed)."""
        if jump_allowed:
            return target.order > self.order
        return target.order == self.order + 1

    @classmethod
    def coerce(cls, value: object) -> "RolloutStage":
        """Map a stored token to a stage; unknown tokens are a hard error."""
        if isinstance(value, RolloutStage):
            return value
        if isinstance(value, bool):
            # YAML 1.1 coerces bare `off`/`on` to False/True; both are errors
            # here because stage tokens are quoted strings in the YAML.
            raise ValueError(f"boolean {value!r} is not a rollout stage")
        try:
            return cls(str(value))
        except ValueError as exc:
            raise ValueError(
                f"{value!r} is not a rollout stage "
                f"(closed vocabulary: {', '.join(STAGE_ORDER_KEYS)})"
            ) from exc


# --------------------------------------------------------------------------- #
# Deterministic audience evaluation
# --------------------------------------------------------------------------- #


def stable_bucket(flag: str, subject: str) -> int:
    """Deterministic 0..99 bucket for a subject on a flag (consistent hash).

    The same (flag, subject) pair always lands in the same bucket, so a
    canary/gradual slice is stable across calls and environments (adapted
    from the defragsuite feature-flag engine's hashed-rollout concept).
    """
    digest = hashlib.sha256(f"{flag}:{subject}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100


@dataclass(frozen=True)
class Audience:
    """The audience a flag is exposed to at its current stage."""

    stage: RolloutStage
    rollout_pct: int
    targeted: Tuple[str, ...] = ()

    def exposes(self, flag: str, subject: str) -> bool:
        """Whether ``subject`` sees the surface at this audience.

        OFF never exposes; FULL exposes everyone; canary/gradual expose an
        explicitly targeted subject or a subject whose stable hash bucket is
        below the rollout percentage.
        """
        if not self.stage.exposed:
            return False
        if self.stage == RolloutStage.FULL:
            return True
        if subject in self.targeted:
            return True
        return stable_bucket(flag, subject) < self.rollout_pct


# --------------------------------------------------------------------------- #
# Stage model (loaded from stage-model.yaml)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class StageSpec:
    """One stage's declarative spec."""

    stage: RolloutStage
    order: int
    rollout_pct: int
    exposed: bool
    description: str = ""
    audiences: Tuple[str, ...] = ()
    ramp_steps: Tuple[int, ...] = ()


@dataclass(frozen=True)
class StageModel:
    """The parsed, validated stage model (data from stage-model.yaml)."""

    default_policy: str
    stages: Mapping[RolloutStage, StageSpec]
    jump_allowed: bool
    every_transition_requires: Tuple[str, ...]
    to_canary_requires: Tuple[str, ...]
    to_gradual_requires: Tuple[str, ...]
    to_full_requires: Tuple[str, ...]
    rollback_target: str

    @classmethod
    def load(cls, doc: Mapping[str, object]) -> "StageModel":
        """Build and validate a stage model from a parsed YAML mapping.

        Raises ``ValueError`` on any unknown stage, non-monotonic order,
        out-of-range percentage, or unknown requirement token - the gate
        cannot be a formality.
        """
        if not isinstance(doc, dict):
            raise ValueError("stage-model document must be a mapping")
        known_requirements = {
            "verify_green",
            "approval_code",
            "audit_record",
            "canary_health_ok",
            "gradual_complete",
        }
        stages_raw = doc.get("stages")
        if not isinstance(stages_raw, dict) or not stages_raw:
            raise ValueError("stage-model.stages must be a non-empty mapping")

        stages: Dict[RolloutStage, StageSpec] = {}
        for token, spec_raw in stages_raw.items():
            stage = RolloutStage.coerce(token)
            if not isinstance(spec_raw, dict):
                raise ValueError(f"stage '{token}' spec must be a mapping")
            order = spec_raw.get("order")
            if not isinstance(order, int) or order != stage.order:
                raise ValueError(
                    f"stage '{token}' order must equal its position "
                    f"({stage.order}), got {order!r}"
                )
            pct = spec_raw.get("rollout_pct")
            if not isinstance(pct, int) or not 0 <= pct <= 100:
                raise ValueError(f"stage '{token}' rollout_pct must be 0..100, got {pct!r}")
            if stage.exposed and pct == 0:
                raise ValueError(f"stage '{token}' is exposed but rollout_pct is 0")
            if not stage.exposed and pct != 0:
                raise ValueError(f"stage '{token}' is not exposed but rollout_pct is {pct}")
            ramp = spec_raw.get("ramp")
            ramp_steps: Tuple[int, ...] = ()
            if isinstance(ramp, dict):
                steps = ramp.get("steps")
                if not isinstance(steps, list) or not steps:
                    raise ValueError(f"stage '{token}' ramp.steps must be a non-empty list")
                ramp_steps = tuple(int(s) for s in steps)
                if any(not 0 < s <= 100 for s in ramp_steps):
                    raise ValueError(f"stage '{token}' ramp steps must be 1..100")
                if tuple(sorted(ramp_steps)) != ramp_steps:
                    raise ValueError(f"stage '{token}' ramp steps must be ascending")
            stages[stage] = StageSpec(
                stage=stage,
                order=order,
                rollout_pct=pct,
                exposed=bool(spec_raw.get("exposed", stage.exposed)),
                description=str(spec_raw.get("description", "")),
                audiences=tuple(str(a) for a in spec_raw.get("audiences", [])),
                ramp_steps=ramp_steps,
            )

        # Closed vocabulary: every stage in order must be present.
        for token in STAGE_ORDER_KEYS:
            if RolloutStage(token) not in stages:
                raise ValueError(f"stage-model is missing stage '{token}'")

        rules = doc.get("promotion_rules")
        if not isinstance(rules, dict):
            raise ValueError("stage-model.promotion_rules must be a mapping")
        if rules.get("mode") not in ("strict-forward",):
            raise ValueError("promotion_rules.mode must be 'strict-forward'")
        jump_allowed = bool(rules.get("jump_allowed", False))

        def reqs(key: str) -> Tuple[str, ...]:
            value = rules.get(key, [])
            if not isinstance(value, list):
                raise ValueError(f"promotion_rules.{key} must be a list")
            items = tuple(str(r) for r in value)
            unknown = [r for r in items if r not in known_requirements]
            if unknown:
                raise ValueError(f"promotion_rules.{key} has unknown requirement(s): {unknown}")
            return items

        every = reqs("every_transition_requires")
        to_canary = reqs("to_canary_requires")
        to_gradual = reqs("to_gradual_requires")
        to_full = reqs("to_full_requires")

        rollback = doc.get("rollback_rules")
        rollback_target = "off"
        if isinstance(rollback, dict):
            if rollback.get("mode") not in (None, "auto"):
                raise ValueError("rollback_rules.mode must be 'auto'")
            target = rollback.get("target", "off")
            if RolloutStage.coerce(target) is not RolloutStage.OFF:
                raise ValueError("rollback_rules.target must be 'off'")
            rollback_target = str(target)

        return cls(
            default_policy=str(doc.get("default_policy", "off")),
            stages=stages,
            jump_allowed=jump_allowed,
            every_transition_requires=every,
            to_canary_requires=to_canary,
            to_gradual_requires=to_gradual,
            to_full_requires=to_full,
            rollback_target=rollback_target,
        )

    def spec(self, stage: RolloutStage) -> StageSpec:
        if stage not in self.stages:
            raise ValueError(f"stage {stage.value!r} is not part of the model")
        return self.stages[stage]

    def target_requirements(self, target: RolloutStage) -> Tuple[str, ...]:
        """Per-target signal requirements (every_transition always applies)."""
        if target == RolloutStage.CANARY:
            return self.to_canary_requires
        if target == RolloutStage.GRADUAL:
            return self.to_gradual_requires
        if target == RolloutStage.FULL:
            return self.to_full_requires
        return ()


# --------------------------------------------------------------------------- #
# Flag state and the promotion gate
# --------------------------------------------------------------------------- #


@dataclass
class FlagState:
    """One flag's current rollout state."""

    name: str
    stage: RolloutStage = RolloutStage.OFF
    rollout_pct: int = 0
    targeted: List[str] = field(default_factory=list)

    def audience(self) -> Audience:
        return Audience(stage=self.stage, rollout_pct=self.rollout_pct, targeted=tuple(self.targeted))

    def to_doc(self) -> Dict[str, object]:
        return {
            "stage": self.stage.value,
            "rollout_pct": self.rollout_pct,
            "targeted": list(self.targeted),
        }

    @classmethod
    def from_doc(cls, name: str, doc: Mapping[str, object]) -> "FlagState":
        if not isinstance(doc, dict):
            raise ValueError(f"flag '{name}' state must be a mapping")
        stage = RolloutStage.coerce(doc.get("stage"))
        pct = doc.get("rollout_pct", 0)
        if not isinstance(pct, int) or not 0 <= pct <= 100:
            raise ValueError(f"flag '{name}' rollout_pct must be 0..100, got {pct!r}")
        targeted_raw = doc.get("targeted", [])
        if not isinstance(targeted_raw, list) or not all(isinstance(t, str) for t in targeted_raw):
            raise ValueError(f"flag '{name}' targeted must be a list of strings")
        return cls(name=name, stage=stage, rollout_pct=pct, targeted=list(targeted_raw))


@dataclass(frozen=True)
class PromotionSignals:
    """The gate signals a promotion carries (all are mechanical)."""

    verify_green: bool = False
    approval_id: Optional[str] = None
    canary_health_ok: Optional[bool] = None
    gradual_complete: bool = False

    def meets(self, requirement: str) -> bool:
        if requirement == "verify_green":
            return self.verify_green
        if requirement == "approval_code":
            return bool(self.approval_id)
        if requirement == "audit_record":
            return True  # the engine always audits; checked by construction
        if requirement == "canary_health_ok":
            return self.canary_health_ok is True
        if requirement == "gradual_complete":
            return self.gradual_complete
        return False


@dataclass(frozen=True)
class PromotionVerdict:
    """The promotion gate's decision plus the unmet reasons (empty = allowed)."""

    allowed: bool
    reasons: Tuple[str, ...] = ()

    @property
    def blocked(self) -> bool:
        return not self.allowed


def check_promotion(
    model: StageModel,
    flag: FlagState,
    target: RolloutStage,
    signals: PromotionSignals,
) -> PromotionVerdict:
    """Decide whether ``flag`` may promote to ``target`` and why not.

    Mirrors the issue #43 ``merge_verdict`` shape: every condition is
    mechanical and the ordered reasons list is empty exactly when permitted.
    """
    reasons: List[str] = []
    if target.order <= flag.stage.order:
        reasons.append(f"cannot promote {flag.stage.value} -> {target.value} (not forward)")
    elif not flag.stage.can_promote_to(target, jump_allowed=model.jump_allowed):
        reasons.append(f"promotion {flag.stage.value} -> {target.value} is not an allowed step")
    if not reasons:
        for requirement in (*model.every_transition_requires, *model.target_requirements(target)):
            if requirement == "audit_record":
                continue  # engine-level guarantee, audited on every transition
            if not signals.meets(requirement):
                reasons.append(f"missing gate signal: {requirement}")
    return PromotionVerdict(allowed=not reasons, reasons=tuple(reasons))


def rollback_decision(model: StageModel, flag: FlagState, health_ok: bool) -> Optional[RolloutStage]:
    """The stage a flag must move to after a health observation.

    A failed health check on any exposed stage must revert the flag to OFF
    (the declared rollback target) - the model never returns "stay on" for a
    failed signal, so a canary failure can never silently leave the flag on.
    An OFF flag already at rest stays OFF (returns ``None`` = no move).
    """
    if not flag.stage.exposed:
        return None
    if health_ok:
        return None
    return RolloutStage.coerce(model.rollback_target)


# --------------------------------------------------------------------------- #
# Declarative validation helpers (used by the gate and tests)
# --------------------------------------------------------------------------- #


def validate_rollout_state_doc(doc: Mapping[str, object]) -> List[str]:
    """Errors for a rollout-state document; every flag must default OFF.

    A flag whose current stage is anything but ``off`` (or whose percentage is
    nonzero while off) is a finding: new surfaces cannot ship on by accident.
    """
    errors: List[str] = []
    if not isinstance(doc, dict):
        return ["rollout-state document must be a mapping"]
    if doc.get("schema_version") != 1:
        errors.append("rollout-state schema_version must be 1")
    flags = doc.get("flags")
    if not isinstance(flags, dict) or not flags:
        errors.append("rollout-state.flags must be a non-empty mapping")
        return errors
    for name, raw in flags.items():
        try:
            state = FlagState.from_doc(str(name), raw if isinstance(raw, dict) else {})
        except ValueError as exc:
            errors.append(f"flag '{name}': {exc}")
            continue
        if state.stage is not RolloutStage.OFF:
            errors.append(f"flag '{name}' must default to off, got '{state.stage.value}'")
        if state.stage is RolloutStage.OFF and state.rollout_pct != 0:
            errors.append(f"flag '{name}' is off but rollout_pct is {state.rollout_pct}")
    return errors


def validate_go_live_plan_doc(
    doc: Mapping[str, object],
    known_flags: Sequence[str],
    model: StageModel,
) -> List[str]:
    """Errors for a go-live plan document (phase coverage + flag resolution).

    Asserts the plan covers every phase 0-8, every surface's flag resolves to
    a known flag, and every declared go-live stage is in the closed model.
    """
    errors: List[str] = []
    if not isinstance(doc, dict):
        return ["go-live plan document must be a mapping"]
    phases = doc.get("phases")
    if not isinstance(phases, dict):
        return ["go-live plan phases must be a mapping"]
    covered = set()
    for phase, body in phases.items():
        if not isinstance(body, dict):
            errors.append(f"phase '{phase}' body must be a mapping")
            continue
        surfaces = body.get("surfaces")
        if not isinstance(surfaces, list) or not surfaces:
            errors.append(f"phase '{phase}' must declare at least one surface")
            continue
        covered.add(str(phase))
        for surface in surfaces:
            if not isinstance(surface, dict):
                errors.append(f"phase '{phase}' has a non-mapping surface")
                continue
            flag = surface.get("flag")
            stage = surface.get("go_live_stage")
            if not isinstance(flag, str) or flag not in known_flags:
                errors.append(f"phase '{phase}' flag {flag!r} does not resolve to a known flag")
            if stage is not None:
                try:
                    RolloutStage.coerce(stage)
                except ValueError as exc:
                    errors.append(f"phase '{phase}' flag {flag!r}: {exc}")
    missing = [p for p in ("0", "1", "2", "3", "4", "5", "6", "7", "8") if p not in covered]
    if missing:
        errors.append(f"go-live plan does not cover phase(s): {', '.join(missing)}")
    return errors
