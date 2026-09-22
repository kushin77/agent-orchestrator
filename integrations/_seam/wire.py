"""The wire primitives both adapters share: ``Response``, ``decode``, ``error_for_status`` (#1208).

``Response`` and ``decode`` were byte-identical in the two adapters; only
``error_for_status`` differed, and only in the two things that are genuinely each
adapter's own: the label its message carries (``paperclip`` / ``hermes``) and the
status→typed-error table it looks the class up in. Those two are data, so they
are the seam's :class:`WireBoundary` argument — the message shape and the
constructor call are single-sourced here, which is the half that had already
started to drift.

---knowledge---
module_id: integrations._seam.wire
system: integrations
app: seam
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [Response, decode, WireBoundary, error_for_status]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json as _json
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping


@dataclass(frozen=True)
class Response:
    """One upstream HTTP response, parsed at the transport seam.

    ``body`` is the decoded JSON body when the response is JSON, else the raw
    text. ``status`` is the HTTP status code, always present.
    """

    status: int
    body: Any = None
    headers: Dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def decode(raw: str) -> Any:
    """Decode a body as JSON, falling back to the raw text."""
    if not raw:
        return None
    try:
        return _json.loads(raw)
    except ValueError:
        return raw


@dataclass(frozen=True)
class WireBoundary:
    """One adapter's wire vocabulary: what its errors are called and built from.

    ``label`` is the word its messages carry, ``error_by_status`` the documented
    status→class mapping, and ``base_error`` the class every unmapped status
    falls back to. The boundary is data, so the two adapters keep different
    error vocabularies while sharing the rendering below.
    """

    label: str
    error_by_status: Mapping[int, type]
    base_error: type

    def error_for_status(self, status: int, path: str, detail: str = "") -> Exception:
        """Build the typed error a given upstream status maps to."""
        return error_for_status(
            status,
            path,
            detail,
            label=self.label,
            error_by_status=self.error_by_status,
            base_error=self.base_error,
        )


def error_for_status(
    status: int,
    path: str,
    detail: str = "",
    *,
    label: str,
    error_by_status: Mapping[int, type],
    base_error: type,
) -> Exception:
    """Build the typed error a given upstream status maps to.

    The seam owns the message shape (``<label> <path>: HTTP <status>`` plus an
    em-dash detail) and the constructor call; the caller owns the vocabulary.
    """
    cls = error_by_status.get(status, base_error)
    message = f"{label} {path}: HTTP {status}"
    if detail:
        message = f"{message} — {detail}"
    return cls(message, status=status, path=path)
