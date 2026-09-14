"""gateway.chat.errors — the OpenAI-compatible error taxonomy (issue #503).

Every refusal this surface issues is one of these, and every one of them
serialises to the shape an OpenAI-compatible client already parses:

.. code-block:: json

    {"error": {"message": "...", "type": "...", "param": null, "code": "..."}}

Fail-closed is a property of the *taxonomy*, not of a caller's discipline: a
refusal is a typed error carrying the HTTP status it earns, so there is no code
path that answers a refused turn with a success envelope.  The dispatch
outcomes of ``gateway/proxy`` are mapped onto this taxonomy by
:func:`refusal_for_outcome`, which reuses the proxy's own status contract
(``proxy.handler.outcome_status``) rather than re-deriving one.
"""

from __future__ import annotations

from typing import Any, Optional

# The four `type` values an OpenAI-compatible client branches on, plus the
# two the platform needs for its own rails.
ERROR_TYPE_INVALID_REQUEST = "invalid_request_error"
ERROR_TYPE_AUTHENTICATION = "authentication_error"
ERROR_TYPE_PERMISSION = "permission_error"
ERROR_TYPE_NOT_FOUND = "not_found_error"
ERROR_TYPE_QUOTA = "insufficient_quota"
ERROR_TYPE_SERVER = "server_error"


class ChatSurfaceError(Exception):
    """Base class: an HTTP status plus an OpenAI-compatible error body."""

    status: int = 500
    code: str = "internal_error"
    error_type: str = ERROR_TYPE_SERVER

    def __init__(
        self,
        message: str,
        *,
        param: Optional[str] = None,
        code: Optional[str] = None,
        error_type: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = str(message)
        self.param = param
        #: Optional non-error evidence (the `ao` block) a refusal may carry.
        self.extra: dict[str, Any] = {}
        if code is not None:
            self.code = str(code)
        if error_type is not None:
            self.error_type = str(error_type)

    def to_openai_error(self) -> dict[str, Any]:
        """The serialisable error envelope (never carries payload text)."""
        return {
            "error": {
                "message": self.message,
                "type": self.error_type,
                "param": self.param,
                "code": self.code,
            }
        }

    @property
    def body(self) -> dict[str, Any]:
        return self.to_openai_error()


class SurfaceDisabled(ChatSurfaceError):
    """The `surfaces.chat` flag is off: the surface is absent, not refused.

    Raised **before** AuthN, so an unpromoted surface is invisible rather than
    distinguishable by an authentication probe (ADR-0023 §5 / the ``live_bridge``
    precedent).  The body is deliberately the OpenAI shape: a client that only
    knows the compatible contract still parses it.
    """

    status = 404
    code = "feature_disabled"
    error_type = ERROR_TYPE_NOT_FOUND


class CredentialRequired(ChatSurfaceError):
    """No credential was presented (there is no anonymous chat)."""

    status = 401
    code = "credential_required"
    error_type = ERROR_TYPE_AUTHENTICATION


class CredentialRefused(ChatSurfaceError):
    """The presented credential did not verify (expired, revoked, forged)."""

    status = 401
    code = "invalid_api_key"
    error_type = ERROR_TYPE_AUTHENTICATION


class CrossTenantRefused(ChatSurfaceError):
    """The request named a tenant other than the credential's own."""

    status = 403
    code = "tenant_mismatch"
    error_type = ERROR_TYPE_PERMISSION


class MalformedRequest(ChatSurfaceError):
    """The request body could not be read (messages, roles, types)."""

    status = 400
    code = "invalid_request"
    error_type = ERROR_TYPE_INVALID_REQUEST


class UnknownModel(ChatSurfaceError):
    """The requested model id is not one this surface advertises."""

    status = 404
    code = "model_not_found"
    error_type = ERROR_TYPE_NOT_FOUND


class ModelNotSelectable(ChatSurfaceError):
    """The named model is advertised for discovery but is not a selectable id.

    The surface selects a **tier**; a provider model id is what the chooser
    resolves, never what a client asks for (ADR-0023 §4).
    """

    status = 400
    code = "model_not_selectable"
    error_type = ERROR_TYPE_INVALID_REQUEST


class GroundingUnreadable(ChatSurfaceError):
    """The assembled grounding block could not be read (fail closed)."""

    status = 400
    code = "invalid_grounding"
    error_type = ERROR_TYPE_INVALID_REQUEST


class TurnRefused(ChatSurfaceError):
    """A guardrail aborted the turn before it reached a model.

    The body carries rule ids only — never the payload that tripped them
    (the guardrails lane's own no-echo rule).
    """

    status = 403
    code = "guardrail_blocked"
    error_type = ERROR_TYPE_PERMISSION


class BudgetRefused(ChatSurfaceError):
    """The budget rail refused the turn before the model call."""

    status = 429
    code = "budget_blocked"
    error_type = ERROR_TYPE_QUOTA


class UngroundedResponse(ChatSurfaceError):
    """The model's answer did not survive inbound re-validation.

    An answer that cites nothing it was given is refused rather than returned:
    a fabricated answer is a disclosure, not a degraded success.
    """

    status = 422
    code = "ungrounded_response"
    error_type = ERROR_TYPE_INVALID_REQUEST


class UpstreamRefused(ChatSurfaceError):
    """The dispatch itself did not serve the turn (fail closed, never a body)."""

    status = 502
    code = "upstream_failed"
    error_type = ERROR_TYPE_SERVER


#: Dispatch outcome -> (status, code, error type) for the outcomes the chat
#: surface can actually produce.  The status half is the proxy's own contract
#: (`proxy.handler.OUTCOME_STATUS`); only the OpenAI `type`/`code` vocabulary is
#: added here.
_OUTCOME_ERRORS: dict[str, tuple[str, str]] = {
    "denied": ("capability_denied", ERROR_TYPE_PERMISSION),
    "blocked": ("budget_blocked", ERROR_TYPE_QUOTA),
    "rate_limited": ("rate_limited", ERROR_TYPE_QUOTA),
    "refused": ("output_refused", ERROR_TYPE_INVALID_REQUEST),
    "cannot_assess": ("cannot_assess", ERROR_TYPE_INVALID_REQUEST),
    "no_healthy_route": ("no_healthy_route", ERROR_TYPE_SERVER),
    "failed": ("upstream_failed", ERROR_TYPE_SERVER),
}


def refusal_for_outcome(outcome: str, message: str) -> ChatSurfaceError:
    """Map a non-served dispatch outcome onto the compatible error taxonomy."""
    from proxy.handler import outcome_status

    code, error_type = _OUTCOME_ERRORS.get(
        outcome, (outcome or "upstream_failed", ERROR_TYPE_SERVER)
    )
    status = outcome_status(outcome)
    if status == 200:  # defensive: a served outcome is never a refusal
        status = 502
        code = "upstream_failed"
        error_type = ERROR_TYPE_SERVER
    return DispatchRefused(
        status=status, message=message, code=code, error_type=error_type
    )


class DispatchRefused(UpstreamRefused):
    """A refusal whose HTTP status is the proxy's own outcome-status contract."""

    def __init__(
        self, *, status: int, message: str, code: str, error_type: str
    ) -> None:
        super().__init__(message, code=code, error_type=error_type)
        self.status = int(status)
