"""The CLI's exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS (issue #28)."""

from __future__ import annotations

import json
from pathlib import Path

import cli
import model

AO = "kushin77/agent-orchestrator"
AO_ITEM = "kushin77/agent-orchestrator#150"


def test_validate_is_ok_for_the_shipped_matrix(capsys) -> None:
    rc = cli.main(["validate"])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.startswith("valid: yes")


def test_validate_is_not_ok_for_a_defective_matrix(tmp_path, capsys) -> None:
    path = Path(tmp_path) / "matrix.yaml"
    path.write_text(
        "version: 1\n"
        "repos:\n"
        "  - id: kushin77/agent-orchestrator\n"
        "    fleet: ao-fleet\n"
        "    gate: make verify\n"
        "    admin_rights: [files]\n"
        "principals:\n"
        "  - id: ao-fleet\n"
        "    kind: repo-fleet\n"
        "    scope: [kushin77/agent-orchestrator]\n"
        "    cross_repo: true\n"
        "    admin_rights: [files]\n"
        "    actors:\n"
        "      - id: ao-soldier-1\n"
        "        role: soldier\n"
        "        posture: executor\n"
        "        model_tier: LOW\n"
        "work_items: []\n",
        encoding="utf-8",
    )
    rc = cli.main(["--matrix", str(path), "validate"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "valid: NO" in captured.out
    assert "cross-repo-kind" in captured.err


def test_validate_is_cannot_assess_for_an_unparseable_matrix(tmp_path, capsys) -> None:
    path = Path(tmp_path) / "matrix.yaml"
    path.write_text("version: 1\nprincipals: [\n", encoding="utf-8")
    rc = cli.main(["--matrix", str(path), "validate"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "CANNOT-ASSESS" in captured.err
    assert "valid: cannot-assess" in captured.out


def test_validate_is_cannot_assess_for_a_missing_matrix(tmp_path, capsys) -> None:
    rc = cli.main(["--matrix", str(Path(tmp_path) / "absent.yaml"), "validate"])
    assert rc == 2
    assert "CANNOT-ASSESS" in capsys.readouterr().err


def test_validate_is_cannot_assess_for_a_non_mapping_root(tmp_path, capsys) -> None:
    path = Path(tmp_path) / "matrix.yaml"
    path.write_text("- version\n- principals\n", encoding="utf-8")
    rc = cli.main(["--matrix", str(path), "validate"])
    assert rc == 2
    assert "must be a mapping" in capsys.readouterr().err


def test_can_act_exit_codes(capsys) -> None:
    assert cli.main(["can-act", "--principal", "ao-soldier-1", "--repo", AO, "--action", "files"]) == 0
    assert cli.main(["can-act", "--principal", "ao-soldier-1", "--repo", "kushin77/capital-underwriting", "--action", "files"]) == 1
    assert cli.main(["can-act", "--principal", "ghost", "--repo", AO, "--action", "files"]) == 2
    capsys.readouterr()


def test_can_act_json_payload(capsys) -> None:
    assert cli.main(["--json", "can-act", "--principal", "ao-soldier-1", "--repo", AO, "--action", "files"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "ALLOW"
    assert payload["exit_code"] == 0


def test_sod_exit_codes(capsys) -> None:
    assert cli.main(["sod", "--work-item", AO_ITEM]) == 0
    assert cli.main(["sod", "--work-item", "kushin77/agent-orchestrator#404040"]) == 2
    capsys.readouterr()


def test_closure_is_not_ok_while_an_item_has_no_evidence(capsys) -> None:
    rc = cli.main(["closure"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "closure-gate-evidence-missing" in captured.out


def test_isolation_command_reports_ok(capsys) -> None:
    rc = cli.main(["isolation"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "isolation: OK" in captured.out


def test_matrix_command_prints_the_assessment(capsys) -> None:
    rc = cli.main(["matrix"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "principal enterprise-controller" in captured.out
    assert "merge-authority" in captured.out
    assert "matrix: ALLOW" in captured.out


def test_matrix_command_json_round_trips(capsys) -> None:
    assert cli.main(["--json", "matrix"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is True
    assert len(payload["principals"]) == 3
    assert [one["id"] for one in payload["principals"] if one["cross_repo"]] == ["enterprise-controller"]


def test_a_missing_schema_is_cannot_assess_not_a_crash(tmp_path, capsys) -> None:
    rc = cli.main(["--schema", str(Path(tmp_path) / "absent.json"), "validate"])
    assert rc == 2
    assert "schema unusable" in capsys.readouterr().err


def test_every_decision_exit_code_matches_the_documented_contract(matrix: model.Matrix) -> None:
    assert model.Verdict.ALLOW.exit_code == 0
    assert model.Verdict.DENY.exit_code == 1
    assert model.Verdict.CANNOT_ASSESS.exit_code == 2
