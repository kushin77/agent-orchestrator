"""The REST surface's error model (issue #651, EPIC #645, ERP-06).

Every refusal this surface emits is the house envelope —
``{ok, status, requestId, data, error}``, built by
``identity/cpapi/router.ok_envelope`` / ``error_envelope`` — so a client that
already speaks the control-plane dialect needs no second one. Nothing here
invents a response shape.

**Two vocabularies, and they are different kinds of thing.**

* The **model's** refusals. ``integrations/erp/core/errors.py`` declares a closed
  ``CODES`` vocabulary with a status per code. This surface *re-raises* those
  objects untouched: the code and the status the ERP model chose are what the
  client sees. That is what "the model is consumable without a translation
  layer" has to mean in practice, and ``negative_control`` measures it
  (``model-refusal-passthrough``) rather than asserting it in prose.

* The **surface's** boundary refusals — the ones that exist only because there
  is a request at all: no route, no such document, the wrong method, no
  principal, a declaration the decision needs but cannot read. Those are
  :data:`BOUNDARY_CODES`, and this surface originates them and nothing else.

There is deliberately **no** code for "a request named another tenant": the
tenant is never read from the request (see ``surface.py``), so such a request
cannot be expressed, and a code for it would be unreachable.

**The model's statuses are read, never re-typed.** :data:`MODEL_STATUS` is built
at import by *constructing* each refusal with its own factory and reading the
status back, and it refuses to import if its key set is not exactly the model's
``CODES``. A status table copied by hand could disagree with the model it claims
to describe; this one is derived, so it cannot.

**No secret is ever carried (GR-6).** A refusal names a route, a document id, a
kind or a reason — never a credential, and never a value that was refused.

---knowledge---
module_id: integrations.erp.api.errors
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [SurfaceError, unauthorized, forbidden, not_found, document_not_found, method_not_allowed, conflict, unavailable, (+3 more)]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Tuple

from identity.cpapi.errors import ApiError

from integrations.erp.auth import model as auth_model
from integrations.erp.auth.model import Refused
from integrations.erp.core import errors as model_errors

__all__ = [
    "AUTH_STATUS",
    "BOUNDARY_CODES",
    "EMITTED_REFUSALS",
    "MODEL_STATUS",
    "STATUSES",
    "SurfaceError",
    "conflict",
    "document_not_found",
    "forbidden",
    "from_decision",
    "from_refusal",
    "internal",
    "method_not_allowed",
    "not_found",
    "unauthorized",
    "unavailable",
]

#: The model's own factories, one per code in ``core.errors.CODES``. Used to
#: *read* each code's status out of the model rather than restating it.
_MODEL_PROBES: Tuple[Tuple[str, Any], ...] = (
    ("invalid_body", lambda: model_errors.invalid_body("probe")),
    ("schema_violation", lambda: model_errors.schema_violation("probe")),
    ("unknown_document_kind", lambda: model_errors.unknown_document_kind("probe")),
    ("unknown_workflow", lambda: model_errors.unknown_workflow("probe")),
    ("unknown_state", lambda: model_errors.unknown_state("probe", "probe")),
    ("unknown_action", lambda: model_errors.unknown_action("a", "s", "w")),
    ("state_jumped", lambda: model_errors.state_jumped("w", "a", "b")),
    ("workflow_invalid", lambda: model_errors.workflow_invalid("probe")),
    ("missing_provenance", lambda: model_errors.missing_provenance("probe")),
    ("unbalanced_posting", lambda: model_errors.unbalanced_posting("probe")),
    ("invalid_transfer", lambda: model_errors.invalid_transfer("probe")),
    ("yaml_unavailable", lambda: model_errors.yaml_unavailable("probe")),
)

#: code -> the HTTP status the ERP model itself gives that refusal.
MODEL_STATUS: Dict[str, int] = {name: probe().status for name, probe in _MODEL_PROBES}

if tuple(sorted(MODEL_STATUS)) != tuple(sorted(model_errors.CODES)):
    # A model code with no probe would be a refusal this surface could deliver
    # without the document declaring its status — i.e. an undeclared contract.
    raise RuntimeError(
        "the model's refusal vocabulary and this surface's probes disagree: "
        f"model={sorted(model_errors.CODES)} probed={sorted(MODEL_STATUS)} — "
        "add a probe for the new code in integrations/erp/api/errors.py"
    )

# --- the surface's own vocabulary -------------------------------------------

#: ``401`` — the caller presented no identity this surface can decide for.
CODE_UNAUTHORIZED = "unauthorized"
#: ``403`` — the ERP-08 layer decided the action is not permitted.
CODE_FORBIDDEN = "forbidden"
#: ``404`` — no route matches the request.
CODE_NOT_FOUND = "not_found"
#: ``404`` — the route matched; the document does not exist in the caller's tenant.
CODE_DOCUMENT_NOT_FOUND = "document_not_found"
#: ``405`` — the path exists, but not for this method.
CODE_METHOD_NOT_ALLOWED = "method_not_allowed"
#: ``409`` — the request conflicts with the store's current state.
CODE_CONFLICT = "conflict"
#: ``500`` — an unexpected failure the surface caught rather than leaked.
CODE_INTERNAL = "internal"
#: ``503`` — a declaration the decision needs cannot be read (CANNOT-ASSESS).
CODE_UNAVAILABLE = "unavailable"

#: The closed set of codes this surface originates. A code outside it is either
#: the model's (re-raised unchanged) or a bug.
BOUNDARY_CODES: Tuple[str, ...] = (
    CODE_CONFLICT,
    CODE_DOCUMENT_NOT_FOUND,
    CODE_FORBIDDEN,
    CODE_INTERNAL,
    CODE_METHOD_NOT_ALLOWED,
    CODE_NOT_FOUND,
    CODE_UNAUTHORIZED,
    CODE_UNAVAILABLE,
)

#: The status each boundary code carries.
_BOUNDARY_STATUS: Mapping[str, int] = {
    CODE_UNAUTHORIZED: 401,
    CODE_FORBIDDEN: 403,
    CODE_NOT_FOUND: 404,
    CODE_DOCUMENT_NOT_FOUND: 404,
    CODE_METHOD_NOT_ALLOWED: 405,
    CODE_CONFLICT: 409,
    CODE_INTERNAL: 500,
    CODE_UNAVAILABLE: 503,
}

#: Every ``(status, code)`` this surface can emit: its own boundary refusals
#: plus every refusal the ERP model can raise through it. The OpenAPI document's
#: ``components.responses`` must be exactly this set, and ``check`` proves the
#: two agree in both directions, so the contract cannot under-declare.
EMITTED_REFUSALS: Tuple[Tuple[int, str], ...] = tuple(
    sorted(
        {(status, code) for code, status in _BOUNDARY_STATUS.items()}
        | {(status, code) for code, status in MODEL_STATUS.items()}
    )
)

#: The distinct statuses this surface can emit, sorted.
STATUSES: Tuple[int, ...] = tuple(sorted({status for status, _ in EMITTED_REFUSALS}))


class SurfaceError(ApiError):
    """One boundary refusal of the REST surface.

    The same ``status/code/message/details`` record as the model's
    :class:`~integrations.erp.core.errors.ErpError`, distinguishable at the type
    level so a caller (and a test) can tell "the surface refused this request
    shape" from "the ERP model refused this document". Both are
    :class:`~identity.cpapi.errors.ApiError`, so one envelope builder serves them.

    A code outside :data:`BOUNDARY_CODES` is refused **at the raise** rather than
    propagated: a refusal a client cannot branch on is a bug in the refuser, and
    catching it here is what keeps the vocabulary closed in practice rather than
    in a comment. (The same discipline as ``auth.model.Refused``.)
    """

    def __init__(self, status: int, code: str, message: str, details: Any) -> None:
        if code not in BOUNDARY_CODES:
            raise ValueError(
                f"SurfaceError({code!r}) — not in the closed boundary vocabulary; "
                "add it to errors.BOUNDARY_CODES (and provoke it in negative_control.py) "
                "or re-raise the model's own refusal instead"
            )
        super().__init__(status, code, message, details)


def _refusal(status: int, code: str, message: str, details: Mapping[str, Any]) -> SurfaceError:
    if code not in BOUNDARY_CODES:
        raise ValueError(
            f"{code!r} is not a boundary code — the model's refusals are re-raised, not rebuilt"
        )
    return SurfaceError(status, code, message, dict(details) or None)


def unauthorized(message: str, **details: Any) -> SurfaceError:
    """``401`` — no principal was supplied, so nothing can be decided."""
    return _refusal(401, CODE_UNAUTHORIZED, message, details)


def forbidden(message: str, *, reason: str, **details: Any) -> SurfaceError:
    """``403`` — the ERP-08 layer decided this action is not permitted.

    ``reason`` is the auth layer's own decision reason, carried verbatim in
    ``details`` so a client can branch on *why* a request was denied (a tenant
    gate is not a missing role) without this surface inventing a second
    vocabulary for it.
    """
    return _refusal(403, CODE_FORBIDDEN, message, {"reason": reason, **details})


def not_found(message: str, **details: Any) -> SurfaceError:
    """``404`` — no route matches this request."""
    return _refusal(404, CODE_NOT_FOUND, message, details)


def document_not_found(message: str, **details: Any) -> SurfaceError:
    """``404`` — the route matched, the document is not there.

    Also the answer for a document that exists in *another* tenant: a document
    outside the caller's tenant is reported as absent, never as forbidden, so
    the surface is not an existence oracle for a tenant the caller cannot see.
    """
    return _refusal(404, CODE_DOCUMENT_NOT_FOUND, message, details)


def method_not_allowed(message: str, *, allowed: Tuple[str, ...], **details: Any) -> SurfaceError:
    """``405`` — the path exists for other methods.

    ``allowed`` rides in ``details`` because this surface is transport-free and
    cannot set a header; the adapter that mounts it turns ``details.allowed``
    into ``Allow``.
    """
    return _refusal(405, CODE_METHOD_NOT_ALLOWED, message, {"allowed": list(allowed), **details})


def conflict(message: str, **details: Any) -> SurfaceError:
    """``409`` — the request conflicts with the store's current state.

    Raising a document that is already there is the one conflict this surface
    originates. The status is the ERP model's own 409 grouping ("a conflict with
    the document's current state"), so a client sees one meaning of 409 across
    the model and the surface that serves it.
    """
    return _refusal(409, CODE_CONFLICT, message, details)


def unavailable(message: str, *, reason: str, **details: Any) -> SurfaceError:
    """``503`` — a declaration the decision needs cannot be read.

    CANNOT-ASSESS, never a denial and never a pass: refusing to *decide* is a
    different answer from deciding "no", and collapsing them is the defect the
    tri-state contract exists to prevent.
    """
    return _refusal(503, CODE_UNAVAILABLE, message, {"reason": reason, **details})


def internal(message: str, **details: Any) -> SurfaceError:
    """``500`` — an unexpected failure, caught rather than leaked.

    The surface catches every exception so a stack trace can never become a
    response body; the refusal names the exception *type* and nothing else, so no
    local value, path or credential reaches a client (GR-6).
    """
    return _refusal(500, CODE_INTERNAL, message, details)


#: The factory for each code an authorization outcome can place a request in.
#: Two codes are deliberately absent: ``method_not_allowed`` (no authorization
#: outcome can produce it) and ``not_found`` (a route lookup, not a decision) —
#: an entry for either would be a branch nothing can reach, and an inert branch
#: is the formality this repository's doctrine forbids.
_BOUNDARY_FACTORY: Mapping[str, Any] = {
    CODE_FORBIDDEN: forbidden,
    CODE_DOCUMENT_NOT_FOUND: document_not_found,
    CODE_UNAUTHORIZED: unauthorized,
    CODE_UNAVAILABLE: unavailable,
}

#: Every reason the ERP-08 layer can *decide* with or *raise*, mapped onto this
#: surface's vocabulary. ``auth.model.REFUSALS`` is the closed set; a reason with
#: no entry here fails at import (see the check below) rather than becoming a 500.
AUTH_STATUS: Dict[str, Tuple[int, str]] = {
    # the tenant gate — refused, and never an existence oracle
    "cross-tenant": (404, CODE_DOCUMENT_NOT_FOUND),
    "tenant-missing": (403, CODE_FORBIDDEN),
    "malformed-principal": (401, CODE_UNAUTHORIZED),
    # the platform's two gates, consumed from identity/rbac
    "permission-denied": (403, CODE_FORBIDDEN),
    "scope-denied": (403, CODE_FORBIDDEN),
    # the role map's decided denials
    "unknown-role": (403, CODE_FORBIDDEN),
    "unknown-kind": (403, CODE_FORBIDDEN),
    "unknown-action": (403, CODE_FORBIDDEN),
    # declaration layers: a declaration that cannot be read is un-assessable
    "empty-role-map": (503, CODE_UNAVAILABLE),
    "field-write-denied": (403, CODE_FORBIDDEN),
    "unknown-field-policy": (503, CODE_UNAVAILABLE),
    "unknown-field": (503, CODE_UNAVAILABLE),
    "contract-unavailable": (503, CODE_UNAVAILABLE),
    "unknown-effect": (503, CODE_UNAVAILABLE),
    "declaration-invalid": (503, CODE_UNAVAILABLE),
    "schema-violation": (503, CODE_UNAVAILABLE),
    "harvest-code-copied": (503, CODE_UNAVAILABLE),
    "harvest-incomplete": (503, CODE_UNAVAILABLE),
}

if set(AUTH_STATUS) != set(auth_model.REFUSALS):
    # The auth lane's vocabulary is closed and this map claims to place all of
    # it. An unplaced reason would reach a client as an unexplained 500, so the
    # divergence fails here — loudly, once — instead.
    raise RuntimeError(
        "the ERP-08 refusal vocabulary and this surface's mapping disagree: "
        f"unmapped={sorted(set(auth_model.REFUSALS) - set(AUTH_STATUS))} "
        f"unknown={sorted(set(AUTH_STATUS) - set(auth_model.REFUSALS))} — "
        "update integrations/erp/api/errors.py:AUTH_STATUS"
    )

if set(_BOUNDARY_FACTORY) != {code for _, code in AUTH_STATUS.values()}:
    raise RuntimeError(
        "every code an authorization outcome can place a request in needs a factory: "
        f"mapped={sorted({code for _, code in AUTH_STATUS.values()})} "
        f"factory={sorted(_BOUNDARY_FACTORY)}"
    )


def _from_reason(reason: str, detail: str, kind: str, document_id: str) -> SurfaceError:
    """Place one auth-layer reason in this surface's vocabulary.

    The status comes from :data:`AUTH_STATUS` and the *object* comes from the
    boundary factory for that code; the two are then compared, so a mapping that
    says ``503`` and a factory that builds a ``403`` cannot both be true.
    """
    status, code = AUTH_STATUS.get(reason, (403, CODE_FORBIDDEN))
    where = f" on {kind!r}" + (f" {document_id!r}" if document_id else "")
    message = f"the authorization layer refused this request{where}: {reason}"
    if detail:
        message = f"{message} — {detail}"
    if code == CODE_DOCUMENT_NOT_FOUND:
        # A cross-tenant request must not reveal that the resource exists.
        message = f"no such document{where} in this tenant"
    error = _BOUNDARY_FACTORY[code](message, reason=reason, detail=detail)
    if error.status != status:
        raise RuntimeError(  # pragma: no cover - a table that disagrees with itself
            f"AUTH_STATUS places {reason!r} at {status} but {code!r} builds a {error.status}"
        )
    return error


def from_decision(decision: Any, *, kind: str = "", document_id: str = "") -> SurfaceError:
    """The refusal for a denied :class:`~integrations.erp.auth.model.Decision`.

    ``reason`` is the auth layer's code, carried in ``details.reason``, so the
    wire code is this surface's and the *why* is the auth layer's — one of each,
    never a second dialect.
    """
    return _from_reason(
        str(getattr(decision, "reason", "") or "permission-denied"),
        str(getattr(decision, "detail", "") or ""),
        kind,
        document_id,
    )


def from_refusal(refusal: Refused, *, kind: str = "", document_id: str = "") -> SurfaceError:
    """The refusal for a ``Refused`` the ERP-08 layer *raised*.

    ``scope.authorize`` raises only when its declarations cannot be read, which is
    CANNOT-ASSESS — so a raised refusal is a ``503`` unless its own reason places
    it elsewhere in :data:`AUTH_STATUS`.
    """
    return _from_reason(refusal.code, refusal.detail, kind, document_id)
