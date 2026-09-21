#!/usr/bin/env bash
# ============================================================================
# scripts/check-watchdog-bounded.sh
#
# Owner-lane: fleet / watchdog
# Class: elite
# Connects-to: consumes=fleet/watchdog.py,fleet/channel.py; gates=AO-GR-21,AO-GR-25
# ============================================================================
#
# AO-GR-21 applied to the WATCHDOG itself (issue #773). The defect it exists for
# was measured on the live fleet 2026-09-15 ~01:00:
#
#   [watchdog] brain: drifted (running b95a8b7, origin/master 37f87f9) — respawned
#   [watchdog] brain: drifted (running b95a8b7, origin/master 37f87f9) — respawned
#
# 132 respawn decisions, 45 `stopping cleanly` cycles, a brain process never older
# than 60s, and NO WORK DONE. `#739` (AO-GR-25) had made the watchdog compare the
# running commit against `origin/master` and respawn on a mismatch. The detection
# was right. The REMEDY was wrong: a respawn re-executes the SAME LOCAL CHECKOUT,
# so when the drift was that the checkout itself was behind, the watchdog took an
# action that could not change the value it compared — and repeated it forever.
#
# What this proves, against the real module (never a re-implementation):
#   1. `running == local HEAD != origin/master` is its own case, `checkout-behind`
#      — not `drifted` — so the remedy can differ. (a)
#   2. Its remedy is a FAST-FORWARD of the checkout, proven against a REAL git
#      repository: a checkout strictly behind its remote moves, and a diverged one
#      is refused BY NAME. A respawn is NOT the remedy. (a)
#   3. A remedy that does not change the observation is attempted at most `cap`
#      times, then escalated EXACTLY ONCE (naming both commits and the checkout)
#      and PARKED. The count is a MEASUREMENT of `respawn`/`fast_forward` calls,
#      not a claim. (b)
#   4. A genuinely drifted rung (rung commit != local HEAD) is still respawned,
#      and a drifted-but-busy rung is recorded PENDING and acted on when the run
#      completes instead of being dropped every tick. (c) (d)
#   5. A typo in the attempt-cap knob REFUSES the pass (exit 2) instead of
#      silently disarming the bound. (e)
#   6. MUTATION PROOF, and it is provoked rather than asserted. Two mutants are
#      built from the REAL source in a scratch tree, each mutation is proved to
#      have LANDED (sha256 before != after), and the SAME probe must DIVERGE:
#        * M1 removes the attempt cap  -> the bounded probe must run away
#          (respawn calls > cap, zero escalations): the runaway itself.
#        * M2 removes the local-HEAD distinction -> the `checkout-behind` case
#          must stop fast-forwarding and start respawning the same checkout,
#          which is the measured defect in one line.
#      A happy-path-only assertion cannot pass this: each mutant has a probe whose
#      value MUST change, and the check names which value changed.
#   7. "A run in flight" is the marker's OWN evidence, not a live loop pid (#366,
#      landed as #793), and it is driven through the REAL `watchdog.run_in_flight`
#      against REAL marker files. Every probe above STUBS `run_in_flight`, which is
#      exactly why the pid-only discriminator was invisible here while it held the
#      live sister's drift lock open: measured 2026-09-14, three markers ~4.5h old
#      with `child_pid: null` named the live loop's pid, so the sister was never
#      respawned and ran a commit predating five merged fixes.
#   8. The hold itself is BOUNDED (#366, half (b)). A rung whose runs die before
#      they can report presents flight on every tick, so `run_in_flight` answers
#      True on every tick and the hold is re-taken forever. The probes drive the
#      real `crash_loop_verdict` and the real remedy ledger and measure: N holds
#      inside the window with no progress ⇒ the respawn is NOT withheld; holds
#      wider than the window, or an observation that MOVES, never spend the budget;
#      and the escape is the ordinary remedy path, so it gets the cap, ONE
#      escalation and the park — never a second unbounded route to respawn.
#   9. Every case that decides a marker is PROVOKED separately: a live child
#      (held), a leftover (crashed), a run that has just started and has no child
#      yet (held), a dead child, a missing or unparseable beat (crashed, and
#      reported), an unreadable marker (crashed, and named) — plus mutant M4, which
#      restores the pid-only rule and must make the remedy stop acting.
#  10. The clock the marker arms are judged on is PINNED, not sampled (#1572). A
#      beat stamp is written at second resolution (`strftime` truncates
#      microseconds), so an age measured against the LIVE clock renders the intended
#      integer only while the read lands inside the write's own wall-clock second —
#      a sub-second window that made this check's verdict partly a coin flip on a
#      byte-identical tree (measured 5 OK / 1 FAIL across 6 venue builds). The flight
#      probe takes ONE whole-second anchor, builds every marker beat from it, and
#      hands that same anchor to the reader through its `now=` seam; the F2 arm
#      therefore asserts a BOUND on a PARSED integer, never a literal, and prints the
#      rendered evidence so a failure is diagnosable from the log alone.
#
# Tri-state contract (docs/QA-GATE.md):
#   0 OK             — every case correct and every mutant caught
#   1 NOT-OK         — a case misclassified, a bound not enforced, a mutant survived
#   2 CANNOT-ASSESS  — python3/git missing, or the tree cannot be imported
#
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
ROOT="$(find_repo_root)" || exit 2

fail=0
note() { printf '  %-6s %s\n' "$1" "$2"; }
bad() { note "FAIL" "$1"; fail=1; }
ok() { note "OK" "$1"; }
info() { printf '  %s\n' "$1"; }

if [ ! -f "$ROOT/fleet/watchdog.py" ] || [ ! -f "$ROOT/fleet/channel.py" ]; then
  echo "check-watchdog-bounded: CANNOT-ASSESS — fleet/watchdog.py or fleet/channel.py is unreadable" >&2
  exit 2
fi
for tool in python3 git; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "check-watchdog-bounded: CANNOT-ASSESS — $tool is not on PATH" >&2
    exit 2
  fi
done

# Unique scratch root: $TMPDIR is a shared, periodically-cleaned cache on this box,
# and a copy that vanishes mid-run reports a failure that is not the code's. The
# template is built from the pid and the clock (never a literal run of placeholder
# characters, which the docs gate reads as an unfinished marker).
work="/tmp/ao-watchdog-bounded.$(date +%s).$$"
mkdir -p "$work" || exit 2
trap 'rm -rf "$work"' EXIT

# --- the probe ----------------------------------------------------------------
#
# One driver, run against the real tree and against each mutant. It drives
# `watchdog.rung_action` and `channel.classify_drift` — the functions that carried
# the defect — with every side effect measured instead of performed, so the
# numbers it prints are counts of real calls into the real code.
cat > "$work/driver.py" <<'DRIVER'
"""Drive the watchdog's bounded remedy; print MEASUREMENTS, never claims."""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

mode = sys.argv[1]
root = Path(sys.argv[2]).resolve()
# BOTH roots of the tree under test go on the path, in this order: the fleet
# directory first for `channel`/`watchdog`, then the tree root for the `governance`
# packages the watchdog imports (`governance.spawn.liveness` carries half of #366).
# Without the second entry the mutant M4 below would be vacuous — the mutant tree's
# `fleet/watchdog.py` would import the REAL `governance.spawn.liveness` and the
# control would pass while proving nothing.
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "fleet"))

# A stale __pycache__ can shadow the tree under test and make a mutant invisible
# (measured: two different mutations once produced byte-identical gate output, see
# fleet/memory notes). Refuse to write bytecode and prove where the import landed.
sys.dont_write_bytecode = True

import channel  # noqa: E402
import watchdog  # noqa: E402

for module in (channel, watchdog, watchdog.spawn_liveness):
    if not Path(module.__file__).resolve().is_relative_to(root):
        print(f"IMPORT-ESCAPED {module.__file__}", file=sys.stderr)
        sys.exit(9)

LOCAL = "local111"
REMOTE = "remote99"
OTHER = "other22"
GONE = "gone000"


def beat(commit):
    return {"pid": 111, "state": "idle", "commit": commit}


def wire(commit, *, inflight=False, ff=None):
    """Patch the process world, and COUNT what the remedy actually does."""
    counts = {"respawn": [], "ff": []}
    watchdog.loop_pid = lambda pattern: 111
    watchdog.read_beat = lambda path: beat(commit)
    watchdog.run_in_flight = lambda: inflight
    watchdog.channel.heartbeat_age_seconds = lambda b, moment=None: 10.0
    watchdog.channel.capability_line = lambda finding: f"{finding.rung}: {finding.case}"
    watchdog.respawn = lambda pattern, script, name="": counts["respawn"].append(name) or True

    def _ff(root_=None, *, remote="origin/master"):
        counts["ff"].append(remote)
        if ff is not None:
            return ff
        return False, LOCAL, "git merge --ff-only refused (not possible to fast-forward)"

    watchdog.fast_forward_checkout = _ff
    return counts


def act(commit, when, *, inflight=False, ff=None):
    counts = wire(commit, inflight=inflight, ff=ff)
    line = watchdog.rung_action(
        "sister",
        "fleet/terminal.py",
        "fleet/terminal.sh",
        Path("/tmp/x"),
        False,
        REMOTE,
        local_head=LOCAL,
        when=when,
    )
    return line, counts


def artifacts():
    directory = watchdog.escalation_dir()
    return sorted(p.name for p in directory.glob("*.json")) if directory.exists() else []


def reset(rung):
    """Start a scenario from a clean ledger, so each count belongs to one scenario."""
    watchdog.drift_record_path(rung).unlink(missing_ok=True)
    directory = watchdog.escalation_dir()
    if directory.exists():
        for path in directory.glob("*.json"):
            path.unlink()


def recorded_attempts(rung):
    return int((watchdog.load_drift_record(rung) or {}).get("attempts") or 0)


if mode == "drift":
    print(f"cap={watchdog.respawn_attempt_cap()}")
    print(f"backoff1={int(watchdog.respawn_backoff_seconds(1))}")
    print(f"backoff3={int(watchdog.respawn_backoff_seconds(3))}")

    # Patch the process world BEFORE the classification probes: `decide` reads the
    # beat age through `channel.heartbeat_age_seconds`, so an unpinned beat would
    # read `stale` and the classification would be measured through the wrong door.
    wire(LOCAL)

    # --- the classification, through the real classifier ---------------------
    print(f"case_checkout_behind={watchdog.decide(111, beat(LOCAL), REMOTE, local_head=LOCAL)[0]}")
    print(f"case_drifted={watchdog.decide(111, beat(OTHER), REMOTE, local_head=LOCAL)[0]}")
    print(f"case_healthy={watchdog.decide(111, beat(REMOTE), REMOTE, local_head=LOCAL)[0]}")
    # The #739 detection must NOT have been weakened: without a local HEAD the
    # same commits are still drift, and an unreadable baseline is still fail-closed.
    print(f"case_no_local_head={watchdog.decide(111, beat(LOCAL), REMOTE)[0]}")
    print(f"case_unreadable_baseline={watchdog.decide(111, beat(LOCAL), 'unknown', local_head=LOCAL)[0]}")

    # --- (a) checkout-behind: the remedy is the CHECKOUT, and it is bounded ---
    reset("sister")
    lines = []
    a_ff = 0
    a_respawn = 0
    for step in range(6):
        line, counts = act(LOCAL, step * 1000)
        lines.append(line)
        a_ff += len(counts["ff"])
        a_respawn += len(counts["respawn"])
    print(f"A_ff_calls={a_ff}")
    print(f"A_respawn_calls={a_respawn}")
    print(f"A_escalations={sum('ESCALATED ONCE' in line for line in lines)}")
    print(f"A_parked_lines={sum('PARKED' in line for line in lines)}")
    print(f"A_unresolved_lines={sum('DRIFT UNRESOLVED' in line for line in lines)}")
    print(f"A_artifacts={len(artifacts())}")
    escalation = next((line for line in lines if "ESCALATED ONCE" in line), "")
    print("A_names_both=" + ("yes" if (LOCAL in escalation and REMOTE in escalation) else "no"))
    print("A_names_checkout=" + ("yes" if watchdog.ROOT.as_posix() in escalation else "no"))

    # --- (b) genuine drift whose respawn never changes the commit -------------
    reset("sister")
    b_lines = []
    b_respawn = 0
    for step in range(6):
        line, counts = act(OTHER, step * 1000)
        b_lines.append(line)
        b_respawn += len(counts["respawn"])
    print(f"B_respawn_calls={b_respawn}")
    print(f"B_escalations={sum('ESCALATED ONCE' in line for line in b_lines)}")
    print(f"B_parked_lines={sum('PARKED' in line for line in b_lines)}")
    print(f"B_artifacts={len(artifacts())}")
    print(f"B_first_line_respawned={'yes' if 'respawned' in b_lines[0] else 'no'}")

    # --- (b2) a remedy that WORKS must reset the counter ----------------------
    # A rung whose commit changes under it is making progress; the bound must not
    # fire on that, or a fleet that is healing would be parked.
    reset("brain")
    progress = []
    escalating = []
    for step, commit in enumerate(["old1", "old1", "old2", "old2", "old3"]):
        counts = wire(commit)
        line = watchdog.rung_action(
            "brain",
            "fleet/brain.py",
            "fleet/brain.sh",
            Path("/tmp/x"),
            False,
            REMOTE,
            local_head=LOCAL,
            when=step * 1000,
        )
        progress.append(recorded_attempts("brain"))
        escalating.append("ESCALATED ONCE" in line)
    print(f"C1_attempts={progress}")
    print(f"C1_escalations={sum(escalating)}")

    # --- (c) a busy rung is recorded pending, then acted on at completion -----
    reset("sister")
    busy_line, busy = act(OTHER, 0, inflight=True)
    record = watchdog.load_drift_record("sister") or {}
    print(f"D_busy_respawn_calls={len(busy['respawn'])}")
    print(f"D_pending_phase={record.get('phase')}")
    print(f"D_pending_running={record.get('running')}")
    idle_line, idle = act(OTHER, 5, inflight=False)
    print(f"D_completion_respawn_calls={len(idle['respawn'])}")
    print(f"D_left_alone={'yes' if 'left alone' in busy_line else 'no'}")

    # --- (d) a typo must not disarm the bound (exit 2, nothing acted on) ------
    os.environ[watchdog.ENV_RESPAWN_ATTEMPTS] = "zero"
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        rc = watchdog.watchdog_once()
    print(f"E_config_rc={rc}")
    print("E_config_names_var=" + ("yes" if watchdog.ENV_RESPAWN_ATTEMPTS in stderr.getvalue() else "no"))

    # --- (f) the crash-loop escape (#366) -------------------------------------
    # `run_in_flight` is pinned True — the ONE stubbed thing here, and it is stubbed
    # in the direction that WOULD hold. Everything the verdict rests on is the real
    # module: the real `crash_loop_verdict`, the real remedy ledger on disk, the real
    # constants.
    del os.environ[watchdog.ENV_RESPAWN_ATTEMPTS]
    reset("sister")
    f_lines = []
    f_respawns = []
    for step in range(watchdog.CRASH_LOOP_RESPAWNS + 1):
        line, counts = act(OTHER, step * 10, inflight=True)
        f_lines.append(line)
        f_respawns.append(len(counts["respawn"]))
    print(f"F_hold_ticks={sum('left alone' in line for line in f_lines[: -1])}")
    print(f"F_respawns_before_budget={sum(f_respawns[: -1])}")
    print(f"F_escape_respawns={f_respawns[-1]}")
    print("F_escape_left_alone=" + ("yes" if "left alone" in f_lines[-1] else "no"))
    print("F_names_N=" + ("yes" if f"N={watchdog.CRASH_LOOP_RESPAWNS}" in f_lines[-1] else "no"))
    print(
        "F_names_window="
        + ("yes" if str(int(watchdog.CRASH_LOOP_WINDOW_SECONDS)) in f_lines[-1] else "no")
    )

    # (g) the escape is NOT a second unbounded path: it takes the ordinary remedy
    # route, so it gets the same cap, ONE escalation and the park. The budget is
    # pinned (to its own defaults, 3 and 1s) so this probe measures the STRUCTURE
    # and not the wall-clock spacing — the spacing is proven separately by the
    # "due in Ns" assertion above, and by `test_the_next_attempt_is_held_until_the_
    # backoff_elapses`. The tick spacing is a quarter of the window, so three holds
    # always fall inside it.
    reset("sister")
    os.environ[watchdog.ENV_RESPAWN_ATTEMPTS] = str(watchdog.DEFAULT_RESPAWN_ATTEMPTS)
    os.environ[watchdog.ENV_RESPAWN_BACKOFF] = "1"
    g_lines = []
    g_respawns = 0
    for step in range(9):
        line, counts = act(OTHER, step * (int(watchdog.CRASH_LOOP_WINDOW_SECONDS) // 4), inflight=True)
        g_lines.append(line)
        g_respawns += len(counts["respawn"])
    print(f"G_escape_respawn_calls={g_respawns}")
    print(f"G_escalations={sum('ESCALATED ONCE' in line for line in g_lines)}")
    print(f"G_parked_lines={sum('PARKED' in line for line in g_lines)}")
    del os.environ[watchdog.ENV_RESPAWN_ATTEMPTS]
    del os.environ[watchdog.ENV_RESPAWN_BACKOFF]

    # (h) the budget is a WINDOW, not a lifetime tally
    reset("sister")
    spacing = int(watchdog.CRASH_LOOP_WINDOW_SECONDS) + 1
    h_respawns = 0
    h_line = ""
    for step in range(watchdog.CRASH_LOOP_RESPAWNS + 2):
        h_line, counts = act(OTHER, step * spacing, inflight=True)
        h_respawns += len(counts["respawn"])
    print(f"H_slow_hold_respawn_calls={h_respawns}")
    print("H_slow_hold_left_alone=" + ("yes" if "left alone" in h_line else "no"))

    # (i) progress restarts the ledger, so a rung that is HEALING is never escaped
    reset("sister")
    i_respawns = 0
    i_line = ""
    for step in range(6):
        i_line, counts = act(f"old{step}", step * 5, inflight=True)
        i_respawns += len(counts["respawn"])
    print(f"I_progress_respawn_calls={i_respawns}")
    print("I_progress_left_alone=" + ("yes" if "left alone" in i_line else "no"))
    print(f"I_progress_ledger_size={len((watchdog.load_drift_record('sister') or {}).get('respawns') or [])}")

    sys.exit(0)

if mode == "ff":
    # The fast-forward remedy, against a REAL repository: `git` in a subprocess,
    # not a stub. This is the half of the fix that a respawn cannot do.
    repo = Path(sys.argv[3])
    changed, head, detail = watchdog.fast_forward_checkout(repo)
    print(f"ff_changed={'yes' if changed else 'no'}")
    print(f"ff_head={head}")
    print(f"ff_refused={'yes' if 'refused' in detail else 'no'}")
    print(f"ff_detail={detail[:160]}")
    sys.exit(0)

if mode == "lease":
    # #977 (issue #706 D5): a second replica of the fleet-cron pair must never
    # act on the same tick. Simulate it by pre-holding the tick's own lease
    # file before calling watchdog_once(), then MEASURE whether the guarded
    # pass ran — not whether it merely reported OK.
    import lease as lease_mod

    calls = []
    watchdog._watchdog_once_locked = lambda force: calls.append(1) or watchdog.channel.EXIT_OK

    lock_path = watchdog.FLEET_DIR / "watchdog.lease"
    holder = lease_mod.FcntlLease(path=lock_path)
    assert holder.acquire() is True, "setup: could not pre-hold the lease"

    rc = watchdog.watchdog_once()
    print(f"LEASE_LOCKED_CALLS={len(calls)}")
    print(f"LEASE_RC={rc}")
    holder.release()
    sys.exit(0)

if mode == "flight":
    # #366 half (a), landed as #793: drive the REAL `run_in_flight` against REAL
    # marker files. This is the point of a separate mode — the probes above stub
    # `run_in_flight`, which is precisely why a pid-only discriminator stayed
    # invisible here while it held the live sister's drift lock open for hours.
    # The marker's `pid` written here is the DRIVER's own pid, i.e. a live process,
    # which is exactly the measured shape: the loop alive, nothing running.
    #
    # #1572: THE CLOCK IS PINNED. A beat stamp is written at second resolution
    # (`strftime` truncates microseconds), so with write instant T, read instant T'
    # and microsecond fraction f(T), the reader ages the stamp to
    # `16200 + f(T) + (T' - T)`: it renders exactly `16200s old` only while the read
    # lands inside the write's OWN wall-clock second. ONE whole-second anchor is taken
    # here and handed to the reader through its `now=` seam, so every age this mode
    # measures is exact by construction instead of by luck.
    anchor = datetime.now(timezone.utc).replace(microsecond=0)
    anchor_epoch = anchor.timestamp()

    def stamp(seconds_ago):
        """A marker beat relative to the pinned `anchor` the reader is given."""
        return (anchor - timedelta(seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def live_stamp(seconds_ago):
        """A beat for an artifact the REAL code times itself (the rung heartbeat)."""
        return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def dead_pid():
        """A pid certainly not alive: a child this process has already reaped."""
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        return child.pid

    runs = watchdog.RUNS_DIR

    def only(name, **fields):
        """Leave exactly ONE marker in RUNS_DIR; report the judgement on it."""
        runs.mkdir(parents=True, exist_ok=True)
        for path in runs.glob("*"):
            path.unlink()
        (runs / f"{name}.json").write_text(json.dumps(fields), encoding="utf-8")
        # The reader is handed the SAME anchor the beat was built from (#1572), so the
        # age it reports is exact instead of a function of when the read landed.
        return watchdog.spawn_liveness.runs_in_flight(runs, now=anchor_epoch)

    live = os.getpid()
    print(f"F_run_stale_seconds={watchdog.spawn_liveness.stale_seconds()}")

    # 1. a live child is work in progress, whatever its beat says
    held, detail = only("live-child", pid=live, child_pid=live, ts=stamp(99999))
    print(f"F1_live_child={'held' if held else 'crashed'}")
    print("F1_names_marker=" + ("yes" if "live-child" in detail else "no"))

    # 2. the measured box shape: the loop's pid alive, no child, a beat nothing advances
    #
    # #1572: this arm used to demand the literal `16200s old` inside `detail`. That
    # integer is derived from the wall clock and the stamp above truncates
    # microseconds, so the equality held only inside a sub-second window and a
    # byte-identical tree went red at random. The INTENT is "the crashed marker reports
    # its child state AND its age": the age is therefore PARSED out of the rendered
    # detail and asserted as a BOUND, and the rendered evidence travels with the arm.
    held, detail = only("phantom", pid=live, child_pid=None, ts=stamp(16200))
    print(f"F2_dead_leftover={'held' if held else 'crashed'}")
    print("F2_names_marker=" + ("yes" if "phantom" in detail else "no"))
    child_named = "child_pid None" in detail
    beat = re.search(r"beat is (\d+)s old", detail)
    age = int(beat.group(1)) if beat else None
    print("F2_child_state=" + ("named" if child_named else "missing"))
    print("F2_reported_age=" + ("absent" if age is None else str(age)))
    print(
        "F2_reports_child_and_age="
        + ("yes" if (child_named and age is not None and 16200 <= age <= 16260) else "no")
    )
    # The rendered string travels with the arm: the driver's output lives in $work,
    # which the check's EXIT trap deletes, so a bare `measured no` would leave a future
    # failure undiagnosable after the fact.
    print("F2_detail=" + detail)

    # 3. `mark_run` writes child_pid: null BEFORE the subagent exists
    held, _ = only("starting", pid=live, child_pid=None, ts=stamp(2))
    print(f"F3_fresh_beat_no_child={'held' if held else 'crashed'}")

    # 4. a dead child with a stale beat is not work
    held, _ = only("dead-child", pid=live, child_pid=dead_pid(), ts=stamp(600))
    print(f"F4_dead_child={'held' if held else 'crashed'}")

    # 5. no readable beat — decided explicitly, and reported
    held, detail = only("no-beat", pid=live, child_pid=None)
    print(f"F5_missing_beat={'held' if held else 'crashed'}")
    print("F5_says_why=" + ("yes" if "unreadable" in detail else "no"))
    held, _ = only("bad-beat", pid=live, child_pid=None, ts="not-a-timestamp")
    print(f"F6_unparseable_beat={'held' if held else 'crashed'}")

    # 6. an unreadable marker is judged, and named — never silently dropped
    for path in runs.glob("*"):
        path.unlink()
    (runs / "torn.json").write_text("{not json", encoding="utf-8")
    held, detail = watchdog.spawn_liveness.runs_in_flight(runs)
    print("F7_torn_marker=" + ("held" if held else "crashed"))
    print("F7_names_it=" + ("yes" if "torn" in detail else "no"))

    # --- the sister's DECISION, with the real `run_in_flight` ------------------
    rung_beat = watchdog.FLEET_DIR / "sister.heartbeat.json"
    rung_beat.parent.mkdir(parents=True, exist_ok=True)

    def sister_decision():
        """One REAL decision over whatever markers are on disk, respawn MEASURED."""
        rung_beat.write_text(
            json.dumps({"pid": 111, "state": "idle", "commit": "old0000", "ts": live_stamp(5)}),
            encoding="utf-8",
        )
        watchdog.drift_record_path("sister").unlink(missing_ok=True)
        if watchdog.escalation_dir().exists():
            for path in watchdog.escalation_dir().glob("*.json"):
                path.unlink()
        calls = []
        watchdog.loop_pid = lambda pattern: 111
        watchdog.respawn = lambda pattern, script, name="": calls.append(name) or True
        watchdog.fast_forward_checkout = lambda root_=None, *, remote="origin/master": (
            False,
            LOCAL,
            "git merge --ff-only refused (not possible to fast-forward)",
        )
        watchdog.channel.capability_line = lambda finding: f"{finding.rung}: {finding.case}"
        line = watchdog.rung_action(
            "sister",
            "fleet/terminal.py",
            "fleet/terminal.sh",
            rung_beat,
            False,
            REMOTE,
            local_head=LOCAL,
        )
        return line, calls

    only("phantom", pid=live, child_pid=None, ts=stamp(16200))
    line, calls = sister_decision()
    print(f"G_crashed_marker_respawn_calls={len(calls)}")
    print("G_left_alone=" + ("yes" if "left alone" in line else "no"))

    only("live-child", pid=live, child_pid=live, ts=stamp(99999))
    line, calls = sister_decision()
    print(f"H_live_child_respawn_calls={len(calls)}")
    print("H_left_alone=" + ("yes" if "left alone" in line else "no"))
    print("H_names_the_marker=" + ("yes" if "held by live-child" in line else "no"))

    only("starting", pid=live, child_pid=None, ts=stamp(2))
    line, calls = sister_decision()
    print(f"I_fresh_beat_respawn_calls={len(calls)}")
    print("I_fresh_beat_left_alone=" + ("yes" if "left alone" in line else "no"))

    sys.exit(0)

print(f"UNKNOWN-MODE {mode}", file=sys.stderr)
sys.exit(9)
DRIVER

run_driver() { # run_driver <root> <state-dir> <mode> <out> [repo]
  local root="$1" state="$2" mode="$3" out="$4" repo="${5:-}"
  mkdir -p "$state"
  # The remedy knobs are scrubbed so a developer's environment cannot change the
  # counts this check asserts (the same reason the driver's own tests set them).
  env -u AO_FLEET_DIR -u AO_WATCHDOG_RESPAWN_ATTEMPTS -u AO_WATCHDOG_RESPAWN_BACKOFF \
    AO_FLEET_DIR="$state" PYTHONDONTWRITEBYTECODE=1 \
    python3 "$work/driver.py" "$mode" "$root" $repo > "$out" 2>&1
}

kv() { # kv <file> <key> -> value (empty when absent)
  sed -n "s/^$2=//p" "$1" | head -1
}

expect() { # expect <file> <key> <expected> <description> [detail-key]
  local got
  got="$(kv "$1" "$2")"
  if [ "$got" = "$3" ]; then
    ok "$4"
  else
    bad "$4 (expected $2=$3, measured $got)"
    # #1572: a failure must be diagnosable from the log ALONE. The driver's output
    # lives in $work, which the EXIT trap deletes, so an arm can name a companion key
    # whose value is the rendered evidence behind its verdict.
    if [ -n "${5:-}" ]; then
      printf '         the arm rendered: %s\n' "$(kv "$1" "$5")"
    fi
  fi
}

# --- 1. the real tree ---------------------------------------------------------
echo "== the real watchdog =="
real_out="$work/real.out"
run_driver "$ROOT" "$work/state-real" drift "$real_out"
real_rc=$?
if [ "$real_rc" -eq 9 ]; then
  echo "check-watchdog-bounded: CANNOT-ASSESS — the probe imported code outside the tree under test" >&2
  exit 2
fi
if [ "$real_rc" -ne 0 ]; then
  bad "the probe could not run against the real tree (rc=$real_rc)"
  sed -n '1,20p' "$real_out" | sed 's/^/    /'
else
  info "$(head -3 "$real_out" | tr '\n' ' ')"
fi

# (a) the two cases are named differently, and the #739 detection is intact
expect "$real_out" case_checkout_behind "checkout-behind" "running == local HEAD != origin/master ⇒ checkout-behind"
expect "$real_out" case_drifted "drifted" "a rung on neither the local HEAD nor origin/master ⇒ drifted"
expect "$real_out" case_healthy "healthy" "running == origin/master ⇒ healthy"
expect "$real_out" case_no_local_head "drifted" "the remote comparison is NOT weakened: no local HEAD ⇒ drifted"
expect "$real_out" case_unreadable_baseline "cannot-assess" "an unreadable baseline is still fail-closed"

# (a) the remedy is the checkout, and it is bounded
expect "$real_out" A_ff_calls "3" "a stale checkout is fast-forwarded (attempt cap 3), never respawned blind"
expect "$real_out" A_respawn_calls "0" "and the rung is not respawned while the checkout cannot move"
expect "$real_out" A_escalations "1" "the unresolved checkout-behind escalates EXACTLY ONCE"
expect "$real_out" A_parked_lines "2" "and is then PARKED, not retried every tick"
expect "$real_out" A_artifacts "1" "exactly one escalation artifact is written"
expect "$real_out" A_names_both "yes" "the escalation names both commits"
expect "$real_out" A_names_checkout "yes" "the escalation names the checkout path"

# (b) an unbounded respawn loop is impossible
expect "$real_out" B_respawn_calls "3" "a respawn that cannot change the commit is attempted at most cap times"
expect "$real_out" B_escalations "1" "then escalated once"
expect "$real_out" B_parked_lines "2" "then parked — the loop cannot continue"
expect "$real_out" B_first_line_respawned "yes" "and the first attempt is a real respawn"

# (b2) progress resets the counter
expect "$real_out" C1_attempts "[1, 2, 1, 2, 1]" "a remedy that changes the observation resets the attempt counter"
expect "$real_out" C1_escalations "0" "so a fleet that is healing is never parked"

# (c)+(d) the busy rung is recorded, not dropped
expect "$real_out" D_busy_respawn_calls "0" "a run in flight is never restarted just to update code"
expect "$real_out" D_left_alone "yes" "and it is reported as left alone"
expect "$real_out" D_pending_phase "pending" "and RECORDED as pending drift"
expect "$real_out" D_completion_respawn_calls "1" "then acted on once the run completes"

# (e) the bound cannot be disarmed by a typo
expect "$real_out" E_config_rc "2" "an unusable attempt cap returns CANNOT-ASSESS, not 0"
expect "$real_out" E_config_names_var "yes" "and names the knob it refused"

# (f)+(g) the crash-loop escape: N holds on an unchanged observation, then the
# remedy is NOT withheld — and the escape is still the bounded remedy.
expect "$real_out" F_hold_ticks "3" "a run in flight holds the lock until the crash-loop budget is spent"
expect "$real_out" F_respawns_before_budget "0" "and nothing is restarted just to update code while it holds"
expect "$real_out" F_escape_respawns "1" "then a crash-looping rung is respawned DESPITE the run in flight"
expect "$real_out" F_escape_left_alone "no" "and is no longer reported as left alone"
expect "$real_out" F_names_N "yes" "the line names N, so the bound is auditable"
expect "$real_out" F_names_window "yes" "and the window it counted in"
expect "$real_out" G_escape_respawn_calls "3" "the escape gets the ordinary budget: at most cap respawns"
expect "$real_out" G_escalations "1" "then escalates EXACTLY ONCE (no second unbounded path)"
expect "$real_out" G_parked_lines "2" "and parks — the crash loop cannot be respawned forever"
expect "$real_out" H_slow_hold_respawn_calls "0" "holds wider than the window are not a crash loop"
expect "$real_out" H_slow_hold_left_alone "yes" "so a slow-but-busy rung is still protected"
expect "$real_out" I_progress_respawn_calls "0" "a hold whose observation MOVES is progress, never a loop"
expect "$real_out" I_progress_left_alone "yes" "so a healing rung is never restarted to update code"
expect "$real_out" I_progress_ledger_size "1" "because progress restarts the respawn ledger"

# --- 2. "a run in flight" is the marker's OWN evidence (#366 half (a), #793) --
echo "== the run markers: a crashed run's leftover must not hold the drift lock =="
flight_out="$work/flight.out"
run_driver "$ROOT" "$work/state-flight" flight "$flight_out"
flight_rc=$?
if [ "$flight_rc" -eq 9 ]; then
  echo "check-watchdog-bounded: CANNOT-ASSESS — the probe imported code outside the tree under test" >&2
  exit 2
fi
if [ "$flight_rc" -ne 0 ]; then
  bad "the marker probe could not run against the real tree (rc=$flight_rc)"
  sed -n '1,20p' "$flight_out" | sed 's/^/    /'
else
  info "$(grep -c . "$flight_out") flight measurements (real marker files, nothing stubbed)"
fi

expect "$flight_out" F1_live_child "held" "a marker with a live child is a run in flight"
expect "$flight_out" F1_names_marker "yes" "and the hold names the marker that holds it"
expect "$flight_out" F2_dead_leftover "crashed" "a leftover (loop pid alive, no child, dead beat) is NOT"
expect "$flight_out" F2_names_marker "yes" "the crashed marker is NAMED — it cannot hold the lock invisibly"
expect "$flight_out" F2_child_state "named" "and the child state it reports is real"
expect "$flight_out" F2_reports_child_and_age "yes" "with its age as a BOUND on a parsed integer, never a literal (#1572)" F2_detail
expect "$flight_out" F3_fresh_beat_no_child "held" "a run that just started has no child yet — never restarted"
expect "$flight_out" F4_dead_child "crashed" "a dead child with a stale beat is not work"
expect "$flight_out" F5_missing_beat "crashed" "a missing beat is decided in the ACTING direction"
expect "$flight_out" F5_says_why "yes" "and the decision is reported, not silent"
expect "$flight_out" F6_unparseable_beat "crashed" "an unparseable beat takes the same decision"
expect "$flight_out" F7_torn_marker "crashed" "an unreadable marker is judged, never dropped"
expect "$flight_out" F7_names_it "yes" "and it is named too"
expect "$flight_out" G_crashed_marker_respawn_calls "1" "so the drift remedy PROCEEDS for a crashed marker"
expect "$flight_out" G_left_alone "no" "and is no longer reported as left alone"
expect "$flight_out" H_live_child_respawn_calls "0" "a live child still stops the remedy — the safety rule holds"
expect "$flight_out" H_left_alone "yes" "reported as before"
expect "$flight_out" H_names_the_marker "yes" "and the held line names its holder"
expect "$flight_out" I_fresh_beat_respawn_calls "0" "a fresh beat protects a starting run"
expect "$flight_out" I_fresh_beat_left_alone "yes" "so a starting run is never restarted to update code"

# --- 3. the remedy against a REAL git checkout -------------------------------
echo "== the fast-forward remedy (a real repository, no network) =="
git_id=(-c user.email=agent773@agents.invalid -c user.name=agent773)
origin="$work/origin.git"
git init --bare -q "$origin" || exit 2
seed="$work/seed"
git clone -q "$origin" "$seed" || exit 2
mkdir -p "$seed"
printf 'one\n' > "$seed/file.txt"
git -C "$seed" "${git_id[@]}" add -A >/dev/null
git -C "$seed" "${git_id[@]}" commit -qm one >/dev/null
git -C "$seed" "${git_id[@]}" push -q origin HEAD:master || exit 2
first="$(git -C "$seed" rev-parse --short HEAD)"
printf 'two\n' >> "$seed/file.txt"
git -C "$seed" "${git_id[@]}" commit -qam two >/dev/null
git -C "$seed" "${git_id[@]}" push -q origin HEAD:master || exit 2
second="$(git -C "$seed" rev-parse --short HEAD)"

behind="$work/behind"
git clone -q "$origin" "$behind" || exit 2
git -C "$behind" -c advice.detachedHead=false reset -q --hard "$first" >/dev/null 2>&1
ff_out="$work/ff.out"
run_driver "$ROOT" "$work/state-ff" ff "$ff_out" "$behind"
if [ "$(kv "$ff_out" ff_changed)" = "yes" ] && [ "$(kv "$ff_out" ff_head)" = "$second" ]; then
  ok "a checkout strictly behind origin/master fast-forwards ($first -> $second)"
else
  bad "the checkout did not fast-forward (measured: $(tr '\n' ' ' < "$ff_out"))"
fi

diverged="$work/diverged"
git clone -q "$origin" "$diverged" || exit 2
git -C "$diverged" -c advice.detachedHead=false reset -q --hard "$first" >/dev/null 2>&1
printf 'local-only\n' >> "$diverged/file.txt"
git -C "$diverged" "${git_id[@]}" commit -qam local-only >/dev/null
run_driver "$ROOT" "$work/state-div" ff "$ff_out" "$diverged"
if [ "$(kv "$ff_out" ff_changed)" = "no" ] && [ "$(kv "$ff_out" ff_refused)" = "yes" ]; then
  ok "a diverged checkout is REFUSED by name, never force-moved"
else
  bad "a diverged checkout was not refused (measured: $(tr '\n' ' ' < "$ff_out"))"
fi

# --- 4. the mutation proof ----------------------------------------------------
copy_tree() { # copy_tree <dest>
  mkdir -p "$1" || return 1
  local part
  for part in fleet governance; do
    [ -e "$ROOT/$part" ] || continue
    cp -R "$ROOT/$part" "$1/$part" 2>/dev/null || return 1
  done
  # A stale __pycache__ carried into the copy would shadow the mutated source and
  # make the control vacuous (measured at #477: two different mutations produced
  # byte-identical output this way).
  find "$1" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  return 0
}

sha_of() { python3 -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1"; }

mutant_probe() { # mutant_probe <name> <mutated-file> <before-sha> <out> [driver-mode]
  local name="$1" file="$2" before="$3" out="$4" mode="${5:-drift}"
  local after
  after="$(sha_of "$file")"
  if [ "$after" = "$before" ]; then
    bad "$name: the mutation did not land (sha256 unchanged) — the control would be vacuous"
    return
  fi
  ok "$name: the mutation landed (sha256 ${before:0:12} -> ${after:0:12})"
  if [ "$(grep -c 'MUTANT' "$file")" -lt 1 ]; then
    bad "$name: the mutated file carries no MUTANT marker — the edit is not identifiable"
    return
  fi
  run_driver "$mutant_tree" "$work/state-$name" "$mode" "$out"
  if [ "$?" -eq 9 ]; then
    bad "$name: the probe imported code outside the mutant tree (vacuous control)"
    return
  fi
}

# --- M1: the attempt cap removed (the runaway itself) ------------------------
echo "== mutant 1: the attempt cap removed =="
mutant_tree="$work/mutant-cap"
copy_tree "$mutant_tree" || {
  echo "check-watchdog-bounded: CANNOT-ASSESS — cannot copy the tree for the mutant" >&2
  exit 2
}
before="$(sha_of "$mutant_tree/fleet/watchdog.py")"
python3 - "$mutant_tree/fleet/watchdog.py" <<'MUTATE'
"""Remove the attempt cap: the pre-#773 unbounded retry, in one line."""
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
needle = "    if attempts > cap:"
if needle not in source:
    print("MUTATE-FAILED", file=sys.stderr)
    sys.exit(1)
mutant = source.replace(needle, "    if False:  # MUTANT: the cap is gone", 1)
if mutant == source:
    print("MUTATE-FAILED", file=sys.stderr)
    sys.exit(1)
path.write_text(mutant, encoding="utf-8")
print("MUTATED")
MUTATE
if [ "$?" -ne 0 ]; then
  bad "the cap mutation could not be constructed (the source moved)"
else
  m1_out="$work/mutant-cap.out"
  mutant_probe "M1" "$mutant_tree/fleet/watchdog.py" "$before" "$m1_out"
  m1_respawn="$(kv "$m1_out" B_respawn_calls)"
  m1_escalations="$(kv "$m1_out" B_escalations)"
  if [ "$m1_respawn" != "3" ] && [ "$m1_escalations" = "0" ]; then
    ok "M1 caught: the uncapped watchdog runs away (respawn_calls=$m1_respawn > cap 3, escalations=$m1_escalations)"
  else
    bad "M1 survived: the unbounded retry was not caught (respawn_calls=$m1_respawn escalations=$m1_escalations)"
  fi
fi

# --- M2: the local-HEAD distinction removed (the measured defect) ------------
echo "== mutant 2: the local-HEAD distinction removed =="
mutant_tree="$work/mutant-head"
copy_tree "$mutant_tree" || {
  echo "check-watchdog-bounded: CANNOT-ASSESS — cannot copy the tree for the mutant" >&2
  exit 2
}
before="$(sha_of "$mutant_tree/fleet/channel.py")"
python3 - "$mutant_tree/fleet/channel.py" <<'MUTATE'
"""Classify a stale checkout as plain drift: respawn the same checkout forever."""
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
needle = "    if local_head not in (None, \"\", \"unknown\") and local_head == running:"
if needle not in source:
    print("MUTATE-FAILED", file=sys.stderr)
    sys.exit(1)
mutant = source.replace(needle, "    if False:  # MUTANT: the checkout is not distinguished", 1)
if mutant == source:
    print("MUTATE-FAILED", file=sys.stderr)
    sys.exit(1)
path.write_text(mutant, encoding="utf-8")
print("MUTATED")
MUTATE
if [ "$?" -ne 0 ]; then
  bad "the local-HEAD mutation could not be constructed (the classifier moved)"
else
  m2_out="$work/mutant-head.out"
  mutant_probe "M2" "$mutant_tree/fleet/channel.py" "$before" "$m2_out"
  m2_ff="$(kv "$m2_out" A_ff_calls)"
  m2_respawn="$(kv "$m2_out" A_respawn_calls)"
  m2_case="$(kv "$m2_out" case_checkout_behind)"
  if [ "$m2_case" = "drifted" ] && [ "$m2_ff" = "0" ] && [ "$m2_respawn" != "0" ]; then
    ok "M2 caught: the stale checkout reads '$m2_case' and is respawned into itself (ff=$m2_ff, respawn=$m2_respawn)"
  else
    bad "M2 survived: the stale checkout is still fast-forwarded (case=$m2_case ff=$m2_ff respawn=$m2_respawn)"
  fi
fi

# --- 4. the tick's own single-writer lease (#977, issue #706 D5) -------------
echo "== the watchdog tick's own lease =="
lease_state="$work/state-lease-real"
lease_out="$work/lease-real.out"
if run_driver "$ROOT" "$lease_state" lease "$lease_out"; then
  real_locked="$(kv "$lease_out" LEASE_LOCKED_CALLS)"
  real_rc="$(kv "$lease_out" LEASE_RC)"
  if [ "$real_locked" = "0" ] && [ "$real_rc" = "0" ]; then
    ok "real fleet/watchdog.py: a pre-held lease stops the tick from acting (rc=$real_rc)"
  else
    bad "real fleet/watchdog.py acted even though the lease was held elsewhere (locked_calls=$real_locked rc=$real_rc)"
  fi
else
  cat "$lease_out" >&2
  bad "the lease driver could not run against the real tree"
fi

echo "== mutant 3: the watchdog tick's lease acquire removed =="
mutant_tree="$work/mutant-lease"
copy_tree "$mutant_tree" || {
  echo "check-watchdog-bounded: CANNOT-ASSESS — cannot copy the tree for the mutant" >&2
  exit 2
}
before="$(sha_of "$mutant_tree/fleet/watchdog.py")"
python3 - "$mutant_tree/fleet/watchdog.py" <<'MUTATE'
"""Remove the tick's own lease guard: two replicas would both act (#977)."""
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
needle = "    if not watchdog_lease.acquire():"
if needle not in source:
    print("MUTATE-FAILED", file=sys.stderr)
    sys.exit(1)
mutant = source.replace(needle, "    if False:  # MUTANT: the lease acquire is gone", 1)
if mutant == source:
    print("MUTATE-FAILED", file=sys.stderr)
    sys.exit(1)
path.write_text(mutant, encoding="utf-8")
print("MUTATED")
MUTATE
if [ "$?" -ne 0 ]; then
  bad "the lease mutation could not be constructed (the source moved)"
else
  after="$(sha_of "$mutant_tree/fleet/watchdog.py")"
  if [ "$after" = "$before" ]; then
    bad "M3: the mutation did not land (sha256 unchanged) — the control would be vacuous"
  else
    ok "M3: the mutation landed (sha256 ${before:0:12} -> ${after:0:12})"
    m3_state="$work/state-lease-mutant"
    m3_out="$work/lease-mutant.out"
    if run_driver "$mutant_tree" "$m3_state" lease "$m3_out"; then
      m3_locked="$(kv "$m3_out" LEASE_LOCKED_CALLS)"
      if [ "$m3_locked" != "0" ]; then
        ok "M3 caught: without the acquire, a second replica acts anyway (locked_calls=$m3_locked)"
      else
        bad "M3 survived: the mutant still refused to act — the probe cannot detect the missing acquire"
      fi
    else
      cat "$m3_out" >&2
      bad "the lease driver could not run against the M3 mutant tree"
    fi
  fi
fi

# --- M4: the crash-loop escape removed (#366 half (b), the measured deadlock) -
echo "== mutant 4: the hold is unbounded again (a crashed run holds the lock forever) =="
mutant_tree="$work/mutant-crashloop"
copy_tree "$mutant_tree" || {
  echo "check-watchdog-bounded: CANNOT-ASSESS — cannot copy the tree for the mutant" >&2
  exit 2
}
before="$(sha_of "$mutant_tree/fleet/watchdog.py")"
python3 - "$mutant_tree/fleet/watchdog.py" <<'MUTATE'
"""Restore the #366 deadlock: the crash-loop verdict never fires, so the
in-flight hold is re-taken on every tick and the drift lock never opens."""
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
needle = "    recent = [stamp for stamp in stamps if 0.0 <= when - stamp <= window]"
if needle not in source:
    print("MUTATE-FAILED", file=sys.stderr)
    sys.exit(1)
mutant = source.replace(
    needle,
    "    recent = []  # MUTANT: the crash-loop budget is never spent, so the hold is unbounded",
    1,
)
if mutant == source:
    print("MUTATE-FAILED", file=sys.stderr)
    sys.exit(1)
path.write_text(mutant, encoding="utf-8")
print("MUTATED")
MUTATE
if [ "$?" -ne 0 ]; then
  bad "the crash-loop mutation could not be constructed (the verdict moved)"
else
  m4_out="$work/mutant-crashloop.out"
  mutant_probe "M4" "$mutant_tree/fleet/watchdog.py" "$before" "$m4_out" drift
  m4_escape="$(kv "$m4_out" F_escape_respawns)"
  m4_left="$(kv "$m4_out" F_escape_left_alone)"
  m4_holds="$(kv "$m4_out" F_hold_ticks)"
  m4_g="$(kv "$m4_out" G_escape_respawn_calls)"
  if [ "$m4_escape" = "0" ] && [ "$m4_left" = "yes" ] && [ "$m4_g" = "0" ]; then
    ok "M4 caught: with the crash-loop verdict removed the rung is held $m4_holds/3 ticks and respawned NEVER (G=$m4_g) — the deadlock is back"
  else
    bad "M4 survived: the crash-loop escape was not caught (hold_ticks=$m4_holds left_alone=$m4_left F=$m4_escape G=$m4_g)"
  fi
fi

# --- M5: the marker's own evidence removed (#366 half (a), #793) --------------
echo "== mutant 5: a live loop pid counts as a run in flight again =="
mutant_tree="$work/mutant-pidonly"
copy_tree "$mutant_tree" || {
  echo "check-watchdog-bounded: CANNOT-ASSESS — cannot copy the tree for the mutant" >&2
  exit 2
}
before="$(sha_of "$mutant_tree/governance/spawn/liveness.py")"
python3 - "$mutant_tree/governance/spawn/liveness.py" <<'MUTATE'
"""Restore the pre-#793 rule: ask whether the loop's pid is alive, and never look
at the marker's own evidence, so a crashed run's leftover holds the lock."""
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
needle = '    if process_alive(child):\n        return Verdict(True, marker, f"live child pid {child}")'
if needle not in source:
    print("MUTATE-FAILED", file=sys.stderr)
    sys.exit(1)
mutant = source.replace(
    needle,
    '    if process_alive(record.get("pid")):  # MUTANT: the loop pid decides, not the marker\n'
    '        return Verdict(True, marker, f"live loop pid {record.get(\'pid\')}")',
    1,
)
if mutant == source:
    print("MUTATE-FAILED", file=sys.stderr)
    sys.exit(1)
path.write_text(mutant, encoding="utf-8")
print("MUTATED")
MUTATE
if [ "$?" -ne 0 ]; then
  bad "the marker-evidence mutation could not be constructed (the discriminator moved)"
else
  m5_out="$work/mutant-pidonly.out"
  mutant_probe "M5" "$mutant_tree/governance/spawn/liveness.py" "$before" "$m5_out" flight
  m5_leftover="$(kv "$m5_out" F2_dead_leftover)"
  m5_respawn="$(kv "$m5_out" G_crashed_marker_respawn_calls)"
  m5_live="$(kv "$m5_out" F1_live_child)"
  m5_live_respawn="$(kv "$m5_out" H_live_child_respawn_calls)"
  if [ "$m5_leftover" = "held" ] && [ "$m5_respawn" = "0" ]; then
    ok "M5 caught: with the pid-only rule back the 4.5h-old leftover reads '$m5_leftover' and the remedy stops acting (G respawn=$m5_respawn)"
  else
    bad "M5 survived: the removed marker-evidence rule was not caught (F2=$m5_leftover G respawn=$m5_respawn)"
  fi
  # Non-vacuity: the mutant must break the RULE and nothing else — a live child is
  # still work, so the divergence above is specific to the evidence it removed.
  if [ "$m5_live" = "held" ] && [ "$m5_live_respawn" = "0" ]; then
    ok "M5 is specific: a live child still holds the lock, so only the removed rule moved"
  else
    bad "M5 is not specific: the live-child case changed too (F1=$m5_live H respawn=$m5_live_respawn)"
  fi
fi

# --- verdict ------------------------------------------------------------------
if [ "$fail" -ne 0 ]; then
  echo "check-watchdog-bounded: FAIL — the watchdog's remedy is not provably bounded" >&2
  exit 1
fi
echo "check-watchdog-bounded: OK — the drift remedy is named, bounded, escalated once and parked; a crashed run's leftover no longer holds the lock and a crash-looping rung is respawned anyway, still under the cap; four mutants of the real source are caught"
exit 0
