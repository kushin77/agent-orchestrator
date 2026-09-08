"""Versioned, envelope-standardized public responses (capital middleware
pattern adapted).

Every response from the public edge rides in the same uniform envelope -
``apiVersion`` + ``requestId`` + ``status`` always, plus either the
downstream body (verbatim pass-through) or a closed-code edge error:

.. code-block:: json

    { "apiVersion": "v1", "requestId": "req_...", "status": 200,
      "body": <downstream body verbatim> }

    { "apiVersion": "v1", "requestId": "req_...", "status": 401,
      "error": { "code": "unauthenticated", "message": "..." } }

The envelope carries version + correlation metadata only.  It never rewrites
a downstream status or body: a backend 403 (authorization denial) stays a
403 and its body passes through untouched - the edge must not turn a denial
into a success, and it has nothing to add to a downstream decision.
"""

from __future__ import annotations

from typing import Any, Optional

from identity.edges.model import (
    API_VERSION,
    CODE_BACKEND_UNAVAILABLE,
    CODE_METHOD_NOT_ALLOWED,
    CODE_UNAUTHENTICATED,
    CODE_UNKNOWN_ROUTE,
    EdgeResponse,
)

#: HTTP status semantic for each edge-origin rejection.
EDGE_STATUS: dict[str, int] = {
    CODE_UNAUTHENTICATED: 401,
    CODE_UNKNOWN_ROUTE: 404,
    CODE_METHOD_NOT_ALLOWED: 405,
    CODE_BACKEND_UNAVAILABLE: 502,
}

EDGE_MESSAGE: dict[str, str] = {
    CODE_UNAUTHENTICATED: (
        "authentication required - a valid session token is needed"
    ),
    CODE_UNKNOWN_ROUTE: "unknown api route",
    CODE_METHOD_NOT_ALLOWED: "method not allowed on this public route",
    CODE_BACKEND_UNAVAILABLE: "backend unavailable",
}


def edge_rejection(
    code: str,
    *,
    request_id: str = "",
    message: Optional[str] = None,
    api_version: str = API_VERSION,
) -> EdgeResponse:
    """An edge-origin rejection envelope (no downstream call was made)."""
    return EdgeResponse(
        status=EDGE_STATUS.get(code, 502),
        api_version=api_version,
        request_id=request_id,
        body=None,
        error_code=code,
        error_message=message or EDGE_MESSAGE.get(code, code),
    )


def passthrough(
    downstream_status: int,
    downstream_body: Any,
    *,
    request_id: str = "",
    api_version: str = API_VERSION,
) -> EdgeResponse:
    """Wrap a downstream answer untouched (status + body verbatim)."""
    return EdgeResponse(
        status=int(downstream_status),
        api_version=api_version,
        request_id=request_id,
        body=downstream_body,
        error_code=None,
        error_message="",
    )
