"""The error taxonomy is mapped from the seam's own errors, not invented (#413)."""

from __future__ import annotations

import pytest

from integrations.paperclip.api import taxonomy

#: The seven statuses the seam doc fixes (docs/PAPERCLIP-ING-INTEGRATION.md §1).
EXPECTED = (400, 401, 403, 404, 409, 422, 503)


def test_taxonomy_order_is_the_seam_order() -> None:
    assert taxonomy.statuses() == EXPECTED


def test_every_status_has_an_entry_with_a_code() -> None:
    entries = {entry["status"]: entry for entry in taxonomy.entries()}
    assert tuple(sorted(entries)) == EXPECTED
    for status in EXPECTED:
        assert entries[status]["codes"], f"status {status} maps to no wire code"


#: The wire codes each status is carried by (from the fleet's own constructors).
EXPECTED_CODES = {
    400: {"validation_error"},
    401: {"unauthorized", "invalid_token", "token_expired", "session_revoked"},
    403: {"cross_tenant", "permission_denied"},
    404: {"not_found"},
    409: {"replayed_run_id"},
    422: {"refused"},
    503: {"unavailable"},
}


@pytest.mark.parametrize("status", EXPECTED)
def test_each_status_is_carried_by_a_real_refusal(status: int) -> None:
    """Every status has at least one test: a real refusal carries it by name."""
    codes = taxonomy.codes_by_status()
    assert status in codes, f"no refusal constructor carries status {status}"
    assert {entry["code"] for entry in codes[status]} == EXPECTED_CODES[status]


def test_responses_expose_all_seven_statuses() -> None:
    responses = taxonomy.responses()
    assert tuple(sorted(int(key) for key in responses)) == EXPECTED
    for status in EXPECTED:
        content = responses[str(status)]["content"]["application/json"]["schema"]
        assert content == {"$ref": "#/components/schemas/Error"}


def test_codes_are_the_fleet_vocabulary() -> None:
    """The codes are the fleet's own — sampled from the merged constructors."""
    codes = {entry["code"] for entry in taxonomy.codes_by_status()[401]}
    assert {"unauthorized", "invalid_token", "token_expired"}.issubset(codes)
    assert taxonomy.codes_by_status()[403][0]["home"].endswith("auth/model.py")
    not_found = [entry for entry in taxonomy.codes_by_status()[404]]
    assert not_found and not_found[0]["code"] == "not_found"
