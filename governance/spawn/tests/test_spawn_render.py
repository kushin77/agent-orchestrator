"""The rendered block: pure, complete, and the only copy of the prose (issue #793).

`scripts/check-spawn-envelope.sh` proves that the PROMPT a spawn carries is this
document's rendering, which is only provable if rendering is a pure function of
the document. These tests pin that, and pin the two claims the block must make:
the gate bound, and that the agent does not own the claim.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governance.spawn import model, render


def test_the_block_is_a_pure_function_of_the_document(envelope: dict) -> None:
    """Two documents that differ only in volatile fields must render identically.

    If a timestamp or a measured headroom leaked into the block, the gate could
    only count matching lines instead of proving the prompt IS this rendering.
    """
    other = json.loads(model.dumps(envelope))
    other["produced_at"] = "1999-01-01T00:00:00Z"
    other["capacity"] = {
        **other["capacity"],
        "effective": 7,
        "binding": "resource",
        "bounds": [{"name": "resource", "limit": 7, "why": "measured on another box"}],
    }

    assert render.render(envelope) == render.render(other)


def test_the_block_is_deterministic_across_calls(envelope: dict) -> None:
    assert render.render(envelope) == render.render(envelope)


def test_the_block_carries_a_marker_a_gate_can_search_for(envelope: dict) -> None:
    block = render.render(envelope)

    assert render.MARKER in block
    assert "spawn-envelope/v1" in block


def test_the_block_carries_every_fact_the_subagent_must_obey(envelope: dict) -> None:
    block = render.render(envelope)

    assert "#793" in block
    assert envelope["trailer"] in block
    assert envelope["session"]["id"] in block
    assert envelope["session"]["branch"] in block
    assert envelope["worktree"] in block
    assert envelope["claim"]["owner"] in block
    assert "#708" in block
    assert envelope["gate"]["of_record"] in block
    assert "AO-GR-22" in block
    assert envelope["capacity"]["permit"]["worktree_key"] in block
    assert f"attempt {envelope['budget']['attempts']}/{envelope['budget']['cap']}" in block
    assert envelope["verify"]["command"] in block


def test_the_block_says_the_gate_is_bounded_to_one_per_worktree(envelope: dict) -> None:
    """The rule one lane broke 26 times must be legible in every spawn's prompt."""
    block = render.render(envelope)

    assert "at most one composite gate per worktree" in block
    assert "bounded box-wide" in block


def test_the_block_says_the_claim_is_not_the_agents_to_take_or_release(envelope: dict) -> None:
    instructions = render.instructions(envelope)

    assert "ALREADY CLAIMED" in instructions
    assert "do NOT\nrun claim and do NOT run release" in instructions.replace("  ", " ") or (
        "run claim" in instructions and "run release" in instructions
    )


def test_the_block_keeps_the_standing_mandate_frontloaded(envelope: dict) -> None:
    prompt = render.prompt(
        envelope, directive_body="the order", directive_id="d-1", context_block="CONTEXT PACK\n"
    )

    assert prompt.index("STANDING MANDATE") < prompt.index(render.MARKER)
    assert prompt.index(render.MARKER) < prompt.index("BRAIN DIRECTIVE d-1")
    assert prompt.index("BRAIN DIRECTIVE d-1") < prompt.index("CONTEXT PACK")
    assert prompt.index("CONTEXT PACK") < prompt.index("Do exactly this, nothing else")


def test_the_mandate_leads_the_block_the_subagent_reads_first(envelope: dict) -> None:
    block = render.render(envelope)

    assert block.startswith("STANDING MANDATE")
    assert block.index("STANDING MANDATE") < block.index(render.MARKER)


def test_the_prose_lives_here_and_nowhere_else(envelope: dict) -> None:
    """A second copy is exactly how the two spawn paths drifted apart."""
    repo_root = Path(__file__).resolve().parents[3]
    terminal = (repo_root / "fleet" / "terminal.py").read_text(encoding="utf-8")
    for sentence in (
        "STANDING MANDATE",
        "GATE OF RECORD: run issue",
        "LEAVE EVERY ARTIFACT TERMINAL",
        "Do exactly this, nothing else",
    ):
        assert sentence not in terminal, f"fleet/terminal.py still inlines {sentence!r}"
    assert "STANDING MANDATE" in (repo_root / "governance" / "spawn" / "render.py").read_text(
        encoding="utf-8"
    )


def test_a_document_that_was_never_validated_is_not_rendered(envelope: dict) -> None:
    """The exporting surface refuses before it renders — never inside it."""
    from governance.spawn import EnvelopeRefused, render_block

    document = dict(envelope)
    document.pop("verify")

    with pytest.raises(EnvelopeRefused) as refused:
        render_block(document)

    assert "verify" in str(refused.value)
