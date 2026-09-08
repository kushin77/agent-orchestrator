"""Path safety for the public edge - traversal guard + route template match.

This is the path-traversal doctrine adapted from the cannibalized saas-rbac
proxy (``frontend-api/src/proxy.ts`` ``parseProxyPath``): HTTP frameworks do
not normalize ``..`` out of a request path, but a backend client *does*
resolve it - so ``/v1/agents/../../internal/secret`` would otherwise slip
past a prefix/section allowlist and hit an unlisted backend route.  The edge
therefore splits the path into plain forward segments *before* matching and
refuses anything that is not one, including percent-encoded forms (a backend
router decodes before matching, so an encoded traversal must be caught here).

Every function here is pure and fails closed (returns ``None`` rather than a
guess).
"""

from __future__ import annotations

import re
from typing import Optional, Sequence

#: A template parameter name: ``{agentId}``, ``{tenantId}``, ...
_PARAM_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# A parsed segment is either a literal string or ("param", name).
_LITERAL = "literal"
_PARAM = "param"


def _decode_once(segment: str) -> Optional[str]:
    """Percent-decode a path segment once, strictly.

    Refuses malformed percent-encoding (a bare ``%``, a truncated escape, or
    a non-hex escape such as ``%zz``) - never guess at what the client meant.
    This mirrors the cannibalized saas-rbac guard, whose ``decodeURIComponent``
    throws on a malformed escape rather than passing it through.
    """
    import re as _re
    import urllib.parse

    # Every '%' must be followed by exactly two hex digits; anything else
    # (bare '%', '%z', '%zz', a trailing '%') is malformed -> refuse.
    if _re.search(r"%(?![0-9A-Fa-f]{2})", segment):
        return None
    try:
        return urllib.parse.unquote(segment)
    except Exception:  # pragma: no cover - defensive
        return None


def parse_path_segments(path: str) -> Optional[list[str]]:
    """Split a request path into plain forward segments, or ``None``.

    The returned segments contain **no** empty/``.``/``..`` members and no
    percent-encoded slash or dot-dot - a path that decodes into a traversal is
    refused here, before the allowlist match and before any backend call.

    Mirrors saas-rbac ``parseProxyPath``:

    - must start with ``/`` and be non-empty (the root is never allowlisted);
    - empty segments (``//``, trailing ``/``) are refused;
    - each segment is decoded once and refused when it is ``.``/``..`` or
      contains a ``/`` (an encoded slash smuggled inside a segment);
    - malformed percent-encoding is refused.
    """
    if not isinstance(path, str) or not path:
        return None
    if not path.startswith("/"):
        return None
    remainder = path[1:]
    if remainder == "":
        return None
    segments = remainder.split("/")
    for segment in segments:
        if segment == "":
            return None
        decoded = _decode_once(segment)
        if decoded is None:
            return None
        if decoded in (".", ".."):
            return None
        if "/" in decoded:
            return None
    return segments


def parse_template(path_template: str) -> Optional[list[tuple[str, str]]]:
    """Parse an allowlisted route template into segment descriptors.

    Each descriptor is ``(kind, value)`` where ``kind`` is ``"literal"`` and
    ``value`` is the literal text, or ``kind`` is ``"param"`` and ``value``
    is the parameter name.  Returns ``None`` when the template is malformed
    (route table validation fails closed on this).
    """
    if not isinstance(path_template, str) or not path_template:
        return None
    if not path_template.startswith("/"):
        return None
    remainder = path_template[1:]
    if remainder == "":
        return None
    parsed: list[tuple[str, str]] = []
    for segment in remainder.split("/"):
        if segment == "":
            return None
        if segment.startswith("{") and segment.endswith("}") and len(segment) > 2:
            name = segment[1:-1]
            if not _PARAM_RE.match(name):
                return None
            parsed.append((_PARAM, name))
        elif "{" in segment or "}" in segment:
            # A stray brace in a literal segment is ambiguous - refuse.
            return None
        else:
            parsed.append((_LITERAL, segment))
    return parsed


def match_template(
    template_segments: Sequence[tuple[str, str]],
    request_segments: Sequence[str],
) -> Optional[dict[str, str]]:
    """Match parsed request segments against a parsed template 1:1.

    Returns the extracted path parameters, or ``None`` when the request path
    does not match the template exactly (no wildcards - the allowlist is
    explicit).
    """
    if len(template_segments) != len(request_segments):
        return None
    params: dict[str, str] = {}
    for (kind, value), request_segment in zip(template_segments, request_segments):
        if kind == _LITERAL:
            if value != request_segment:
                return None
        else:
            params[value] = request_segment
    return params


def backend_params_in(path_template: str) -> Optional[list[str]]:
    """The ``{name}`` parameters referenced by a template (backend or api)."""
    parsed = parse_template(path_template)
    if parsed is None:
        return None
    return [value for kind, value in parsed if kind == _PARAM]
