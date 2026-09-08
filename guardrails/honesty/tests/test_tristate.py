"""Tri-state model tests (issue #28, acceptance criterion 1).

Covers the exit-code contract (0/1/2/124), the alias vocabulary
(PASS/FAIL/UNKNOWN), fail-closed aggregation (UNKNOWN never passes) and JSON
serialization.  Both directions are asserted: valid spellings map correctly
and invalid spellings raise rather than silently passing.
"""

from __future__ import annotations

import pytest

from honesty.tristate import (
    TriState,
    aggregate,
    deserialize,
    from_exit_code,
    from_json,
    parse,
    serialize,
    to_exit_code,
    to_json,
)


class TestExitCodeContract:
    def test_exit_zero_is_ok(self) -> None:
        assert from_exit_code(0) is TriState.OK

    def test_exit_one_is_not_ok(self) -> None:
        assert from_exit_code(1) is TriState.NOT_OK

    def test_exit_two_is_cannot_assess(self) -> None:
        assert from_exit_code(2) is TriState.CANNOT_ASSESS

    def test_timeout_124_is_cannot_assess_never_pass(self) -> None:
        assert from_exit_code(124) is TriState.CANNOT_ASSESS

    def test_any_non_contract_exit_is_cannot_assess(self) -> None:
        # A guard returning 3/126/-1 is broken or was killed; it must never
        # read as a pass (fail-closed).
        for rc in (3, 126, 127, 255, -1):
            assert from_exit_code(rc) is TriState.CANNOT_ASSESS

    def test_to_exit_code_maps_back(self) -> None:
        assert to_exit_code(TriState.OK) == 0
        assert to_exit_code(TriState.NOT_OK) == 1
        assert to_exit_code(TriState.CANNOT_ASSESS) == 2


class TestParseAliases:
    def test_issue_body_vocabulary(self) -> None:
        assert parse("OK") is TriState.OK
        assert parse("NOT-OK") is TriState.NOT_OK
        assert parse("CANNOT-ASSESS") is TriState.CANNOT_ASSESS

    def test_fleet_umbrella_aliases(self) -> None:
        assert parse("PASS") is TriState.OK
        assert parse("FAIL") is TriState.NOT_OK
        assert parse("UNKNOWN") is TriState.CANNOT_ASSESS

    def test_case_and_separator_tolerant(self) -> None:
        assert parse("ok") is TriState.OK
        assert parse("not-ok") is TriState.NOT_OK
        assert parse("not ok") is TriState.NOT_OK
        assert parse("cannot assess") is TriState.CANNOT_ASSESS
        assert parse("NOT_OK") is TriState.NOT_OK
        assert parse("CANNOT_ASSESS") is TriState.CANNOT_ASSESS

    def test_passes_tristate_through(self) -> None:
        assert parse(TriState.OK) is TriState.OK

    def test_unknown_spelling_raises_never_passes(self) -> None:
        with pytest.raises(ValueError):
            parse("MAYBE")
        with pytest.raises(ValueError):
            parse("")


class TestIsPassSemantics:
    def test_only_ok_is_pass(self) -> None:
        assert TriState.OK.is_pass is True
        assert TriState.NOT_OK.is_pass is False
        assert TriState.CANNOT_ASSESS.is_pass is False

    def test_passes_alias_matches_is_pass(self) -> None:
        assert TriState.OK.passes is True
        assert TriState.CANNOT_ASSESS.passes is False

    def test_is_fail_and_is_unknown(self) -> None:
        assert TriState.NOT_OK.is_fail is True
        assert TriState.OK.is_fail is False
        assert TriState.CANNOT_ASSESS.is_unknown is True
        assert TriState.OK.is_unknown is False


class TestAggregate:
    def test_all_ok_is_ok(self) -> None:
        assert aggregate([TriState.OK, TriState.OK]) is TriState.OK

    def test_empty_set_is_ok(self) -> None:
        assert aggregate([]) is TriState.OK

    def test_any_not_ok_fails_gate(self) -> None:
        assert aggregate([TriState.OK, TriState.NOT_OK]) is TriState.NOT_OK

    def test_unknown_never_passes(self) -> None:
        # A single CANNOT-ASSESS keeps the aggregate from reading PASS.
        assert aggregate([TriState.OK, TriState.CANNOT_ASSESS]) is TriState.CANNOT_ASSESS

    def test_not_ok_beats_unknown(self) -> None:
        assert aggregate([TriState.CANNOT_ASSESS, TriState.NOT_OK]) is TriState.NOT_OK

    def test_aggregate_never_returns_ok_when_unknown_present(self) -> None:
        for combo in (
            [TriState.CANNOT_ASSESS],
            [TriState.CANNOT_ASSESS, TriState.CANNOT_ASSESS],
            [TriState.OK, TriState.OK, TriState.CANNOT_ASSESS],
        ):
            assert aggregate(combo) is not TriState.OK


class TestSerialization:
    def test_serialize_dict(self) -> None:
        assert serialize(TriState.NOT_OK) == {"status": "NOT-OK"}

    def test_deserialize_roundtrip(self) -> None:
        for state in TriState:
            assert deserialize(serialize(state)) is state

    def test_json_roundtrip(self) -> None:
        for state in TriState:
            assert from_json(to_json(state)) is state

    def test_deserialize_unknown_raises(self) -> None:
        with pytest.raises((ValueError, KeyError)):
            deserialize({"status": "MAYBE"})
