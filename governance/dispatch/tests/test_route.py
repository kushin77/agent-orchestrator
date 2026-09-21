"""The tier-space <-> capability-space adapter, issue #1701 (parent #1268).

`governance/dispatch/tiered.py` (claude/deepseek, L0/L1/L2) and
`fleet/routing.py` (hermes/paperclip, capability-space) do not compose today:
a task cannot flow claude -> deepseek -> hermes -> paperclip. `route.py` is
the one adapter that translates a routing *decision* between the two
vocabularies; the mailbox/dead-letter transport (`fleet/runaway.py`) is real
and untouched — only the providers are faked (no network).

This test drives one task end to end: claude -> deepseek -> hermes ->
paperclip, writing each hop's real dead-letter-shaped record through
`fleet/runaway.dead_letter`, and asserts the hop order and that the dead-letter
records name the runtime. It fails before `route.py` exists (no adapter to
resolve a mixed tier+capability task) and passes after.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "fleet"))

import route  # noqa: E402
import runaway  # noqa: E402
import routing  # noqa: E402


def test_task_flows_claude_deepseek_hermes_paperclip_via_the_adapter(tmp_path):
    """One task, four hops, resolved through both routers via route.resolve."""
    rp = routing.policy()

    # Hop 1-2 (tier-space): claude then deepseek, both at L0, via tiered.py.
    claude_hop = route.resolve({"tier": "L0"}, policy=None, routing_policy=rp)["hops"][0]
    assert claude_hop["runtime"] in ("claude", "deepseek")  # provider resolved by policy

    hop_claude = route.resolve({"tier": "L0", "provider": "claude"}, routing_policy=rp)["hops"][0]
    hop_deepseek = route.resolve({"tier": "L0", "provider": "deepseek"}, routing_policy=rp)["hops"][0]
    assert hop_claude["runtime"] == "claude"
    assert hop_deepseek["runtime"] == "deepseek"

    # Hop 3-4 (capability-space): hermes then paperclip, via fleet/routing.py,
    # reached through the SAME adapter call — the whole point of #1701.
    hop_hermes = route.resolve({"capability": "code-author"}, routing_policy=rp)["hops"][0]
    hop_paperclip = route.resolve({"capability": "research"}, routing_policy=rp)["hops"][0]
    assert hop_hermes["runtime"] == "hermes"
    assert hop_paperclip["runtime"] == "paperclip"

    # A single task naming BOTH a tier and a capability resolves through BOTH
    # routers in one call — the negative control the issue asks for: before
    # route.py existed, nothing could do this.
    mixed = route.resolve(
        {"tier": "L1", "provider": "deepseek", "capability": "code-author"}, routing_policy=rp
    )
    assert [hop["runtime"] for hop in mixed["hops"]] == ["deepseek", "hermes"]

    ordered_hops = [hop_claude, hop_deepseek, hop_hermes, hop_paperclip]
    directive_id = "route-1701-e2e"

    # Drive the hop chain through the REAL mailbox/dead-letter layer
    # (fleet/runaway.py) — providers are faked (no network: no gh, no claude
    # subprocess, no live Nous call), but the terminal artifact is the real
    # one `control:drop` and the runaway guard both write.
    for index, hop in enumerate(ordered_hops):
        runaway.dead_letter(
            f"{directive_id}-{index}",
            f"hop {index}: routed to {hop['runtime']}",
            base=tmp_path,
            dropped_by=hop["runtime"],
        )

    records = [
        runaway.record_shape(base=tmp_path, directive_id=f"{directive_id}-{i}")
        for i in range(len(ordered_hops))
    ]
    named_runtimes = [record["dropped_by"] for record in records]
    assert named_runtimes == ["claude", "deepseek", "hermes", "paperclip"]
    for hop, record in zip(ordered_hops, records):
        assert hop["runtime"] in record["reason"]


def test_hermes_hop_is_classified_not_refused_as_unknown_runtime():
    """The claude->deepseek->hermes->paperclip e2e path's hermes hop must be a
    peer-check-recognized identity (issue #1562), not fall through to
    ``unknown``. Fails before peers.classify_channel knows a hermes prefix,
    passes after.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import peers  # noqa: E402

    rp = routing.policy()
    hop_hermes = route.resolve({"capability": "code-author"}, routing_policy=rp)["hops"][0]
    assert hop_hermes["runtime"] == "hermes"

    channel, evidence = peers.classify_channel(f"{hop_hermes['runtime']}-agent-1")
    assert channel == "hermes"
    assert "hermes" in evidence.lower()


def test_resolve_refuses_a_persona_outside_capability_space(tmp_path):
    """A capability-space persona route.py cannot hand off to is a named refusal."""
    rp = routing.policy()

    class _FakePolicy:
        personas = rp.personas
        finops = rp.finops

        def route(self, task):
            return {"persona": "not-a-real-persona", "tier": "flash"}

    import pytest

    with pytest.raises(routing.RoutingRefusal) as exc:
        route.resolve({"capability": "code-author"}, routing_policy=_FakePolicy())
    assert exc.value.code == "runtime-not-capability-space"


def test_resolve_refuses_a_task_naming_neither_tier_nor_capability():
    import pytest
    import tiered

    with pytest.raises(tiered.TieredRefusal) as exc:
        route.resolve({})
    assert exc.value.reason == "route-empty"


def test_tier_for_runtime_maps_capability_space_personas_into_tier_space():
    rp = routing.policy()
    assert route.tier_for_runtime("hermes", routing_policy=rp) == "L0"
    assert route.tier_for_runtime("paperclip", routing_policy=rp) == "L0"
    assert route.tier_for_runtime("nobody", routing_policy=rp) is None
