"""The receipt — what one command did, and what a replay hands back.

``ao-control <verb>`` prints one receipt per action: the effect record the RC-3
API returns in the console envelope's ``data``. Nothing is inferred from a status
code — a receipt exists only if the plane returned one, which is what makes "no
silent success" a property of the client rather than a promise about the server.

There are two receipts, and the difference is the point:

* the **live** receipt, the effect of the command this call delivered;
* the **original** receipt, which RC-4 hands back when a command id has already
  been applied — carried inside the refusal message after the marker
  ``portal/server/control_audit.py`` declares, as canonical JSON. A client that
  looked only at the status would report a replay as a failure-with-no-evidence;
  parsing the marker is what lets it show the operator the effect that already
  happened, exactly once.

JSON is the machine surface and text is the human one (the CLI is not a
dashboard — ADR-0022, consumed by ADR-0025 §6.2); both are rendered from the same
receipt, so they cannot disagree.


---knowledge---
module_id: control-plane.cli.aoctl.receipt
system: control-plane
app: cli
solution_class: enterprise
patterns: [one-receipt-per-action, no-silent-success, dual-rendering]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [render, envelope, replay_receipt, FIELDS, OUTPUT_LIMIT]
invariants: "a receipt exists only if the plane returned one, so nothing is inferred from a status code; JSON and text are rendered from the same receipt and cannot disagree"
gotchas: "a replay hands the ORIGINAL receipt back inside the refusal message after the marker portal/server/control_audit.py declares, as canonical JSON"
related: ["#556", "#554"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional

from . import contract

#: How much of a receipt's free text the human rendering shows. The machine
#: rendering is never clipped.
OUTPUT_LIMIT = 600

#: The fields of RC-3's effect record, in the order the human rendering shows
#: them. Named here only for display order; the receipt itself is whatever the
#: plane returned, printed whole in JSON.
FIELDS = (
    ("commandId", "command"),
    ("actor", "actor"),
    ("lever", "lever"),
    ("args", "args"),
    ("exitCode", "exit code"),
    ("auditAction", "audit action"),
    ("requestedAt", "requested"),
)


def replay_receipt(message: str, *, marker: Optional[str] = None) -> Optional[dict[str, Any]]:
    """The original receipt a replay refusal carries, or ``None`` if it has none.

    The marker is read from ``control_audit.py`` unless one is passed, and the
    payload after it is parsed as JSON because that is what RC-4 documents it as
    (``canonical``: sorted keys, compact separators).

    ``raw_decode``, not ``loads``: the marker's payload is embedded in a larger
    message — the seam renders the console's whole error object into the text it
    raises (``error_for_status(status, path, str(body["error"]))``), so the
    receipt is followed by that object's closing punctuation. Parsing the value
    at the marker and ignoring what trails it reads the receipt out of the
    message as it really arrives. A message with the marker but no parsable
    payload yields ``None`` — read as "no receipt", never as an invented one.
    """
    if not message:
        return None
    marker = marker if marker is not None else contract.receipt_marker()
    index = message.find(marker)
    if index < 0:
        return None
    payload = message[index + len(marker):].lstrip()
    if not payload:
        return None
    try:
        parsed, _end = json.JSONDecoder().raw_decode(payload)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def render(receipt: Mapping[str, Any], *, verb: str) -> str:
    """The human rendering of one receipt."""
    lines = [
        f"ao-control: OK — {receipt.get('verb') or verb} "
        f"({receipt.get('effectClass') or 'unknown effect'})"
    ]
    for key, label in FIELDS:
        if key not in receipt:
            continue
        value = receipt[key]
        if key == "args":
            rendered = " ".join(str(item) for item in value) if value else "(none)"
        elif value is None:
            rendered = "(none)"
        else:
            rendered = str(value)
        lines.append(f"  {label:<12} {_clip(rendered, OUTPUT_LIMIT)}")
    if receipt.get("output"):
        lines.append("  lever said:")
        for line in str(receipt["output"]).splitlines():
            lines.append(f"    {line}")
    if receipt.get("content") is not None:
        lines.append(
            "  structured content: "
            f"{_clip(json.dumps(receipt['content'], sort_keys=True), OUTPUT_LIMIT)}"
        )
    return "\n".join(lines)


def envelope(
    *,
    verb: str,
    verdict: str,
    command_id: str = "",
    request: Optional[Mapping[str, Any]] = None,
    receipt: Optional[Mapping[str, Any]] = None,
    refusal: Optional[Any] = None,
    note: str = "",
) -> dict[str, Any]:
    """The ``--json`` document: one machine-shaped answer per invocation.

    It carries the verdict, the verb, the request the CLI built (always, so a
    ``--dry-run`` and a real call can be compared), and exactly one of a receipt
    or a refusal. ``note`` is the one free-text remark a rendering may need —
    today only "this irreversible verb will need its confirmation" — and it is a
    field rather than a comment so the human and the machine rendering cannot
    disagree about whether it was said.
    """
    payload: dict[str, Any] = {
        "ok": verdict == "OK",
        "verdict": verdict,
        "verb": verb,
        "commandId": command_id,
    }
    if request is not None:
        payload["request"] = dict(request)
    if receipt is not None:
        payload["receipt"] = dict(receipt)
    if refusal is not None:
        payload["refusal"] = refusal.as_json()
    if note:
        payload["note"] = note
    return payload


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "..."
