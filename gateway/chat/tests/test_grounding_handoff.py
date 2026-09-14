"""The grounding hand-off: assembled elsewhere, carried through unchanged (#503).

The endpoint does **not** assemble grounding and does **not** declare a tool.  It
accepts what the grounding lane (``gateway/mcp``) assembled — the prefix, the
citations envelope and the tool declarations — and passes them through: the
prefix the model is given is the text the assembler produced (modulo the
guardrails lane's own untrusted-delimiter wrapping, which is the *guards'* job,
not this surface's), and the tool declarations reach the dispatch as the same
objects.

The integration here uses the **real** ``GroundingAssembler`` over a reachable
declared family, then holds the model's citations against the assembler's own
``CitationsEnvelope`` — so the "grounded" claim is checked by the authority that
produced the grounding, not by this lane's own opinion of it.
"""

from __future__ import annotations

import json

import pytest

from chat_fixtures import (
    AGENT,
    FRAGMENT_SOURCE,
    TENANT,
    grounded_payload,
    token_of,
    with_grounding,
)

from gateway.chat.errors import ChatSurfaceError
from mcp.grounding import CitationError


def assembled_turn():
    """A real ``gateway/mcp`` assembly over a reachable declared family."""
    from mcp.grounding import GroundingAssembler, GroundingRequest, Need

    assembler = GroundingAssembler(tenant_id=TENANT)
    return assembler.assemble(
        GroundingRequest(
            delta="Which agents are declared for this tenant?",
            needs=(Need(family="knowledge", operation="query", arguments=(("text", "adr"),)),),
        )
    )


def test_the_mcp_assembler_prefix_passes_through(rigged, credential):
    """A prefix assembled by ``gateway/mcp`` over a reachable family is carried through.

    Two things are held at once, and the second one is a **finding**:

    1. the assembled prefix reaches the dispatch (inside the guardrails lane's
       untrusted delimiters) — the pass-through the issue requires;
    2. the grounding lane's own ``source_id`` vocabulary
       (``board:#1@<revision>``) does **not** satisfy the citation pattern of
       ``registry/chat``'s ``grounded-answer`` schema
       (``^(bridge|tool_call|ticket):...``), so a turn grounded on that family
       cannot produce a schema-valid citation and the dispatch fails *closed*
       (``cannot_assess``).  This lane composes the two authorities and does not
       own either, so the mismatch is reported rather than papered over: it is a
       cross-lane contract item (``gateway/mcp`` ?? ``registry/chat``), not
       something this surface may fix by rewriting a source id it was given.
    """
    grounded = assembled_turn()
    assert grounded.fragments, grounded.why()
    fragment = grounded.fragments[0]
    prefix = grounded.render()
    prompts: list[object] = []
    inner = rigged.surface.gateway

    class Spy:
        def dispatch(self, agent_id, task_request):
            prompts.append(task_request)
            return inner.dispatch(agent_id, task_request)

    rigged.surface.gateway = Spy()
    rigged.script(
        grounded_payload(
            answer="The board snapshot names this ticket.", source_id=fragment.source_id
        )
    )
    body = with_grounding(
        {"model": "MED", "messages": [{"role": "user", "content": grounded.delta_text}]},
        {
            "prefix": prefix,
            "fragments": [
                {"source_id": fragment.source_id, "text": grounded.static_text, "kind": "board"}
            ],
        },
    )
    with pytest.raises(Exception) as raised:
        rigged.surface.completions(body, token=token_of(credential))
    error = raised.value
    # 1. the prefix was handed through (the guard wrapped it; nothing was dropped)
    assert prompts, "the dispatch never saw the assembled prefix"
    assert grounded.static_text in prompts[0].input["fragments"]
    # 2. the vocabulary mismatch is a loud, fail-closed refusal — never an answer
    assert error.status == 422
    assert error.to_openai_error()["error"]["code"] == "cannot_assess"
    assert rigged.records and rigged.records[0].outcome == "cannot_assess"


def test_the_grounding_envelope_checks_this_surface_citations(rigged, body, credential, grounding):
    """The grounding lane's own envelope verifies the citations this surface returns."""
    from mcp.grounding import Citation, CitationsEnvelope

    rigged.script(grounded_payload())
    response = rigged.surface.completions(
        with_grounding(body, grounding), token=token_of(credential)
    )
    cited = [source["id"] for source in response["ao"]["citations"]["sources"]]
    envelope = CitationsEnvelope(
        citations=(
            Citation(
                source_id=FRAGMENT_SOURCE,
                family="board",
                authority="fleet-board",
                revision="r7",
                kind="ticket",
            ),
        )
    )
    envelope.assert_grounded(cited)  # the authority agrees with the answer
    with pytest.raises(CitationError):
        envelope.assert_grounded(cited + ["ticket:NOT-GIVEN"])


def test_the_tool_declarations_pass_through_unchanged(rigged, body, credential, grounding):
    """Same objects, same order, nothing added — and none declared by the surface."""
    seen: list[object] = []
    inner = rigged.surface.gateway

    class Spy:
        def dispatch(self, agent_id, task_request):
            seen.append(task_request)
            return inner.dispatch(agent_id, task_request)

    rigged.surface.gateway = Spy()
    rigged.script(grounded_payload())
    request = with_grounding(body, grounding)
    response = rigged.surface.completions(request, token=token_of(credential))
    dispatched = seen[0].metadata["tools"]
    assert len(dispatched) == len(request["tools"])
    assert json.dumps(dispatched) == json.dumps(request["tools"], sort_keys=False)
    # the surface declares no tool of its own, and says so
    assert response["ao"]["dispatch"]["declaresTools"] is False
    assert response["ao"]["dispatch"]["toolsSupplied"] == len(request["tools"])


def test_no_tools_means_no_tools(rigged, body, credential, grounding):
    """A turn that declared none gets none: the surface adds no tool."""
    seen: list[object] = []
    inner = rigged.surface.gateway

    class Spy:
        def dispatch(self, agent_id, task_request):
            seen.append(task_request)
            return inner.dispatch(agent_id, task_request)

    rigged.surface.gateway = Spy()
    rigged.script(grounded_payload())
    block = dict(grounding)
    block.pop("tools", None)
    request = dict(body)
    request["grounding"] = block
    response = rigged.surface.completions(request, token=token_of(credential))
    assert list(seen[0].metadata["tools"]) == []
    assert response["ao"]["dispatch"]["toolsSupplied"] == 0


def test_an_unreadable_grounding_block_is_refused(rigged, body, credential):
    request = dict(body)
    request["grounding"] = {"fragments": [{"no_source": True}]}
    with pytest.raises(Exception) as raised:
        rigged.surface.completions(request, token=token_of(credential))
    error = raised.value
    assert getattr(error, "status", None) == 400
    assert error.to_openai_error()["error"]["code"] == "invalid_grounding"


def test_the_portal_client_hydrates_this_surface(rigged, body, credential, grounding):
    """The consumer's own accumulator parses these frames (issue #508's client)."""
    from portal.server.chat import _TurnCapture

    rigged.script(grounded_payload())
    frames = list(
        rigged.surface.completions_stream(
            with_grounding(dict(body, stream=True), grounding),
            token=token_of(credential),
        )
    )
    capture = _TurnCapture(tier="MED")
    for frame in frames:
        if frame.strip() == "data: [DONE]":
            continue
        capture.absorb(json.loads(frame[len("data:") :].strip()))
    turn = capture.as_turn("turn-1")
    assert turn["text"].startswith("The ingest worker")
    assert turn["grounding"]["state"] == "OK"
    assert turn["sources"][0]["id"] == FRAGMENT_SOURCE
    assert turn["fragments"][0]["supported"] is True
    assert FRAGMENT_SOURCE in turn["fragments"][0]["label"]
    assert turn["usage"]["state"] == "OK"
    assert turn["degraded"]["degraded"] is True  # MED claimed, LOW resolved
    assert turn["degraded"]["toTier"] == "LOW"
    assert turn["state"] == "complete"
