"""The error taxonomy — MAPPED from the seam's own errors, not invented (#413).

The seam doc (``docs/PAPERCLIP-ING-INTEGRATION.md`` §1) fixes seven upstream
statuses: ``400`` validation · ``401`` bad caller identity · ``403``
known-but-not-allowed · ``404`` missing/outside company scope · ``409``
conflict/owned/locked · ``422`` business-rule rejection · ``503`` dependency
unreachable.

Nothing here restates them by hand. The status set and its descriptions come
from the seam's own typed errors (``integrations.paperclip.model.ERROR_BY_STATUS``
— each class documents its status), and the wire ``code`` for each status is
read by *calling* the fleet's existing refusal constructors and reading back
``.status`` / ``.code`` (:mod:`integrations.paperclip.auth.model` and
:mod:`integrations.paperclip.api.errors`). A code that no constructor carries
would simply not appear — the table cannot drift from the code it describes.

---knowledge---
module_id: integrations.paperclip.api.taxonomy
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: [codes_by_status, entries, responses, statuses]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ..auth import model as _auth_model
from ..model import ERROR_BY_STATUS
from . import errors as _errors

#: The seven statuses, in ascending order (the taxonomy's own order).
TAXONOMY_ORDER: Tuple[int, ...] = (400, 401, 403, 404, 409, 422, 503)

#: (home, factory) — each factory returns a real refusal object; the entry's
#: status and code are read from it, never restated.
_CODE_PROBES: Tuple[Tuple[str, Any], ...] = (
    ("integrations/paperclip/auth/model.py", lambda: _auth_model.validation_error("provoked")),
    ("integrations/paperclip/auth/model.py", _auth_model.unauthorized),
    ("integrations/paperclip/auth/model.py", _auth_model.invalid_token),
    ("integrations/paperclip/auth/model.py", _auth_model.token_expired),
    ("integrations/paperclip/auth/model.py", _auth_model.session_revoked),
    ("integrations/paperclip/auth/model.py", _auth_model.cross_company),
    ("integrations/paperclip/auth/model.py", lambda: _auth_model.permission_denied("run:read")),
    ("integrations/paperclip/auth/model.py", _auth_model.replayed_run_id),
    ("integrations/paperclip/api/errors.py", lambda: _errors.not_found("provoked")),
    ("integrations/paperclip/api/errors.py", lambda: _errors.refused("provoked")),
    ("integrations/paperclip/api/errors.py", lambda: _errors.unavailable("provoked")),
)


def _first_line(text: str) -> str:
    """The first non-empty line of a docstring, stripped."""
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def codes_by_status() -> Dict[int, List[Dict[str, str]]]:
    """Map each status to the wire codes that carry it, with each code's home."""
    table: Dict[int, Dict[str, str]] = {}
    for home, factory in _CODE_PROBES:
        refusal = factory()
        table.setdefault(int(refusal.status), {})[str(refusal.code)] = home
    return {
        status: [{"code": code, "home": homes[code]} for code in sorted(homes)]
        for status, homes in table.items()
    }


def entries() -> List[Dict[str, Any]]:
    """The taxonomy as a deterministic list, one entry per status in order."""
    codes = codes_by_status()
    out: List[Dict[str, Any]] = []
    for status in TAXONOMY_ORDER:
        error_class = ERROR_BY_STATUS.get(status)
        out.append(
            {
                "status": status,
                "error_type": error_class.__name__ if error_class is not None else "PaperclipError",
                "description": _first_line(error_class.__doc__ if error_class is not None else "")
                or "the boundary error this status maps to",
                "codes": [c["code"] for c in codes.get(status, [])],
            }
        )
    return out


def responses() -> Dict[str, Any]:
    """The OpenAPI ``components.responses`` map, one entry per taxonomy status."""
    return {
        str(entry["status"]): {
            "description": entry["description"],
            "content": {
                "application/json": {"schema": {"$ref": "#/components/schemas/Error"}}
            },
        }
        for entry in entries()
    }


def statuses() -> Tuple[int, ...]:
    """The closed status set the surface documents."""
    return TAXONOMY_ORDER
