"""The named refusals — every code the plane can send, and what the CLI calls it.

The issue's acceptance is *"an unreachable plane exits non-zero with a named
reason"*, and the refusal matrix adds the rest: ``404 feature_disabled``,
``401``, ``405``, ``422``, ``403``, ``409``, ``503``. Each is asserted here as a
**name**, not as a number, because a number is what the operator cannot act on.
"""

from __future__ import annotations

import pytest

from _doubles import refusal_envelope

from integrations.paperclip.model import error_for_status

from aoctl import refusals
from aoctl.plane import envelope_code
from aoctl.refusals import (
    EXIT_CANNOT_ASSESS,
    EXIT_REFUSED,
    Refusal,
    local_refusal,
    named_refusal,
)


# --- one row per status the family can answer ------------------------------
@pytest.mark.parametrize(
    "status,envelope,code,exit_code",
    [
        (404, "feature_disabled", "feature_disabled", EXIT_CANNOT_ASSESS),
        (404, "not_found", "not_found", EXIT_REFUSED),
        (401, "unauthorized", "unauthorized", EXIT_REFUSED),
        (405, "method_not_allowed", "method_not_allowed", EXIT_REFUSED),
        (422, "unknown_verb", "unknown_verb", EXIT_REFUSED),
        (403, "verb_not_exposed", "verb_not_exposed", EXIT_REFUSED),
        (403, "scope_denied", "scope_denied", EXIT_REFUSED),
        (403, "permission_denied", "permission_denied", EXIT_REFUSED),
        (409, "duplicate_command", "duplicate_command", EXIT_REFUSED),
        (409, "lever_refused", "lever_refused", EXIT_REFUSED),
        (503, "lever_unreachable", "lever_unreachable", EXIT_CANNOT_ASSESS),
        (503, "vocabulary_unavailable", "vocabulary_unavailable", EXIT_CANNOT_ASSESS),
        (400, "invalid_request", "invalid_request", EXIT_REFUSED),
    ],
)
def test_each_refusal_code_is_named_from_the_planes_own_envelope(status, envelope, code, exit_code):
    refusal = named_refusal(status=status, envelope_code=envelope, detail="plane said so")
    assert refusal.code == code
    assert refusal.exit_code == exit_code
    assert refusal.reason == refusals.REASONS[code][0]
    assert refusal.label == code
    assert refusal.render().startswith(f"ao-control: {refusal.verdict} — {code}")


def test_the_flag_gate_is_cannot_assess_and_says_why():
    refusal = named_refusal(status=404, envelope_code="feature_disabled")
    assert refusal.verdict == "CANNOT-ASSESS"
    assert "surfaces.remote_control" in refusal.reason
    assert "invisible" in refusal.reason


@pytest.mark.parametrize("status,codes", sorted(refusals.MATRIX.items()))
def test_a_status_with_no_envelope_names_the_codes_it_allows(status, codes):
    """A status several codes share is never guessed down to one.

    The verdict is then whatever those codes agree on, and ``CANNOT-ASSESS``
    when they disagree — which is what "no verdict is available" means.
    """
    refusal = named_refusal(status=status)
    if len(codes) == 1:
        assert refusal.code == codes[0]
    else:
        assert refusal.code is None
        assert refusal.codes == codes
        assert all(code in refusal.render() for code in codes)
        assert "no code" in refusal.render()
    verdicts = {refusals.REASONS[code][1] for code in codes}
    expected = verdicts.pop() if len(verdicts) == 1 else EXIT_CANNOT_ASSESS
    assert refusal.exit_code == expected != 0


def test_a_code_that_does_not_belong_to_the_status_is_not_claimed():
    """Honesty: a 401 cannot be reported as a lever that was unreachable."""
    refusal = named_refusal(status=401, envelope_code="lever_unreachable")
    assert refusal.code == "unauthorized"


def test_an_unmapped_status_is_still_a_refusal():
    refusal = named_refusal(status=418, detail="I am a teapot")
    assert refusal.code is None
    assert refusal.reason == refusals.UNMAPPED_REASON
    assert refusal.exit_code == EXIT_REFUSED
    assert refusal.label == "http_418"


def test_a_status_the_matrix_does_not_know_is_split_the_way_http_is():
    """A 4xx is a verdict; a 5xx is the plane failing to reach one."""
    assert named_refusal(status=429).exit_code == EXIT_REFUSED
    assert named_refusal(status=500).exit_code == EXIT_CANNOT_ASSESS
    assert named_refusal(status=599).exit_code == EXIT_CANNOT_ASSESS


def test_an_unreachable_plane_is_never_a_success():
    refusal = named_refusal(status=0, detail="URLError: connection refused")
    assert refusal.code == "plane_unreachable"
    assert refusal.exit_code == EXIT_CANNOT_ASSESS != 0
    assert refusal.verdict == "CANNOT-ASSESS"
    assert "never delivered" in refusal.reason


# --- the reader is pinned to the seam's own rendering ----------------------
def test_the_code_reader_is_pinned_to_the_seams_own_rendering():
    """``HttpTransport`` renders the console's envelope into the error it raises.

    This builds the message with the seam's *own* ``error_for_status`` and the
    console's *own* envelope shape (``{"code", "message"}``), so the reader is
    pinned against the thing it will really be given rather than against a
    string someone typed into a test.
    """
    envelope = refusal_envelope(404, "feature_disabled", "the family is OFF")
    raised = error_for_status(404, "/api/control/fleet/pause", str(envelope["error"]))
    message = str(raised)
    assert "HTTP 404" in message
    assert envelope_code(message) == "feature_disabled"
    assert envelope_code("paperclip /x: HTTP 503 — lever_unreachable") == "lever_unreachable"
    assert envelope_code("") is None
    assert envelope_code("nothing to read here") is None


def test_the_seam_maps_the_console_statuses_to_typed_errors():
    """The client relies on ``.status``; the seam is where it comes from."""
    for status in refusals.MATRIX:
        raised = error_for_status(status, "/api/control/fleet/pause")
        assert int(raised.status) == status


# --- the CLI's own refusals ------------------------------------------------
LOCAL_CODES = (
    "no_session",
    "confirmation_required",
    "surface_drift",
    "vocabulary_unreadable",
    "contract_unavailable",
    "address_invalid",
    "plane_malformed",
)


@pytest.mark.parametrize("code", LOCAL_CODES)
def test_every_local_refusal_is_named(code):
    refusal = local_refusal(code, "because")
    assert refusal.code == code
    assert refusal.reason == refusals.REASONS[code][0]
    assert refusal.detail == "because"
    assert refusal.exit_code in (EXIT_REFUSED, EXIT_CANNOT_ASSESS)


def test_an_unnamed_local_refusal_is_a_bug_not_a_quiet_refusal():
    with pytest.raises(KeyError):
        local_refusal("something_invented")


def test_every_local_code_has_a_reason():
    for code in LOCAL_CODES:
        assert code in refusals.REASONS


def test_the_exit_contract_is_the_tristate_the_repo_uses():
    assert (refusals.EXIT_OK, EXIT_REFUSED, EXIT_CANNOT_ASSESS) == (0, 1, 2)


def test_a_refusal_is_raisable_and_carries_its_verdict():
    """A refusal is a verdict: a caller that ignores it must fail loudly."""
    refusal = named_refusal(status=403, envelope_code="permission_denied")
    with pytest.raises(Refusal) as caught:
        raise refusal
    assert caught.value.code == "permission_denied"
    assert caught.value.as_json()["verdict"] == "REFUSED"


def test_a_replay_receipt_is_carried_when_the_plane_hands_one_back():
    original = {"commandId": "cmd_1", "verb": "fleet.pause", "exitCode": 0}
    refusal = named_refusal(status=409, envelope_code="duplicate_command").with_receipt(original)
    assert refusal.receipt == original
    assert refusal.as_json()["receipt"] == original
    assert "original receipt" in refusal.render()
