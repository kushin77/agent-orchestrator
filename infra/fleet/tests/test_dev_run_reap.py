"""infra/fleet/dev_run.py — the `reap` role's dry-run dispatch form (issue #830/#901).

D2's harness (#710) proves each scheduled job's dry-run form is genuinely
inert. `ao-fleet-reap` (#830, folding the #516 worktree reaper into the
schedule) is the fourth scheduled job, and this holds it to the same contract
the `prune`/`reconcile` roles already have: it is declared in `ROLES`, its
role table agrees with `fleet/cron.py`'s markers, it never carries `--apply`,
and it is dispatched through `bash` rather than the Python interpreter — the
bug this file would have caught, since `scripts/prune-worktrees.sh` is a shell
script and `dispatch()` used to hard-code `sys.executable` for every role.
"""

from __future__ import annotations

import sys
from pathlib import Path

# `dev_run.py` (and the `cron.py` its role table is checked against) are
# standalone scripts, not packages (repo convention: namespace modules), so
# their directories go on `sys.path` here rather than via a `conftest.py` —
# a same-named `conftest.py` in this directory and in `fleet/tests/` collide
# in `sys.modules` under plain (no-`__init__.py`) test collection, which is
# exactly the failure mode this repo's own `fleet/tests/conftest.py` docstring
# warns about for redirected runtime paths.
_INFRA_FLEET_DIR = Path(__file__).resolve().parent.parent
_FLEET_DIR = _INFRA_FLEET_DIR.parent.parent / "fleet"
for _dir in (_INFRA_FLEET_DIR, _FLEET_DIR):
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))

import cron
import dev_run


def test_reap_role_is_declared():
    names = {role.marker: role for role in dev_run.ROLES}
    assert "ao-fleet-reap" in names
    role = names["ao-fleet-reap"]
    assert role.name == "reap"
    assert role.argv == ("scripts/prune-worktrees.sh",)
    assert role.disposition == "dry-run"


def test_reap_role_uses_bash_not_the_python_interpreter():
    role = {r.marker: r for r in dev_run.ROLES}["ao-fleet-reap"]
    assert role.interpreter == ("bash",)
    assert role.interpreter != (sys.executable,)


def test_other_roles_still_default_to_the_python_interpreter():
    for role in dev_run.ROLES:
        if role.marker == "ao-fleet-reap":
            continue
        assert role.interpreter == (sys.executable,)


def test_dispatch_builds_bash_argv_for_reap(monkeypatch, tmp_path):
    role = {r.marker: r for r in dev_run.ROLES}["ao-fleet-reap"]
    seen = {}

    class FakeCompleted:
        returncode = 0
        stdout = "prune-worktrees: 0 stale, 0 kept\n"
        stderr = ""

    def fake_run(argv, cwd=None, env=None, capture_output=None, text=None, timeout=None, check=None):
        seen["argv"] = argv
        return FakeCompleted()

    monkeypatch.setattr(dev_run.subprocess, "run", fake_run)
    record = dev_run.dispatch(role, tmp_path, dict(), timeout=5)
    assert seen["argv"][0] == "bash"
    assert seen["argv"][1] == "scripts/prune-worktrees.sh"
    assert record["rc"] == 0
    assert record["apply"] is False


def test_role_table_matches_cron_markers_including_reap():
    markers = list(cron.MARKERS)
    assert "ao-fleet-reap" in markers
    findings = dev_run.check_role_table(markers)
    assert findings == []


def test_reap_role_never_carries_apply():
    findings = dev_run.check_no_apply()
    assert findings == []
    role = {r.marker: r for r in dev_run.ROLES}["ao-fleet-reap"]
    assert dev_run.FORBIDDEN_TOKEN not in role.argv


def test_load_markers_reads_from_cron_module(tmp_path):
    import pathlib

    repo = pathlib.Path(cron.__file__).resolve().parent.parent
    markers = dev_run.load_markers(repo)
    assert markers is not None
    assert "ao-fleet-reap" in markers
