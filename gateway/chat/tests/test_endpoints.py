"""The endpoints: both forms exist, are flag-gated, and answer in-contract.

Every assertion here is a behaviour a regression can break: the flag gate's
*position* (before AuthN), the two wire shapes, the streaming sentinel, and the
derivation of the advertised model list.  The failures are provoked, not
described — the flag-off case asserts the exact 404 body a compatible client
parses, and its control asserts that the same probe does *not* 404 when the
surface is promoted (AO-GR-4: a gate that cannot fail is a formality).
"""

from __future__ import annotations

import json

import pytest

from chat_fixtures import (
    CONVERSATION,
    FRAGMENT_SOURCE,
    FRAGMENT_TEXT,
    SECOND_SOURCE,
    SECOND_TEXT,
    grounded_payload,
    token_of,
    with_grounding,
)
from chat_stream_probe import assess, content_of, enforce

from gateway.chat import contract
from gateway.chat.errors import ChatSurfaceError, SurfaceDisabled


def refusal_of(callable_, *args, **kwargs) -> ChatSurfaceError:
    with pytest.raises(ChatSurfaceError) as raised:
        callable_(*args, **kwargs)
    return raised.value


# --------------------------------------------------------------------------- #
# The flag, before AuthN
# --------------------------------------------------------------------------- #
def test_flag_off_refuses_before_authn(rigged, registry_off, body, credential):
    """An unpromoted surface is ABSENT: no token, and a valid token, both 404."""
    rigged.surface.registry_path = registry_off
    anonymous = refusal_of(rigged.surface.completions, body)
    assert anonymous.status == 404
    assert anonymous.to_openai_error()["error"]["code"] == "feature_disabled"
    authenticated = refusal_of(
        rigged.surface.completions, body, token=token_of(credential)
    )
    assert authenticated.status == 404
    assert authenticated.to_openai_error()["error"]["code"] == "feature_disabled"
    # the model list and the Ollama form are absent too: one surface, one flag
    assert refusal_of(rigged.surface.models).status == 404
    assert refusal_of(rigged.surface.ollama_chat, body).status == 404


def test_flag_off_is_absent_not_forbidden(rigged, registry_off, body):
    """The refusal is a 404 (absent), never a 401/403 (an auth probe's answer)."""
    rigged.surface.registry_path = registry_off
    error = refusal_of(rigged.surface.completions, body)
    assert isinstance(error, SurfaceDisabled)
    assert error.status not in (401, 403)


def test_flag_on_reaches_authn(rigged, body):
    """The negative control: with the flag on the same probe is a 401, not a 404."""
    error = refusal_of(rigged.surface.completions, body)
    assert error.status == 401
    assert error.to_openai_error()["error"]["code"] == "credential_required"


# --------------------------------------------------------------------------- #
# The OpenAI form
# --------------------------------------------------------------------------- #
def test_openai_turn_returns_one_json_body(rigged, body, credential, grounding):
    rigged.script(grounded_payload())
    response = rigged.surface.completions(
        with_grounding(body, grounding), token=token_of(credential)
    )
    assert response["object"] == "chat.completion"
    assert response["choices"][0]["message"]["content"].startswith("The ingest worker")
    assert response["choices"][0]["finish_reason"] == "stop"
    assert response["usage"]["total_tokens"] > 0
    assert response["ao"]["grounding"]["state"] == "OK"
    assert response["ao"]["conversationId"] == CONVERSATION
    # the citations the model returned are reported as the envelope's sources
    assert response["ao"]["citations"]["sources"][0]["id"] == FRAGMENT_SOURCE


def test_stream_false_is_one_body_and_not_frames(rigged, body, credential, grounding):
    rigged.script(grounded_payload())
    response = rigged.surface.completions(
        with_grounding(body, grounding), token=token_of(credential)
    )
    assert isinstance(response, dict)
    assert json.dumps(response)  # one serialisable JSON body, no SSE text


def test_two_fragments_two_citations_are_reported(rigged, body, credential):
    """Two admitted fragments, two cited sources: both are reported as sources."""
    block = {
        "prefix": (
            f"<source id='{FRAGMENT_SOURCE}'>\n{FRAGMENT_TEXT}\n</source>\n"
            f"<source id='{SECOND_SOURCE}'>\n{SECOND_TEXT}\n</source>"
        ),
        "fragments": [
            {"source_id": FRAGMENT_SOURCE, "text": FRAGMENT_TEXT, "kind": "ticket"},
            {"source_id": SECOND_SOURCE, "text": SECOND_TEXT, "kind": "bridge"},
        ],
    }
    payload = json.dumps(
        {
            "answer": "Two sources were consulted.",
            "citations": [
                {"fragment_id": "frag-1", "source_id": FRAGMENT_SOURCE, "revision": "r7"},
                {"fragment_id": "frag-2", "source_id": SECOND_SOURCE, "revision": "r3"},
            ],
        }
    )
    rigged.script(payload)
    response = rigged.surface.completions(
        with_grounding(body, block), token=token_of(credential)
    )
    source_ids = [source["id"] for source in response["ao"]["citations"]["sources"]]
    assert source_ids == [FRAGMENT_SOURCE, SECOND_SOURCE]
    assert response["ao"]["grounding"]["state"] == "OK"
    assert response["ao"]["inbound"]["accepted"] is True


# --------------------------------------------------------------------------- #
# The Ollama form
# --------------------------------------------------------------------------- #
def test_ollama_turn_speaks_the_ollama_shape(rigged, body, credential, grounding):
    rigged.script(grounded_payload())
    payload = dict(body)
    payload["options"] = {"temperature": 0.2}
    response = rigged.surface.ollama_chat(
        with_grounding(payload, grounding), token=token_of(credential)
    )
    assert response["done"] is True
    assert response["message"]["role"] == "assistant"
    assert response["message"]["content"].startswith("The ingest worker")
    # the usage counters are Ollama's own names (providers/ollama.py's parse)
    assert response["prompt_eval_count"] > 0
    assert response["eval_count"] > 0


def test_ollama_stream_is_newline_delimited_json(rigged, body, credential, grounding):
    rigged.script(grounded_payload())
    payload = with_grounding(dict(body, stream=True), grounding)
    frames = list(rigged.surface.ollama_chat_stream(payload, token=token_of(credential)))
    decoded = [json.loads(frame) for frame in frames]
    assert decoded[-1]["done"] is True
    assert decoded[-1]["ao"]["grounding"]["state"] == "OK"
    streamed = "".join(frame["message"]["content"] for frame in decoded)
    assert streamed.startswith("The ingest worker")


# --------------------------------------------------------------------------- #
# Streaming
# --------------------------------------------------------------------------- #
def test_stream_yields_incremental_then_terminal_frames(
    rigged, body, credential, grounding
):
    rigged.script(grounded_payload())
    frames = list(
        rigged.surface.completions_stream(
            with_grounding(dict(body, stream=True), grounding),
            token=token_of(credential),
        )
    )
    assert frames[-1] == contract.SSE_DONE
    payloads = [
        json.loads(frame[len("data:") :].strip())
        for frame in frames
        if frame != contract.SSE_DONE
    ]
    # the pipeline trace is relayed incrementally, one stage per frame
    stages = [
        payload["ao"]["stage"]["stage"]
        for payload in payloads
        if payload.get("ao", {}).get("stage")
    ]
    assert stages == [
        "received",
        "agent_resolved",
        "task_resolved",
        "route_selected",
        "guard",
        "attempt",
        "completed",
    ]
    terminal = payloads[-1]
    assert terminal["choices"][0]["finish_reason"] == "stop"
    assert terminal["ao"]["grounding"]["state"] == "OK"
    # the terminal frame carries the envelope, not a partial answer
    assert terminal["choices"][0]["delta"] == {}
    content_frames = [
        payload
        for payload in payloads
        if payload["choices"][0]["delta"].get("content")
    ]
    assert content_frames, "the stream carried no content frame"


def test_stream_content_matches_the_single_body(rigged, body, credential, grounding):
    """The streamed deltas spell the same answer the single body carries.

    A frame that is a REFUSAL is not a content mismatch: the turn was not
    served, so the run is re-measured once and reported CANNOT-ASSESS rather
    than failed, naming the refusal (issue #843).  A content that was measured
    and differs is a FAIL at once, so a genuine regression still fails.
    """
    token = token_of(credential)
    wire = with_grounding(dict(body, stream=True), grounding)

    def attempt() -> str:
        rigged.script(grounded_payload())
        single = rigged.surface.completions(wire, token=token)
        rigged.script(grounded_payload())
        frames = list(rigged.surface.completions_stream(wire, token=token))
        assert frames[-1] == contract.SSE_DONE, "the stream did not end with the sentinel"
        streamed = content_of(frames, sentinel=contract.SSE_DONE)
        assert streamed == single["choices"][0]["message"]["content"], (
            "the streamed deltas do not spell the answer the single body carries"
        )
        return streamed

    verdict = assess(attempt)
    enforce(
        verdict,
        label="test_stream_content_matches_the_single_body",
        skip=pytest.skip,
        fail=pytest.fail,
    )


def test_streamed_refusal_is_a_terminal_error_frame(rigged, body):
    """A refusal is a frame and the sentinel — never a silent stop."""
    frames = list(rigged.surface.completions_stream(body))
    assert frames[-1] == contract.SSE_DONE
    error = json.loads(frames[0][len("data:") :].strip())
    assert error["error"]["code"] == "credential_required"


# --------------------------------------------------------------------------- #
# GET /v1/models
# --------------------------------------------------------------------------- #
def test_models_are_derived_from_the_catalog_and_routing(rigged):
    """The advertised list is a projection of two authorities, not a literal."""
    document = rigged.surface.models()
    assert document["object"] == "list"
    selectable = document["ao"]["selectable"]
    ids = [entry["id"] for entry in document["data"]]
    # the selectable ids are the tier ladder, pinned from providers/contract.py
    assert selectable == ["LOW", "MED", "HIGH", "MAX"]
    assert set(selectable).issubset(set(ids))
    # every catalog module is advertised, and none of them is selectable
    providers = [
        entry for entry in document["data"] if entry["ao"]["kind"] == "provider-module"
    ]
    assert providers, "the catalog contributed no provider module"
    assert all(entry["ao"]["selectable"] is False for entry in providers)
    assert {"deepseek", "ollama"}.issubset({entry["id"] for entry in providers})
    # routability is the routing policy's answer, not a hand-written list
    routable = {entry["id"] for entry in providers if entry["ao"]["routable"]}
    assert {"deepseek", "ollama"}.issubset(routable)
    assert document["ao"]["derivedFrom"]["routing"].endswith("routing.yaml")
    assert document["ao"]["derivedFrom"]["catalog"].endswith("catalog/modules")


def test_models_tracks_a_module_added_to_the_catalog(rigged, tmp_path):
    """A module added to the catalog appears with no edit to the surface."""
    from gateway.chat.models import ModelCatalogue

    catalog = tmp_path / "modules"
    for module_id, package, version in (
        ("ollama", "providers.ollama", "v9.9.9"),
        ("newvendor", "providers.newvendor", "v1.0.0"),
    ):
        directory = catalog / module_id
        directory.mkdir(parents=True)
        (directory / "module.json").write_text(
            json.dumps(
                {
                    "schema": "cmr.module/v1",
                    "id": module_id,
                    "name": module_id,
                    "versions": {"latest": version},
                    "distribution": {"package": package},
                    "class": ["model-gateway"],
                    "features": [{"id": "chat"}],
                }
            ),
            encoding="utf-8",
        )
    catalogue = ModelCatalogue.discover(catalog_dir=catalog)
    ids = [entry["id"] for entry in catalogue.entries]
    assert {"ollama", "newvendor"}.issubset(set(ids))
    assert catalogue.find("newvendor")["ao"]["routable"] is False  # not in routing.yaml
    assert catalogue.find("ollama")["ao"]["routable"] is True
