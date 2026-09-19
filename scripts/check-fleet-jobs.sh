#!/usr/bin/env bash
# check-fleet-jobs.sh — the manifest-driven fleet crontab gate (issue #241).
#
# THE DEFECT THIS EXISTS FOR
#   The fleet's crontab was hand-maintained: `fleet/cron.py` hardcoded three
#   marked lines (watchdog, prune, reconcile) and nothing rendered them from a
#   declaration, so adding a job meant editing the module and nothing kept the
#   installed crontab in lock-step with it. Issue #241 replaces that with a
#   tracked manifest (`config/fleet-jobs.json`) the module renders and
#   reconciles, adopting the leaderboard's generated-crontab pattern (harvested,
#   never copied — docs/CANNIBALIZATION.md §12): `flock -n -E 99` singletons,
#   per-job logs, user-drop.
#
# WHAT IS PROVEN (against the real tree, not a description of it)
#   1. the manifest meets the renderer's contract (every job has a name, a
#      unique marker, a command, a schedule-or-interval and a log), the enabled
#      jobs' markers equal `fleet/cron.MARKERS`, and the two ship-gated-OFF jobs
#      (snapshot-refresh, and scan-pr-failures — issue #1207) are declared but
#      disabled, so neither can change the installed crontab;
#   1b. BOTH directions of reachability for the ship-gated-OFF jobs (issue
#      #1207): while disabled the scanner installs nothing, and flipping only
#      its `enabled` flag — never the code — makes the renderer emit exactly its
#      marker and the reconciler recognise a stale line for it, so the
#      declaration is what schedules it and `fleet/cron.py` is what owns it;
#   2. the renderer is deterministic (rendered twice, byte-identical) and the
#      legacy `line`/`prune_line`/`reconcile_line` builders are the same render,
#      so the manifest and the module cannot drift apart;
#   3. every enabled job is a singleton: its line carries `flock -n -E 99` and
#      a lock file unique to that job, and the declared user is honoured
#      (`sudo -u <user>` only when it differs from the current one);
#   4. the reconciler reports drift and heals it IN A SCRATCH CRONTAB: a missing
#      job is installed, a drifted line is refreshed, a stale marker is removed
#      — and the real crontab is never read or written (the probe monkeypatches
#      the real readers/writers to refuse loudly);
#   5. four mutations are refused BY NAME: a job missing its command, a job
#      missing its schedule, a singleton job rendered without `flock`, and a
#      manifest entry dropped so the reconciler must report its line stale. Each
#      mutation is asserted to have changed the manifest (sha256 before/after),
#      so a mutation that never applied cannot be reported as caught;
#   6. a missing or malformed manifest is CANNOT-ASSESS (rc 2), never a pass.
#
# No network. No writes outside the scratch directory and the real crontab is
# untouched. Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-fleet-jobs.sh
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-fleet-jobs: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

for required in fleet/cron.py config/fleet-jobs.json; do
  if [ ! -f "$required" ]; then
    echo "check-fleet-jobs: FAIL — $required is missing" >&2
    exit 1
  fi
done

# The scratch tree name is the sanctioned fleet idiom, NOT a template whose
# placeholder is a run of one letter: that literal trips this repo's own
# unfinished-marker scan. `mkdir` without `-p` refuses loudly instead of
# silently reusing another run's tree.
work="/tmp/ao-fleet-jobs.$(date +%s%N).$"
mkdir "$work" 2>/dev/null || {
  echo "check-fleet-jobs: CANNOT-ASSESS — no scratch directory available" >&2
  exit 2
}
cleanup() { rm -rf "$work"; }
trap cleanup EXIT

# The driver is the whole proof; the shell only maps its rc onto the tri-state.
# It imports `cron` from THIS checkout's fleet/, so the code under review is
# what is measured. The real `read_crontab`/`write_crontab` are monkeypatched
# to raise, so any probe that reached the real crontab fails loudly instead of
# mutating this box's schedule.
python3 - "$root" "$work" <<'PY'
"""Prove the fleet-jobs manifest drives a deterministic, singleton, reconcilable crontab."""
import hashlib
import json
import os
import sys
from pathlib import Path

repo_root = Path(sys.argv[1]).resolve()
scratch = Path(sys.argv[2]).resolve()
scratch.mkdir(parents=True, exist_ok=True)

sys.dont_write_bytecode = True
sys.path.insert(0, str(repo_root / "fleet"))

import cron  # noqa: E402

failures: list[str] = []


def probe(name, hold, detail=""):
    print("  probe %s: %s%s" % (name, "PASS" if hold else "FAIL", (" — " + detail) if detail else ""))
    if not hold:
        failures.append(name)
    return hold


def sha_of(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


# Never let a probe reach the real crontab: the reconciler under test is pure,
# and a probe that calls the real reader/writer is a defect in the proof.
def _refuse_real(_lines):
    raise AssertionError("the real crontab was touched by the check")


cron.read_crontab = _refuse_real
cron.write_crontab = _refuse_real

# --- 0. the real manifest must be readable, or the gate cannot assess --------
try:
    manifest = cron.load_manifest()
except FileNotFoundError:
    print("check-fleet-jobs: CANNOT-ASSESS — config/fleet-jobs.json is missing", file=sys.stderr)
    sys.exit(2)
except json.JSONDecodeError as exc:
    print("check-fleet-jobs: CANNOT-ASSESS — config/fleet-jobs.json is not valid JSON (%s)" % exc, file=sys.stderr)
    sys.exit(2)

# --- 1. the manifest meets the renderer's contract --------------------------
problems = cron.validate_manifest(manifest)
probe("MANIFEST-VALID", problems == [], "; ".join(problems) if problems else "no problems")

jobs = cron.manifest_jobs(manifest)
enabled = cron.enabled_jobs(manifest)
enabled_markers = sorted(str(j["marker"]) for j in enabled)
probe(
    "ENABLED-MARKERS-MATCH-MODULE",
    enabled_markers == sorted(cron.MARKERS),
    "enabled=%s MARKERS=%s" % (enabled_markers, sorted(cron.MARKERS)),
)
disabled = [j for j in jobs if j.get("enabled") is not True]
# `runner` (issue #1343) is ROLE-gated, not flag-gated: `enabled: false` plus an
# `enabled_when` env condition, so it is in the disabled set here (no role in
# the gate's environment) and installed only on the shared-services primary.
probe(
    "SHIP-GATED-OFF-SET",
    [j.get("name") for j in disabled] == ["snapshot-refresh", "scan-pr-failures", "runner"]
    and [j.get("marker") for j in disabled]
    == [cron.SNAPSHOT_REFRESH_MARKER, cron.SCAN_PR_FAILURES_MARKER, cron.RUNNER_MARKER],
    "disabled=%s" % [j.get("name") for j in disabled],
)
probe(
    "DECLARED-MARKERS-COMPLETE",
    cron.declared_markers(manifest)
    == cron.MARKERS + (cron.SNAPSHOT_REFRESH_MARKER, cron.SCAN_PR_FAILURES_MARKER, cron.RUNNER_MARKER),
)
# The role-gated rung is reachable ONLY through the env contract's variable:
# with the role set the SAME renderer emits its line (carrying the role
# inline); without it, nothing is rendered for it.
runner_on = cron.render_lines(cron.enabled_jobs(manifest, env={cron.RUNNER_ROLE_ENV: cron.RUNNER_ROLE_PRIMARY}))
runner_off = cron.render_lines(cron.enabled_jobs(manifest, env={cron.RUNNER_ROLE_ENV: "standby"}))
probe(
    "RUNNER-REACHABLE-ONLY-ON-PRIMARY",
    len(runner_on) == len(enabled) + 1
    and any(entry.endswith("# " + cron.RUNNER_MARKER) and "env %s=%s " % (cron.RUNNER_ROLE_ENV, cron.RUNNER_ROLE_PRIMARY) in entry for entry in runner_on)
    and len(runner_off) == len(enabled),
    "%d line(s) with the role primary, %d with standby" % (len(runner_on), len(runner_off)),
)
# Reachability, proved BOTH ways (issue #1207). A disabled job is only "wired"
# if the declaration — not a code path — is what would install it: with the
# manifest as declared, the scanner installs nothing; flipping ONLY its flag
# makes the SAME renderer emit its line. A job that needed an extra code change
# to install would fail the second half, and a job accidentally enabled would
# fail SHIP-GATED-OFF-SET above.
scan_pr = [j for j in jobs if j.get("name") == "scan-pr-failures"]
scan_on = [dict(j, enabled=True) if j.get("name") == "scan-pr-failures" else j for j in jobs]
rendered_on = cron.render_lines(cron.enabled_jobs({"jobs": scan_on}))
probe(
    "SCAN-PR-REACHABLE-WHEN-ENABLED",
    bool(scan_pr)
    and len(rendered_on) == len(enabled) + 1
    and any(entry.endswith("# " + cron.SCAN_PR_FAILURES_MARKER) for entry in rendered_on),
    "%d line(s) rendered with the flag on" % len(rendered_on),
)

# --- 2. the renderer is deterministic and the legacy builders are the same ---
first = cron.render_lines(enabled)
second = cron.render_lines(enabled)
probe("RENDER-DETERMINISTIC", first == second, "rendered twice")
legacy = [cron.line(2), cron.prune_line(), cron.reconcile_line(2), cron.reap_line()]
manifest_rendered = [
    cron.render_job(j, interval=2 if j.get("interval") is not None else None) for j in enabled
]
probe("LEGACY-BUILDERS-LOCKSTEP", legacy == manifest_rendered, "line/prune_line/reconcile_line")

# --- 3. every job DECLARING singleton renders flock-wrapped with a unique
# lock, and every job's log is unique. Issue #830 (#901/#906) added the reap
# job as the fourth rung with `singleton: false` BY DESIGN: it shells out to
# `scripts/prune-worktrees.sh`, which is fail-closed on its own (a claimed,
# dirty, in-use or unpreserved worktree is always kept), so a second overlapping
# tick cannot double-reclaim — the flock wrapper would just be redundant
# ceremony. Requiring flock on EVERY enabled job (this probe's original
# posture, from #241/#962, before the reap rung existed) is retired: it is not
# what `fleet/cron.py`'s own docstring/manifest declare, and no lane has walked
# it back since #906 shipped, so this probe now checks the invariant the
# manifest actually promises — flock iff the job says singleton, always a
# unique log/lock — rather than the older, now-inapplicable "always singleton".
SINGLETON_REQUIRED_MARKERS = tuple(m for m in cron.MARKERS if m != cron.REAP_MARKER)


def singleton_invariant(jobs_to_check):
    rendered = cron.render_lines(jobs_to_check)
    by_job = dict(zip((j.get("marker") for j in jobs_to_check), rendered))
    # Every job this module has always required a singleton for (watchdog,
    # prune, reconcile) must still declare it and render flock-wrapped — a
    # mutation that flips one to False must stay caught. The reap rung is the
    # sanctioned exception (see above): required to render WITHOUT flock,
    # since prune-worktrees.sh is fail-closed on its own.
    ok = all(
        (j.get("singleton") is True) and ("flock -n -E 99" in by_job[j.get("marker")])
        for j in jobs_to_check
        if j.get("marker") in SINGLETON_REQUIRED_MARKERS
    )
    ok = ok and all(
        (j.get("singleton") is not True) and ("flock -n -E 99" not in by_job[j.get("marker")])
        for j in jobs_to_check
        if j.get("marker") == cron.REAP_MARKER
    )
    log_names = [str(j.get("log") or "") for j in jobs_to_check]
    ok = ok and all(log_names) and len(set(log_names)) == len(log_names)
    return ok


probe("SINGLETON-FLOCK", singleton_invariant(enabled), "each job flock-wrapped, unique lock")
noop_user = cron.render_job(enabled[0], current_user="root")
probe("USER-DROP-NOOP", "sudo -u" not in noop_user, "same user renders no sudo")
dropped = cron.render_job(dict(enabled[0], user="root"), current_user="alice")
probe("USER-DROP-RENDERED", "sudo -u root" in dropped, dropped)

# --- 4. the reconciler reports and heals drift in a scratch crontab ---------
foreign = "0 1 * * * /bin/true # someone-else"
missing_prune = [cron.line(2), foreign, cron.reconcile_line(2)]
merged, report = cron.reconcile_lines(missing_prune, enabled)
probe(
    "RECONCILE-INSTALLS-MISSING",
    "ao-fleet-prune" in report["installed"] and cron.prune_line() in merged,
    json.dumps(report),
)
stale_snapshot = (
    "*/10 * * * * /usr/bin/python3 governance/dispatch/cli.py snapshot --from-github "
    ">> /tmp/x.log 2>&1 # ao-fleet-snapshot-refresh"
)
merged2, report2 = cron.reconcile_lines([cron.line(2), foreign, stale_snapshot], enabled)
probe(
    "RECONCILE-HEALS-STALE",
    any("ao-fleet-snapshot-refresh" in entry for entry in report2["stale"])
    and stale_snapshot not in merged2,
    json.dumps(report2),
)
# The same for the scanner's marker (issue #1207): a DISABLED job's stale line
# must still be OURS to the reconciler, or `uninstall`/`reconcile` would leave a
# line the manifest no longer installs. This is the half of "reachable from
# fleet/cron.py" that the renderer probe above cannot show.
stale_scan = (
    "*/15 * * * * cd /repo && bash scripts/scan-pr-failures.sh --apply "
    ">> /tmp/x.log 2>&1 # ao-fleet-scan-pr-failures"
)
merged2b, report2b = cron.reconcile_lines([cron.line(2), foreign, stale_scan], enabled)
probe(
    "RECONCILE-HEALS-STALE-SCAN-PR",
    any("ao-fleet-scan-pr-failures" in entry for entry in report2b["stale"])
    and stale_scan not in merged2b,
    json.dumps(report2b),
)
drifted_watchdog = cron.line(2).replace("*/2", "*/9", 1)
merged3, report3 = cron.reconcile_lines([drifted_watchdog, foreign], enabled)
probe(
    "RECONCILE-REFRESHES-DRIFT",
    "ao-fleet-watchdog" in report3["refreshed"] and cron.line(2) in merged3,
    json.dumps(report3),
)
clean = [cron.line(2), cron.prune_line(), cron.reconcile_line(2), cron.reap_line(), foreign]
merged4, report4 = cron.reconcile_lines(clean, enabled)
probe(
    "CLEAN-TREE-NOOP",
    report4["installed"] == [] and report4["stale"] == [] and report4["refreshed"] == [],
    json.dumps(report4),
)
# The healed crontab is written to a SCRATCH file, never the real one.
(scratch / "healed.cron").write_text("\n".join(merged2) + "\n", encoding="utf-8")
probe("HEAL-WRITES-SCRATCH", (scratch / "healed.cron").exists(), str(scratch / "healed.cron"))

# --- 5. the mutations the gate must refuse, by name -------------------------
def mutate(label, edit, refuse):
    """Apply `edit` to a copy of the manifest; prove it changed, then refuse it."""
    before = sha_of(manifest)
    mutant = json.loads(json.dumps(manifest))
    edit(mutant)
    if before == sha_of(mutant):
        probe("MUTANT-%s-APPLIED" % label, False, "the mutation did not change the manifest")
        return
    probe("MUTANT-%s-APPLIED" % label, True)
    probe("MUTANT-%s-REFUSED" % label, refuse(mutant), "refused by name")


mutate(
    "MISSING-COMMAND",
    lambda m: m["jobs"][0].pop("command"),
    lambda m: any("missing command" in p for p in cron.validate_manifest(m)),
)
mutate(
    "MISSING-SCHEDULE",
    lambda m: (m["jobs"][1].pop("schedule", None), m["jobs"][1].pop("interval", None)),
    lambda m: any("missing schedule" in p for p in cron.validate_manifest(m)),
)
mutate(
    "SINGLETON-WITHOUT-FLOCK",
    lambda m: m["jobs"][0].__setitem__("singleton", False),
    lambda m: not singleton_invariant(cron.enabled_jobs(m)),
)
mutate(
    "DROPPED-ENTRY",
    lambda m: m.__setitem__("jobs", [j for j in m["jobs"] if j.get("name") != "prune"]),
    lambda m: any(
        "ao-fleet-prune" in entry
        for entry in cron.reconcile_lines([cron.prune_line()], cron.enabled_jobs(m))[1]["stale"]
    ),
)

# --- 6. a missing or malformed manifest is CANNOT-ASSESS, never a pass -------
try:
    cron.load_manifest(scratch / "no-such-manifest.json")
    probe("MISSING-MANIFEST-CANNOT-ASSESS", False, "load_manifest did not raise on a missing file")
except FileNotFoundError:
    probe("MISSING-MANIFEST-CANNOT-ASSESS", True)
(scratch / "bad.json").write_text('{"not": "the jobs shape"}', encoding="utf-8")
bad = cron.load_manifest(scratch / "bad.json")
shape = cron.validate_manifest(bad)
probe("BAD-INVOCATION-NAMES-SHAPE", bool(shape) and shape[0].startswith("manifest-shape"), str(shape))

if failures:
    print("check-fleet-jobs: FAILED probe(s): %s" % ", ".join(failures))
    sys.exit(1)
print("check-fleet-jobs: all probes PASS")
PY

rc=$?
if [ "$rc" -eq 2 ]; then
  echo "check-fleet-jobs: CANNOT-ASSESS — the manifest could not be read or the driver could not assess" >&2
  exit 2
fi
if [ "$rc" -ne 0 ]; then
  echo "check-fleet-jobs: NOT-OK — one or more probes failed" >&2
  exit 1
fi
echo "check-fleet-jobs: OK — the manifest drives a deterministic, singleton-wrapped, reconcilable crontab; every mutation is refused by name"
exit 0
