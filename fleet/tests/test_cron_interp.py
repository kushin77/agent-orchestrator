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
    assert cron.path_lines() is None
    merged = cron.install_lines([], interval=2)
    assert all("PATH=" not in entry for entry in merged)
    assert cron.PATH_COMMENT not in merged
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
    # ONE `PATH=` line, and its VALUE is the declared value and nothing else.
    # This pin used to be the string `PATH=... # ao-fleet-path` — a trailing
    # comment on an environment-setting line, which cron reads as part of the
    # value (#1416). Pinning what was shipped made the defect the contract: the
    # last `:`-element became `/usr/bin # ao-fleet-path`, so `/usr/bin` left
    # cron's PATH and the runner rung died `failed to execute env`.
    assert merged[0] == cron.PATH_COMMENT
    assert cron._marker_of(merged[0]) == cron.PATH_MARKER
    assert merged[1] == "PATH=/x/venv/bin:/snap/bin:/usr/bin"
    assignments = [entry for entry in merged if entry.startswith("PATH=")]
    assert assignments == ["PATH=/x/venv/bin:/snap/bin:/usr/bin"]
    assert "#" not in assignments[0], "cron reads a trailing comment as part of the value"


def test_a_trailing_comment_on_the_assignment_is_never_rendered(monkeypatch):
    """The defect's shape must not come back: no managed PATH line may carry
    text after its value, and the marker must not be glued onto it."""
    monkeypatch.setenv("AO_FLEET_CRON_PATH", "/x/venv/bin:/snap/bin:/usr/bin")
    for entry in cron.install_lines([], interval=2) + cron.path_lines():
        if entry.startswith("PATH="):
            assert entry == f"PATH=/x/venv/bin:/snap/bin:/usr/bin"
            assert cron.PATH_MARKER not in entry


def test_an_install_refreshes_the_old_trailing_comment_form(monkeypatch):
    """A crontab installed by the pre-#1416 renderer carries the marker on the
    assignment. An install must REPLACE it, not coexist with it."""
    monkeypatch.delenv("AO_FLEET_PYTHON", raising=False)
    monkeypatch.setenv("AO_FLEET_CRON_PATH", "/x/venv/bin:/snap/bin:/usr/bin")
    legacy = "PATH=/x/venv/bin:/snap/bin:/usr/bin # ao-fleet-path"
    merged, report = cron.reconcile_lines(
        [legacy, "0 0 * * * /foreign/job >> /dev/null 2>&1"],
        cron._enabled_jobs_safe(),
        interval=2,
    )
    assert report["refreshed"] == [cron.PATH_MARKER]
    assert legacy not in merged
    assert merged.count(cron.PATH_COMMENT) == 1
    assert [entry for entry in merged if entry.startswith("PATH=")] == [
        "PATH=/x/venv/bin:/snap/bin:/usr/bin"
    ]
    assert "0 0 * * * /foreign/job >> /dev/null 2>&1" in merged


def test_both_vars_set_together(monkeypatch):
    monkeypatch.setenv("AO_FLEET_PYTHON", "/x/venv/bin/python3")
    monkeypatch.setenv("AO_FLEET_CRON_PATH", "/x/venv/bin:/snap/bin:/usr/bin")
    merged = cron.install_lines([], interval=2)
    assert merged[0] == cron.PATH_COMMENT
    assert merged[1] == "PATH=/x/venv/bin:/snap/bin:/usr/bin"
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
    # both halves of the block go: the marker AND the assignment beneath it
    assert all(not entry.startswith("PATH=") for entry in merged)
    assert any(cron._marker_of(entry) == cron.PATH_MARKER for entry in report["stale"])


def test_uninstall_removes_the_whole_path_block(monkeypatch):
    monkeypatch.setenv("AO_FLEET_CRON_PATH", "/x/venv/bin:/snap/bin:/usr/bin")
    installed = cron.install_lines(["0 0 * * * /foreign/job >> /dev/null 2>&1"], interval=2)
    kept, ours = cron.remove_lines(installed)
    assert kept == ["0 0 * * * /foreign/job >> /dev/null 2>&1"]
    assert any(entry.startswith("PATH=") for entry in ours)
    assert cron.PATH_COMMENT in ours


def test_status_shows_the_path_assignment_not_just_its_marker(monkeypatch):
    monkeypatch.setenv("AO_FLEET_CRON_PATH", "/x/venv/bin:/snap/bin:/usr/bin")
    installed = cron.install_lines([], interval=2)
    own = cron.installed_lines(installed)
    assert "PATH=/x/venv/bin:/snap/bin:/usr/bin" in own
    assert cron.PATH_COMMENT in own


def test_render_prints_what_install_writes(monkeypatch):
    """`render` is documented as what `install` would write. It omitted the
    PATH block entirely (#1416), so a render could look right — no PATH line —
    while install wrote one."""
    monkeypatch.setenv("AO_FLEET_CRON_PATH", "/x/venv/bin:/snap/bin:/usr/bin")
    monkeypatch.delenv("AO_FLEET_PYTHON", raising=False)
    rendered = cron.path_lines() or []
    assert rendered == [cron.PATH_COMMENT, "PATH=/x/venv/bin:/snap/bin:/usr/bin"]


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
