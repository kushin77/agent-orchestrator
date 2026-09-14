"""Tri-state honesty: an unassessable input is never a pass (issue #151).

Every test here builds a view that *cannot* be trusted and asserts the result is
CANNOT-ASSESS — rc 2, never rc 0. The last test pins the precedence rule:
CANNOT-ASSESS dominates NOT-OK, because an input set that could not be read can
understate the findings of the part that could.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from conftest import ROLLUP_DIR, build_report, inventory, org, run_cli, sme, write_tree

TENANT = {"alpha": {"ceiling": 300.0, "repos": ["fx/one"]}}
CLEAN = [inventory("fx/one", "alpha", [sme()])]

CLI_BASE = ("project", "--inventory-dir", "inventory", "--schema", str(ROLLUP_DIR / "schema.yaml"))


def codes(report):
    return sorted(f.code for f in report.findings)


def test_a_clean_projection_is_ok_and_exits_zero(tmp_path):
    report = build_report(tmp_path, org(TENANT, ceiling=500.0), CLEAN)
    assert report.status == "ok"
    assert report.exit_code == 0
    assert run_cli(tmp_path, *CLI_BASE, "--org", "org.yaml").returncode == 0


def test_malformed_yaml_is_cannot_assess(tmp_path):
    org_doc = org(TENANT, ceiling=500.0)
    write_tree(tmp_path, org_doc, CLEAN)
    (tmp_path / "inventory" / "fx-one.yaml").write_text(
        "schema: ao.rollup/fleet-inventory-v1\nrepo: fx/one\nt enant: [\n", encoding="utf-8"
    )

    result = run_cli(tmp_path, *CLI_BASE, "--org", "org.yaml")
    assert result.returncode == 2
    output = result.stdout + result.stderr
    # The finding names the file and what is wrong with it, and is marked
    # cannot-assess — the lowercase severity the report uses for the tri-state.
    assert "[cannot-assess]" in output
    assert "not valid YAML" in output
    assert "fx-one.yaml" in output


def test_a_repo_without_an_inventory_is_cannot_assess_not_a_smaller_total(tmp_path):
    tenants = {"alpha": {"ceiling": 300.0, "repos": ["fx/one", "fx/absent"]}}
    report = build_report(tmp_path, org(tenants, ceiling=500.0), CLEAN)

    assert "INVENTORY_MISSING" in codes(report)
    named = [f.subject for f in report.findings if f.code == "INVENTORY_MISSING"]
    assert named == ["fx/absent"]
    assert report.enterprise.missing_repos == ("fx/absent",)
    assert report.enterprise.spend.assessed is False
    assert report.status == "cannot-assess"
    assert report.exit_code == 2
    assert run_cli(tmp_path, *CLI_BASE, "--org", "org.yaml").returncode == 2


def test_an_empty_fleet_is_cannot_assess(tmp_path):
    report = build_report(tmp_path, org(TENANT, ceiling=500.0), [inventory("fx/one", "alpha", [])])

    assert "FLEET_EMPTY" in codes(report)
    assert report.enterprise.closure.ratio is None
    assert report.status == "cannot-assess"
    assert report.exit_code == 2


def test_an_sme_with_no_declared_capacity_is_cannot_assess(tmp_path):
    report = build_report(
        tmp_path,
        org(TENANT, ceiling=500.0),
        [
            inventory(
                "fx/one",
                "alpha",
                [
                    sme(id="qa-sme", capacity_hours=0),
                    sme(id="iac-sme", capacity_hours=20.0, engaged_hours=10.0),
                ],
            )
        ],
    )

    assert codes(report).count("UTILIZATION_UNASSESSABLE") == 2  # SME scope + tenant scope
    assert report.enterprise.utilization.assessed is False
    # The ratio is still formed over the SMEs that CAN be assessed, and says so.
    assert report.enterprise.utilization.unassessable == ("fx/one#qa-sme",)
    assert report.enterprise.utilization.ratio == 0.5
    assert report.status == "cannot-assess"
    assert report.exit_code == 2


def test_a_repo_with_no_dispatched_work_has_no_closure_rate(tmp_path):
    report = build_report(
        tmp_path,
        org(TENANT, ceiling=500.0),
        [inventory("fx/one", "alpha", [sme(dispatched=0, closed=0)])],
    )

    assert "CLOSURE_UNASSESSABLE" in codes(report)
    assert report.enterprise.closure.ratio is None
    assert report.status == "cannot-assess"
    assert report.exit_code == 2


def test_a_repo_with_no_drift_checks_has_no_drift_rate(tmp_path):
    report = build_report(
        tmp_path,
        org(TENANT, ceiling=500.0),
        [inventory("fx/one", "alpha", [sme()], checks=0, findings=0)],
    )

    assert "DRIFT_UNASSESSABLE" in codes(report)
    assert report.enterprise.drift.ratio is None
    assert report.status == "cannot-assess"
    assert report.exit_code == 2


def test_a_schema_that_uses_an_unsupported_keyword_is_refused_not_ignored(tmp_path):
    """FAIL CLOSED: a keyword the validator does not implement must not pass."""
    write_tree(tmp_path, org(TENANT, ceiling=500.0), CLEAN)
    unsupported = tmp_path / "unsupported-schema.yaml"
    unsupported.write_text(
        "schema: http://json-schema.org/draft-07/schema#\n"
        "definitions:\n"
        "  org:\n"
        "    oneOf:\n"
        "      - type: object\n"
        "  inventory:\n"
        "    type: object\n",
        encoding="utf-8",
    )

    result = run_cli(
        tmp_path,
        "project",
        "--org",
        "org.yaml",
        "--inventory-dir",
        "inventory",
        "--schema",
        str(unsupported),
    )
    assert result.returncode == 2
    assert "oneOf" in result.stderr


def test_a_declaration_that_violates_the_schema_is_cannot_assess(tmp_path):
    bad = inventory("fx/one", "alpha", [sme(spend_usd="lots")])
    report = build_report(tmp_path, org(TENANT, ceiling=500.0), [bad])

    assert report.status == "cannot-assess"
    assert report.exit_code == 2
    problem = next(f for f in report.findings if f.code == "INPUT_INVENTORY")
    assert "spend_usd" in problem.message


def test_a_missing_org_or_an_empty_inventory_directory_is_cannot_assess(tmp_path):
    (tmp_path / "inventory").mkdir()
    missing_org = run_cli(tmp_path, *CLI_BASE, "--org", "org.yaml")
    assert missing_org.returncode == 2
    assert "CANNOT-ASSESS" in missing_org.stdout + missing_org.stderr

    (tmp_path / "org.yaml").write_text(
        yaml.safe_dump(org(TENANT, ceiling=500.0), sort_keys=False), encoding="utf-8"
    )
    no_inventory = run_cli(tmp_path, *CLI_BASE, "--org", "org.yaml")
    assert no_inventory.returncode == 2
    assert "no *.yaml inventory found" in no_inventory.stdout + no_inventory.stderr


def test_cannot_assess_dominates_not_ok(tmp_path):
    """Both a real violation and an unreadable input: the honest answer is 2."""
    tenants = {"alpha": {"ceiling": 300.0, "repos": ["fx/one", "fx/absent"]}}
    over = [inventory("fx/one", "alpha", [sme(weekly_spend_ceiling_usd=10.0, spend_usd=99.0)])]
    report = build_report(tmp_path, org(tenants, ceiling=500.0), over)

    assert "SPEND_OVER_CEILING" in codes(report)
    assert "INVENTORY_MISSING" in codes(report)
    # The violation is still reported in full, with its excess named.
    assert report.enterprise.spend.smes_over_ceiling == ("fx/one#qa-sme",)
    assert report.enterprise.smes[0].excess_usd == 89.0
    assert report.status == "cannot-assess"
    assert report.exit_code == 2


def test_every_cannot_assess_status_maps_to_a_non_zero_exit_code():
    from model import EXIT_CODES, STATUS_CANNOT_ASSESS, STATUS_NOT_OK, STATUS_OK

    assert EXIT_CODES == {STATUS_OK: 0, STATUS_NOT_OK: 1, STATUS_CANNOT_ASSESS: 2}
    assert EXIT_CODES[STATUS_CANNOT_ASSESS] != 0


def test_validate_reports_on_the_declarations_alone(tmp_path):
    write_tree(tmp_path, org(TENANT, ceiling=500.0), CLEAN)
    ok = run_cli(
        tmp_path,
        "validate",
        "--org",
        "org.yaml",
        "--inventory-dir",
        "inventory",
        "--schema",
        str(ROLLUP_DIR / "schema.yaml"),
    )
    assert ok.returncode == 0, ok.stdout + ok.stderr

    broken = tmp_path / "broken"
    write_tree(
        broken,
        org(TENANT, ceiling=500.0),
        [inventory("fx/one", "alpha", [sme(spend_usd="lots")])],
    )
    not_ok = run_cli(
        broken,
        "validate",
        "--org",
        "org.yaml",
        "--inventory-dir",
        "inventory",
        "--schema",
        str(ROLLUP_DIR / "schema.yaml"),
    )
    assert not_ok.returncode == 1
    assert "INVALID" in not_ok.stdout

    nowhere = run_cli(
        broken,
        "validate",
        "--org",
        "org.yaml",
        "--inventory-dir",
        "inventory",
        "--schema",
        str(Path(broken) / "no-such-schema.yaml"),
    )
    assert nowhere.returncode == 2
