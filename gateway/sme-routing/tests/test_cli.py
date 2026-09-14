"""CLI exit-code contract tests: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.

The exit codes are the machine contract a gate reads, so they are asserted
directly (in-process, and once through a real subprocess to prove the entry
point works from any cwd).
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any, Dict

import pytest

from cli import CANNOT_ASSESS, NOT_OK, OK, main


# --- OK paths ---------------------------------------------------------------
def test_validate_is_ok(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate"]) == OK
    captured = capsys.readouterr().out
    assert "validate: OK" in captured
    assert "fail-safe unknown_task_type=deep" in captured


def test_vocabulary_is_ok(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["vocabulary"]) == OK
    captured = capsys.readouterr().out
    for name in ("tier flash", "tier pro", "tier auditor", "route fast", "route strict"):
        assert name in captured
    assert "squad_default: shell" in captured
    assert "sme_default: sniper-generic" in captured


def test_route_fast_path_is_ok(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["route", "--type", "doc_update", "--text", "tighten the README"]) == OK
    captured = capsys.readouterr().out
    assert "route=fast" in captured
    assert "tier=flash" in captured


def test_route_strict_path_is_ok(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["route", "--text", "rotate the production secret"]) == OK
    captured = capsys.readouterr().out
    assert "route=strict" in captured
    assert "tier=auditor" in captured
    assert "chain=planner,executor,verifier,critic" in captured


def test_route_unknown_type_still_routes_to_deep(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["route", "--type", "teleport", "--text", "x"]) == OK
    captured = capsys.readouterr().out
    assert "route=deep" in captured
    assert "fail_safe: yes" in captured


def test_route_json_shape(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["route", "--json", "--text", "neutral"]) == OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["route"] == "deep"
    assert payload["fail_safe"] is True
    assert payload["caps"]["tier"] == payload["tier"]


def test_sme_subcommand_is_ok(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["sme", "--text", "rotate the terraform state bucket"]) == OK
    captured = capsys.readouterr().out
    assert "domain=infra" in captured
    assert "sme=terraform" in captured
    assert "module=vendor/gcp-gatekeeper" in captured


def test_dispatch_within_caps_is_ok(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["dispatch", "--text", "neutral", "--tokens", "40"]) == OK
    assert "status=dispatched" in capsys.readouterr().out


def test_dispatch_escalation_is_ok(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(
        ["dispatch", "--type", "doc_update", "--text", "neutral", "--tokens", "9000"]
    ) == OK
    captured = capsys.readouterr().out
    assert "status=dispatched" in captured
    assert "tier=pro" in captured


# --- NOT-OK and the human terminal ------------------------------------------
def test_dispatch_cap_breach_without_escalation_is_not_ok(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(
        [
            "dispatch", "--type", "doc_update", "--text", "neutral",
            "--tokens", "9000", "--no-escalate",
        ]
    ) == NOT_OK
    captured = capsys.readouterr().out
    assert "status=refused" in captured
    assert "token cap breached" in captured


def test_dispatch_human_terminal_is_ok_because_the_engine_behaved(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main(
        ["dispatch", "--type", "doc_update", "--text", "neutral", "--tokens", "40000"]
    )
    captured = capsys.readouterr().out
    assert "status=human_advisor" in captured
    assert rc == OK


def test_a_violated_invariant_is_not_ok(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "route-policy.yaml":
            document["dispatch_defaults"]["unknown_task_type"] = "fast"
        return document

    variant = policy_variant(mutate)
    assert main(["--policies", str(variant), "validate"]) == NOT_OK


# --- CANNOT-ASSESS ----------------------------------------------------------
def test_malformed_policy_is_cannot_assess(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "capability-registry.yaml":
            document.pop("agents")
        return document

    variant = policy_variant(mutate)
    assert main(["--policies", str(variant), "validate"]) == CANNOT_ASSESS
    assert main(["--policies", str(variant), "route", "--text", "x"]) == CANNOT_ASSESS


def test_missing_policy_directory_is_cannot_assess(tmp_path) -> None:
    assert main(["--policies", str(tmp_path / "absent"), "validate"]) == CANNOT_ASSESS


def test_an_unusable_request_is_cannot_assess() -> None:
    assert main(["route", "--text", "x", "--complexity", "150"]) == CANNOT_ASSESS


def test_a_malformed_policy_never_reports_ok(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "tier-policy.yaml":
            document["tiers"]["flash"]["max_tokens"] = "big"
        return document

    variant = policy_variant(mutate)
    for argv in (["validate"], ["vocabulary"], ["demo"], ["sme", "--text", "x"]):
        assert main(["--policies", str(variant)] + argv) == CANNOT_ASSESS


# --- demo -------------------------------------------------------------------
def test_demo_is_ok(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["demo"]) == OK
    captured = capsys.readouterr().out
    for label in (
        "fast-path dispatch",
        "deep-path dispatch",
        "strict-path dispatch",
        "unknown task type -> deep fail-safe",
        "token-cap breach refused",
        "token-cap breach escalated",
        "human/advisor hand-off",
    ):
        assert label in captured
    assert "demo: OK" in captured


def test_demo_json_reports_success(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["demo", "--json"]) == OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert len(payload["cases"]) >= 7


def test_demo_goes_not_ok_when_a_demonstrated_path_changes(policy_variant) -> None:
    """The demo is not a printed claim: change a demonstrated chain and it fails."""
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "route-policy.yaml":
            # A schema-valid, invariant-valid change that breaks the fast-path
            # expectation (chain == ("executor",)).
            document["routes"]["fast"]["agents"] = ["executor", "verifier"]
        return document

    variant = policy_variant(mutate)
    assert main(["--policies", str(variant), "demo"]) == NOT_OK


# --- the real entry point ---------------------------------------------------
def test_entry_point_runs_from_another_cwd(module_dir, tmp_path) -> None:
    proc = subprocess.run(
        [sys.executable, str(module_dir / "cli.py"), "validate"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == OK, proc.stderr
    assert "validate: OK" in proc.stdout


def test_entry_point_reports_cannot_assess_for_a_bad_policy_dir(module_dir, tmp_path) -> None:
    proc = subprocess.run(
        [sys.executable, str(module_dir / "cli.py"), "--policies",
         str(tmp_path / "absent"), "validate"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == CANNOT_ASSESS
    assert "CANNOT-ASSESS" in proc.stderr
