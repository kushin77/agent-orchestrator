"""fleet/cron.py — AO_FLEET_PYTHON / AO_FLEET_CRON_PATH (issue #1370).

Measurement: `AO_RUNNER_HOST_ROLE=primary python3 fleet/cron.py install` wrote
crontab lines with `/usr/bin/python3` (Python 3.12) and no `PATH=`; the verify
the runner rung launches needs the 3.14 venv at `~/ao-verify-venv` (3.12's
`Path.glob("**")` skips files — #1245's knowledge-index red) and `gh`/`gcloud`
from `/snap/bin`. These tests pin: an explicit AO_FLEET_PYTHON/AO_FLEET_CRON_PATH
changes every rendered line and adds exactly one PATH= line; neither var set
leaves the default `/usr/bin/python3` and no PATH= line unchanged; and
`config_drift` reports in-sync vs drift-by-name.
"""

from __future__ import annotations

import cron


def test_default_interpreter_and_no_path_line_unchanged(monkeypatch):
    monkeypatch.delenv("AO_FLEET_PYTHON", raising=False)
    monkeypatch.delenv("AO_FLEET_CRON_PATH", raising=False)
    assert cron.interpreter() == "/usr/bin/python3"
    assert cron.path_line() is None
    merged = cron.install_lines([], interval=2)
    assert all("PATH=" not in entry for entry in merged)
    assert all(
        "/usr/bin/python3" in entry
        for entry in merged
        if cron._marker_of(entry) in (cron.MARKER, cron.PRUNE_MARKER, cron.RECONCILE_MARKER)
    )


def test_ao_fleet_python_changes_every_managed_line(monkeypatch):
    monkeypatch.setenv("AO_FLEET_PYTHON", "/x/venv/bin/python3")
    monkeypatch.delenv("AO_FLEET_CRON_PATH", raising=False)
    assert cron.interpreter() == "/x/venv/bin/python3"
    merged = cron.install_lines([], interval=2)
    python_lines = [
        entry
        for entry in merged
        if cron._marker_of(entry) in (cron.MARKER, cron.PRUNE_MARKER, cron.RECONCILE_MARKER)
    ]
    assert python_lines, "expected the watchdog/prune/reconcile lines to be installed"
    for entry in python_lines:
        assert "/x/venv/bin/python3" in entry
        assert "/usr/bin/python3" not in entry
    assert all("PATH=" not in entry for entry in merged)


def test_ao_fleet_cron_path_emits_exactly_one_path_line_at_top_of_block(monkeypatch):
    monkeypatch.delenv("AO_FLEET_PYTHON", raising=False)
    monkeypatch.setenv("AO_FLEET_CRON_PATH", "/x/venv/bin:/snap/bin:/usr/bin")
    merged = cron.install_lines([], interval=2)
    path_lines = [entry for entry in merged if cron._marker_of(entry) == cron.PATH_MARKER]
    assert len(path_lines) == 1
    assert path_lines[0] == "PATH=/x/venv/bin:/snap/bin:/usr/bin # ao-fleet-path"
    assert merged[0] == path_lines[0]


def test_both_vars_set_together(monkeypatch):
    monkeypatch.setenv("AO_FLEET_PYTHON", "/x/venv/bin/python3")
    monkeypatch.setenv("AO_FLEET_CRON_PATH", "/x/venv/bin:/snap/bin:/usr/bin")
    merged = cron.install_lines([], interval=2)
    path_lines = [entry for entry in merged if cron._marker_of(entry) == cron.PATH_MARKER]
    assert len(path_lines) == 1
    assert merged[0] == path_lines[0]
    python_lines = [
        entry
        for entry in merged
        if cron._marker_of(entry) in (cron.MARKER, cron.PRUNE_MARKER, cron.RECONCILE_MARKER)
    ]
    for entry in python_lines:
        assert "/x/venv/bin/python3" in entry


def test_path_line_removed_when_var_unset_again(monkeypatch):
    monkeypatch.setenv("AO_FLEET_CRON_PATH", "/x/venv/bin:/snap/bin:/usr/bin")
    installed = cron.install_lines([], interval=2)
    monkeypatch.delenv("AO_FLEET_CRON_PATH", raising=False)
    merged, report = cron.reconcile_lines(installed, cron._enabled_jobs_safe(), interval=2)
    assert all(cron._marker_of(entry) != cron.PATH_MARKER for entry in merged)
    assert any(cron._marker_of(entry) == cron.PATH_MARKER for entry in report["stale"])


def test_config_drift_reports_in_sync_when_nothing_set(monkeypatch):
    monkeypatch.delenv("AO_FLEET_PYTHON", raising=False)
    monkeypatch.delenv("AO_FLEET_CRON_PATH", raising=False)
    installed = cron.install_lines([], interval=2)
    assert cron.config_drift(installed) == []


def test_config_drift_reports_in_sync_when_env_matches_installed(monkeypatch):
    monkeypatch.setenv("AO_FLEET_PYTHON", "/x/venv/bin/python3")
    monkeypatch.setenv("AO_FLEET_CRON_PATH", "/x/venv/bin:/snap/bin:/usr/bin")
    installed = cron.install_lines([], interval=2)
    assert cron.config_drift(installed) == []


def test_config_drift_names_ao_fleet_python_when_interpreter_changes(monkeypatch):
    monkeypatch.delenv("AO_FLEET_CRON_PATH", raising=False)
    monkeypatch.setenv("AO_FLEET_PYTHON", "/x/venv/bin/python3")
    installed = cron.install_lines([], interval=2)
    monkeypatch.setenv("AO_FLEET_PYTHON", "/y/other/bin/python3")
    findings = cron.config_drift(installed)
    assert any("AO_FLEET_PYTHON" in finding for finding in findings)


def test_config_drift_names_ao_fleet_cron_path_when_path_changes(monkeypatch):
    monkeypatch.delenv("AO_FLEET_PYTHON", raising=False)
    monkeypatch.setenv("AO_FLEET_CRON_PATH", "/x/venv/bin:/snap/bin:/usr/bin")
    installed = cron.install_lines([], interval=2)
    monkeypatch.setenv("AO_FLEET_CRON_PATH", "/different/path")
    findings = cron.config_drift(installed)
    assert any("AO_FLEET_CRON_PATH" in finding for finding in findings)


def test_config_drift_names_ao_fleet_cron_path_when_var_removed(monkeypatch):
    monkeypatch.delenv("AO_FLEET_PYTHON", raising=False)
    monkeypatch.setenv("AO_FLEET_CRON_PATH", "/x/venv/bin:/snap/bin:/usr/bin")
    installed = cron.install_lines([], interval=2)
    monkeypatch.delenv("AO_FLEET_CRON_PATH", raising=False)
    findings = cron.config_drift(installed)
    assert any("AO_FLEET_CRON_PATH" in finding for finding in findings)
