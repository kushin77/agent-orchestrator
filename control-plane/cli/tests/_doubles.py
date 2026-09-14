"""Offline doubles and registry surgery for the CLI's suite.

**Why a transport double at all.** The CLI's transport is the boundary's own
seam (``integrations/paperclip/client.py``) and the suite uses the seam's
``FixtureTransport`` for every ordinary case. Two behaviours still need a double,
and neither makes a network call:

``UnreachableTransport``
    "the plane is not there" is a *transport* failure (``URLError`` from
    ``urllib``), which the seam's fixture transport cannot produce — it is a
    table of canned answers, and an unreachable plane has no answer to can. The
    acceptance that an unreachable plane exits non-zero with a named reason would
    otherwise be untestable offline.
``ConsoleTransport``
    the seam's ``HttpTransport`` turns a non-2xx into
    ``error_for_status(status, path, str(body["error"]))`` — the console's
    envelope, code included. ``FixtureTransport`` raises with the status alone, so
    a refusal's *code* would never reach a test. This double reproduces
    ``HttpTransport``'s documented error construction from a table; the reader it
    feeds is separately pinned against the seam's own ``error_for_status``
    rendering in ``test_refusals.py``, so the two cannot drift apart unnoticed.

Everything here is stdlib plus the seam: no socket is opened, and the gate that
runs this suite never touches the network.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence
from urllib.error import URLError

import yaml

from integrations.paperclip.model import Response, error_for_status

#: The committed registry, and where it lives (repo-root relative).
REGISTRY_RELATIVE = "control-plane/control/verbs.yaml"


def ok_envelope(data: Mapping[str, Any], *, status: int = 200) -> Dict[str, Any]:
    """The console's success envelope (``portal/server/app.py::_ok``)."""
    return {
        "ok": True,
        "status": status,
        "requestId": "req_test",
        "data": dict(data),
        "error": None,
    }


def refusal_envelope(status: int, code: str, message: str) -> Dict[str, Any]:
    """The console's refusal envelope (``ApiError`` -> ``error.code``)."""
    return {
        "ok": False,
        "status": status,
        "requestId": "req_test",
        "data": None,
        "error": {"code": code, "message": message},
    }


def effect_record(
    *,
    verb: str,
    command_id: str = "cmd_test",
    effect_class: str = "hold",
    actor: str = "user:ops@example.com",
    exit_code: int = 0,
    output: str = "",
    content: Optional[Any] = None,
) -> Dict[str, Any]:
    """One RC-3 effect record — the receipt the API returns."""
    record: Dict[str, Any] = {
        "commandId": command_id,
        "verb": verb,
        "effectClass": effect_class,
        "capability": "fleet:operate",
        "auditAction": verb,
        "actor": actor,
        "idempotent": True,
        "lever": "fleet/control.py#pause",
        "args": [],
        "exitCode": exit_code,
        "output": output,
        "requestedAt": "2026-09-14T00:00:00Z",
    }
    if content is not None:
        record["content"] = content
    return record


class UnreachableTransport:
    """The seam's ``Transport``, pointed at nothing: every call fails to connect."""

    def __init__(self, reason: str = "connection refused") -> None:
        self.reason = reason
        self.requests: list[Dict[str, Any]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Response:  # pragma: no cover - the raise is the behaviour under test
        self.requests.append({"method": method, "path": path, "headers": dict(headers or {})})
        raise URLError(self.reason)


class ConsoleTransport:
    """``HttpTransport``'s documented behaviour, from a table, over no socket.

    A configured ``(method, path)`` answers with the console envelope it was given
    — and, on a non-2xx, raises exactly what the real transport raises: the typed
    error from the seam's own ``error_for_status``, carrying
    ``str(body["error"])`` as its detail. An unconfigured address raises
    ``URLError``, so a drifted path fails loudly instead of reading as a default.
    """

    def __init__(self, responses: Mapping[tuple, tuple]) -> None:
        self.responses = dict(responses)
        self.requests: list[Dict[str, Any]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Response:
        self.requests.append(
            {"method": method.upper(), "path": path, "headers": dict(headers or {}), "body": json}
        )
        try:
            status, body = self.responses[(method.upper(), path)]
        except KeyError as exc:
            raise URLError(f"no answer configured for {method.upper()} {path}") from exc
        if not 200 <= int(status) < 300:
            detail = str((body or {}).get("error", "")) if isinstance(body, Mapping) else ""
            raise error_for_status(int(status), path, detail)
        return Response(status=int(status), body=body, headers={})


# ---------------------------------------------------------------------------
# registry surgery — the vocabulary is consumed, so the suite edits the document
# ---------------------------------------------------------------------------
def registry_document(path: str = REGISTRY_RELATIVE) -> Dict[str, Any]:
    """The committed registry, parsed."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    return yaml.safe_load((root / path).read_text(encoding="utf-8"))


def _rows(document: Dict[str, Any]) -> Sequence[Dict[str, Any]]:
    return document["verbs"]


def row_of(document: Dict[str, Any], verb_id: str) -> Dict[str, Any]:
    for row in _rows(document):
        if row["id"] == verb_id:
            return row
    raise KeyError(verb_id)


def withhold(document: Dict[str, Any], verb_id: str, why: str = "test: withheld") -> Dict[str, Any]:
    """Mark a declared verb ``exposed: false`` with a reason (RC-2's own shape)."""
    row = row_of(document, verb_id)
    row["exposed"] = False
    row["why_not_exposed"] = why
    return document


def drop(document: Dict[str, Any], verb_id: str) -> Dict[str, Any]:
    """Remove a verb from the vocabulary — the drift the CLI must refuse."""
    document["verbs"] = [row for row in _rows(document) if row["id"] != verb_id]
    return document
