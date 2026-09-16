"""fleet/cron.py — the manifest-driven crontab generator + reconciler (issue #241).

These tests pin the pure render/reconcile core offline: the crontab lines are
rendered from a declarative job catalog, each job is a `flock -n -E 99`
singleton with its own log and an optional user drop, and drift is healed by a
reconciler that installs missing lines, refreshes drifted ones and removes
stale markers. Nothing here touches the real crontab or the live `.fleet/`:
the reconciler under test is a pure function over strings, and the renderer
writes no file.
"""

from __future__ import annotations

import cron

ROOT = "/repo"
USER = "me"


def watch_job(**overrides) -> dict:
    job = {
        "name": "watchdog",
        "marker": "ao-fleet-watchdog",
        "interval": 2,
        "command": "/usr/bin/python3 fleet/watchdog.py run",
        "user": "",
        "log": "watchdog.log",
        "singleton": True,
        "enabled": True,
    }
    job.update(overrides)
    return job


def prune_job(**overrides) -> dict:
    job = {
        "name": "prune",
        "marker": "ao-fleet-prune",
        "schedule": "23 4 * * *",
        "command": "/usr/bin/python3 fleet/prune.py run --apply",
        "user": "",
        "log": "prune.log",
        "singleton": True,
        "enabled": True,
    }
    job.update(overrides)
    return job


def reconcile_job(**overrides) -> dict:
    job = {
        "name": "reconcile",
        "marker": "ao-fleet-reconcile",
        "interval": 2,
        "command": "/usr/bin/python3 governance/reconcile/cli.py watch --once --apply",
        "user": "",
        "log": "reconcile.log",
        "singleton": True,
        "enabled": True,
    }
    job.update(overrides)
    return job


JOBS = [watch_job(), prune_job(), reconcile_job()]


def rendered(jobs: list[dict]) -> list[str]:
    """The multi-job crontab for `jobs`, pinned to a fixed root and user."""
    return cron.render_lines(jobs, root=ROOT, current_user=USER)


def reconcile(input_lines: list[str], jobs: list[dict] | None = None):
    return cron.reconcile_lines(input_lines, jobs or JOBS, root=ROOT, current_user=USER)


# --- the renderer ------------------------------------------------------------


def test_render_job_is_a_singleton_with_its_own_log_and_marker():
    line = cron.render_job(watch_job(), root=ROOT, current_user=USER)
    assert line.startswith("*/2 * * * * cd /repo &&")
    assert "/usr/bin/flock -n -E 99" in line
    assert "watchdog.lock" in line
    assert "/usr/bin/python3 fleet/watchdog.py run" in line
    assert "watchdog.log" in line
    assert line.endswith("# ao-fleet-watchdog")


def test_render_job_interval_override():
    assert cron.render_job(watch_job(), interval=5).startswith("*/5 * * * *")


def test_render_job_schedule_job_ignores_interval():
    assert cron.render_job(prune_job(), interval=9).startswith("23 4 * * *")


def test_user_drop_is_rendered_only_when_the_user_differs():
    same = cron.render_job(watch_job(user="root"), current_user="root")
    assert "sudo" not in same
    other = cron.render_job(watch_job(user="root"), current_user="alice")
    assert "sudo -u root" in other
    none = cron.render_job(watch_job(user=""), current_user="alice")
    assert "sudo" not in none


def test_each_job_gets_a_distinct_lock_file():
    lines = rendered(JOBS)
    locks = [token for token in " ".join(lines).split() if token.endswith(".lock")]
    assert len(locks) == len(JOBS)
    assert len(set(locks)) == len(JOBS)


# --- the manifest ------------------------------------------------------------


def test_the_real_manifest_validates_and_gates_the_snapshot_job_off():
    manifest = cron.load_manifest()
    assert cron.validate_manifest(manifest) == []
    jobs = cron.manifest_jobs(manifest)
    enabled = cron.enabled_jobs(manifest)
    assert [job["name"] for job in enabled] == ["watchdog", "prune", "reconcile"]
    disabled = [job for job in jobs if job not in enabled]
    assert [job["name"] for job in disabled] == ["snapshot-refresh"]
    assert [job["marker"] for job in disabled] == [cron.SNAPSHOT_REFRESH_MARKER]
    assert sorted(job["marker"] for job in enabled) == sorted(cron.MARKERS)


def test_validate_manifest_refuses_missing_fields_by_name():
    missing_command = [watch_job(command="")]
    assert any("missing command" in p for p in cron.validate_manifest({"jobs": missing_command}))
    missing_schedule = [watch_job(interval=None, schedule="")]
    assert any("missing schedule" in p for p in cron.validate_manifest({"jobs": missing_schedule}))
    assert cron.validate_manifest({"jobs": "nope"})[0].startswith("manifest-shape")


# --- the reconciler ----------------------------------------------------------


def test_reconcile_installs_missing_and_keeps_foreign():
    foreign = "0 1 * * * /bin/true # other"
    merged, report = reconcile([rendered([watch_job()])[0], foreign])
    assert "ao-fleet-prune" in report["installed"]
    assert "ao-fleet-reconcile" in report["installed"]
    assert foreign in merged
    assert merged[-1] == rendered([reconcile_job()])[0]


def test_reconcile_removes_a_stale_marker():
    stale = (
        "*/10 * * * * /usr/bin/python3 governance/dispatch/cli.py snapshot --from-github "
        ">> x.log 2>&1 # ao-fleet-snapshot-refresh"
    )
    merged, report = reconcile([rendered([watch_job()])[0], stale])
    assert any("ao-fleet-snapshot-refresh" in entry for entry in report["stale"])
    assert stale not in merged


def test_reconcile_refreshes_a_drifted_line():
    drifted = rendered([watch_job(interval=9)])[0]
    merged, report = reconcile([drifted])
    assert "ao-fleet-watchdog" in report["refreshed"]
    assert rendered([watch_job()])[0] in merged


def test_reconcile_is_a_noop_on_a_clean_crontab():
    clean = rendered(JOBS)
    merged, report = reconcile(clean)
    assert report["installed"] == [] and report["stale"] == [] and report["refreshed"] == []
    assert merged == clean


def test_install_lines_and_remove_lines():
    merged = cron.install_lines([], 2)
    assert len([entry for entry in merged if cron._is_ours(entry)]) == 3
    kept, ours = cron.remove_lines(merged)
    assert kept == []
    assert len(ours) == 3


def test_render_is_deterministic():
    assert rendered(JOBS) == rendered(JOBS)
