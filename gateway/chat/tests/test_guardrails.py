"""Guardrails: a refused turn never reaches a provider, and never lands ungrounded.

Two directions are asserted, and both are provoked:

* **outbound** — retrieved material carrying an injection is quarantined and the
  turn is refused (403, rule ids only), and a DLP block-class value in the prompt
  aborts the call.  In both cases the audit sink is **empty**: the provider was
  never dispatched, which is the only proof that a "block" actually blocked.
* **inbound** — an answer that cites a source the turn was never given, or that
  cites nothing at all, is refused (422) rather than returned to the client.
"""

from __future__ import annotations

import pytest

from chat_fixtures import (
    FRAGMENT_SOURCE,
    POISONED_TEXT,
    grounded_payload,
    token_of,
    with_grounding,
)

from gateway.chat.errors import ChatSurfaceError


def refusal_of(callable_, *args, **kwargs) -> ChatSurfaceError:
    with pytest.raises(ChatSurfaceError) as raised:
        callable_(*args, **kwargs)
    return raised.value


def block_class_value() -> str:
    """A DLP block-class payload, assembled at runtime (never a literal leak)."""
    return "api" + "_key = " + "Zq4" + "m" * 24


# --------------------------------------------------------------------------- #
# Outbound: the retrieval-injection guard
# --------------------------------------------------------------------------- #
def test_a_poisoned_fragment_is_refused_before_dispatch(rigged, body, credential, grounding):
    rigged.script(grounded_payload())
    request = with_grounding(
        dict(body),
        {
            "prefix": f"<source id='{FRAGMENT_SOURCE}'>\n{POISONED_TEXT}\n</source>",
            "fragments": [
                {"source_id": FRAGMENT_SOURCE, "text": POISONED_TEXT, "kind": "ticket"}
            ],
        },
    )
    error = refusal_of(rigged.surface.completions, request, token=token_of(credential))
    assert error.status == 403
    assert error.to_openai_error()["error"]["code"] == "guardrail_blocked"
    assert rigged.records == [], "a quarantined fragment still reached a provider"
    assert rigged.metered == [], "a refused turn must not be billed"
    verdicts = error.extra["verdicts"]
    assert any(verdict["guard"] == "retrieval" for verdict in verdicts)


def test_a_benign_fragment_is_admitted_and_reaches_the_provider(
    rigged, body, credential, grounding
):
    """The control: the same path with benign material dispatches, guard-wrapped."""
    seen: list[object] = []
    inner = rigged.surface.gateway

    class Spy:
        def dispatch(self, agent_id, task_request):
            seen.append(task_request)
            return inner.dispatch(agent_id, task_request)

    rigged.surface.gateway = Spy()
    rigged.script(grounded_payload())
    response = rigged.surface.completions(
        with_grounding(body, grounding), token=token_of(credential)
    )
    assert response["ao"]["grounding"]["admitted"] == [FRAGMENT_SOURCE]
    assert len(rigged.records) == 1
    # the material the model was given is the guard's own wrapped form, never the
    # raw retrieved text (an injected closing delimiter cannot escape it)
    assert "<untrusted>" in seen[0].input["fragments"]
    assert any(
        verdict["guard"] == "retrieval" for verdict in response["ao"]["verdicts"]
    )


# --------------------------------------------------------------------------- #
# Outbound: DLP egress
# --------------------------------------------------------------------------- #
def test_a_dlp_block_class_value_aborts_the_call(rigged, body, grounding, credential):
    rigged.script(grounded_payload())
    request = with_grounding(
        dict(body, messages=[{"role": "user", "content": f"Draft the note. {block_class_value()}"}]),
        grounding,
    )
    error = refusal_of(rigged.surface.completions, request, token=token_of(credential))
    assert error.status == 403
    assert error.to_openai_error()["error"]["code"] == "guardrail_blocked"
    assert rigged.records == [], "a blocked prompt still reached a provider"
    # the refusal names the rule family, never the payload it tripped on
    assert block_class_value() not in error.message


def test_a_clean_prompt_is_not_refused_by_the_egress_guard(
    rigged, body, credential, grounding
):
    """The control for the test above: a clean prompt dispatches."""
    rigged.script(grounded_payload())
    response = rigged.surface.completions(
        with_grounding(body, grounding), token=token_of(credential)
    )
    assert response["ao"]["grounding"]["state"] == "OK"


# --------------------------------------------------------------------------- #
# Inbound: the answer is re-validated, not trusted
# --------------------------------------------------------------------------- #
def test_a_citation_the_turn_was_never_given_is_refused(rigged, body, credential, grounding):
    from chat_fixtures import fabricated_payload

    rigged.script(fabricated_payload())
    error = refusal_of(
        rigged.surface.completions,
        with_grounding(body, grounding),
        token=token_of(credential),
    )
    assert error.status == 422
    assert error.to_openai_error()["error"]["code"] == "ungrounded_response"
    assert error.extra["inbound"]["accepted"] is False


def test_an_uncited_answer_never_validates(rigged, body, credential, grounding):
    """``chat-answer``'s own schema demands one citation; the floor enforces it."""
    from chat_fixtures import uncited_payload

    rigged.script(uncited_payload())
    error = refusal_of(
        rigged.surface.completions,
        with_grounding(body, grounding),
        token=token_of(credential),
    )
    assert error.status == 422
    # either the module's schema (the proxy's typed validation) or the surface's
    # citation floor refuses it — both are fail-closed, and both are honest
    assert error.to_openai_error()["error"]["code"] in (
        "ungrounded_response",
        "cannot_assess",
    )
