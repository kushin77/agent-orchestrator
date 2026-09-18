"""Every declared check has a demonstrated failure path (#147).

A layer gate that has never been observed failing is not known to work. This
file provokes one violation per check and requires the check to report FAIL —
and, where the layer's severity says so, requires the run to keep its declared
BLOCKING/WARNING behaviour while reporting it.
"""

from __future__ import annotations

import shutil

import pytest

import importlib.util as _importlib_util  # noqa: E402
from pathlib import Path as _ConftestPath  # noqa: E402

# A bare ``from conftest import ...`` is not safe here: when this suite is
# collected alongside other governance suites, every one of their
# ``tests/conftest.py`` files lands under the same bare module identity
# ``conftest`` in ``sys.modules``, so whichever conftest is imported LAST
# silently wins the name for the rest of collection (issues #699, #702, #1042).
# Loading this file's own conftest by absolute path guarantees this module
# always gets ITS directory's conftest regardless of collection order.
_conftest_spec = _importlib_util.spec_from_file_location(
    "governance_cto_overlay_tests_conftest", _ConftestPath(__file__).with_name("conftest.py")
)
_conftest = _importlib_util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
engine = _conftest.engine
state_of = _conftest.state_of
summary_of = _conftest.summary_of

# Assembled at run time for the same reason as in test_signals.py: the
# repository's own secret gate must not fire on this source.
PRIVATE_KEY_MATERIAL = "-----BEGIN RSA " + "PRIVATE KEY-----\n"
BROKEN_SCRIPT = "#!/usr/bin/env bash\nif [ 1 -eq 1 ]; then\n"
BROKEN_COMPOSE = "services:\n  web: [\n"
UNFORMATTED_TERRAFORM = 'variable "probe" {\n  type=string\n}\n'
DOCKERFILE_WITHOUT_BASE = "RUN echo hi\n"


def test_executive_adr_check_fails_without_records(make_repo, assess):
    root = make_repo("roster-adr", drop=["docs/decision-records/ADR-0001-fixture.md"])
    report = assess(root)
    assert state_of(report, "executive", "adr-check") == engine.STATE_FAIL
    assert summary_of(report, "executive").status == "BLOCKING-FAIL"


def test_executive_dep_policy_fails_on_a_tracked_vendor_tree(make_repo, assess):
    root = make_repo(
        "roster-deps",
        extra_files={"node_modules/left-pad/index.js": "module.exports = 1;\n"},
    )
    report = assess(root)
    assert state_of(report, "executive", "dep-policy") == engine.STATE_FAIL
    assert report.exit_code == engine.EXIT_NOT_OK


def test_executive_security_baseline_fails_on_private_key_material(make_repo, assess):
    root = make_repo("roster-security", extra_files={"keys/service.pem": PRIVATE_KEY_MATERIAL})
    report = assess(root, tier="critical")
    assert state_of(report, "executive", "security-baseline") == engine.STATE_FAIL


def test_engineering_syntax_fails_on_an_unparsable_script(make_repo, assess):
    root = make_repo("roster-syntax", extra_files={"scripts/oops.sh": BROKEN_SCRIPT})
    report = assess(root)
    assert state_of(report, "engineering", "syntax") == engine.STATE_FAIL
    assert report.exit_code == engine.EXIT_NOT_OK


def test_engineering_coverage_fails_on_a_declared_suite_without_tests(make_repo, assess):
    root = make_repo("roster-coverage", drop=["sample/tests/test_sample.py"])
    report = assess(root)
    assert state_of(report, "engineering", "coverage") == engine.STATE_FAIL


def test_engineering_sast_is_indeterminate_when_the_tool_is_absent(make_repo, assess):
    if shutil.which("semgrep"):
        pytest.skip("semgrep is installed here, so the absence branch cannot be exercised")
    root = make_repo("roster-sast")
    report = assess(root, tier="critical")
    assert state_of(report, "engineering", "sast") == engine.STATE_INDET
    assert any("engineering" in reason for reason in report.cannot_assess)


def test_devops_dockerfile_lint_fails_without_a_base_image(make_repo, assess):
    root = make_repo(
        "roster-docker", extra_files={"svc/Dockerfile": DOCKERFILE_WITHOUT_BASE}
    )
    report = assess(root)
    assert state_of(report, "devops", "dockerfile-lint") == engine.STATE_FAIL
    assert summary_of(report, "devops").status == "WARNING"
    assert report.exit_code == engine.EXIT_OK


def test_devops_compose_validate_fails_on_broken_yaml(make_repo, assess):
    root = make_repo(
        "roster-compose", extra_files={"deploy/docker-compose.yml": BROKEN_COMPOSE}
    )
    report = assess(root)
    assert state_of(report, "devops", "compose-validate") == engine.STATE_FAIL


def test_devops_terraform_fmt_fails_on_unformatted_iac(make_repo, assess):
    if not shutil.which("terraform"):
        pytest.skip("terraform is not installed here, so the formatter cannot run")
    root = make_repo(
        "roster-terraform",
        extra_files={"infra/terraform/probe.tf": UNFORMATTED_TERRAFORM},
    )
    report = assess(root)
    assert state_of(report, "devops", "terraform-fmt") == engine.STATE_FAIL


def test_support_log_harvest_fails_without_the_telemetry_surface(make_repo, assess):
    root = make_repo(
        "roster-telemetry", drop=["telemetry/README.md", "telemetry/collect.py"]
    )
    report = assess(root)
    assert state_of(report, "support", "log-harvest") == engine.STATE_FAIL


def test_support_self_heal_fails_without_the_worker(make_repo, assess):
    root = make_repo("roster-reconcile", drop=["governance/reconcile/cli.py"])
    report = assess(root)
    assert state_of(report, "support", "self-heal") == engine.STATE_FAIL
    assert summary_of(report, "support").status == "WARNING"


def test_support_incident_response_fails_without_the_runbook(make_repo, assess):
    root = make_repo("roster-incident")
    report = assess(root, tier="critical")
    assert state_of(report, "support", "incident-response") == engine.STATE_FAIL
