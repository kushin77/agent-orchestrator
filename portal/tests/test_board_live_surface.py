"""portal fleet-board live surface tests (issue #880, EPIC #878 lane L1).

Proves the acceptance criterion for the live surface:

* ``portal/schemas/fleet-board-row.schema.json`` validates a genuine row
  derived from ``.board/snapshot.json`` + ``.board/claims.jsonl``;
* a malformed row (missing field, wrong type, unknown field) is refused **by
  name** — the negative control (no false green);
* the surface is feature-flag-gated OFF until promoted, before authN, exactly
  like the workbook-11 views it sits beside;
* the served route (``GET /api/board/rows``) returns the joined, validated
  rows once the flag is flipped on for the test.
"""

from __future__ import annotations

import json

import pytest
import yaml
from conftest import REPO_ROOT, console_sso, login_as

from portal.server.app import build_app
from portal.server.config_flags import FLEET_BOARD_SURFACE, surface_enabled
from portal.server.livestore import (
    BOARD_ROW_SCHEMA,
    BoardSurface,
    _validate_row,
    load_board_row_schema,
    load_board_rows,
)

TENANT = "acme"


def _write_snapshot(path, issues):
    path.write_text(
        json.dumps({"generated_at": "2026-09-17T00:00:00Z", "source": "x", "issues": issues}),
        encoding="utf-8",
    )


def _write_claims(path, lines):
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# The flag gate (GR-5: a new surface ships OFF)
# --------------------------------------------------------------------------- #
def test_config_declares_the_board_on():
    document = yaml.safe_load(
        (REPO_ROOT / "portal" / "config" / "feature-flags.yaml").read_text(
            encoding="utf-8"
        )
    )
    entry = document["surfaces"][FLEET_BOARD_SURFACE]
    assert entry["default"] in (True, "on"), "the fleet board must ship ON (GR-5 reversal)"
    assert surface_enabled(REPO_ROOT, surface=FLEET_BOARD_SURFACE) is True


def test_the_default_app_requires_authn_not_hidden_by_flag():
    app = build_app(sso=console_sso())
    anonymous = login_as(app, "root@platform.example.com", TENANT)
    anonymous.cookies.clear()
    status, payload = anonymous.get("/api/board/rows")
    assert status == 401
    assert payload["error"]["code"] == "unauthorized"


# --------------------------------------------------------------------------- #
# Schema: a genuine row validates; a malformed one is refused by name
# --------------------------------------------------------------------------- #
def test_schema_validates_a_genuine_row():
    schema = load_board_row_schema(REPO_ROOT)
    good_row = {
        "schema": BOARD_ROW_SCHEMA,
        "number": 880,
        "title": "Wire the portal live surface",
        "state": "OPEN",
        "lane": "portal",
        "claimed_by": "frontend-sme",
        "claimed_at": "2026-09-17T00:00:00Z",
        "labels": ["type:task"],
    }
    assert _validate_row(good_row, schema) == []


@pytest.mark.parametrize(
    "bad_row,expected_fragment",
    [
        # missing required field
        (
            {
                "schema": BOARD_ROW_SCHEMA,
                "title": "no number",
                "state": "OPEN",
                "lane": "",
                "claimed_by": "",
            },
            "missing required field 'number'",
        ),
        # wrong type (state must be a string)
        (
            {
                "schema": BOARD_ROW_SCHEMA,
                "number": 1,
                "title": "wrong type",
                "state": 7,
                "lane": "",
                "claimed_by": "",
            },
            "expected string",
        ),
        # not in the closed enum
        (
            {
                "schema": BOARD_ROW_SCHEMA,
                "number": 1,
                "title": "bad state",
                "state": "HALF_OPEN",
                "lane": "",
                "claimed_by": "",
            },
            "is not one of",
        ),
        # unknown field (additionalProperties: false)
        (
            {
                "schema": BOARD_ROW_SCHEMA,
                "number": 1,
                "title": "extra field",
                "state": "OPEN",
                "lane": "",
                "claimed_by": "",
                "unexpected": "nope",
            },
            "unexpected field 'unexpected'",
        ),
    ],
)
def test_schema_refuses_a_malformed_row_by_name(bad_row, expected_fragment):
    """Negative control: the schema can and does fail (no false green)."""
    schema = load_board_row_schema(REPO_ROOT)
    errors = _validate_row(bad_row, schema)
    assert errors, f"expected {bad_row!r} to be refused"
    assert any(expected_fragment in error for error in errors)


# --------------------------------------------------------------------------- #
# load_board_rows: joins the two live documents and refuses bad rows by name
# --------------------------------------------------------------------------- #
def test_load_board_rows_joins_snapshot_and_claims(tmp_path):
    snapshot = tmp_path / "snapshot.json"
    claims = tmp_path / "claims.jsonl"
    _write_snapshot(
        snapshot,
        [
            {"number": 880, "title": "Portal live surface", "state": "OPEN", "labels": []},
            {"number": 881, "title": "Unclaimed issue", "state": "OPEN", "labels": ["type:task"]},
        ],
    )
    _write_claims(
        claims,
        [
            {"event": "claim", "issue": 880, "agent": "frontend-sme", "lane": "portal",
             "at": "2026-09-17T00:00:00Z"},
        ],
    )
    result = load_board_rows(REPO_ROOT, snapshot_path=snapshot, claims_path=claims)
    assert result.rejected == ()
    by_number = {row["number"]: row for row in result.rows}
    assert by_number[880]["claimed_by"] == "frontend-sme"
    assert by_number[880]["lane"] == "portal"
    assert by_number[881]["claimed_by"] == ""
    assert by_number[881]["lane"] == ""


def test_load_board_rows_refuses_a_malformed_source_row_by_name(tmp_path):
    """A corrupt snapshot degrades honestly: the bad row is named, not served."""
    snapshot = tmp_path / "snapshot.json"
    claims = tmp_path / "claims.jsonl"
    _write_snapshot(
        snapshot,
        [
            {"number": 1, "title": "fine", "state": "OPEN", "labels": []},
            # 'state' is not in the closed OPEN/CLOSED vocabulary
            {"number": 2, "title": "corrupt", "state": "SOMETHING_ELSE", "labels": []},
        ],
    )
    _write_claims(claims, [])
    result = load_board_rows(REPO_ROOT, snapshot_path=snapshot, claims_path=claims)
    assert [row["number"] for row in result.rows] == [1]
    assert len(result.rejected) == 1
    assert result.rejected[0].identifier == "2"
    assert any("is not one of" in error for error in result.rejected[0].errors)


def test_a_released_claim_leaves_the_row_unclaimed(tmp_path):
    snapshot = tmp_path / "snapshot.json"
    claims = tmp_path / "claims.jsonl"
    _write_snapshot(snapshot, [{"number": 5, "title": "x", "state": "OPEN", "labels": []}])
    _write_claims(
        claims,
        [
            {"event": "claim", "issue": 5, "agent": "a", "lane": "l", "at": "t1"},
            {"event": "release", "issue": 5, "agent": "a", "lane": "", "at": "t2"},
        ],
    )
    result = load_board_rows(REPO_ROOT, snapshot_path=snapshot, claims_path=claims)
    assert result.rows[0]["claimed_by"] == ""
    assert result.rows[0]["lane"] == ""


# --------------------------------------------------------------------------- #
# Route: GET /api/board/rows once the flag is flipped on for the test
# --------------------------------------------------------------------------- #
def test_the_route_returns_rows_once_flipped_on(tmp_path):
    snapshot = tmp_path / "snapshot.json"
    claims = tmp_path / "claims.jsonl"
    _write_snapshot(snapshot, [{"number": 9, "title": "y", "state": "OPEN", "labels": []}])
    _write_claims(claims, [])
    surface = BoardSurface(
        repo_root=REPO_ROOT,
        enabled=True,
        snapshot_path=snapshot,
        claims_path=claims,
    )
    app = build_app(sso=console_sso(), board_surface=surface)
    api = login_as(app, "root@platform.example.com", TENANT)
    status, payload = api.get("/api/board/rows")
    assert status == 200
    data = payload["data"]
    assert data["schema"] == BOARD_ROW_SCHEMA
    assert [row["number"] for row in data["rows"]] == [9]
    assert data["rejected"] == []


def test_the_route_is_get_only(tmp_path):
    surface = BoardSurface(repo_root=REPO_ROOT, enabled=True)
    app = build_app(sso=console_sso(), board_surface=surface)
    api = login_as(app, "root@platform.example.com", TENANT)
    status, payload = api.post("/api/board/rows", body={})
    assert status == 405
