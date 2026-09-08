"""CLI smoke tests (issue #28): status / aggregate / analyze / negative /
attest subcommands behave as honest gates (real exit codes, no false green).
"""

from __future__ import annotations

import json
import os

from honesty.cli import main


class TestStatusCommand:
    def test_status_maps_exit_codes(self, capsys) -> None:
        assert main(["status", "0"]) == 0
        assert capsys.readouterr().out.strip() == "OK"
        assert main(["status", "1"]) == 0
        assert capsys.readouterr().out.strip() == "NOT-OK"
        assert main(["status", "124"]) == 0
        assert capsys.readouterr().out.strip() == "CANNOT-ASSESS"


class TestAggregateCommand:
    def test_all_ok_passes(self, capsys) -> None:
        assert main(["aggregate", "OK", "PASS"]) == 0
        assert capsys.readouterr().out.strip() == "OK"

    def test_not_ok_fails_gate(self, capsys) -> None:
        assert main(["aggregate", "OK", "NOT-OK", "UNKNOWN"]) == 1
        assert capsys.readouterr().out.strip() == "NOT-OK"

    def test_unknown_never_passes(self, capsys) -> None:
        # CANNOT-ASSESS aggregate exits with code 2 (never 0 / never OK).
        assert main(["aggregate", "OK", "CANNOT-ASSESS"]) == 2
        assert capsys.readouterr().out.strip() == "CANNOT-ASSESS"


class TestAnalyzeCommand:
    def test_honest_guards_clean_in_strict_mode(self, fixtures_dir: str) -> None:
        honest = os.path.join(fixtures_dir, "honest")
        assert main(["analyze", honest, "--strict"]) == 0

    def test_formality_fixture_fails_strict_mode(self, fixtures_dir: str) -> None:
        formality = os.path.join(fixtures_dir, "formality", "check_never_fails.sh")
        assert main(["analyze", formality, "--strict"]) == 1

    def test_review_mode_still_reports_findings(self, fixtures_dir: str) -> None:
        # Non-strict analyze is a review aid: exit 0, findings printed.
        formality = os.path.join(fixtures_dir, "formality")
        assert main(["analyze", formality]) == 0


class TestNegativeCommand:
    def test_manifest_green(self, manifest_path: str, capsys) -> None:
        assert main(["negative", manifest_path]) == 0
        out = capsys.readouterr().out
        assert "[PASS]" in out
        assert "[FAIL]" not in out


class TestAttestCommand:
    def test_attest_writes_evidence_json(
        self, tmp_path, capsys
    ) -> None:
        out = tmp_path / "attestation.json"
        rc = main(
            [
                "attest",
                "--guard-id",
                "check_blocklist",
                "--rc",
                "1",
                "--evidence",
                "violation present in violation.txt",
                "--controls",
                "blocklist_violation",
                "-o",
                str(out),
            ]
        )
        assert rc == 0
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["verdict"]["status"] == "NOT-OK"
        assert payload["exit_code"] == 1
        assert payload["controls"] == ["blocklist_violation"]

    def test_cannot_assess_attestation_is_rejected(
        self, tmp_path, capsys
    ) -> None:
        out = tmp_path / "attestation.json"
        rc = main(
            [
                "attest",
                "--guard-id",
                "g",
                "--rc",
                "2",
                "--evidence",
                "",
                "-o",
                str(out),
            ]
        )
        assert rc == 1  # a CANNOT-ASSESS attestation is not evidence
