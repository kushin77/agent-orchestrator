"""Nothing the request body says about identity, keys or spend is authoritative.

These are the acceptance criteria that a "helpful" refactor breaks first: a body
``tenantId`` that selects a tenant, an ``api_key`` that reaches the model, a
``budget`` that overrides the FinOps rail, a conversation id that widens the
credential's scope.  Every case asserts the refusal *or* the non-effect, and the
control asserts the legitimate path still works (a refusal that refuses
everything proves nothing).
"""

from __future__ import annotations

import pytest

from chat_fixtures import (
    CONVERSATION,
    OTHER_CONVERSATION,
    OTHER_TENANT,
    TENANT,
    grounded_payload,
    token_of,
    with_grounding,
)

from gateway.chat.errors import ChatSurfaceError


def refusal_of(callable_, *args, **kwargs) -> ChatSurfaceError:
    with pytest.raises(ChatSurfaceError) as raised:
        callable_(*args, **kwargs)
    return raised.value


# --------------------------------------------------------------------------- #
# The credential is the front door
# --------------------------------------------------------------------------- #
def test_no_token_is_refused(rigged, body, grounding):
    error = refusal_of(rigged.surface.completions, with_grounding(body, grounding))
    assert error.to_openai_error()["error"]["code"] == "credential_required"


def test_forged_token_is_refused(rigged, body, grounding):
    error = refusal_of(
        rigged.surface.completions,
        with_grounding(body, grounding),
        token="nope",
    )
    assert error.status == 401
    assert error.to_openai_error()["error"]["code"] == "invalid_api_key"


def test_revoked_credential_is_refused(rigged, body, grounding, credential, revocation_store):
    """An unexpired credential whose jti is revoked never reaches the model."""
    from identity.sso.tokens import revoke_jti

    revoke_jti(revocation_store, credential.session.token_id, int(credential.session.issued_at) + 1)
    error = refusal_of(
        rigged.surface.completions,
        with_grounding(body, grounding),
        token=token_of(credential),
    )
    assert error.status == 401
    assert error.to_openai_error()["error"]["code"] == "invalid_api_key"


def test_a_token_scoped_to_another_conversation_is_refused(rigged, body, grounding, mint):
    foreign = mint(conversation_id=OTHER_CONVERSATION)
    request = dict(with_grounding(body, grounding))
    request["conversationId"] = CONVERSATION
    error = refusal_of(rigged.surface.completions, request, token=token_of(foreign))
    assert error.status == 403
    assert error.to_openai_error()["error"]["code"] == "tenant_mismatch"


# --------------------------------------------------------------------------- #
# A claimed tenant is refused, never honoured
# --------------------------------------------------------------------------- #
def test_a_foreign_tenant_claim_is_refused(rigged, body, grounding, credential):
    request = dict(with_grounding(body, grounding))
    request["tenantId"] = OTHER_TENANT
    error = refusal_of(rigged.surface.completions, request, token=token_of(credential))
    assert error.status == 403
    assert error.to_openai_error()["error"]["code"] == "tenant_mismatch"
    assert rigged.records == [], "a refused tenant claim still reached a provider"


def test_the_correct_tenant_claim_changes_nothing(rigged, body, grounding, credential):
    """The claim is recorded as a claim; the credential remains the authority."""
    rigged.script(grounded_payload())
    request = dict(with_grounding(body, grounding))
    request["tenantId"] = TENANT
    response = rigged.surface.completions(request, token=token_of(credential))
    assert response["ao"]["tenantId"] == TENANT
    assert "tenantId" in response["ao"]["claim"]["ignored"]
    assert response["ao"]["claim"]["honoured"] is False


def test_a_body_api_key_is_ignored(rigged, body, grounding, credential):
    """A body credential is neither read nor passed on: the token is the only key."""
    rigged.script(grounded_payload())
    request = dict(with_grounding(body, grounding))
    request["api_key"] = "ignored-by-design"
    response = rigged.surface.completions(request, token=token_of(credential))
    assert response["ao"]["tenantId"] == TENANT
    assert response["ao"]["claim"]["ignored"] == ["api_key"]
    assert "ignored-by-design" not in json_of(response)


def test_a_body_budget_cannot_override_the_finops_rail(rigged, body, grounding, credential):
    """The budget figure comes from telemetry/chat's rails, never from a body."""
    rigged.script(grounded_payload())
    request = dict(with_grounding(body, grounding))
    request["budgetUsd"] = 0
    request["role"] = "root_admin"
    response = rigged.surface.completions(request, token=token_of(credential))
    assert sorted(response["ao"]["claim"]["ignored"]) == ["budgetUsd", "role"]
    # the rail's own verdict is reported, and it is the shipped (observe) one
    assert response["ao"]["budget"]["decision"] in ("allow", "observe", "would_warn")


# --------------------------------------------------------------------------- #
# The flag is checked before AuthN
# --------------------------------------------------------------------------- #
def test_flag_off_beats_a_forged_credential(rigged, registry_off, body, grounding):
    """An unpromoted surface is absent even to a *bad* credential (flag first)."""
    rigged.surface.registry_path = registry_off
    error = refusal_of(
        rigged.surface.completions, with_grounding(body, grounding), token="forged"
    )
    assert error.status == 404
    assert error.to_openai_error()["error"]["code"] == "feature_disabled"


def json_of(document) -> str:
    import json

    return json.dumps(document, sort_keys=True)
