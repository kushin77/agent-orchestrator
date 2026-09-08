"""Negative-control + blockproof runner tests (issue #28, acceptance
criterion 2).

The decisive property: a guard that CANNOT fail fails its own negative
control.  Every honest fixture guard is proven to discriminate (fails on a
planted violation, passes clean input, and returns CANNOT-ASSESS on input it
cannot assess) and every declared manifest control passes.
"""

from __future__ import annotations

import json
import os

import pytest

from honesty.negative_control import (
    NegativeControl,
    all_passed,
    exit_code_for_outcomes,
    load_manifest,
    run_control,
    run_controls,
    write_report,
)
from honesty.tristate import TriState


def _control(
    honesty_root: str, guard_rel: str, args: list[str], expect: TriState, cid: str
) -> NegativeControl:
    # Args stay as written and are interpreted from the guard's working dir
    # (honesty_root), mirroring how the manifest runner behaves.
    return NegativeControl(
        id=cid,
        guard=os.path.join(honesty_root, guard_rel),
        args=list(args),
        expect=expect,
    )


class TestHonestGuardsDiscriminate:
    def test_blocklist_clean_passes(self, honesty_root: str) -> None:
        c = _control(
            honesty_root,
            "fixtures/honest/check_blocklist.sh",
            ["fixtures/inputs/clean.txt"],
            TriState.OK,
            "clean",
        )
        outcome = run_control(c, cwd=honesty_root)
        assert outcome.verdict is TriState.OK
        assert outcome.passed

    def test_blocklist_violation_fails(self, honesty_root: str) -> None:
        # NEGATIVE control: a planted forbidden token must fail the guard.
        c = _control(
            honesty_root,
            "fixtures/honest/check_blocklist.sh",
            ["fixtures/inputs/violation.txt"],
            TriState.NOT_OK,
            "violation",
        )
        outcome = run_control(c, cwd=honesty_root)
        assert outcome.verdict is TriState.NOT_OK
        assert outcome.exit_code == 1
        assert outcome.passed

    def test_blocklist_unreadable_is_cannot_assess(self, honesty_root: str) -> None:
        c = _control(
            honesty_root,
            "fixtures/honest/check_blocklist.sh",
            ["fixtures/inputs/does-not-exist-xyz.txt"],
            TriState.CANNOT_ASSESS,
            "unreadable",
        )
        outcome = run_control(c, cwd=honesty_root)
        assert outcome.verdict is TriState.CANNOT_ASSESS
        assert outcome.passed

    def test_artifacts_all_present_passes(self, honesty_root: str) -> None:
        c = _control(
            honesty_root,
            "fixtures/honest/check_all_artifacts.sh",
            ["fixtures/inputs/clean.txt", "fixtures/inputs/violation.txt"],
            TriState.OK,
            "all",
        )
        outcome = run_control(c, cwd=honesty_root)
        assert outcome.verdict is TriState.OK
        assert outcome.passed

    def test_artifacts_missing_fails(self, honesty_root: str) -> None:
        c = _control(
            honesty_root,
            "fixtures/honest/check_all_artifacts.sh",
            ["fixtures/inputs/clean.txt", "fixtures/inputs/does-not-exist-xyz.txt"],
            TriState.NOT_OK,
            "missing",
        )
        outcome = run_control(c, cwd=honesty_root)
        assert outcome.verdict is TriState.NOT_OK
        assert outcome.passed

    def test_tool_missing_is_fail_not_skip(self, honesty_root: str) -> None:
        c = _control(
            honesty_root,
            "fixtures/honest/check_tool_present.sh",
            ["definitely-not-a-real-tool-xyz"],
            TriState.NOT_OK,
            "tool-missing",
        )
        outcome = run_control(c, cwd=honesty_root)
        assert outcome.verdict is TriState.NOT_OK
        assert outcome.passed

    def test_tool_present_passes(self, honesty_root: str) -> None:
        c = _control(
            honesty_root,
            "fixtures/honest/check_tool_present.sh",
            ["bash"],
            TriState.OK,
            "tool-present",
        )
        outcome = run_control(c, cwd=honesty_root)
        assert outcome.verdict is TriState.OK
        assert outcome.passed


class TestRunnerCatchesLyingGuards:
    def test_never_fail_guard_fails_its_own_negative_control(
        self, honesty_root: str
    ) -> None:
        # The formality guard cannot fail: feed it a file with no marker and
        # expect NOT-OK.  It returns OK, so the negative control FAILS --
        # this is exactly how a no-false-green violation is detected.
        c = _control(
            honesty_root,
            "fixtures/formality/check_never_fails.sh",
            ["fixtures/inputs/clean.txt"],
            TriState.NOT_OK,
            "must-fail-but-cannot",
        )
        outcome = run_control(c, cwd=honesty_root)
        assert outcome.verdict is TriState.OK  # it could not fail
        assert outcome.passed is False
        assert exit_code_for_outcomes([outcome]) == 1

    def test_skip_as_pass_guard_cannot_meet_its_negative_control(
        self, honesty_root: str
    ) -> None:
        # The SKIP-counted-as-PASS guard exits 0 on the skip path; a control
        # that requires a hard fail on an unconfigured provider is not met.
        c = _control(
            honesty_root,
            "fixtures/formality/check_skip_is_pass.sh",
            [],
            TriState.NOT_OK,
            "skip-must-fail",
        )
        outcome = run_control(c, cwd=honesty_root)
        assert outcome.verdict is not TriState.NOT_OK
        assert outcome.passed is False


class TestBlockproof:
    def test_blockproof_records_evidence(self, honesty_root: str) -> None:
        c = _control(
            honesty_root,
            "fixtures/honest/check_blocklist.sh",
            ["fixtures/inputs/violation.txt"],
            TriState.NOT_OK,
            "violation",
        )
        outcome = run_control(c, cwd=honesty_root)
        proof = outcome.blockproof()
        assert proof["control"] == "violation"
        assert proof["exit_code"] == 1
        assert proof["verdict"] == "NOT-OK"
        assert proof["expected"] == "NOT-OK"
        assert proof["proof"] is True
        assert "forbidden" in proof["output"]  # the actual evidence text


class TestManifest:
    def test_manifest_loads_all_controls(self, manifest_path: str) -> None:
        controls = load_manifest(manifest_path)
        assert len(controls) == 8
        ids = {c.id for c in controls}
        assert {"blocklist_clean", "blocklist_violation", "tool_missing"} <= ids

    def test_manifest_controls_all_pass(self, manifest_path: str) -> None:
        controls = load_manifest(manifest_path)
        outcomes = run_controls(controls, cwd=os.path.dirname(manifest_path))
        assert all_passed(outcomes), [
            (o.control_id, o.verdict.value, o.expected.value) for o in outcomes
        ]

    def test_manifest_report_is_roundtrippable(
        self, manifest_path: str, tmp_path
    ) -> None:
        controls = load_manifest(manifest_path)
        outcomes = run_controls(controls, cwd=os.path.dirname(manifest_path))
        report = tmp_path / "blockproofs.json"
        write_report(outcomes, str(report))
        payload = json.loads(report.read_text(encoding="utf-8"))
        assert payload["result"] == "PASS"
        assert len(payload["controls"]) == 8
        assert all(c["proof"] for c in payload["controls"])


class TestMissingGuardIsCannotAssess:
    def test_unrunnable_guard_is_cannot_assess_not_pass(
        self, tmp_path
    ) -> None:
        c = NegativeControl(
            id="gone", guard=str(tmp_path / "does-not-exist.sh"), expect=TriState.NOT_OK
        )
        outcome = run_control(c, cwd=str(tmp_path))
        assert outcome.verdict is TriState.CANNOT_ASSESS
        assert outcome.passed is False
