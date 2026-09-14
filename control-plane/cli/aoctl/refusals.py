"""The named refusals — one reason per code, and the CLI's exit contract.

A control CLI's failure modes are its contract. RC-3 answers a refused command
with a status *and* a machine code in the console's own envelope
(``{"error": {"code", "message"}}``), and RC-2 declares the closed refusal-code
set the vocabulary may carry. This module is the client half of that vocabulary:
it maps what the plane returned, or what the CLI itself declined to send, onto
**one named reason** and one exit code.

The exit contract is this repo's tri-state, consumed rather than re-invented
(``fleet/control.py``, ``governance/*/cli.py``, the gate scripts):

=====  ================  ==================================================
exit   verdict           meaning
=====  ================  ==================================================
``0``  OK                the plane answered and the receipt is the effect
``1``  REFUSED           a named refusal — the command was **not** applied
``2``  CANNOT-ASSESS     no verdict was obtainable: the plane could not be
                         reached, or the plane itself reported that it could
                         not assess the command, or the vocabulary the CLI
                         must trust could not be read
=====  ================  ==================================================

An unreachable plane therefore exits ``2`` — non-zero, named, and never a silent
success (the issue's own acceptance). ``argparse`` also exits ``2`` on a usage
error; that coincidence is stated in ``cli``'s help rather than papered over.

Two tables, and the difference between them matters:

``MATRIX``
    status -> the codes RC-3 can answer with *at that status*. A status with one
    candidate names its code; a status with several is named from the plane's own
    envelope when it carried one, and otherwise the CLI names **all** the
    candidates rather than guessing one — an offline transport that raises with
    the status alone cannot be read for more than that, and a guess would be a
    refusal asserting a fact the CLI does not hold.

``REASONS``
    code -> the operator-facing reason and the verdict. The set is closed on
    purpose: a code with no reason here would be a refusal the CLI cannot explain.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional

#: The CLI's exit contract (this repo's tri-state).
EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_CANNOT_ASSESS = 2

#: status -> the codes RC-3 can answer with at that status, consumed from
#: ``portal/server/control_api.py``'s refusal matrix (its module docstring is
#: the authority for the order and the codes) and, for 401/403/405/409/422/503,
#: from RC-2's closed refusal-code set. Two keys are deliberately **not** in the
#: registry's set, and each is named where it comes from:
#:
#: ``400``  the console's own request-shape refusal (``invalid_request``) — RC-3
#:          states it is the transport's shape, not a control refusal;
#: ``404``  the **flag gate** (``feature_disabled``) — the family is invisible
#:          while ``surfaces.remote_control`` is OFF, and RC-2 carries no 404
#:          because a disabled family is not a verb-level refusal (ADR-0025 D1.1).
MATRIX: dict[int, tuple[str, ...]] = {
    400: ("invalid_request",),
    401: ("unauthorized",),
    403: ("verb_not_exposed", "scope_denied", "permission_denied"),
    404: ("feature_disabled", "not_found"),
    405: ("method_not_allowed",),
    409: ("duplicate_command", "lever_refused"),
    422: ("unknown_verb",),
    503: ("lever_unreachable", "vocabulary_unavailable"),
}

#: code -> (the named reason, the verdict). The verdict is what the CLI exits
#: with, so a *refusal* and a *no verdict* cannot be conflated by accident: 503
#: is the plane saying "I could not assess this", which is CANNOT-ASSESS, while a
#: 403/409/422 is a verdict it did reach.
REASONS: dict[str, tuple[str, int]] = {
    # -- the flag gate (pre-authN, ADR-0025 D1.1) --------------------------
    "feature_disabled": (
        "the plane's control family is feature-flag-gated OFF "
        "(surfaces.remote_control): the family is invisible, not merely "
        "unauthorised, so no command was applied",
        EXIT_CANNOT_ASSESS,
    ),
    # -- the caller --------------------------------------------------------
    "unauthorized": (
        "the plane verified no caller session for this request",
        EXIT_REFUSED,
    ),
    "method_not_allowed": (
        "the plane serves this family POST only; every control verb is a command",
        EXIT_REFUSED,
    ),
    # -- the vocabulary ----------------------------------------------------
    "unknown_verb": (
        "the plane does not declare this verb in its closed vocabulary",
        EXIT_REFUSED,
    ),
    "verb_not_exposed": (
        "the registry withholds this verb from the remote surface",
        EXIT_REFUSED,
    ),
    "not_found": (
        "the plane serves no control route at that address: either the family is "
        "not installed on the plane the CLI was pointed at, or the id it was "
        "built from is not the one the plane serves",
        EXIT_REFUSED,
    ),
    # -- authorisation (identity/rbac, ADR-0025 D2.3) ----------------------
    "scope_denied": (
        "the caller is out of the platform scope the fleet is controlled at",
        EXIT_REFUSED,
    ),
    "permission_denied": (
        "the caller's roles do not grant the verb's declared capability",
        EXIT_REFUSED,
    ),
    # -- the effect --------------------------------------------------------
    "duplicate_command": (
        "the command id was already applied (or is still in flight): the plane "
        "applies a command once and hands back the original receipt",
        EXIT_REFUSED,
    ),
    "lever_refused": (
        "the local lever declined the command: the state it holds conflicts "
        "with the order, and the CLI does not retry it another way",
        EXIT_REFUSED,
    ),
    "lever_unreachable": (
        "the local lever could not be reached, so the plane could not assess "
        "the command and nothing was applied",
        EXIT_CANNOT_ASSESS,
    ),
    "vocabulary_unavailable": (
        "the plane could not read its own control vocabulary, so it refused to "
        "guess what the verb means",
        EXIT_CANNOT_ASSESS,
    ),
    # -- the request shape -------------------------------------------------
    "invalid_request": (
        "the plane rejected the request shape",
        EXIT_REFUSED,
    ),
    # -- the plane itself --------------------------------------------------
    "plane_unreachable": (
        "the plane could not be reached, so the command was never delivered "
        "and nothing was applied",
        EXIT_CANNOT_ASSESS,
    ),
    "plane_malformed": (
        "the plane answered with a shape this CLI does not understand, so its "
        "verdict is unknown",
        EXIT_CANNOT_ASSESS,
    ),
    "address_invalid": (
        "the plane address is not one this client can use, so no request could "
        "be built for it",
        EXIT_CANNOT_ASSESS,
    ),
    # -- refusals the CLI makes locally, before anything is sent -----------
    "no_session": (
        "no console session token was supplied, and the control family's only "
        "caller identity is the console session (ADR-0025 D2): nothing was sent",
        EXIT_REFUSED,
    ),
    "confirmation_required": (
        "this verb's effect class is irreversible, so it needs an explicit "
        "confirmation (--confirm <verb>): nothing was sent",
        EXIT_REFUSED,
    ),
    "surface_drift": (
        "the CLI's verb table names a command id the control-verb registry does "
        "not declare: the CLI cannot verify the plane's vocabulary, so it "
        "refuses rather than sending a command it cannot name",
        EXIT_CANNOT_ASSESS,
    ),
    "vocabulary_unreadable": (
        "the control-verb registry could not be read, so the CLI cannot name "
        "the command it would send",
        EXIT_CANNOT_ASSESS,
    ),
    "contract_unavailable": (
        "the console contract module the CLI reads its wire constants from "
        "could not be imported",
        EXIT_CANNOT_ASSESS,
    ),
}

#: The generic reason for a status the matrix does not know. Named rather than
#: swallowed: an unmapped status is still a refusal, never a success.
UNMAPPED_REASON = "the plane refused the command with an unmapped status"


class Refusal(Exception):
    """One named refusal — the verdict, the reason, and what the plane said.

    An exception, like the console's own ``ApiError`` and the seam's
    ``PaperclipError``: a refusal is a verdict, and a caller that forgets to look
    at it must fail loudly rather than read a missing receipt as an effect.

    ``code`` is the single code named for this refusal, or ``None`` when the
    plane answered a status that several codes share and it did not carry one
    (see ``MATRIX``). ``codes`` is every code the status allows, so a consumer
    can render the honest set rather than a guess.
    """

    def __init__(
        self,
        *,
        code: Optional[str],
        reason: str,
        exit_code: int,
        status: int = 0,
        detail: str = "",
        codes: tuple[str, ...] = (),
        receipt: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(f"{code or 'refusal'} (HTTP {status})")
        self.code = code
        self.reason = reason
        self.exit_code = exit_code
        self.status = status
        self.detail = detail
        self.codes = codes
        self.receipt = receipt

    def with_receipt(self, receipt: Optional[Mapping[str, Any]]) -> "Refusal":
        """The same refusal, carrying the receipt the plane handed back."""
        return Refusal(
            code=self.code,
            reason=self.reason,
            exit_code=self.exit_code,
            status=self.status,
            detail=self.detail,
            codes=self.codes,
            receipt=receipt,
        )

    @property
    def verdict(self) -> str:
        """``OK`` / ``REFUSED`` / ``CANNOT-ASSESS`` — the repo's own words."""
        return {EXIT_OK: "OK", EXIT_REFUSED: "REFUSED", EXIT_CANNOT_ASSESS: "CANNOT-ASSESS"}[
            self.exit_code
        ]

    @property
    def label(self) -> str:
        """What to call this refusal in one token (the code, or the code set)."""
        if self.code:
            return self.code
        if self.codes:
            return "|".join(self.codes)
        return f"http_{self.status}"

    def as_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "verdict": self.verdict,
            "code": self.code,
            "codes": list(self.codes),
            "reason": self.reason,
            "status": self.status,
            "detail": self.detail,
            "exitCode": self.exit_code,
        }
        if self.receipt is not None:
            payload["receipt"] = self.receipt
        return payload

    def render(self) -> str:
        """The operator-facing rendering: one named line, then the plane's words.

        A receipt attached to a replay is printed as its own block, because it is
        evidence of a **previous** effect rather than of this call.
        """
        header = f"ao-control: {self.verdict} — {self.label}"
        if self.status:
            header = f"{header} (HTTP {self.status})"
        lines = [header, f"  {self.reason}"]
        if self.codes and not self.code:
            lines.append(
                "  the plane answered a status this family shares between "
                f"{', '.join(self.codes)}, and its envelope carried no code"
            )
        if self.detail:
            lines.append(f"  plane: {_clip(self.detail)}")
        if self.receipt is not None:
            lines.append("  original receipt (this command applied once, earlier):")
            lines.append(_indent(json.dumps(dict(self.receipt), sort_keys=True, indent=2), 4))
        return "\n".join(lines)


def _clip(text: str, limit: int = 400) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _indent(text: str, width: int) -> str:
    pad = " " * width
    return "\n".join(pad + line for line in text.splitlines())


def named_refusal(*, status: int, envelope_code: Optional[str] = None, detail: str = "") -> Refusal:
    """The refusal a returned status names, with the verdict that status carries.

    ``envelope_code`` is the code the plane's own envelope carried, when the
    transport preserved it. It is honoured only if the matrix allows it at that
    status — a code that does not belong to the status is not a code this refusal
    can honestly claim.
    """
    if status == 0:
        reason, exit_code = REASONS["plane_unreachable"]
        return Refusal(
            code="plane_unreachable", reason=reason, exit_code=exit_code, detail=detail
        )
    candidates = MATRIX.get(status, ())
    code: Optional[str] = None
    if envelope_code and envelope_code in candidates:
        code = envelope_code
    elif len(candidates) == 1:
        code = candidates[0]
    if code is not None:
        reason, exit_code = REASONS[code]
        return Refusal(
            code=code,
            reason=reason,
            exit_code=exit_code,
            status=status,
            detail=detail,
            codes=candidates,
        )
    if candidates:
        # No single code could be named. The verdict is then whatever the codes
        # the status allows agree on — and when they disagree (only 404 does:
        # the flag gate is CANNOT-ASSESS, an absent route is a refusal) the CLI
        # holds no verdict at all, which is exactly what CANNOT-ASSESS means.
        verdicts = {REASONS[candidate][1] for candidate in candidates}
        exit_code = verdicts.pop() if len(verdicts) == 1 else EXIT_CANNOT_ASSESS
        reason = (
            "the plane refused with one of "
            + ", ".join(candidates)
            + " and its envelope carried no single code"
        )
    else:
        reason = UNMAPPED_REASON
        # A status the matrix does not know is still split the way HTTP is: a
        # 5xx is the plane failing to reach a verdict, a 4xx is a verdict.
        exit_code = EXIT_CANNOT_ASSESS if status >= 500 else EXIT_REFUSED
    return Refusal(
        code=None,
        reason=reason,
        exit_code=exit_code,
        status=status,
        detail=detail,
        codes=candidates,
    )


def local_refusal(code: str, detail: str = "") -> Refusal:
    """A refusal the CLI makes itself — before, or instead of, sending anything.

    The code set is closed: a local refusal that is not in ``REASONS`` is a bug
    in this package, and it fails loudly rather than emitting an unexplained
    refusal.
    """
    try:
        reason, exit_code = REASONS[code]
    except KeyError as exc:  # pragma: no cover - guarded by the tests
        raise KeyError(f"{code!r} is not a named local refusal") from exc
    return Refusal(code=code, reason=reason, exit_code=exit_code, detail=detail)
