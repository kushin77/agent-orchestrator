#!/usr/bin/env bash
# check-board-gate.sh — the board gate: enforcement (#143) + board triggers (#727).
#
# PART 1 — ENFORCEMENT (issue #143)
#   The board does not add a new standard; it re-runs the required CMR gates
#   (#139 knowledge-index, #140 conformance, #141 lessons, #142 remediation)
#   for real and refuses to report ok unless every one of them does. A
#   board-approved, timeboxed exception (governance/board/exceptions.yaml) can
#   downgrade one named check's failure to a visible, non-fatal "excepted"
#   state; an expired exception stops applying automatically and the failure
#   counts in full. Three or more failures of the same check escalate.
#
# PART 2 — BOARD TRIGGERS (issue #727)
#   A claim against a stale board snapshot is refused with `snapshot-stale`, and
#   for a long time nothing ran the remedy the refusal itself names. The
#   consumer re-read the directive every cycle, so the refusal degenerated into
#   the runaway epic #708 exists to bound. The trigger contract is: exactly ONE
#   bounded refresh, and when freshness does not return the directive is PARKED
#   (held by `channel watch`, never re-dispatched) until the board is fresh
#   again. This part PROVOKES that contract against a real scratch fleet:
#
#     * REFRESH-ONCE           — a stale snapshot triggers exactly ONE refresh;
#     * PARKED                 — a refresh that does not clear the staleness
#                                parks the directive and KEEPS its envelope in
#                                the inbox (a park is a hold, not a retirement);
#     * PARK-HOLDS             — `channel watch` refuses the parked directive
#                                while still dispatching a healthy one, so
#                                "watch returned nothing" cannot pass vacuously;
#     * REPORT-ONCE            — the transition is reported once, naming the
#                                snapshot's generated_at and the threshold;
#     * RELEASE-ON-FRESH       — freshness returning releases the park with NO
#                                operator action;
#     * CONTROL-REFRESH-ONCE-CAN-FAIL — a scratch copy of the module with the
#                                park check removed refreshes AGAIN (calls=2),
#                                so the "exactly one" probe above is proved able
#                                to fail (GR-12: no false green);
#     * NEGATIVE-CONTROL-ONE-REFRESH — the open-seam control: TWO loop passes
#                                over one stale snapshot, with the refresh
#                                attempts COUNTED (not inferred) — the total must
#                                be exactly ONE, and the second pass must report
#                                the directive held and not re-dispatch it;
#     * NEGATIVE-CONTROL-NOT-REDISPATCHED — the parked directive is not
#                                dispatchable, so pass 2 cannot re-dispatch it.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. NOT-OK outranks
# CANNOT-ASSESS: a definite defect is never softened into "could not assess".
#
# No network. No writes outside the scratch directory.
#
# Usage: bash scripts/check-board-gate.sh
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-board-gate: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

status=0
# Keep the strongest signal: NOT-OK (1) outranks CANNOT-ASSESS (2) outranks OK.
bump() {
  case "$1" in
    1) status=1 ;;
    2) if [ "$status" -ne 1 ]; then status=2; fi ;;
  esac
}

# --- part 1: the enforcement board (issue #143) ------------------------------
python3 governance/board/cli.py check
board_rc=$?
bump "$board_rc"

# --- part 2: the board triggers (issue #727) ---------------------------------
# The scratch tree name is the sanctioned fleet idiom, NOT a `mktemp` template:
# a template whose placeholder is a run of one letter trips this repo's OWN
# unfinished-marker scan (check-docs.sh lints code for those markers). `mkdir`
# without `-p` refuses loudly instead of silently reusing another run's tree.
work="/tmp/ao-board-trigger.$(date +%s%N).$$"
mkdir "$work" 2>/dev/null || {
  echo "check-board-gate: CANNOT-ASSESS — no scratch directory available" >&2
  exit 2
}
cleanup() { rm -rf "$work"; }
trap cleanup EXIT

driver="$work/driver.py"
cat > "$driver" <<'PY'
"""Provoke the board trigger (issue #727); PASS only when the refusal is bounded.

Run as: driver.py <repo-root> <scratch-root>

Every probe prints PASS/FAIL by NAME, so the caller sees exactly which property
broke. The module under test is imported from the real tree, and the last probe
imports a MUTATED scratch copy whose park check was removed — the "exactly one
refresh" probe must reproduce the runaway there, or it proves nothing.
"""

import argparse
import importlib.util
import io
import json
import shutil
import sys
from contextlib import redirect_stdout
from pathlib import Path

repo_root = Path(sys.argv[1]).resolve()
scratch = Path(sys.argv[2]).resolve()
scratch.mkdir(parents=True, exist_ok=True)

fleet_root = repo_root / "fleet"
dispatch_root = repo_root / "governance" / "dispatch"

sys.dont_write_bytecode = True
# A cached module would serve the ORIGINAL behaviour and fake a passing probe.
for cache in (fleet_root / "__pycache__", dispatch_root / "__pycache__"):
    shutil.rmtree(cache, ignore_errors=True)
sys.path.insert(0, str(dispatch_root))
sys.path.insert(0, str(fleet_root))

import channel  # noqa: E402
import terminal  # noqa: E402
import snapshot as board  # noqa: E402

STALE = "2020-01-01T00:00:00Z"
#: Bound once, here: the control's `resolved` check compares the mutant's action
#: against it, and a bare name would be a NameError at exactly the moment the
#: control is trying to prove something.
ACTION_PARKED = board.ACTION_PARKED
failures: list[str] = []


def probe(name: str, hold: bool, detail: str = "") -> bool:
    print(f"  probe {name}: {'PASS' if hold else 'FAIL'}" + (f" — {detail}" if detail else ""))
    if not hold:
        failures.append(name)
    return hold


fleet = scratch / "fleet"
(fleet / "inbox").mkdir(parents=True, exist_ok=True)
snapshot = scratch / "snapshot.json"
#: The negative control runs its OWN snapshot file against its OWN fleet root.
#: Two scenarios sharing one file is how the control above came to be asked its
#: question against the FRESH snapshot probe RELEASE-ON-FRESH had just written:
#: the file is a shared mutable, so a probe that rewrites it silently re-arms
#: every later check. Separate files (and an explicit stale rewrite before each
#: scenario) make that class of coupling impossible to reintroduce.
negative_snapshot = scratch / "negative-snapshot.json"


def write_snapshot(stamp: str, path: Path | None = None) -> None:
    target = path or snapshot
    target.write_text(
        json.dumps({"generated_at": stamp, "source": "gate", "issues": []}) + "\n",
        encoding="utf-8",
    )


def plant(directive_id: str, ts: str) -> None:
    payload = {
        "id": directive_id,
        "ts": ts,
        "type": "directive",
        "from": "brain",
        "to": "sister",
        "correlation_id": f"c-{directive_id}",
        "model": {"tier": "flash", "thinking": "none"},
        "task": {"kind": "work", "issue": 727, "lane": "governance"},
    }
    (fleet / "inbox" / f"{directive_id}.json").write_text(
        json.dumps(payload) + "\n", encoding="utf-8"
    )


def refusing(calls: list) -> object:
    """A refresh runner that counts its call and REFUSES (the gate is offline)."""

    def runner(cmd, **kwargs):
        calls.append(cmd)
        raise RuntimeError("network unreachable (the gate runs offline)")

    return runner


def apply_mutation(source: str, anchor: str, replacement: str, target: Path) -> tuple[bool, str]:
    """Write ``source`` with ``anchor`` replaced, proving the edit really landed.

    Only ever mutates the scratch copy: the anchor must appear exactly once (an
    ambiguous anchor would silently rewrite the wrong branch) and the result must
    differ from the input (a no-op replace would leave the control testing the
    real code and passing for the wrong reason).
    """
    count = source.count(anchor)
    if count != 1:
        return False, f"the mutation anchor matched {count} time(s) in snapshot.py, not once"
    mutated = source.replace(anchor, replacement)
    if mutated == source:
        return False, "the mutation did not change the source"
    target.write_text(mutated, encoding="utf-8")
    return True, "applied"


def loop_pass(
    directive_id: str,
    *,
    base: Path,
    path: Path | None = None,
    module=None,
) -> tuple[str, int]:
    """One consumer pass over a stale snapshot; returns ``(action, refresh attempts)``.

    ``attempts`` is COUNTED, never asserted: this is the same refresh seam
    ``terminal.board_trigger`` drives, so the count is the loop's real behaviour.
    ``module``/``path`` default to the real module and the scenario's own
    snapshot; the control passes its mutant and its own file.
    """
    target = module or board
    calls: list = []
    verdict = target.refresh_or_park(
        directive_id,
        snapshot_path=path or snapshot,
        base=base,
        repo="owner/repo",
        runner=refusing(calls),
    )
    return verdict.action, len(calls)


#: The negative control's own directive and fleet root — deliberately NOT the
#: main scenario's, so the two cannot contaminate one another. The root is a
#: scratch directory, never the worktree's ``.fleet/``: the gate must not write
#: into live runtime state (a sibling lane's brain loop reads it).
NEGATIVE_FLEET = scratch / "negative-fleet"
NEGATIVE_DIRECTIVE = "d-negative"
shutil.rmtree(NEGATIVE_FLEET, ignore_errors=True)
NEGATIVE_FLEET.mkdir(parents=True, exist_ok=True)


# Redirect the two paths the park and the guard derive their root from, and the
# two side effects that would otherwise shell out to the real channel.
channel.INBOX = fleet / "inbox"
channel.DONE = fleet / "done"
terminal.RUNS = fleet / "runs"
terminal.stream_run_event = lambda *a, **k: None
reports: list[dict] = []


def capture_report(directive_id, key, message_type, body, severity="warn"):
    reports.append({"directive": directive_id, "key": key, "body": body})
    return True


terminal.report_once = capture_report


def watch() -> tuple[int, str]:
    captured = io.StringIO()
    with redirect_stdout(captured):
        rc = channel.cmd_watch(
            argparse.Namespace(
                timeout_seconds=0.05,
                interval=0.01,
                skip=[],
                snapshot=str(snapshot),
                stale_minutes=None,
            )
        )
    return rc, captured.getvalue()


write_snapshot(STALE)
plant("d-stale", "2026-09-14T00:00:00Z")
plant("d-healthy", "2026-09-14T00:00:01Z")

calls: list = []
first = board.refresh_or_park(
    "d-stale", snapshot_path=snapshot, base=fleet, repo="owner/repo", runner=refusing(calls)
)
second = board.refresh_or_park(
    "d-stale", snapshot_path=snapshot, base=fleet, repo="owner/repo", runner=refusing(calls)
)
probe(
    "REFRESH-ONCE",
    first.action == board.ACTION_PARKED and len(calls) == 1,
    f"action={first.action} refresh attempts={len(calls)}",
)
marker = board.park_record("d-stale", base=fleet) or {}
probe(
    "PARKED",
    board.parked("d-stale", base=fleet)
    and second.action == board.ACTION_HELD
    and len(calls) == 1
    and (fleet / "inbox" / "d-stale.json").exists()
    and marker.get("generated_at") == STALE
    and marker.get("threshold_minutes") == board.DEFAULT_STALENESS_MINUTES,
    f"second={second.action} envelope_kept={(fleet / 'inbox' / 'd-stale.json').exists()} "
    f"evidence={marker.get('generated_at')}/threshold={marker.get('threshold_minutes')}",
)

rc, out = watch()
probe(
    "PARK-HOLDS",
    rc == channel.EXIT_OK and '"id": "d-healthy"' in out and "d-stale" not in out,
    f"rc={rc} healthy_dispatched={'\"id\": \"d-healthy\"' in out} parked_dispatched={'d-stale' in out}",
)

trigger = terminal.board_trigger("d-stale", 727, runner=refusing([]), snapshot_path=str(snapshot))
probe(
    "REPORT-ONCE",
    len(reports) == 1
    and STALE in reports[0]["body"]
    and f"threshold {board.DEFAULT_STALENESS_MINUTES}m" in reports[0]["body"],
    f"reports={len(reports)} evidence={'yes' if reports and STALE in reports[0]['body'] else 'no'}",
)

write_snapshot(board.now_iso())
rc, out = watch()
probe(
    "RELEASE-ON-FRESH",
    rc == channel.EXIT_OK
    and '"id": "d-stale"' in out
    and not board.parked("d-stale", base=fleet),
    f"rc={rc} released={'d-stale' in out} park_cleared={not board.parked('d-stale', base=fleet)}",
)
# NOTE: the release above deliberately leaves `snapshot` FRESH. Every scenario
# below writes the snapshot it needs before it runs, so none of them inherits
# that state — the control used to, and answered the wrong question because of it.

# The control: the same two-pass scenario through a copy with the park check
# REMOVED. It must refresh on BOTH passes — otherwise the probes above are
# vacuous.
#
# TWO things must be true for the mutation to be VISIBLE, and the control now
# establishes both explicitly rather than inheriting them:
#
#  1. the scenario must start from a STALE snapshot. `refresh_or_park` returns
#     `fresh` BEFORE it ever reaches the park check, so a control asked its
#     question against the fresh snapshot that probe RELEASE-ON-FRESH just wrote
#     would take that path and refresh zero times — failing for a scenario
#     reason, not because the detector is unbreakable. This is the trap the
#     previous revision fell into; the scenario now re-asserts staleness.
#  2. the scratch copy must be the module actually under test. `channel` and
#     `terminal` are already imported above and both do `import snapshot`, so
#     the bare name is registered; the scratch copy is loaded under that SAME
#     name so both the loader and every module GLOBAL resolve to the mutant.
mutant_fleet = scratch / "mutant-fleet"
mutant_fleet.mkdir(parents=True, exist_ok=True)
# A park left by an earlier run would short-circuit pass 1 before it reached the
# refresh, so the mutant's own park namespace starts empty. The real tree is
# never touched.
shutil.rmtree(mutant_fleet / "parked", ignore_errors=True)
mutant_snapshot = scratch / "mutant-snapshot.json"
write_snapshot(STALE, mutant_snapshot)
anchor = "    if parked(directive_id, base):"
replacement = "    if False:"
source = (dispatch_root / "snapshot.py").read_text(encoding="utf-8")
target = scratch / "mutant" / "snapshot.py"
target.parent.mkdir(parents=True, exist_ok=True)
applied, why = apply_mutation(source, anchor, replacement, target)
if not applied:
    probe("CONTROL-REFRESH-ONCE-CAN-FAIL", False, why)
else:
    on_disk = target.read_text(encoding="utf-8")
    for cached in sorted(target.parent.rglob("__pycache__")):
        shutil.rmtree(cached, ignore_errors=True)
    spec = importlib.util.spec_from_file_location("snapshot", target)
    mutant = importlib.util.module_from_spec(spec)
    sys.modules["snapshot"] = mutant
    spec.loader.exec_module(mutant)
    # `channel` resolves the trigger module LAZILY and memoizes it; `terminal`
    # shares that resolver by reference, so one cache clear covers both. Without
    # it the scenario would keep serving the REAL module to the very probe the
    # mutation is supposed to change.
    channel._BOARD_SNAPSHOT = mutant
    # PROVE the mutation APPLIED and PROVE the question was asked of the mutant:
    # the scratch source must no longer carry the anchor (and must carry the
    # replacement); the module must have resolved inside the scratch tree; and
    # the global the scenario branches on must be the mutant's own function
    # object, not the real module's. A control that cannot show all four is a
    # formality, not a provocation (GR-12).
    mutant_file = Path(mutant.__file__).resolve()
    under_scratch = mutant_file.is_relative_to(scratch)
    resolved = (
        why == "applied"
        and on_disk.count(anchor) == 0
        and on_disk.count(replacement) == 1
        and under_scratch
        and mutant.parked.__module__ == "snapshot"
        and "    if False:" in Path(mutant.__file__).read_text(encoding="utf-8")
        # ...and the loop's own seam serves the mutant, not a cached real module.
        and channel.board_snapshot() is mutant
        and terminal.board_snapshot() is mutant
    )
    if not resolved:
        probe(
            "CONTROL-REFRESH-ONCE-CAN-FAIL",
            False,
            f"the mutant was not the module under test: applied={why} "
            f"anchor_left={on_disk.count(anchor)} replacement={on_disk.count(replacement)} "
            f"file={mutant_file} under_scratch={under_scratch} "
            f"parked_from={getattr(mutant.parked, '__module__', '?')}",
        )
    else:
        # The mutant's verdict on the scenario it was asked to break.
        mutant_first, mutant_attempts_1 = loop_pass(
            "d-mutant", base=mutant_fleet, path=mutant_snapshot, module=mutant
        )
        mutant_second, mutant_attempts_2 = loop_pass(
            "d-mutant", base=mutant_fleet, path=mutant_snapshot, module=mutant
        )
        mutant_attempts = mutant_attempts_1 + mutant_attempts_2
        proof = (
            f"mutant source={mutant_file.name} anchor_removed={on_disk.count(anchor) == 0} "
            f"parked_from={mutant.parked.__module__} "
            f"pass1={mutant_first}/{mutant_attempts_1} pass2={mutant_second}/{mutant_attempts_2}"
        )
        # BOTH halves must hold: with the park check gone, each pass reaches the
        # refresh (one attempt each) instead of the park. Two passes that
        # refreshed only once is exactly the mutation being invisible; a
        # non-parked verdict means the scenario never reached the branch at all.
        if not (
            mutant_attempts == 2
            and mutant_first == ACTION_PARKED
            and mutant_second == ACTION_PARKED
        ):
            probe(
                "CONTROL-REFRESH-ONCE-CAN-FAIL",
                False,
                f"the park-free mutant refreshed {mutant_attempts} time(s) over 2 passes "
                f"(pass1={mutant_first} pass2={mutant_second}); it must be 2 "
                "or the REFRESH-ONCE probe cannot fail",
            )
        else:
            probe("CONTROL-REFRESH-ONCE-CAN-FAIL", True, f"2 refresh attempts, park removed — {proof}")

# --- the open-seam negative control (issue #727) ------------------------------
# A stale snapshot must produce exactly ONE refresh attempt over TWO loop passes,
# and the second pass must NOT re-dispatch the directive. The count is MEASURED
# (`loop_pass` returns it), never asserted from the action alone: an action of
# `parked` with two attempts would be the runaway wearing the park's name.
# Its own snapshot file and fleet root keep it independent of the halves above.
write_snapshot(STALE, negative_snapshot)
pass1_action, pass1_calls = loop_pass(
    NEGATIVE_DIRECTIVE, base=NEGATIVE_FLEET, path=negative_snapshot
)
pass2_action, pass2_calls = loop_pass(
    NEGATIVE_DIRECTIVE, base=NEGATIVE_FLEET, path=negative_snapshot
)
park_marker = board.park_record(NEGATIVE_DIRECTIVE, base=NEGATIVE_FLEET) or {}
probe(
    "NEGATIVE-CONTROL-ONE-REFRESH",
    pass1_calls == 1 and pass2_calls == 0 and pass1_action == ACTION_PARKED and pass2_action == board.ACTION_HELD,
    f"pass1 attempts={pass1_calls} action={pass1_action}; pass2 attempts={pass2_calls} "
    f"action={pass2_action}; total={pass1_calls + pass2_calls} (must be 1)",
)

# The open seam is the consumer's own question, asked of the SAME directive: the
# parked one is not dispatchable, so the second pass cannot re-dispatch it.
probe(
    "NEGATIVE-CONTROL-NOT-REDISPATCHED",
    board.park_record(NEGATIVE_DIRECTIVE, base=NEGATIVE_FLEET) is not None
    and board.ACTION_HELD in board.HELD_ACTIONS
    and board.StaleTrigger(NEGATIVE_DIRECTIVE, board.ACTION_HELD).dispatchable is False
    and board.StaleTrigger(NEGATIVE_DIRECTIVE, board.ACTION_PARKED).dispatchable is False
    and park_marker.get("generated_at") == STALE
    and park_marker.get("threshold_minutes") == board.DEFAULT_STALENESS_MINUTES,
    f"park evidence generated_at={park_marker.get('generated_at')} "
    f"threshold={park_marker.get('threshold_minutes')} parked={board.parked(NEGATIVE_DIRECTIVE, base=NEGATIVE_FLEET)} "
    "held and parked are both non-dispatchable",
)

if failures:
    print(f"PROBES: FAIL ({', '.join(failures)})")
    raise SystemExit(1)
print("PROBES: PASS")
PY

[ -s "$driver" ] || {
  echo "check-board-gate: CANNOT-ASSESS — the trigger driver was not written" >&2
  exit 2
}

echo "== board triggers: the stale-snapshot refusal (issue #727) =="
if python3 "$driver" "$root" "$work/state" > "$work/trigger.log" 2>&1; then
  sed 's/^/  /' "$work/trigger.log"
  trigger_rc=0
else
  trigger_rc=$?
  sed 's/^/  /' "$work/trigger.log" >&2
fi
if [ "$trigger_rc" -eq 0 ]; then
  echo "  OK    board triggers (one bounded refresh, park, release)"
elif [ "$trigger_rc" -eq 2 ]; then
  echo "  NOTE  board triggers could not be assessed" >&2
else
  echo "  FAIL  board triggers (see the probes above)" >&2
fi
bump "$trigger_rc"

case "$status" in
  1) echo "check-board-gate: NOT-OK (board enforcement=$board_rc, board triggers=$trigger_rc)" >&2; exit 1 ;;
  2) echo "check-board-gate: CANNOT-ASSESS (board enforcement=$board_rc, board triggers=$trigger_rc)" >&2; exit 2 ;;
  *) exit 0 ;;
esac
