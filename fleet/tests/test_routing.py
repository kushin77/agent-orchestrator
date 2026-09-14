"""Capability routing: the fleet speaks Hermes' routing vocabulary (ADR-0012; #300, #301).

The decision this suite pins: the vendored `hermes-agents` contract owns the
orchestration vocabulary, dispatch is routed by the capability the work needs, and
the registry personas that are actually dispatched are the resolver's source of
truth. The mapping therefore lives in EXACTLY ONE place — the declarative policy
(`fleet/profiles/routing.policy.json`, read by `fleet/routing.py`) backed by the
registry cards — and the brain keeps no second copy of it.

Every check here can fail: the policy/registry cross-checks are provoked with real
mutations, the refusals are asserted by name, and the "one source" guard is proved
by mutation in the PR evidence. A declaration check that cannot fail is a
formality (GR-12).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import channel
import routing

ROOT = Path(__file__).resolve().parents[2]
FLEET = ROOT / "fleet"
POLICY_PATH = FLEET / "profiles" / "routing.policy.json"
HERMES_CARD = "registry/personas/cards/hermes.yaml"
PAPERCLIP_CARD = "registry/personas/cards/paperclip.yaml"


def policy() -> routing.RoutingPolicy:
    return routing.policy()


def mutated(tmp_path: Path, **changes) -> Path:
    """A copy of the real policy with `changes` applied, for a fail-closed probe."""
    data = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    for dotted, value in changes.items():
        target = data
        parts = dotted.split(".")
        for part in parts[:-1]:
            target = target[part]
        if value is _DELETE:
            target.pop(parts[-1], None)
        else:
            target[parts[-1]] = value
    path = tmp_path / "routing.policy.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


class _Delete:
    """Sentinel: remove the key rather than set it."""


_DELETE = _Delete()


# --- provenance (GR-10) ------------------------------------------------------


def test_the_policy_records_the_harvest_it_consumes():
    """GR-10: a consumed asset names its source, path, verdict and landing."""
    provenance = policy().policy["provenance"]
    assert "GR-10" in provenance["doctrine"]
    sources = provenance["harvested_from"]
    paths = [entry["path"] for entry in sources]
    assert any("vendor/CMR/catalog/modules/hermes-agents" in path for path in paths), paths
    assert HERMES_CARD in paths, "the dispatched hermes persona card is part of the harvest"
    assert PAPERCLIP_CARD in paths, "the dispatched paperclip persona card is part of the harvest"
    hermes_entry = sources[paths.index(HERMES_CARD)]
    assert hermes_entry["verdict"] == "REGISTRY" and "MED" in hermes_entry["asset"]
    assert all(entry["verdict"] in ("PATTERN", "REGISTRY", "REFERENCE") for entry in sources)
    # No file was copied, so no `harvested_from` marker is required — stated, not implied.
    assert "no source file was copied" in provenance["doctrine"]


def test_the_policy_names_the_decision_record_and_the_capability_vocabulary():
    """The capability ids are Hermes' vocabulary (the ADR's own list)."""
    declared = set(policy().capability_ids()) | set(policy().unrouted)
    for capability in (
        "code-author",
        "test-author",
        "test-run",
        "code-review",
        "research",
        "docs-authoring",
        "orchestrate",
        "memory-ops",
    ):
        assert capability in declared, f"{capability!r} is missing from the routing vocabulary"


# --- capability -> persona -> tier (ADR-0012 decision (b)) -------------------


def test_code_work_resolves_to_the_hermes_persona_at_med():
    decision = policy().route({"lane": "hermes"})
    assert decision["persona"] == "hermes"
    assert decision["capability"] == "code-author"
    assert decision["card"] == HERMES_CARD
    # Hermes is a MED persona; MED maps onto the harvested flash tier.
    assert (decision["tier"], decision["thinking"]) == ("flash", "none")
    assert routing.tier_for("test-author") == ("flash", "none")
    assert routing.tier_for("test-run") == ("flash", "none")


def test_research_and_docs_resolve_to_the_paperclip_persona_at_low():
    for task in ({"lane": "research"}, {"lane": "paperclip"}, {"lane": "knowledge"}):
        decision = policy().route(task)
        assert decision["persona"] == "paperclip", task
        assert decision["card"] == PAPERCLIP_CARD, task
        assert (decision["tier"], decision["thinking"]) == ("flash", "none"), task
    assert policy().route({"lane": "docs-authoring"})["capability"] == "docs-authoring"


def test_a_lane_a_persona_owns_resolves_to_that_persona():
    """The registry cards' `ownedLanes` are load-bearing, not decorative (#300)."""
    assert set(policy().owned_lanes("hermes")) == {"hermes", "code-authoring"}
    # paperclip gained the reporting lane with the `module-brief` capability (#447).
    assert set(policy().owned_lanes("paperclip")) == {"paperclip", "knowledge", "module-brief"}
    assert policy().primary_capability("hermes") == "code-author"
    assert policy().primary_capability("paperclip") == "research"


def test_the_resolver_names_the_registry_cards_it_read():
    """#300's check: the resolution reports the card it read, and the file is real."""
    cards = policy().cards_read()
    assert set(cards) == {HERMES_CARD, PAPERCLIP_CARD}
    for card in cards:
        assert (ROOT / card).is_file(), f"{card} does not exist"
    assert policy().route({"lane": "hermes"})["card"] in cards
    assert policy().route({"lane": "fleet"})["card"] is None, "no persona is invented for a plain lane"


def test_a_route_the_registry_does_not_back_is_listed_not_hidden():
    """Ratchet: a new unbacked route must not appear silently."""
    assert policy().unregistered_routes() == ("code-review",)
    assert "code-review" in policy().capability_ids()


# --- the risk floor outranks the persona tier --------------------------------


def test_the_high_floor_outranks_the_persona_tier():
    decision = policy().route({"lane": "secrets-handling"})
    assert decision["risk"] == "high"
    assert (decision["tier"], decision["thinking"]) == ("pro", "low")
    # ...and it applies to a persona-routed capability too, whatever the tier said.
    assert routing.tier_for("code-author", routing.HIGH) == ("pro", "low")
    assert routing.tier_for("research", routing.HIGH) == ("pro", "low")
    assert routing.tier_for("code-author", routing.NORMAL) == ("flash", "none")


def test_the_floor_never_lowers_a_block_that_is_already_higher():
    """Escalation is a floor, never a ceiling — the ADR's own wording."""
    assert policy().floor("auditor", "high") == ("auditor", "high")
    assert policy().floor("pro", "medium") == ("pro", "medium")
    assert policy().floor("flash", "none") == ("pro", "low")


def test_the_floor_matches_substrings_on_purpose():
    """A conservative direction, pinned rather than assumed.

    The floor tokens are matched as substrings of the lane and the title, so a lane
    that merely contains one is floored: `code-authoring` holds "auth". An
    unnecessary escalation is cheap; a missed one is not — so this over-inclusion is
    the behaviour that survives the port, and narrowing it would be a deliberate
    change to a security floor, not a side effect of ADR-0012.
    """
    assert policy().needs_high_floor("code-authoring", "") is True
    assert policy().needs_high_floor("fleet", "author the tests for the gate") is True
    assert policy().needs_high_floor("fleet", "write the runbook") is False
    for token in policy().high_floor_tokens:
        assert policy().needs_high_floor(f"lane-{token}", "") is True


# --- fail closed -------------------------------------------------------------


def test_an_unknown_capability_is_refused_by_name():
    with pytest.raises(routing.RoutingRefusal) as exc:
        routing.agent_for("make-coffee")
    assert exc.value.code == "capability-unknown"
    assert "make-coffee" in exc.value.reason
    with pytest.raises(routing.RoutingRefusal) as exc:
        routing.capability_for({"lane": "fleet", "capability": "make-coffee"})
    assert exc.value.code == "capability-unknown"


def test_an_unrouted_capability_is_refused_by_name_not_defaulted():
    """`orchestrate` is real vocabulary, but no registered persona owns it."""
    with pytest.raises(routing.RoutingRefusal) as exc:
        routing.agent_for("orchestrate")
    assert exc.value.code == "capability-unrouted"
    assert "orchestrate" in exc.value.reason
    with pytest.raises(routing.RoutingRefusal):
        routing.tier_for("orchestrate")


def test_a_malformed_capability_claim_is_refused():
    with pytest.raises(routing.RoutingRefusal) as exc:
        routing.capability_for({"lane": "fleet", "capability": "   "})
    assert exc.value.code == "capability-malformed"


def test_a_lane_that_claims_no_capability_gets_the_declared_default():
    """The non-blocking half of the adapter: a plain lane keeps running as before."""
    assert routing.capability_for({"lane": "fleet"}) is None
    assert routing.tier_for(None) == policy().default_block


# --- determinism -------------------------------------------------------------


def test_resolution_is_deterministic_and_lane_outranks_title():
    first = policy().route({"lane": "hermes", "title": "write the docs"})
    for _ in range(5):
        assert policy().route({"lane": "hermes", "title": "write the docs"}) == first
    assert first["capability"] == "code-author", "the lane's persona own decides, not the title"
    assert policy().route({"lane": "fleet", "title": "write the docs"})["capability"] == "docs-authoring"


def test_the_hint_order_is_the_policys_own():
    """A task matching several hints resolves by the policy's declaration order.

    The order is pinned here so a reordering — which would silently change which
    persona a lane resolves to — is a visible diff, not a drift.
    """
    assert [hint["capability"] for hint in policy().hints] == [
        "code-author",
        "test-author",
        "test-run",
        "code-review",
        "research",
        "docs-authoring",
        "memory-ops",
    ]
    # 'review-docs' matches both the review hint and the docs hint: the declared
    # order decides, so the answer cannot drift with the file's key order.
    assert policy().capability_for({"lane": "review-docs"}) == "code-review"
    # The lane outranks the title even when the title matches an earlier hint.
    assert policy().capability_for({"lane": "docs-authoring", "title": "review it"}) == "docs-authoring"


# --- the policy cannot drift from the registry or the wire -------------------


def test_a_policy_tier_that_disagrees_with_the_registry_card_is_refused(tmp_path):
    path = mutated(tmp_path, **{"personas.hermes.tier": "HIGH"})
    with pytest.raises(routing.RoutingRefusal) as exc:
        routing.load(path)
    assert exc.value.code == "card-tier-mismatch"
    assert HERMES_CARD in exc.value.reason


def test_a_card_capability_the_policy_does_not_route_is_refused(tmp_path):
    path = mutated(tmp_path, **{"capabilities.test-author": _DELETE})
    with pytest.raises(routing.RoutingRefusal) as exc:
        routing.load(path)
    assert exc.value.code == "registry-capability-unrouted"
    assert "test-author" in exc.value.reason


def test_a_route_to_an_unregistered_persona_is_refused(tmp_path):
    path = mutated(tmp_path, **{"capabilities.code-author": {"persona": "ghost"}})
    with pytest.raises(routing.RoutingRefusal) as exc:
        routing.load(path)
    assert exc.value.code == "persona-unregistered"


def test_a_missing_registry_card_is_refused(tmp_path):
    path = mutated(tmp_path, **{"personas.hermes.card": "registry/personas/cards/ghost.yaml"})
    with pytest.raises(routing.RoutingRefusal) as exc:
        routing.load(path)
    assert exc.value.code == "card-missing"


def test_a_stripped_policy_is_refused(tmp_path):
    path = mutated(tmp_path, **{"risk": _DELETE})
    with pytest.raises(routing.RoutingRefusal) as exc:
        routing.load(path)
    assert exc.value.code == "policy-incomplete" and "risk" in exc.value.reason


def test_a_hint_pointing_at_unrouted_vocabulary_is_refused(tmp_path):
    path = mutated(tmp_path, **{"hints": [{"capability": "orchestrate", "words": ["plan"]}]})
    with pytest.raises(routing.RoutingRefusal) as exc:
        routing.load(path)
    assert exc.value.code == "hint-unrouted"


def test_the_finops_vocabulary_agrees_with_the_wire_that_enforces_it():
    """One vocabulary: the policy cannot name a tier or thinking level the wire refuses."""
    assert policy().tier_vocabulary == tuple(channel.MODEL_TIERS)
    assert policy().thinking_vocabulary == tuple(channel.THINKING_LEVELS)
    assert set(policy().finops["tier_map"].values()) <= set(channel.MODEL_TIERS)
    assert set(policy().finops["thinking"].values()) <= set(channel.THINKING_LEVELS)
    assert policy().default_block == ("flash", "none")
    assert policy().high_floor_block == ("pro", "low")


# --- escalation policy -------------------------------------------------------


def test_escalation_requires_an_observed_reason():
    """Escalating speculatively is what the policy's rule forbids."""
    for reason in ("", "   ", None):
        with pytest.raises(routing.RoutingRefusal) as exc:
            routing.escalate("flash", "none", reason=reason)
        assert exc.value.code == "escalation-without-reason"


def test_escalation_moves_one_rung_up_and_never_downgrades():
    """One rung of thinking, else one tier up at that tier's floor — never down."""
    assert routing.escalate("flash", "none", reason="the first attempt failed") == ("flash", "low")
    assert routing.escalate("flash", "high", reason="thinking is exhausted") == ("pro", "low")
    assert routing.escalate("pro", "medium", reason="still failing") == ("pro", "high")
    assert routing.escalate("auditor", "high", reason="already at the top") == ("auditor", "high")

    tiers = policy().tier_vocabulary
    thinking = policy().thinking_vocabulary
    for tier, level in (("flash", "none"), ("flash", "high"), ("pro", "medium"), ("pro", "high")):
        raised_tier, raised_thinking = routing.escalate(tier, level, reason="observed difficulty")
        assert tiers.index(raised_tier) >= tiers.index(tier), "a tier must never go down"
        assert (tiers.index(raised_tier), thinking.index(raised_thinking)) > (
            tiers.index(tier),
            thinking.index(level),
        ), "an escalation must actually raise something"


def test_escalation_refuses_a_vocabulary_it_does_not_know():
    with pytest.raises(routing.RoutingRefusal) as exc:
        routing.escalate("quantum", "none", reason="observed")
    assert exc.value.code == "tier-unknown"
    with pytest.raises(routing.RoutingRefusal) as exc:
        routing.escalate("flash", "deeper", reason="observed")
    assert exc.value.code == "thinking-unknown"


# --- one source of truth -----------------------------------------------------


def test_the_mapping_lives_in_exactly_one_place():
    """The dialect has ONE home; a second copy in the brain is the drift ADR-0012 stops.

    `fleet/brain.py` may consult the policy — it may not restate it. This fails if a
    capability id, or the registry-card path the resolver reads, is hard-coded into
    the brain again (the pre-port dialect), or if the floor vocabulary stops being
    derived from the policy.
    """
    import brain

    source = (FLEET / "brain.py").read_text(encoding="utf-8")
    for capability in policy().capability_ids():
        assert capability not in source, (
            f"fleet/brain.py hard-codes the capability {capability!r} — the mapping belongs to "
            "fleet/profiles/routing.policy.json (read by fleet/routing.py)"
        )
    assert "registry/personas" not in source, (
        "fleet/brain.py resolves registry cards itself — fleet/routing.py is the only reader"
    )
    assert brain.HIGH_FLOOR_LANES == policy().high_floor_tokens, (
        "the brain's floor vocabulary must be DERIVED from the policy, not declared beside it"
    )
    assert (brain.DEFAULT_TIER, brain.DEFAULT_THINKING) == policy().default_block
    assert "routing." in source, "the brain must consult the routing module"


def test_the_policy_is_the_only_routing_policy_in_the_fleet_layer():
    """A second policy file would be a second dialect, whatever it is called."""
    candidates = sorted(path.name for path in (FLEET / "profiles").glob("*.json"))
    assert candidates == ["brain.profile.json", "routing.policy.json"]
