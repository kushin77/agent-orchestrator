#!/usr/bin/env bash
# check-runaway-guard.sh — the bounded-runaway gate (issue #723).
#
# THE DEFECT THIS EXISTS FOR
#   A directive the fleet could not execute was re-read by EVERY loop cycle, for
#   ever: `fleet/terminal.py` left it pending with no attempt counter, no delay
#   and no terminal state (a refused claim, a run that did not land, the
#   self-heal re-dispatch, an in-flight hold, an untracked foreign claim), and
#   `report_once` deduped the *report* while the *attempt* repeated. The issue's
#   own `Verify:` names this script: it must provoke a runaway and FAIL it.
#
# WHAT IS PROVEN (against the real tree, not a description of it)
#   1. the wiring exists where it runs: `channel watch` filters on the guard, the
#      loop's held paths go through `guard_retire`, and the runtime locations are
#      declared in `fleet/runtime.py`;
#   2. the attempt count PERSISTS across a restart — proved across two OS
#      processes, so a restarted loop continues the budget instead of resetting;
#   3. the backoff is the HARVESTED contract — `min(base * 2**(n-1), 300)` from
#      `vendor/CMR/ops/retry.sh` — pinned numerically, and re-read from the
#      vendored script itself whenever the `vendor/CMR` submodule is initialised
#      (a NOTE when it is not: a fresh worktree has no submodule, and the numeric
#      pin is the authority there, never an unverified claim);
#   4. a refused claim, a run that did not land and a self-heal increment ONE
#      counter, in one file;
#   5. a directive inside its backoff is HELD by `watch` and released when it
#      elapses, while fresh work is dispatched immediately — so "watch returned
#      nothing" can never be mistaken for a working hold;
#   6. after K attempts a directive is DEAD-LETTERED: it leaves the inbox, and
#      `watch` refuses it even when its envelope is deliberately re-planted;
#   7. the guard is LOUD when misconfigured rather than silently disabled.
#
# PROVOCATION (the gate must be able to fail — GR-12, no-false-green)
#   The driver runs against the real `fleet/` tree and then against TWO scratch
#   copies of it, each carrying ONE mutation applied by literal replacement (the
#   replacement is asserted to have happened, so a mutation that never applied
#   cannot be reported as "the gate caught it"):
#     * CAP-DISABLED  — the attempt cap returns a huge number, so nothing is ever
#       retired; the driver MUST fail the probe DEAD-LETTER-NEVER-RETIRED;
#     * BACKOFF-FLAT  — the backoff returns 0s, so a held directive is dispatched
#       inside its own backoff; the driver MUST fail NOT-YET-DUE-HELD.
#   The gate requires that NAMED probe to fail (a mutant may break others too —
#   the CAP mutant also breaks MISCONFIG-IS-LOUD, which is correct: a cap that
#   cannot be read is a guard that cannot be configured).
#   Both scratch copies are deleted on exit and the real tree's digest is
#   compared before/after, so "restore" is proved rather than asserted.
#
# No network. No writes outside the scratch directory. Exit-code contract:
# 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-runaway-guard.sh
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-runaway-guard: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

for required in fleet/runaway.py fleet/terminal.py fleet/channel.py fleet/runtime.py \
                fleet/tests/test_runaway_guard.py; do
  if [ ! -f "$required" ]; then
    echo "check-runaway-guard: FAIL — $required is missing" >&2
    exit 1
  fi
done

# The scratch tree name is the sanctioned fleet idiom, NOT a `mktemp` template: a
# template whose placeholder is a run of one letter trips this repo's OWN
# unfinished-marker scan (`check-docs.sh` lints code for those markers), so the
# gate would fail docs-lint. `mkdir` without `-p` refuses loudly instead of
# silently reusing another run's tree.
work="/tmp/ao-runaway-guard.$(date +%s%N).$"
mkdir "$work" 2>/dev/null || {
  echo "check-runaway-guard: CANNOT-ASSESS — no scratch directory available" >&2
  exit 2
}
cleanup() { rm -rf "$work"; }
trap cleanup EXIT

fail=0
ok()  { printf '  OK    %s\n' "$1"; }
bad() { printf '  FAIL  %s\n' "$1" >&2; fail=$((fail + 1)); }
note() { printf '  NOTE  %s\n' "$1"; }

digest() { sha256sum "$1" | awk '{print $1}'; }

# --- 1. the wiring is declared where it actually runs ------------------------
plumbing=(
  "fleet/channel.py|runaway.dispatchable|channel watch filters on the guard (a retired directive is never returned)"
  "fleet/terminal.py|guard_retire|the loop's held paths go through the guard"
  "fleet/terminal.py|runaway.record_attempt|the loop counts an attempt on the decision path"
  "fleet/runtime.py|ATTEMPTS|the attempt counters' runtime location is declared"
  "fleet/runtime.py|DEAD_LETTER|the terminal store's runtime location is declared"
  "fleet/runaway.py|AO_RUNAWAY_ATTEMPTS|K is configurable, and the module documents it"
  "fleet/runaway.py|AO_RUNAWAY_BACKOFF|the backoff base is configurable, and documented"
  "fleet/runaway.py|BACKOFF_CAP_SECONDS = 300|the harvested 300s cap is pinned"
)
for entry in "${plumbing[@]}"; do
  IFS='|' read -r file marker label <<< "$entry"
  if grep -qF -- "$marker" "$file"; then
    ok "$label ($file: $marker)"
  else
    bad "$label — $file no longer carries: $marker"
  fi
done

# Every held path must be counted: the five non-dispatching branches of the loop
# (claim refused, run did not land, self-heal, in-flight, orphaned) plus the
# definition. A branch that stopped calling the guard is a runaway again, and a
# count is the only offline proof that all five still do.
guard_calls="$(grep -c 'guard_retire(' fleet/terminal.py || true)"
if [ "${guard_calls:-0}" -ge 6 ]; then
  ok "all five held paths call guard_retire ($guard_calls occurrences incl. the definition)"
else
  bad "only $guard_calls guard_retire call site(s) — a held path lost its attempt counter"
fi

# --- 2. the driver (shared by the real tree and both mutants) ----------------
driver="$work/driver.py"
cat > "$driver" <<'PY'
"""Provoke the runaway guard; PASS only when it bounds, holds and retires.

Run as: driver.py <fleet-root> <real-repo-root> <state-dir>

Every probe prints PASS/FAIL by NAME, so the caller (and a mutant run) can see
exactly which property broke. The driver imports `channel` and `runaway` from
<fleet-root>, so pointing it at a mutated scratch copy runs the mutation.
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

fleet_root = Path(sys.argv[1]).resolve()
repo_root = Path(sys.argv[2]).resolve()
state = Path(sys.argv[3]).resolve()
state.mkdir(parents=True, exist_ok=True)

sys.dont_write_bytecode = True
shutil.rmtree(fleet_root / "__pycache__", ignore_errors=True)
sys.path.insert(0, str(fleet_root))   # channel / runaway / runtime under test
sys.path.insert(0, str(repo_root))    # governance.policy.lease, for channel

os.environ["AO_FLEET_DIR"] = str(state)
os.environ["AO_RUNAWAY_ATTEMPTS"] = "3"
os.environ["AO_RUNAWAY_BACKOFF"] = "30"

import channel  # noqa: E402
import runaway  # noqa: E402

CAP = int(os.environ["AO_RUNAWAY_ATTEMPTS"])
failures: list[str] = []


def probe(name: str, hold: bool, detail: str = "") -> bool:
    print(f"  probe {name}: {'PASS' if hold else 'FAIL'}" + (f" — {detail}" if detail else ""))
    if not hold:
        failures.append(name)
    return hold


def plant(directive_id: str, ts: str = "2026-09-14T00:00:00Z") -> Path:
    inbox = state / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / f"{directive_id}.json"
    path.write_text(
        json.dumps(
            {
                "id": directive_id,
                "ts": ts,
                "type": "directive",
                "from": "brain",
                "to": "sister",
                "task": {"kind": "work", "issue": 723},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def clear_inbox() -> None:
    """Every probe starts from a known mailbox — leftovers would fake a result."""
    for path in (state / "inbox").glob("*.json"):
        path.unlink()


def watch(skip: list[str] | None = None) -> tuple[int, str]:
    """Run `channel watch` once; return (exit code, stdout)."""
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        rc = channel.cmd_watch(Namespace(timeout_seconds=0.05, interval=0.01, skip=skip or []))
    return rc, captured.getvalue()


def drive(directive_id: str, reason: str, attempts: int) -> int:
    """Mirror terminal.guard_retire: count each attempt, retire once exhausted."""
    for _ in range(attempts):
        record = runaway.record_attempt(directive_id, reason, base=state)
        if record.exhausted and not runaway.dead_lettered(directive_id, base=state):
            runaway.dead_letter(directive_id, reason, base=state)
    return runaway.load(directive_id, base=state).attempts


CHILD = """
import json, os, sys
sys.path.insert(0, sys.argv[1])
import runaway
for reason in ("claim refused", "run did not land"):
    runaway.record_attempt("d-restart", reason)
print(json.loads((runaway.attempts_dir() / "d-restart.json").read_text())["attempts"])
"""

# 1. the count survives a restart (two separate interpreters, one state dir)
child_env = {
    **os.environ,
    "AO_FLEET_DIR": str(state),
    "PYTHONDONTWRITEBYTECODE": "1",
}
runs = []
for _ in range(2):
    done = subprocess.run(
        [sys.executable, "-c", CHILD, str(fleet_root)],
        capture_output=True,
        text=True,
        env=child_env,
    )
    runs.append(done.stdout.strip() if done.returncode == 0 else done.stderr.strip()[-160:])
probe(
    "COUNTER-PERSISTS",
    runs == ["2", "4"],
    f"a restarted loop must continue the budget: {runs}",
)

# 2. the backoff is the harvested contract, not a local curve
series = [runaway.backoff_delay(n, base=30) for n in range(1, 7)]
probe(
    "BACKOFF-CONTRACT",
    series == [30, 60, 120, 240, 300, 300] and runaway.BACKOFF_CAP_SECONDS == 300,
    f"min(base * 2**(n-1), 300) must hold: got {series}, cap {runaway.BACKOFF_CAP_SECONDS}",
)
vendored = repo_root / "vendor" / "CMR" / "ops" / "retry.sh"
if vendored.exists():
    text = vendored.read_text(encoding="utf-8")
    probe(
        "BACKOFF-HARVEST-SOURCE",
        "delay=$((BACKOFF * (2 ** (n - 1))))" in text and '[ "$delay" -gt 300 ] && delay=300' in text,
        f"the vendored contract this module harvested has drifted: {vendored}",
    )
else:
    print(f"  NOTE  the vendor/CMR submodule is not initialised — the harvest is pinned "
          f"numerically by BACKOFF-CONTRACT instead ({vendored} unreadable)")

# 3. one counter for a refused claim, a run that did not land and a self-heal
refused = runaway.record_attempt("d-shared", "claim refused: refused", base=state)
crashed = runaway.record_attempt("d-shared", "run did not land: failed", base=state)
healed = runaway.record_attempt("d-shared", "self-heal: our own run no longer exists", base=state)
shared_file = state / "attempts" / "d-shared.json"
reasons = json.loads(shared_file.read_text(encoding="utf-8"))["reasons"] if shared_file.exists() else []
probe(
    "SHARED-COUNTER",
    (refused.attempts, crashed.attempts, healed.attempts) == (1, 2, 3) and len(reasons) == 3,
    f"a self-heal must be an attempt, not a fresh start: counts "
    f"{(refused.attempts, crashed.attempts, healed.attempts)}, reasons {reasons}",
)

# 4. non-vacuity: fresh work is dispatched immediately, or the holds below prove nothing
clear_inbox()
plant("d-fresh")
rc, out = watch()
probe(
    "FRESH-WORK-NOT-HELD",
    rc == channel.EXIT_OK and '"id": "d-fresh"' in out,
    f"the guard held a directive it had never seen: rc={rc}",
)
clear_inbox()

# 5. a directive inside its backoff is HELD, and released when the wait elapses
plant("d-held")
runaway.record_attempt("d-held", "claim refused: transient", base=state)
rc, out = watch()
held_ok = (
    rc == channel.EXIT_NOT_OK
    and "d-held" not in out
    and (state / "inbox" / "d-held.json").exists()
    and runaway.remaining("d-held", base=state) > 0
)
# Re-stamp the attempt in the past: the same directive must become dispatchable
# again, or the hold would be a permanent wedge rather than a backoff.
runaway.record_attempt("d-held", "claim refused: transient", base=state, now=0)
rc2, out2 = watch()
probe(
    "NOT-YET-DUE-HELD",
    held_ok and rc2 == channel.EXIT_OK and '"id": "d-held"' in out2,
    f"held={held_ok} (rc={rc}) then released rc={rc2}",
)
clear_inbox()

# 6. after K attempts the directive is DEAD-LETTERED and never watched again
plant("d-terminal", ts="2026-09-14T00:00:00Z")
plant("d-other", ts="2026-09-14T00:00:01Z")
attempts = drive("d-terminal", "claim refused: always fails", CAP)
artifact = state / "dead-letter" / "d-terminal.json"
retired = (
    attempts == CAP
    and runaway.dead_lettered("d-terminal", base=state)
    and artifact.exists()
    and not (state / "inbox" / "d-terminal.json").exists()
    and runaway.dispatchable("d-terminal", base=state) is False
)
rc, out = watch()
served_other = rc == channel.EXIT_OK and '"id": "d-other"' in out and "d-terminal" not in out
# ...and the move is not what is being tested: put the order BACK in the inbox.
plant("d-terminal", ts="2026-09-14T00:00:00Z")
rc2, out2 = watch(skip=["d-other"])
refused_again = rc2 == channel.EXIT_NOT_OK and "d-terminal" not in out2
body = json.loads(artifact.read_text(encoding="utf-8")) if artifact.exists() else {}
probe(
    "DEAD-LETTER-NEVER-RETIRED",
    retired and served_other and refused_again and body.get("attempts") == CAP,
    f"attempts={attempts} retired={retired} other_served={served_other} "
    f"refused_when_replanted={refused_again}",
)

# 7. a misconfigured budget is LOUD, not a silent disable
os.environ["AO_RUNAWAY_ATTEMPTS"] = "zero"
loud = False
try:
    runaway.record_attempt("d-loud", "claim refused", base=state)
except runaway.RunawayConfigError:
    loud = True
read_tolerant = runaway.dispatchable("d-untouched", base=state) is True
del os.environ["AO_RUNAWAY_ATTEMPTS"]
os.environ["AO_RUNAWAY_ATTEMPTS"] = str(CAP)
probe(
    "MISCONFIG-IS-LOUD",
    loud and read_tolerant,
    f"refused={loud} and the read path must stay tolerant={read_tolerant}",
)

if failures:
    print(f"PROBES: FAIL ({', '.join(failures)})")
    raise SystemExit(1)
print("PROBES: PASS")
PY

[ -s "$driver" ] || {
  echo "check-runaway-guard: CANNOT-ASSESS — the driver was not written" >&2
  exit 2
}

# --- 3. the real tree --------------------------------------------------------
sha_before="$(digest fleet/runaway.py)"
echo "== runaway guard: the real tree =="
if python3 "$driver" "$root/fleet" "$root" "$work/state-real" > "$work/real.log" 2>&1; then
  cat "$work/real.log"
  ok "the real tree bounds, holds and retires a runaway directive"
else
  cat "$work/real.log" >&2
  bad "the guard failed against the real tree (see the probes above)"
fi

# --- 4. provocation: a mutation the gate MUST refuse, by name ----------------
# Each scratch copy is a full copy of `fleet/`, so `channel` and `runaway` are
# mutated together and the watch filter inherits the mutation — a mutation that
# only touched one module could otherwise be invisible to the probe it must break.
provoke() { # provoke <label> <anchor> <replacement> <expected-probe>
  label="$1"; anchor="$2"; replacement="$3"; expected="$4"
  scratch="$work/$label"
  cp -R "$root/fleet" "$scratch" 2>/dev/null
  if [ ! -f "$scratch/runaway.py" ]; then
    bad "could not stage the $label mutant (no scratch copy of fleet/)"
    return
  fi
  if ! python3 - "$scratch/runaway.py" "$anchor" "$replacement" <<'PY'
import sys
from pathlib import Path

path, anchor, replacement = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
source = path.read_text(encoding="utf-8")
before = source.count(anchor)
if before != 1:
    print(f"the mutation anchor matched {before} times, not once", file=sys.stderr)
    raise SystemExit(3)
mutated = source.replace(anchor, replacement)
if mutated == source:
    print("the mutation did not change the source", file=sys.stderr)
    raise SystemExit(3)
path.write_text(mutated, encoding="utf-8")
print("MUTANT-APPLIED:", anchor, "->", replacement)
PY
  then
    bad "the $label mutation could not be applied (see the reason above)"
    return
  fi
  echo "== runaway guard: provocation $label (must fail $expected) =="
  if python3 "$driver" "$scratch" "$root" "$work/state-$label" > "$work/$label.log" 2>&1; then
    bad "$label was NOT refused — the gate cannot see the runaway it exists for"
  elif grep -q "probe $expected: FAIL" "$work/$label.log"; then
    grep -E "probe .*: (PASS|FAIL)|^  NOTE|PROBES:" "$work/$label.log"
    ok "$label was refused by name ($expected)"
  else
    cat "$work/$label.log" >&2
    bad "$label failed, but not for the reason the provocation targets ($expected)"
  fi
}

provoke "CAP-DISABLED" \
  'return _positive_int(ENV_ATTEMPT_CAP, DEFAULT_ATTEMPT_CAP)' \
  'return 10 ** 9' \
  "DEAD-LETTER-NEVER-RETIRED"

provoke "BACKOFF-FLAT" \
  'return min(delay, BACKOFF_CAP_SECONDS)' \
  'return 0' \
  "NOT-YET-DUE-HELD"

# --- 5. the tree was never mutated, and the test corpus proves the same ------
sha_after="$(digest fleet/runaway.py)"
if [ "$sha_before" = "$sha_after" ]; then
  ok "the real fleet/runaway.py is byte-identical before and after the provocations"
else
  bad "a provocation modified the real tree ($sha_before -> $sha_after)"
fi

if env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q \
     fleet/tests/test_runaway_guard.py > "$work/pytest.log" 2>&1; then
  ok "the guard's own suite passes ($(tail -n 1 "$work/pytest.log" | tr -d '\r'))"
else
  tail -n 20 "$work/pytest.log" >&2
  bad "fleet/tests/test_runaway_guard.py is not green"
fi

# --- 6. the honest note about the wiring (owned by the wiring lane, not here) -
if grep -qF 'bash scripts/check-runaway-guard.sh' scripts/verify.sh 2>/dev/null; then
  ok "scripts/verify.sh runs this check"
else
  note "scripts/verify.sh does not name this check yet — wire it as:"
  note "  'runaway-guard|bash scripts/check-runaway-guard.sh'"
  note "(check-gate-coverage refuses a newly delivered scripts/check-*.sh that no gate file invokes,"
  note " so the gate of record stays red until that one line lands)"
fi

echo ""
if [ "$fail" -eq 0 ]; then
  echo "check-runaway-guard: OK — the runaway guard bounds, holds and retires, and refuses a disabled guard"
  exit 0
fi
echo "check-runaway-guard: NOT-OK — $fail check(s) failed" >&2
exit 1
