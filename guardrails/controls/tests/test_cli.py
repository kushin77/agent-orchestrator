"""CLI: list/get/toggle/check-report/self-test and their honest exit codes."""

from __future__ import annotations

import json

from controls import cli
from controls.audit import JsonlControlAuditLog
from controls.registry import default_controls_path, load_state


def _args(tmp_path, *rest):
    return [
        "--state",
        str(tmp_path / "state.json"),
        "--audit",
        str(tmp_path / "audit.jsonl"),
        *rest,
    ]


def test_list_reports_default_off(capsys, tmp_path):
    assert cli.main(_args(tmp_path, "list")) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "246" in out
    assert "off" in out
    assert out.count("446") == 0


def test_get_unknown_control_fails(capsys, tmp_path):
    assert cli.main(_args(tmp_path, "get", "nope")) == cli.EXIT_FAIL


def test_get_known_control_is_json(capsys, tmp_path):
    assert cli.main(_args(tmp_path, "get", "model-call-budget")) == cli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["id"] == "model-call-budget"
    assert payload["enabled"] is False
    assert payload["status"] == 246


def test_toggle_flips_and_audits(capsys, tmp_path):
    assert cli.main(_args(tmp_path, "toggle", "model-call-budget", "--on", "--actor", "t")) == cli.EXIT_OK
    record = json.loads(capsys.readouterr().out)
    assert record["after"] is True
    assert record["status_after"] == 446
    # a second reader sees the persisted state and the single audit record
    assert load_state(tmp_path / "state.json")["model-call-budget"] is True
    assert len(JsonlControlAuditLog(tmp_path / "audit.jsonl").records()) == 1


def test_toggle_unknown_control_fails(capsys, tmp_path):
    assert cli.main(_args(tmp_path, "toggle", "nope", "--on")) == cli.EXIT_FAIL
    assert "unknown control" in capsys.readouterr().err


def test_check_report_all_off_is_ok(capsys, tmp_path):
    assert cli.main(_args(tmp_path, "check-report")) == cli.EXIT_OK
    assert "246" in capsys.readouterr().out


def test_check_report_enabled_control_is_not_ok(capsys, tmp_path):
    cli.main(_args(tmp_path, "toggle", "model-call-budget", "--on"))
    assert cli.main(_args(tmp_path, "check-report")) == cli.EXIT_FAIL
    assert "446" in capsys.readouterr().out


def test_check_report_json(capsys, tmp_path):
    assert cli.main(_args(tmp_path, "check-report", "--json")) == cli.EXIT_OK
    rows = json.loads(capsys.readouterr().out)
    assert {row["status"] for row in rows} == {246}


def test_self_test_holds(capsys):
    assert cli.main(["self-test"]) == cli.EXIT_OK
    assert "invariant(s) hold" in capsys.readouterr().out


def test_self_test_mutation_is_refused(capsys):
    assert cli.main(["self-test", "--mutate", "default-on"]) == cli.EXIT_OK
    assert "refused" in capsys.readouterr().out


def test_default_registry_path_exists():
    assert default_controls_path().is_file()
