"""SME-squad routing engine: task -> route/tier decision -> dispatch outcome.

Pure, offline, deterministic: stdlib + PyYAML only, no network, no clock, no
global state. The router reads the declared policies in
``gateway/sme-routing/policies/`` (validated by ``smeroute_config``) and never
hard-codes a policy value.

What it decides
---------------
``route(task)`` answers, from declared data:

* which **domain** the task belongs to and therefore which **SME** handles it
  (``classify_domain`` / ``classify_sme``) and which authoritative **module**
  owns that domain;
* which **squad** (board lens) carries it (``classify_squad``);
* which **route** (``fast`` / ``deep`` / ``strict``), **agent chain** and
  **worker fleets** the task takes;
* which **model tier** (``flash`` / ``pro`` / ``auditor``) it runs on and that
  tier's **timeout / token cap / fallback**.

FAIL-SAFE: a task with no positive signal -- including a task type the route
policy does not declare -- routes to the **deep** path, never to the cheap
path. ``Decision.fail_safe`` records that the default was applied, and the
route policy's own ``dispatch_defaults.unknown_task_type`` (enforced to be
``deep`` by the loader) is what supplies it.

Cost controls
-------------
``dispatch(task)`` enforces the chosen tier's caps. Exceeding a cap is an
explicit outcome, never a silent pass:

* ``escalate=False`` -- the breach is a hard ``refused`` outcome (rc 1). The
  tier's fallback is reported, but nothing is climbed.
* ``escalate=True``  -- the router climbs the declared ladder (flash -> pro ->
  auditor). Each rung is recorded in ``Outcome.attempts``. When the top tier's
  caps are also breached there is no higher tier, and the escalation
  TERMINATES at the distinct ``human_advisor`` outcome -- not an exception, and
  never an unbounded loop (the ladder is validated acyclic and strictly
  increasing, and the loop is bounded by the tier count).

Tri-state: an unusable request (unknown key, out-of-range complexity) cannot be
assessed -- ``TaskInvalid`` -- and the CLI maps that to rc 2, exactly like a
malformed policy.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from smeroute_config import (  # noqa: E402  (path arranged above)
    CapabilityRegistry,
    PolicyBundle,
    PolicyInvariantViolated,
    RoutePolicy,
    TierPolicy,
    load_bundle,
    normalise_task_type,
)

__all__ = [
    "TaskInvalid",
    "DISPATCHED",
    "REFUSED",
    "HUMAN_ADVISOR",
    "TierCaps",
    "Decision",
    "Attempt",
    "Outcome",
    "Router",
    "load_router",
]

DISPATCHED = "dispatched"
REFUSED = "refused"
HUMAN_ADVISOR = "human_advisor"

_KNOWN_TASK_KEYS = frozenset(
    {"id", "type", "text", "complexity", "risk", "tokens", "timeout_seconds", "metadata"}
)
_RISK_LEVELS = ("low", "high")


class TaskInvalid(ValueError):
    """The task cannot be assessed (unknown key, malformed value) -- rc 2."""


@dataclass(frozen=True)
class TierCaps:
    """The cost controls the router enforces for the chosen tier."""

    tier: str
    models: Tuple[str, ...]
    timeout_seconds: int
    max_tokens: int
    fallback: Optional[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tier": self.tier,
            "models": list(self.models),
            "timeout_seconds": self.timeout_seconds,
            "max_tokens": self.max_tokens,
            "fallback": self.fallback,
        }


@dataclass(frozen=True)
class Decision:
    """A routing decision: route, chain, tier and its cost controls."""

    task_id: str
    route: str
    path_mode: str
    tier: str
    chain: Tuple[str, ...]
    worker_types: Tuple[str, ...]
    domain: str
    sme: str
    squad: str
    module: str
    fail_safe: bool
    reason: str
    caps: TierCaps

    def as_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "route": self.route,
            "path_mode": self.path_mode,
            "tier": self.tier,
            "chain": list(self.chain),
            "worker_types": list(self.worker_types),
            "domain": self.domain,
            "sme": self.sme,
            "squad": self.squad,
            "module": self.module,
            "fail_safe": self.fail_safe,
            "reason": self.reason,
            "caps": self.caps.as_dict(),
        }


@dataclass(frozen=True)
class Attempt:
    """One rung of the escalation ladder and what the caps said about it."""

    tier: str
    accepted: bool
    reason: str

    def as_dict(self) -> Dict[str, Any]:
        return {"tier": self.tier, "accepted": self.accepted, "reason": self.reason}


@dataclass(frozen=True)
class Outcome:
    """The dispatch result: dispatched, refused, or a terminal human hand-off."""

    decision: Decision
    status: str
    tier: str
    attempts: Tuple[Attempt, ...] = field(default_factory=tuple)
    reason: str = ""

    @property
    def dispatched(self) -> bool:
        return self.status == DISPATCHED

    @property
    def escalated(self) -> bool:
        return len(self.attempts) > 1

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "tier": self.tier,
            "escalated": self.escalated,
            "attempts": [attempt.as_dict() for attempt in self.attempts],
            "reason": self.reason,
            "decision": self.decision.as_dict(),
        }


class Router:
    """Routes a task to a chain + tier and enforces that tier's cost controls."""

    def __init__(self, bundle: PolicyBundle) -> None:
        self._bundle = bundle

    # --- accessors ---------------------------------------------------------
    @property
    def bundle(self) -> PolicyBundle:
        return self._bundle

    @property
    def registry(self) -> CapabilityRegistry:
        return self._bundle.capability_registry

    @property
    def routes(self) -> RoutePolicy:
        return self._bundle.route_policy

    @property
    def tiers(self) -> TierPolicy:
        return self._bundle.tier_policy

    # --- classification ----------------------------------------------------
    def classify_domain(self, text: str) -> str:
        """Highest-scoring declared SME domain, or ``general`` on a zero score.

        Deterministic: the harvested bash classifier iterated an unordered
        associative array, so a score tie was arbitrary; this port resolves a
        tie to the first declared domain.
        """
        lowered = str(text).lower()
        best = "general"
        best_score = 0
        for name, domain in self.registry.sme_domains.items():
            score = 0
            for label in domain.labels:
                score += lowered.count(label.lower())
            if score > best_score:
                best = name
                best_score = score
        return best

    def classify_sme(self, text: str) -> str:
        """The SME profile that handles ``text`` (never empty)."""
        return self.registry.sme_domains[self.classify_domain(text)].sme

    def classify_squad(self, text: str) -> str:
        """The squad (board lens) for ``text``, first declared match wins."""
        lowered = str(text).lower()
        for name, squad in self.registry.squads.items():
            for keyword in squad.keywords:
                if keyword in lowered:
                    return name
        return self.registry.squad_default

    def classify(self, text: str) -> Dict[str, str]:
        domain = self.classify_domain(text)
        return {
            "domain": domain,
            "sme": self.registry.sme_domains[domain].sme,
            "squad": self.classify_squad(text),
            "module": self.registry.sme_domains[domain].module,
        }

    # --- routing -----------------------------------------------------------
    def _parse_task(self, task: Any) -> Dict[str, Any]:
        if not isinstance(task, Mapping):
            raise TaskInvalid(f"task must be a mapping, got {type(task).__name__}")
        unknown = sorted(set(task) - _KNOWN_TASK_KEYS)
        if unknown:
            raise TaskInvalid(
                f"unknown task key(s) {unknown}: the router will not route a task "
                "it cannot fully assess (put extra data under 'metadata')"
            )
        text = task.get("text", "")
        if text is None:
            text = ""
        if not isinstance(text, str):
            raise TaskInvalid("task 'text' must be a string")

        task_type = task.get("type")
        if task_type is not None and not isinstance(task_type, str):
            raise TaskInvalid("task 'type' must be a string")

        complexity = task.get("complexity")
        if complexity is not None:
            if isinstance(complexity, bool) or not isinstance(complexity, int):
                raise TaskInvalid("task 'complexity' must be an integer 0..100")
            if not 0 <= complexity <= 100:
                raise TaskInvalid(
                    f"task 'complexity' {complexity} is outside 0..100"
                )

        risk = task.get("risk")
        if risk is not None and risk not in _RISK_LEVELS:
            raise TaskInvalid(f"task 'risk' must be one of {list(_RISK_LEVELS)}")

        tokens = task.get("tokens")
        if tokens is not None:
            if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
                raise TaskInvalid("task 'tokens' must be a non-negative integer")

        timeout = task.get("timeout_seconds")
        if timeout is not None:
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                raise TaskInvalid("task 'timeout_seconds' must be a number")
            if timeout < 0:
                raise TaskInvalid("task 'timeout_seconds' must be non-negative")

        return {
            "id": str(task.get("id", "")),
            "type": task_type,
            "text": text,
            "complexity": complexity,
            "risk": risk,
            "tokens": tokens,
            "timeout_seconds": timeout,
        }

    @staticmethod
    def _first_keyword(lowered: str, keywords: Sequence[str]) -> Optional[str]:
        for keyword in keywords:
            if str(keyword).lower() in lowered:
                return str(keyword)
        return None

    def _route(self, request: Mapping[str, Any]) -> Decision:
        thresholds = self.routes.thresholds
        defaults = self.routes.normalised_defaults
        text = request["text"]
        lowered = text.lower()
        task_type = request["type"]
        complexity = request["complexity"]
        risk = request["risk"]
        tokens = request["tokens"]

        route_name: str
        signals: List[str] = []
        fail_safe = False

        risk_hit = self._first_keyword(lowered, thresholds.risk_high_keywords)
        if risk == "high":
            route_name = "strict"
            signals.append("risk=high")
        elif risk_hit is not None:
            route_name = "strict"
            signals.append(f"risk keyword {risk_hit!r}")
        elif task_type is not None and normalise_task_type(task_type) in defaults:
            route_name = defaults[normalise_task_type(task_type)]
            signals.append(f"declared task type {task_type!r}")
        elif task_type is not None:
            route_name = "deep"
            fail_safe = True
            signals.append(
                f"unrecognised task type {task_type!r} -> deep (fail-safe)"
            )
        else:
            complexity_hit = self._first_keyword(
                lowered, thresholds.complexity_keywords
            )
            if complexity_hit is not None:
                route_name = "deep"
                signals.append(f"complexity keyword {complexity_hit!r}")
            elif tokens is not None and tokens >= thresholds.deep_path_min_tokens:
                route_name = "deep"
                signals.append(
                    f"token estimate {tokens} >= deep_path_min_tokens "
                    f"{thresholds.deep_path_min_tokens}"
                )
            elif tokens is not None and tokens <= thresholds.fast_path_max_tokens:
                route_name = "fast"
                signals.append(
                    f"token estimate {tokens} <= fast_path_max_tokens "
                    f"{thresholds.fast_path_max_tokens}"
                )
            else:
                route_name = "deep"
                fail_safe = True
                signals.append("no positive signal -> deep (fail-safe)")

        route_spec = self.routes.routes[route_name]

        candidates: List[Tuple[str, str]] = [
            (route_spec.model_tier, f"route {route_name}")
        ]
        if complexity is not None:
            candidates.append(
                (
                    self.tiers.tier_for_complexity(complexity),
                    f"complexity {complexity}",
                )
            )
        if task_type is not None:
            override = self.tiers.normalised_overrides.get(
                normalise_task_type(task_type)
            )
            if override is not None:
                candidates.append((override, f"task override {task_type!r}"))
        tier_name = max((name for name, _ in candidates), key=self.tiers.rank)

        domain = self.classify_domain(text)
        sme_domain = self.registry.sme_domains[domain]
        tier_spec = self.tiers.tier(tier_name)
        caps = TierCaps(
            tier=tier_spec.name,
            models=tier_spec.models,
            timeout_seconds=tier_spec.timeout_seconds,
            max_tokens=tier_spec.max_tokens,
            fallback=tier_spec.fallback,
        )
        tier_reason = ", ".join(
            f"{name} (from {source})" for name, source in candidates
        )
        reason = (
            f"route={route_name} from {signals[0] if signals else 'declared default'}"
            f"; tier={tier_name} = highest of [{tier_reason}]; "
            f"sme={sme_domain.sme} domain={domain}"
        )
        return Decision(
            task_id=request["id"],
            route=route_name,
            path_mode=route_spec.path_mode,
            tier=tier_name,
            chain=route_spec.agents,
            worker_types=route_spec.worker_types,
            domain=domain,
            sme=sme_domain.sme,
            squad=self.classify_squad(text),
            module=sme_domain.module,
            fail_safe=fail_safe,
            reason=reason,
            caps=caps,
        )

    def route(self, task: Any) -> Decision:
        """Route ``task`` to a chain + tier. Never refuses; never guesses silently."""
        return self._route(self._parse_task(task))

    # --- cost controls -----------------------------------------------------
    def check_caps(self, tier_name: str, tokens: Optional[int] = None,
                   timeout_seconds: Optional[float] = None) -> Tuple[bool, str]:
        """Whether a request fits the tier's declared caps, with the reason."""
        if tier_name not in self.tiers.tiers:
            raise PolicyInvariantViolated(
                f"tier {tier_name!r} is not declared in the tier policy"
            )
        spec = self.tiers.tier(tier_name)
        if tokens is not None and tokens > spec.max_tokens:
            return False, (
                f"token cap breached: requested {tokens} > tier {tier_name!r} "
                f"max_tokens {spec.max_tokens}"
            )
        if timeout_seconds is not None and timeout_seconds > spec.timeout_seconds:
            return False, (
                f"timeout cap breached: requested {timeout_seconds}s > tier "
                f"{tier_name!r} timeout_seconds {spec.timeout_seconds}"
            )
        return True, (
            f"within tier {tier_name!r} caps "
            f"(max_tokens {spec.max_tokens}, timeout {spec.timeout_seconds}s)"
        )

    def dispatch(self, task: Any, *, escalate: bool = True) -> Outcome:
        """Route ``task``, then enforce the tier's caps.

        ``escalate=False`` -- a breach is a hard refusal (the fallback is named
        but not climbed). ``escalate=True`` -- climb the declared ladder; when
        even the top tier cannot take the request, terminate at the distinct
        ``human_advisor`` outcome.
        """
        request = self._parse_task(task)
        decision = self._route(request)
        tokens = request["tokens"]
        timeout = request["timeout_seconds"]

        attempts: List[Attempt] = []
        visited: List[str] = []
        current: Optional[str] = decision.tier
        limit = len(self.tiers.tiers) + 1

        while current is not None:
            if len(visited) >= limit:
                raise PolicyInvariantViolated(
                    "escalation exceeded the tier count: the ladder is not bounded"
                )
            accepted, reason = self.check_caps(current, tokens, timeout)
            attempts.append(Attempt(tier=current, accepted=accepted, reason=reason))
            if accepted:
                return Outcome(
                    decision=decision,
                    status=DISPATCHED,
                    tier=current,
                    attempts=tuple(attempts),
                    reason=reason,
                )
            if not escalate:
                return Outcome(
                    decision=decision,
                    status=REFUSED,
                    tier=current,
                    attempts=tuple(attempts),
                    reason=reason,
                )
            visited.append(current)
            current = self.tiers.tier(current).fallback

        terminal = visited[-1] if visited else decision.tier
        return Outcome(
            decision=decision,
            status=HUMAN_ADVISOR,
            tier=terminal,
            attempts=tuple(attempts),
            reason=(
                f"no higher tier than {terminal!r} (its fallback is null): "
                "terminal escalation is a human/advisor hand-off"
            ),
        )


def load_router(policies_dir: Optional[Any] = None) -> Router:
    """Load the validated policy bundle and wrap it in a ``Router``."""
    return Router(load_bundle(policies_dir))
