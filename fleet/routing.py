#!/usr/bin/env python3
"""Capability routing — the fleet's single source of dispatch vocabulary (ADR-0012).

Why this module exists
----------------------
``vendor/CMR/catalog/modules/hermes-agents`` describes itself as *"agent routing,
capability registry, escalation, and model-tiering patterns for multi-tenant
agent orchestration and persona-driven task execution"* — verbatim what
``fleet/brain.py`` used to hand-implement as a private dialect (``choose_model``
plus a local ``HIGH_FLOOR_LANES`` tuple). ADR-0012 decides that boundary: the
vendored contract is the vocabulary of record, dispatch is routed by the
capability the work needs, and the registry personas that are actually dispatched
are the resolver's source of truth.

This module is that consumption, as a **port**: one declarative policy
(``fleet/profiles/routing.policy.json``) plus a small pure API over it. The director
asks this module instead of keeping a second copy of the dialect, and the registry
cards are read and cross-checked at load — a policy that drifts from the registry
fails closed *by name* rather than dispatching on a stale vocabulary.

The API::

    capability_for(task)          -> the capability the work needs (or None)
    agent_for(capability)         -> the registered persona that owns it
    tier_for(capability, risk)    -> the FinOps (tier, thinking) block
    escalate(tier, thinking, ...) -> one rung up, on an observed reason only
    route(task, risk)             -> the whole decision, including the card read

The only I/O is loading the policy and the registry cards it names; nothing here
touches the network, the mailbox or the repo tree (ADR-0012 decision (d) — a
non-blocking adapter over behaviour that is already live).

Provenance (GR-10) is recorded in the policy's ``provenance`` block. ``vendor/``
is pinned read-only and no file was copied, so no ``harvested_from`` marker is
required: the pattern and the vocabulary are reused, not the file.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "fleet" / "profiles" / "routing.policy.json"

#: Sections a policy must carry. A stripped one is a named refusal, never a
#: defaulted value — a policy that is half-loaded cannot route.
REQUIRED_SECTIONS = (
    "provenance",
    "personas",
    "capabilities",
    "hints",
    "finops",
    "risk",
    "escalation",
)

#: The risk levels ``tier_for`` accepts. ``HIGH`` is the security/secrets/auth/
#: production-IaC floor; ``NORMAL`` is everything else.
HIGH = "high"
NORMAL = "normal"


class RoutingRefusal(Exception):
    """A refusal with a stable code and a reason — never a silent default persona.

    The director turns one of these into a named refusal for the principal (the same
    posture as the channel's own refusals): routing that cannot be justified is
    reported, not guessed.
    """

    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(f"{code}: {reason}")


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RoutingRefusal("policy-unreadable", f"cannot read the routing policy {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RoutingRefusal("policy-malformed", f"the routing policy {path} is not a JSON object")
    return payload


def _read_card(path: Path, persona: str) -> dict:
    """The registry persona card, parsed.

    A card that cannot be read is a refusal: resolving a persona from a card
    nobody could read is exactly the decorative-registry failure this port exists
    to prevent (#300).
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - the repo gate requires PyYAML
        raise RoutingRefusal(
            "card-unreadable", f"PyYAML is required to read the persona card {path}"
        ) from exc
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise RoutingRefusal(
            "card-missing", f"the persona card for {persona!r} is not readable: {path} ({exc})"
        ) from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise RoutingRefusal(
            "card-malformed", f"the persona card for {persona!r} is not valid YAML: {path} ({exc})"
        ) from exc
    if not isinstance(data, dict):
        raise RoutingRefusal(
            "card-malformed", f"the persona card for {persona!r} is not a mapping: {path}"
        )
    return data


class RoutingPolicy:
    """The loaded policy: capability -> persona -> tier, plus the risk floor.

    Constructed only through :func:`load` (or directly with a parsed policy for
    tests). ``__init__`` validates the policy against the registry cards it names
    and raises :class:`RoutingRefusal` on any disagreement, so an invalid policy
    can never be used to route work.
    """

    def __init__(self, policy: dict, *, root: Path = ROOT) -> None:
        self.policy = policy
        self.root = Path(root)
        self._read_cards: dict[str, dict] = {}
        self._lanes: dict[str, str] = {}
        self._primary: dict[str, str] = {}
        self._validate()

    # --- vocabulary ---------------------------------------------------------

    @property
    def personas(self) -> dict:
        return self.policy["personas"]

    @property
    def capabilities(self) -> dict:
        return self.policy["capabilities"]

    @property
    def unrouted(self) -> dict:
        return self.policy.get("unrouted_capabilities") or {}

    @property
    def shared(self) -> dict:
        return self.policy.get("shared_capabilities") or {}

    @property
    def hints(self) -> tuple[dict, ...]:
        return tuple(self.policy["hints"])

    @property
    def finops(self) -> dict:
        return self.policy["finops"]

    @property
    def escalation(self) -> dict:
        return self.policy["escalation"]

    @property
    def high_floor_tokens(self) -> tuple[str, ...]:
        """The risk vocabulary: a lane/title naming one of these is floored."""
        return tuple(self.policy["risk"]["high_floor"]["tokens"])

    @property
    def tier_vocabulary(self) -> tuple[str, ...]:
        return tuple(self.escalation["order"]["tier"])

    @property
    def thinking_vocabulary(self) -> tuple[str, ...]:
        return tuple(self.escalation["order"]["thinking"])

    @property
    def default_block(self) -> tuple[str, str]:
        block = self.finops["default"]
        return str(block["tier"]), str(block["thinking"])

    @property
    def high_floor_block(self) -> tuple[str, str]:
        """The high floor the policy escalates to for risk-bearing work."""
        block = self.policy["risk"]["high_floor"]
        return str(block["tier"]), str(block["thinking"])

    def capability_ids(self) -> tuple[str, ...]:
        """Every capability id this policy routes, in declaration order."""
        return tuple(self.capabilities)

    def card_for(self, persona: str) -> str:
        """The registry card path the resolver reads for ``persona``."""
        return str(self.personas[persona]["card"])

    def cards_read(self) -> tuple[str, ...]:
        """Every registry card the loader actually read and verified."""
        return tuple(self.personas[persona]["card"] for persona in self.personas)

    def registry_capabilities(self, persona: str) -> tuple[str, ...]:
        """The capabilities that persona's own registry card declares."""
        return tuple(self._read_cards[persona].get("capabilitySet") or ())

    def owned_lanes(self, persona: str) -> tuple[str, ...]:
        """The lanes that persona's own registry card owns."""
        return tuple(self._read_cards[persona].get("ownedLanes") or ())

    def primary_capability(self, persona: str) -> str | None:
        """The capability a lane-owned task resolves to: the card's first entry.

        The card decides the order, so this module holds no preference of its own.
        """
        return self._primary.get(persona)

    def unregistered_routes(self) -> tuple[str, ...]:
        """Routed capabilities the owning card does not (yet) declare.

        An explicit, ratcheted list rather than a silent gap: a *new* route the
        registry does not back shows up here, and the suite pins the set so it
        cannot grow unnoticed.
        """
        return tuple(
            capability
            for capability, route in self.capabilities.items()
            if capability not in self.registry_capabilities(route["persona"])
        )

    # --- the resolver -------------------------------------------------------

    def capability_for(self, task: dict) -> str | None:
        """The capability the work needs, or None when the task claims none.

        Resolution order: an explicit ``task.capability`` (validated — an unknown
        or unrouted id is a named refusal, never a silent default persona), then
        the lane when a registry card owns it, then the policy's ``hints`` over
        the lane and over the title. The lane outranks the title, and the hint
        order is the policy's own, so the same task always resolves the same way.
        """
        task = task if isinstance(task, dict) else {}
        claimed = task.get("capability")
        if claimed is not None:
            if not isinstance(claimed, str) or not claimed.strip():
                raise RoutingRefusal(
                    "capability-malformed", "task.capability must be a non-empty capability id"
                )
            return self._validate_capability(claimed.strip())
        lane = str(task.get("lane") or "").lower()
        title = str(task.get("title") or "").lower()
        owned = self._lanes.get(lane)
        if owned is not None:
            return self._primary.get(owned)
        for haystack in (lane, title):
            if not haystack:
                continue
            for hint in self.hints:
                words = tuple(str(word).lower() for word in hint.get("words") or ())
                if any(word in haystack for word in words):
                    return str(hint["capability"])
        return None

    def _validate_capability(self, capability: str) -> str:
        if capability in self.capabilities:
            return capability
        if capability in self.unrouted:
            raise RoutingRefusal(
                "capability-unrouted",
                f"{capability!r} is declared vocabulary but no registered persona owns it: "
                f"{self.unrouted[capability]}",
            )
        raise RoutingRefusal(
            "capability-unknown",
            f"{capability!r} is not in this policy's capability vocabulary "
            f"({', '.join(self.capability_ids())})",
        )

    def agent_for(self, capability: str) -> str:
        """The registered persona that owns ``capability`` (ADR-0012 decision (b))."""
        return str(self.capabilities[self._validate_capability(capability)]["persona"])

    def risk_for(self, lane: str | None = None, title: str | None = None, capability: str | None = None) -> str:
        """The risk level of a unit of work: ``high`` when the floor applies.

        A lane or title naming a floor token (security / secrets / auth / identity
        / IaC / infra / terraform) is high; so is a capability the policy marks
        high-risk. Everything else is normal.
        """
        haystack = f"{lane or ''} {title or ''}".lower()
        if any(token in haystack for token in self.high_floor_tokens):
            return HIGH
        if capability and self.policy["risk"].get("capability_risk", {}).get(capability) == HIGH:
            return HIGH
        return NORMAL

    def needs_high_floor(self, lane: str | None, title: str | None) -> bool:
        """The boolean form of :meth:`risk_for` — the director's old question."""
        return self.risk_for(lane, title) == HIGH

    def tier_for(self, capability: str | None, risk: str | None = None) -> tuple[str, str]:
        """The FinOps ``(tier, thinking)`` block for a capability at a risk level.

        A capability resolves through its persona's Hermes tier to a FinOps tier
        (``tier_map``); ``None`` means the task claimed no capability, so the
        policy's declared default applies. A high risk is floored — the floor
        outranks the persona tier (ADR-0012 decision (b)).
        """
        level = risk if risk is not None else NORMAL
        if level not in (HIGH, NORMAL):
            raise RoutingRefusal(
                "risk-unknown", f"risk must be {NORMAL!r} or {HIGH!r}, not {level!r}"
            )
        if capability is None:
            tier, thinking = self.default_block
        else:
            # An unknown or unrouted capability refuses here rather than being
            # silently defaulted: a claimed capability the registry cannot back is
            # a finding, not a fallback (#300).
            capability = self._validate_capability(capability)
            if risk is None:
                level = self.risk_for(capability=capability)
            persona = self.agent_for(capability)
            tier = str(self.finops["tier_map"][str(self.personas[persona]["tier"])])
            thinking = str(self.finops["thinking"][tier])
        if level == HIGH:
            tier, thinking = self.floor(tier, thinking)
        return tier, thinking

    def floor(self, tier: str, thinking: str) -> tuple[str, str]:
        """Raise ``(tier, thinking)`` to the high floor — never lower it."""
        floor_tier = str(self.policy["risk"]["high_floor"]["tier"])
        floor_thinking = str(self.policy["risk"]["high_floor"]["thinking"])
        self._require_vocabulary(tier, thinking)
        if self._rank(self.tier_vocabulary, tier) < self._rank(self.tier_vocabulary, floor_tier):
            tier = floor_tier
        if self._rank(self.thinking_vocabulary, thinking) < self._rank(self.thinking_vocabulary, floor_thinking):
            thinking = floor_thinking
        return tier, thinking

    def escalate(self, tier: str, thinking: str, *, reason: str) -> tuple[str, str]:
        """One rung up, on an observed reason — the policy's escalation rule.

        Escalation is evidence-driven, so a blank reason is refused by name. It
        moves one rung of thinking effort, or one tier up (at that tier's own
        thinking floor) when thinking is already at the top; it never downgrades
        and never skips a rung.
        """
        if not isinstance(reason, str) or not reason.strip():
            raise RoutingRefusal(
                "escalation-without-reason",
                "escalation requires the observed difficulty that justifies it "
                "(the policy escalates once, on evidence, never speculatively)",
            )
        self._require_vocabulary(tier, thinking)
        thinking_rank = self._rank(self.thinking_vocabulary, thinking)
        if thinking_rank < len(self.thinking_vocabulary) - 1:
            return tier, self.thinking_vocabulary[thinking_rank + 1]
        tier_rank = self._rank(self.tier_vocabulary, tier)
        if tier_rank < len(self.tier_vocabulary) - 1:
            raised = self.tier_vocabulary[tier_rank + 1]
            return raised, str(self.finops["thinking"][raised])
        return tier, thinking

    def route(self, task: dict, risk: str | None = None) -> dict:
        """The whole routing decision, including the registry card that backed it.

        This is what the director reports into the directive: the capability, the
        persona, the card path it read, and the FinOps block — one call, one
        deterministic answer.
        """
        task = task if isinstance(task, dict) else {}
        capability = self.capability_for(task)
        lane = str(task.get("lane") or "")
        title = str(task.get("title") or "")
        if risk is None:
            risk = self.risk_for(lane, title, capability)
        persona = self.agent_for(capability) if capability else None
        tier, thinking = self.tier_for(capability, risk)
        return {
            "capability": capability,
            "persona": persona,
            "card": self.card_for(persona) if persona else None,
            "tier": tier,
            "thinking": thinking,
            "risk": risk,
        }

    # --- validation ---------------------------------------------------------

    def _rank(self, order: tuple[str, ...], value: str) -> int:
        return order.index(value)

    def _require_vocabulary(self, tier: str, thinking: str) -> None:
        if tier not in self.tier_vocabulary:
            raise RoutingRefusal(
                "tier-unknown",
                f"{tier!r} is not one of the FinOps tiers ({', '.join(self.tier_vocabulary)})",
            )
        if thinking not in self.thinking_vocabulary:
            raise RoutingRefusal(
                "thinking-unknown",
                f"{thinking!r} is not one of the thinking levels ({', '.join(self.thinking_vocabulary)})",
            )

    def _validate(self) -> None:
        """Fail closed unless the policy and the registry cards agree."""
        missing = [section for section in REQUIRED_SECTIONS if section not in self.policy]
        if missing:
            raise RoutingRefusal(
                "policy-incomplete", f"the routing policy is missing {', '.join(missing)}"
            )
        for tier in self.tier_vocabulary:
            if tier not in self.finops["thinking"]:
                raise RoutingRefusal(
                    "policy-incomplete", f"the FinOps tier {tier!r} has no thinking level declared"
                )
        for value in tuple(self.finops["tier_map"].values()) + (self.default_block[0],):
            if value not in self.tier_vocabulary:
                raise RoutingRefusal(
                    "tier-unknown",
                    f"tier_map names {value!r}, which is not a FinOps tier "
                    f"({', '.join(self.tier_vocabulary)})",
                )
        for value in tuple(self.finops["thinking"].values()) + (self.default_block[1],):
            if value not in self.thinking_vocabulary:
                raise RoutingRefusal(
                    "thinking-unknown",
                    f"a thinking level {value!r} is not one of "
                    f"({', '.join(self.thinking_vocabulary)})",
                )
        if set(self.capabilities) & set(self.unrouted):
            raise RoutingRefusal(
                "capability-ambiguous",
                "these capabilities are both routed and unrouted: "
                f"{', '.join(sorted(set(self.capabilities) & set(self.unrouted)))}",
            )
        if not self.policy["risk"]["high_floor"]["tokens"]:
            raise RoutingRefusal(
                "policy-incomplete", "the risk floor declares no tokens, so nothing is ever floored"
            )
        self.floor(self.default_block[0], self.default_block[1])
        self._verify_personas()
        self._verify_hints()

    def _verify_personas(self) -> None:
        """Read every named registry card and require the policy to agree with it."""
        for capability, route in self.capabilities.items():
            persona = route.get("persona")
            if persona not in self.personas:
                raise RoutingRefusal(
                    "persona-unregistered",
                    f"capability {capability!r} routes to persona {persona!r}, which is not declared "
                    f"in the policy ({', '.join(self.personas)})",
                )
        for persona, declared in self.personas.items():
            card = self.root / str(declared["card"])
            data = _read_card(card, persona)
            self._read_cards[persona] = data
            if str(data.get("id") or "") != persona:
                raise RoutingRefusal(
                    "card-mismatch",
                    f"the card {declared['card']} declares id {data.get('id')!r}, not {persona!r}",
                )
            card_tier = str(data.get("defaultModelTier") or "")
            if card_tier != str(declared["tier"]):
                raise RoutingRefusal(
                    "card-tier-mismatch",
                    f"{declared['card']} declares defaultModelTier {card_tier!r} but the policy routes "
                    f"{persona!r} at {declared['tier']!r} — one of the two is stale",
                )
            card_lanes = tuple(data.get("ownedLanes") or ())
            if tuple(declared.get("lanes") or ()) != card_lanes:
                raise RoutingRefusal(
                    "card-lanes-mismatch",
                    f"{declared['card']} owns lanes {card_lanes} but the policy declares "
                    f"{tuple(declared.get('lanes') or ())} for {persona!r} — one of the two is stale",
                )
            for lane in card_lanes:
                owner = self._lanes.get(str(lane).lower())
                if owner is not None and owner != persona:
                    raise RoutingRefusal(
                        "lane-ambiguous",
                        f"lane {lane!r} is owned by both {owner!r} and {persona!r}",
                    )
                self._lanes[str(lane).lower()] = persona
            primary = self.registry_capabilities(persona)[:1]
            if primary:
                self._primary[persona] = primary[0]
                if self.capabilities.get(primary[0], {}).get("persona") != persona:
                    raise RoutingRefusal(
                        "primary-capability-misrouted",
                        f"{declared['card']} lists {primary[0]!r} first, but the policy routes it "
                        f"to {self.capabilities.get(primary[0], {}).get('persona')!r}",
                    )
            for capability in tuple(data.get("capabilitySet") or ()):
                owner = self.capabilities.get(capability)
                if owner is None:
                    raise RoutingRefusal(
                        "registry-capability-unrouted",
                        f"{declared['card']} declares capability {capability!r}, which this policy does "
                        "not route to any persona",
                    )
                allowed = {persona, *self.shared.get(capability, ())}
                if owner["persona"] not in allowed:
                    raise RoutingRefusal(
                        "registry-capability-misrouted",
                        f"{declared['card']} declares capability {capability!r}, but the policy routes it "
                        f"to {owner['persona']!r} and no shared_capabilities entry covers that",
                    )
        for capability, personas in self.shared.items():
            declared_by = {
                persona for persona in personas if capability in self.registry_capabilities(persona)
            }
            if declared_by != set(personas):
                raise RoutingRefusal(
                    "shared-capability-mismatch",
                    f"shared_capabilities lists {capability!r} for {sorted(personas)}, but only "
                    f"{sorted(declared_by)} declare it in their registry card",
                )

    def _verify_hints(self) -> None:
        """A hint that resolves to unrouted vocabulary would refuse live dispatch."""
        for hint in self.hints:
            capability = str(hint.get("capability") or "")
            if capability not in self.capabilities:
                raise RoutingRefusal(
                    "hint-unrouted",
                    f"hint {capability!r} is not a routed capability — a task matching it could not "
                    "be dispatched",
                )


# The one loader, and a cached default policy for the module-level functions.
_DEFAULT: RoutingPolicy | None = None


def load(path: Path | str | None = None, *, root: Path = ROOT) -> RoutingPolicy:
    """Load and validate the routing policy — the module's only reader.

    ``make verify`` requires the policy to be a real gate, not a declaration: a
    malformed or registry-disagreeing policy raises :class:`RoutingRefusal`.
    """
    return RoutingPolicy(_read_json(Path(path) if path else POLICY_PATH), root=root)


def policy() -> RoutingPolicy:
    """The process-wide policy (loaded once)."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = load()
    return _DEFAULT


def reset_cache() -> None:
    """Drop the cached policy — for a caller that changed the policy on disk."""
    global _DEFAULT
    _DEFAULT = None


def capability_for(task: dict) -> str | None:
    """The capability ``task`` needs, or None when it claims none."""
    return policy().capability_for(task)


def agent_for(capability: str) -> str:
    """The registered persona that owns ``capability``."""
    return policy().agent_for(capability)


def tier_for(capability: str | None, risk: str | None = None) -> tuple[str, str]:
    """The FinOps ``(tier, thinking)`` block for a capability at a risk level."""
    return policy().tier_for(capability, risk)


def escalate(tier: str, thinking: str, *, reason: str) -> tuple[str, str]:
    """One rung up, on an observed reason (never speculative, never a downgrade)."""
    return policy().escalate(tier, thinking, reason=reason)


def needs_high_floor(lane: str | None, title: str | None) -> bool:
    """Whether the lane/title names security/secrets/auth/IaC work."""
    return policy().needs_high_floor(lane, title)


def route(task: dict, risk: str | None = None) -> dict:
    """The whole routing decision for ``task``."""
    return policy().route(task, risk)


def high_floor_tokens() -> tuple[str, ...]:
    """The risk vocabulary the policy floors on."""
    return policy().high_floor_tokens
