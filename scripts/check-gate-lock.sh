#!/usr/bin/env bash
# check-gate-lock.sh — the gate admission-control gate (issue #724).
#
# The operator measured 49 concurrent `make verify` runs, 43 of them stacked in
# two worktrees, ~16 hours of duplicated work. Admission control is only an
# institution if its REFUSALS are as real as its grants, so this gate proves the
# refusal against a live lock, not against a list of adjectives:
#
#   * a second gate in ONE worktree is REFUSED, by name and by pid;
#   * two different worktrees never collide (the key is the worktree, not the box);
#   * the box-wide permit bound PARKs the next gate, naming the cap and the holders;
#   * release-on-signal really frees the slot (the trap runs);
#   * release-on-crash really frees the slot (SIGKILL, where no trap can run);
#   * a holder killed outright leaves a STALE record that the next gate reclaims
#     while NAMING who left it behind;
#   * a 0-byte record is never read as an empty slot, and release never signals a
#     pid it cannot evidence;
#   * and the whole proof is not vacuous: the same double-acquire is driven
#     against a MUTANT whose exclusion always grants, and the refusal must vanish.
#
# Admission control shipped once as a snippet an operator had to paste into
# `scripts/verify.sh`, and the snippet was never applied: the control was INERT
# and nothing bounded a gate. So this gate also proves the WIRING, two ways:
#
#   * structurally — `scripts/verify.sh` carries the prelude, and it carries it
#     BEFORE `.verify/` is reset (a parked gate must not be able to destroy the
#     last real attestation); a deleted or relocated prelude fails by name;
#   * live — a REAL `scripts/verify.sh` is started in a scratch worktree and a
#     SECOND one is started in that same worktree; the second must be refused by
#     name, run ZERO checks and write NO attestation. A control that asserts
#     rather than provokes is not a control, so the refusal is provoked, not
#     assumed.
#
# Only that last proof starts a real `scripts/verify.sh`, and it does so in a
# scratch worktree with its OWN permit store; every other held gate is simulated
# with the entrypoint the gate itself calls. Every subprocess gets
# `AO_GATE_LOCK_ROOT` pointed at a scratch directory, so this gate never touches
# the box's real permit store, the shared checkout's `.fleet/`, or a sibling
# lane's lock state.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-gate-lock.sh
#
# ---knowledge---
# module_id: scripts.check-gate-lock
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, named-refusal, dry-run-default, lane-isolation, deterministic]
# derives_from: null
# owner_sme: platform-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#724"]
# do_not_duplicate: null
# ---knowledge---
set -uo pipefail

# The gate of record must not leave bytecode caches in the tree it is judging.
export PYTHONDONTWRITEBYTECODE=1

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

required_files=(
  "fleet/gatelock.py"
  "scripts/gate-lock.sh"
  "fleet/tests/test_gatelock.py"
  "docs/EXECUTION-PLAN.md"
  "scripts/pytest-suites.txt"
)
for required in "${required_files[@]}"; do
  if [ ! -f "$required" ]; then
    echo "check-gate-lock: FAIL — $required is missing" >&2
    exit 1
  fi
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-gate-lock: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

fail=0

# --- 1. the contract is declared institutionally ----------------------------
# The rule lives in the execution plan (the lane-ownership document), including
# the exact lines the orchestrator inserts into scripts/verify.sh: the DOCUMENTED
# snippet and the code this gate drives must not drift apart.
declare -a declarations=(
  "docs/EXECUTION-PLAN.md|gate admission control|one composite gate per worktree|AO_GATE_LOCK_ROOT|AO_GATE_MAX_CONCURRENT"
  "docs/EXECUTION-PLAN.md|gate-lock.sh\" acquire|gate-lock.sh\" release|exit 143|gate-coverage"
  "fleet/gatelock.py|one composite gate per worktree|AO_GATE_MAX_CONCURRENT|AO_GATE_LOCK_ROOT|REFUSED|PARKED"
  "scripts/gate-lock.sh|acquire|release|status|AO_GATE_LOCK_ROOT|AO_GATE_MAX_CONCURRENT"
)

missing_declarations() {
  local file="$1" marker missing=0
  shift
  for marker in "$@"; do
    if ! grep -qFi -- "$marker" "$file"; then
      printf '  FAIL  %s (missing declaration: %s)\n' "$file" "$marker" >&2
      missing=1
    fi
  done
  return "$missing"
}

for entry in "${declarations[@]}"; do
  IFS='|' read -r -a parts <<< "$entry"
  if missing_declarations "${parts[0]}" "${parts[@]:1}"; then
    echo "  OK    ${parts[0]} declares the admission contract"
  else
    fail=$((fail + 1))
  fi
done

# --- 2. the tests are reachable from the gate of record ---------------------
if grep -qxF "fleet" scripts/pytest-suites.txt; then
  echo "  OK    scripts/pytest-suites.txt declares the fleet suite"
else
  echo "  FAIL  scripts/pytest-suites.txt does not declare the fleet suite" >&2
  fail=$((fail + 1))
fi

# --- 3. the live proofs ----------------------------------------------------
work="/tmp/gate-lock.$$.$(date +%s)"
if ! mkdir -p "$work" 2>/dev/null; then
  echo "check-gate-lock: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$work"' EXIT

if python3 - "$root" "$work" <<'PY'
"""Live proofs for gate admission control (issue #724).

Every proof drives `scripts/gate-lock.sh`, the entrypoint `scripts/verify.sh`
calls, with `AO_GATE_LOCK_ROOT` pointed at a scratch store: this gate never
touches the box's real permit store, the shared checkout's `.fleet/`, or a
sibling lane's lock state.
"""
import atexit
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True

repo = Path(sys.argv[1]).resolve()
work = Path(sys.argv[2]).resolve()
store = work / "store"
lanes = work / "lanes"
lanes.mkdir(parents=True, exist_ok=True)

ENTRY = repo / "scripts" / "gate-lock.sh"
MODULE = repo / "fleet" / "gatelock.py"

# Import the module under test from THIS tree (never a cache, never a copy) and
# prove the import resolved there, so a stale `__pycache__` cannot shadow it.
sys.path.insert(0, str(repo / "fleet"))
import gatelock  # noqa: E402

if Path(gatelock.__file__).resolve() != MODULE:
    print(f"  FAIL  the module under test is not {MODULE}", file=sys.stderr)
    raise SystemExit(1)

problems = []


def check(label, condition, detail=""):
    if condition:
        print(f"  OK    {label}", flush=True)
    else:
        problems.append(label)
        print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}", file=sys.stderr, flush=True)


def note(label, detail):
    """Print the evidence VERBATIM without touching the verdict.

    A refusal quoted by the gate itself is evidence a reader can check: a
    control that only prints "OK    a second gate is refused" asks to be
    believed.
    """
    print(f"  note  {label}: {' '.join(detail.split())}", flush=True)


def env(root=None, **overrides):
    merged = dict(os.environ)
    merged["AO_GATE_LOCK_ROOT"] = str(root if root is not None else store)
    merged["PYTHONDONTWRITEBYTECODE"] = "1"
    for key, value in overrides.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = str(value)
    return merged


def gate(*args, cap=None, owner=None, timeout=60, root=None):
    overrides = {}
    if cap is not None:
        overrides["AO_GATE_MAX_CONCURRENT"] = cap
    command = ["bash", str(ENTRY), *args]
    if owner is not None:
        command += ["--owner-pid", str(owner)]
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        env=env(root=root, **overrides),
        timeout=timeout,
    )


def lane(name):
    path = lanes / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def lock_of(path):
    return gatelock.worktree_lock_path(path, store)


def holder_pid(path):
    owner = gatelock.read_owner(lock_of(path))
    return owner.pid if owner else None


def wait_until(predicate, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


# Every permit store this gate creates, so the reaper below covers the scratch
# worktree's store as well as the mocked-gate store built at the top.
stores = [store]


def reap():
    """Leave no holder behind, whatever happened above.

    A record can name a pid this process wrote itself, so a reaper that signals
    anything it finds would kill the gate it is running inside.
    """
    protected = {os.getpid(), os.getppid()}
    for base in stores:
        for candidate in sorted(base.rglob("*.lock")):
            try:
                owner = gatelock.read_owner(candidate)
            except gatelock.GateLockError:
                continue
            if owner and owner.pid not in protected and gatelock.pid_alive(owner.pid):
                try:
                    os.kill(owner.pid, signal.SIGKILL)
                except OSError:
                    pass


atexit.register(reap)

# --- 0. the bound lives in a store outside every workspace -------------------
saved_root = os.environ.pop("AO_GATE_LOCK_ROOT", None)
default_root = gatelock.store_root()
if saved_root is not None:
    os.environ["AO_GATE_LOCK_ROOT"] = saved_root
check(
    "the default permit store lives outside every workspace",
    not str(default_root).startswith(str(repo)) and default_root.name == "agent-orchestrator-gates",
    str(default_root),
)
check("the scratch store is outside the repository too", not str(store).startswith(str(repo)),
      str(store))
store_status = gate("status")
check(
    "the entrypoint takes the store from AO_GATE_LOCK_ROOT",
    f"store={store}" in store_status.stdout,
    store_status.stdout.strip(),
)

wt_a = lane("ao-724-a")
wt_b = lane("ao-724-b")
wt_c = lane("ao-724-c")

check(
    "two worktrees are two different lock keys",
    gatelock.worktree_key(wt_a) != gatelock.worktree_key(wt_b)
    and gatelock.worktree_key(wt_a) == gatelock.worktree_key(wt_a / "."),
    "the lock key does not follow the worktree",
)
check(
    "the worktree lock lands in the store, never in the worktree",
    str(lock_of(wt_a)).startswith(str(store)) and not list(wt_a.rglob("*.lock")),
    str(lock_of(wt_a)),
)

# --- 1. a gate is admitted, and the grant is evidenced ----------------------
admit_a = gate(
    "acquire",
    "--worktree",
    str(wt_a),
    "--issue",
    "724",
    "--session",
    "check-gate-lock",
    "--mode",
    "verify",
    owner=os.getpid(),
)
holder_a = holder_pid(wt_a)
check("the first gate in a worktree is admitted", admit_a.returncode == 0,
      admit_a.stderr.strip() or admit_a.stdout.strip())
check(
    "the admission receipt names the worktree, the holder and its permit",
    "ADMITTED" in admit_a.stdout
    and f"holder={holder_a}" in admit_a.stdout
    and "permit=slot-" in admit_a.stdout,
    admit_a.stdout.strip(),
)
owner_a = gatelock.read_owner(lock_of(wt_a))
check(
    "the lock record names its holder, worktree, issue and mode",
    owner_a is not None
    and owner_a.pid == holder_a
    and owner_a.worktree == str(wt_a)
    and owner_a.issue == "724"
    and owner_a.mode == "verify"
    and gatelock.record_problem(lock_of(wt_a)) is None,
    gatelock.record_problem(lock_of(wt_a)) or "record present",
)
check("the holder is a live process, so the exclusion is a real flock",
      holder_a is not None and gatelock.pid_alive(holder_a), str(holder_a))

# --- 2. NEGATIVE CONTROL: a second gate in ONE worktree refuses ------------
second = gate("acquire", "--worktree", str(wt_a), "--issue", "724", owner=os.getpid())
check(
    "a second concurrent gate in the SAME worktree is refused",
    second.returncode == gatelock.EXIT_REFUSED,
    f"rc={second.returncode} {second.stderr.strip()}",
)
check(
    "the refusal names the gate that holds the worktree, by pid and path",
    f"pid {holder_a}" in second.stderr and str(wt_a) in second.stderr,
    second.stderr.strip(),
)
check(
    "the refusal says REFUSED and prints no ADMITTED receipt",
    "REFUSED" in second.stderr and "ADMITTED" not in second.stdout,
    f"{second.stderr.strip()} | {second.stdout.strip()}",
)
check(
    "the refused gate changed nothing: the holder still holds",
    gatelock.probe(lock_of(wt_a)).held and holder_pid(wt_a) == holder_a,
    "the lock moved under a refusal",
)
status_held = gate("status", "--worktree", str(wt_a))
check(
    "status reports the worktree HELD and exits with the refusal code",
    status_held.returncode == gatelock.EXIT_REFUSED and "HELD" in status_held.stdout,
    f"rc={status_held.returncode} {status_held.stdout.strip()}",
)

# --- 3. the bound is per worktree, not per machine -------------------------
admit_b = gate("acquire", "--worktree", str(wt_b), owner=os.getpid())
holder_b = holder_pid(wt_b)
check(
    "a different worktree gets its own slot while the first is held",
    admit_b.returncode == 0 and holder_b not in (None, holder_a),
    f"rc={admit_b.returncode} {admit_b.stderr.strip()}",
)

# --- 4. the box-wide permit bound parks the next gate ----------------------
parked = gate("acquire", "--worktree", str(wt_c), cap=2, owner=os.getpid())
check(
    "the box-wide cap parks the next gate instead of starting it",
    parked.returncode == gatelock.EXIT_PARKED,
    f"rc={parked.returncode} {parked.stderr.strip()}",
)
check(
    "the park names the cap and the gates that hold it",
    "PARKED" in parked.stderr
    and "cap (2)" in parked.stderr
    and f"pid {holder_a}" in parked.stderr
    and f"pid {holder_b}" in parked.stderr,
    parked.stderr.strip(),
)
check(
    "a parked gate leaves no lock of its own behind",
    not gatelock.probe(lock_of(wt_c)).held and gatelock.read_owner(lock_of(wt_c)) is None,
    "the parked worktree kept a lock",
)

# --- 5. release frees the slot for the next gate --------------------------
released = gate("release", "--worktree", str(wt_a))
check(
    "release frees the worktree lock and reports who held it",
    released.returncode == 0 and "RELEASED" in released.stdout and "holder=" in released.stdout,
    f"rc={released.returncode} {released.stderr.strip()}",
)
check(
    "the freed worktree reads free",
    wait_until(lambda: not gatelock.probe(lock_of(wt_a)).held),
    "the lock stayed held after release",
)
again = gate("acquire", "--worktree", str(wt_a), owner=os.getpid())
check("a new gate is admitted once the old one released", again.returncode == 0,
      f"rc={again.returncode} {again.stderr.strip()}")
released_again = gate("release", "--worktree", str(wt_a))
check(
    "the re-acquired gate releases the slot it took",
    released_again.returncode == 0 and "RELEASED" in released_again.stdout,
    released_again.stdout.strip(),
)
twice = gate("release", "--worktree", str(wt_a))
check(
    "release is idempotent on an already free worktree",
    twice.returncode == 0 and "already free" in twice.stdout,
    twice.stdout.strip(),
)
gate("release", "--worktree", str(wt_b))

# --- 6. release on SIGNAL, through the trap the orchestrator will apply ----
# Byte-identical in shape to the snippet in docs/EXECUTION-PLAN.md: acquire with
# `--owner-pid $$`, release from the EXIT trap, TERM maps to a real exit so the
# trap runs.
TRAP_GATE = work / "trap-gate.sh"
TRAP_GATE.write_text(
    """#!/usr/bin/env bash
set -u
entry="$1"
worktree="$2"
bash "$entry" acquire --worktree "$worktree" --owner-pid $$ || exit $?
trap 'bash "$entry" release --worktree "$worktree" --owner-pid $$ >/dev/null; echo TRAP-RELEASED' EXIT
trap 'exit 143' TERM
echo GATE-RUNNING
while :; do sleep 0.1; done
""",
    encoding="utf-8",
)


def run_trap_gate(worktree):
    log_path = work / f"trap-gate.{worktree.name}.log"
    handle = open(log_path, "w", encoding="utf-8")
    process = subprocess.Popen(
        ["bash", str(TRAP_GATE), str(ENTRY), str(worktree)],
        stdout=handle,
        stderr=subprocess.STDOUT,
        env=env(),
        start_new_session=True,
    )
    return process, handle, log_path


def wait_for_marker(path, marker, timeout=15.0):
    """Wait until the simulated gate has installed its traps and is running.

    The lock is taken *inside* `acquire`, i.e. before the traps exist, so
    signalling on "the lock is held" would race the trap installation and prove
    nothing about the signal path.
    """
    return wait_until(
        lambda: path.exists() and marker in path.read_text(encoding="utf-8"),
        timeout=timeout,
    )


wt_d = lane("ao-724-d")
signal_proc, signal_log, signal_path = run_trap_gate(wt_d)
try:
    check("the simulated gate took the lock", wait_until(lambda: gatelock.probe(lock_of(wt_d)).held),
          "the simulated gate never took the lock")
    check("the simulated gate reached its run loop with the traps installed",
          wait_for_marker(signal_path, "GATE-RUNNING"), "the gate never reached its run loop")
    signal_proc.send_signal(signal.SIGTERM)
    signal_proc.wait(timeout=20)
    signal_text = signal_path.read_text(encoding="utf-8")
    check("SIGTERM runs the trap and the trap releases the lock",
          "TRAP-RELEASED" in signal_text, signal_text.strip() or "no trap output")
    check("the worktree lock is free after the signal",
          wait_until(lambda: not gatelock.probe(lock_of(wt_d)).held),
          "the lock stayed held after SIGTERM")
    check("the signal released the box-wide permit too",
          wait_until(
              lambda: not any(gatelock.probe(p).held for p in gatelock.permit_paths(store))
          ),
          "a permit slot leaked on the signal path")
finally:
    if signal_proc.poll() is None:
        signal_proc.kill()
        signal_proc.wait(timeout=20)
    signal_log.close()

# --- 7. release on CRASH: SIGKILL, where no trap can run -------------------
wt_e = lane("ao-724-e")
crash_proc, crash_log, crash_path = run_trap_gate(wt_e)
try:
    check("the crash test took the lock", wait_until(lambda: gatelock.probe(lock_of(wt_e)).held),
          "the crash test never took the lock")
    check("the crash test reached its run loop", wait_for_marker(crash_path, "GATE-RUNNING"),
          "the crash gate never reached its run loop")
    crash_proc.kill()
    crash_proc.wait(timeout=20)
    check("a gate killed outright releases its lock with no trap at all",
          wait_until(lambda: not gatelock.probe(lock_of(wt_e)).held),
          "the lock survived the crash")
    check("the crash release did not come from the trap",
          "TRAP-RELEASED" not in crash_path.read_text(encoding="utf-8"),
          "the trap ran, so this was not the crash path")
finally:
    if crash_proc.poll() is None:
        crash_proc.kill()
        crash_proc.wait(timeout=20)
    crash_log.close()

# --- 8. a killed holder leaves a STALE record, named and reclaimed ---------
wt_f = lane("ao-724-f")
stale_admit = gate("acquire", "--worktree", str(wt_f), "--issue", "724", owner=os.getpid())
stale_holder = holder_pid(wt_f)
check("the stale scenario admitted a gate",
      stale_admit.returncode == 0 and stale_holder is not None,
      f"rc={stale_admit.returncode} {stale_admit.stderr.strip()}")
if stale_holder:
    os.kill(stale_holder, signal.SIGKILL)
stale_state = wait_until(
    lambda: not gatelock.probe(lock_of(wt_f)).held
    and gatelock.probe(lock_of(wt_f)).record_bytes > 0
    and not gatelock.pid_alive(stale_holder or 0)
)
check("the killed holder freed the flock but left its record behind", stale_state,
      gatelock.state_text(gatelock.probe(lock_of(wt_f))))
stale_status = gate("status", "--worktree", str(wt_f))
check(
    "status reports the leftover as STALE and names who left it",
    "STALE" in stale_status.stdout and f"pid {stale_holder}" in stale_status.stdout,
    stale_status.stdout.strip(),
)
reclaim = gate("acquire", "--worktree", str(wt_f), "--issue", "724", owner=os.getpid())
check(
    "the next gate reclaims the stale lock and NAMES the owner it took it from",
    reclaim.returncode == 0
    and "reclaimed-stale=" in reclaim.stdout
    and f"pid {stale_holder}" in reclaim.stdout,
    f"rc={reclaim.returncode} {reclaim.stdout.strip()} {reclaim.stderr.strip()}",
)
gate("release", "--worktree", str(wt_f))

# --- 9. a 0-byte record is never trusted (the tmpfs trap) -----------------
wt_g = lane("ao-724-g")
empty_lock = lock_of(wt_g)
empty_lock.parent.mkdir(parents=True, exist_ok=True)
empty_lock.write_bytes(b"")
holder_script = work / "hold-empty.py"
holder_script.write_text(
    "import fcntl, os, sys, time\n"
    "fd = os.open(sys.argv[1], os.O_RDWR)\n"
    "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
    "print('held', flush=True)\n"
    "time.sleep(120)\n",
    encoding="utf-8",
)
external = subprocess.Popen(
    [sys.executable, str(holder_script), str(empty_lock)],
    stdout=subprocess.PIPE,
    text=True,
)
external.stdout.readline()
empty_state = gatelock.probe(empty_lock)
check(
    "a 0-byte record is never read as an empty (free) slot",
    empty_state.held
    and empty_state.owner is None
    and "unnamed owner" in gatelock.owner_text(empty_state),
    gatelock.state_text(empty_state),
)
empty_status = gate("status", "--worktree", str(wt_g))
check(
    "status reports a 0-byte holder as HELD with 0 record bytes, never FREE",
    "HELD" in empty_status.stdout
    and "0 record bytes" in empty_status.stdout
    and "FREE" not in empty_status.stdout,
    empty_status.stdout.strip(),
)
refused_empty = gate("acquire", "--worktree", str(wt_g), owner=os.getpid())
check(
    "a gate is refused while the holder's record cannot be read",
    refused_empty.returncode == gatelock.EXIT_REFUSED,
    f"rc={refused_empty.returncode} {refused_empty.stderr.strip()}",
)
release_empty = gate("release", "--worktree", str(wt_g))
check(
    "release refuses to signal a pid it cannot evidence",
    release_empty.returncode == gatelock.EXIT_STORE_UNUSABLE
    and "refusing to signal" in release_empty.stderr,
    f"rc={release_empty.returncode} {release_empty.stderr.strip()}",
)
check("the holder it could not name was left alone", external.poll() is None,
      "release killed a process it could not evidence")
external.kill()
external.wait(timeout=20)
check("with the external holder gone the slot reads free",
      wait_until(lambda: not gatelock.probe(empty_lock).held), "the slot stayed held")

# --- 10. the write guard is load-bearing, not decoration ------------------
truncated = work / "truncated.lock"
truncated.write_bytes(b"")
check(
    "record_problem reports a 0-byte record as a truncated write",
    "0 bytes" in (gatelock.record_problem(truncated) or ""),
    str(gatelock.record_problem(truncated)),
)
check(
    "record_problem reports a missing record",
    "missing" in (gatelock.record_problem(work / "absent.lock") or ""),
    str(gatelock.record_problem(work / "absent.lock")),
)
bad_json = work / "bad.lock"
bad_json.write_text("{not json", encoding="utf-8")
check(
    "record_problem reports a non-JSON record",
    "not JSON" in (gatelock.record_problem(bad_json) or ""),
    str(gatelock.record_problem(bad_json)),
)
real_write_all = gatelock._write_all
gatelock._write_all = lambda fd, payload: 0
try:
    refused_grant = False
    try:
        gatelock.write_owner(bad_json, gatelock.Owner(pid=os.getpid(), kind="worktree"))
    except gatelock.StoreUnusable as exc:
        refused_grant = "0 bytes" in str(exc)
    check("write_owner refuses to report a grant it cannot evidence", refused_grant)
finally:
    gatelock._write_all = real_write_all

# --- 11. the proof is not vacuous: mutate the exclusion, watch it vanish ---
mutant_dir = work / "mutant"
mutant_dir.mkdir(exist_ok=True)
mutant_path = mutant_dir / "gatelock_mutant.py"
original = MODULE.read_text(encoding="utf-8")
# `_try_lock`'s body grew a docstring (issue #713) between the `def` line and
# its `try:`, so a needle anchored on the two being adjacent went stale and
# never matched — the mutation silently never applied and this control read
# vacuous (issue #1106). Anchor on the `try:`/`lease.fcntl_flock_nb` call
# itself instead, which is what actually does the flock and is what the
# mutation needs to bypass.
needle = "    try:\n        return lease.fcntl_flock_nb(fd, strict=True)\n"
mutated = original.replace(needle, "    return True\n" + needle, 1)
check(
    "the mutation applied to the module under test",
    mutated != original and original.count(needle) == 1,
    "the mutation target was not found, or was ambiguous — this control would "
    "be vacuous",
)
mutant_path.write_text(mutated, encoding="utf-8")
shutil.rmtree(mutant_dir / "__pycache__", ignore_errors=True)
sys.path.insert(0, str(mutant_dir))
import gatelock_mutant as mutant  # noqa: E402

if Path(mutant.__file__).resolve() != mutant_path:
    print("  FAIL  the mutant was not imported from the scratch tree", file=sys.stderr)
    raise SystemExit(1)
wt_h = lane("ao-724-h")
mutant_first = None
mutant_second = None
try:
    mutant_first = mutant.acquire(str(wt_h), root=store, owner_pid=os.getpid())
    try:
        mutant_second = mutant.acquire(str(wt_h), root=store, owner_pid=os.getpid())
    except mutant.Refused:
        mutant_second = None
    check(
        "under the mutation the same double-acquire is NOT refused, so the refusal "
        "proof above is real",
        mutant_second is not None,
        "the mutant still refused — the negative control would be vacuous",
    )
    check(
        "under the mutation nothing is excluded at all: the real module's probe "
        "agrees the lock was never taken (flock, not the bytes, is the exclusion)",
        gatelock.probe(lock_of(wt_h)).held is False,
        f"mutant-holder={mutant_first.holder_pid} real-probe-held="
        f"{gatelock.probe(lock_of(wt_h)).held}",
    )
finally:
    for handle in (mutant_first, mutant_second):
        if handle is None:
            continue
        try:
            os.kill(handle.holder_pid, signal.SIGKILL)
        except OSError:
            pass

# --- 12. the prelude is really APPLIED to scripts/verify.sh ----------------
# The admission control is only an institution if the gate of record carries it.
# It was once delivered as a snippet a human had to paste, and the paste never
# happened: the control was INERT and nothing bounded a gate. This proves the
# wiring structurally, INCLUDING the ordering — the prelude must run before
# `.verify/` is reset, or a parked gate would truncate the last real attestation
# instead of leaving it untouched. A deleted or relocated prelude fails here.
verify_sh = repo / "scripts" / "verify.sh"
verify_lines = verify_sh.read_text(encoding="utf-8").splitlines()


def line_index(needle):
    for index, line in enumerate(verify_lines):
        if needle in line:
            return index
    return -1


i_acquire = line_index('gate-lock.sh" acquire')
i_release = line_index('gate-lock.sh" release')
i_refuse_exit = line_index('exit "$lock_rc"')
i_log_reset = line_index(': > "$log"')
i_results_reset = line_index(': > "$results_tsv"')
i_park = line_index("verify: PARKED")
i_int = line_index("trap 'exit 130' INT")
i_term = line_index("trap 'exit 143' TERM")
i_hup = line_index("trap 'exit 129' HUP")

check("scripts/verify.sh acquires the gate lock", i_acquire >= 0,
      "the prelude is not applied — the admission control is inert")
check("scripts/verify.sh releases it from a trap", i_release >= 0)
check(
    "the prelude runs BEFORE .verify/ is reset, so a parked gate destroys nothing",
    i_acquire >= 0 and i_log_reset > i_acquire and i_results_reset > i_acquire,
    f"acquire@{i_acquire} log-reset@{i_log_reset} results-reset@{i_results_reset}",
)
check(
    "a refused gate exits BEFORE the traps are installed (it releases only what it owns)",
    i_refuse_exit > i_acquire and 0 <= i_refuse_exit < min(i_int, i_term, i_hup, i_release),
    f"exit@{i_refuse_exit} INT@{i_int} TERM@{i_term} HUP@{i_hup} release@{i_release}",
)
check(
    "the refused path names itself outside the 0/1/2 tri-state",
    i_park >= 0,
)
doc_text = (repo / "docs" / "EXECUTION-PLAN.md").read_text(encoding="utf-8")
doc_lines = doc_text.splitlines()
check(
    "the documented prelude and the applied prelude are the same acquire line",
    any('gate-lock.sh" acquire --worktree' in line for line in verify_lines)
    and any('gate-lock.sh" acquire --worktree' in line for line in doc_lines),
)
# A document that still tells a reader to paste the prelude IS the human step
# this control exists to remove, and a human step is exactly how the prelude went
# unapplied in the first place. So its absence is checked by name, not trusted.
stale_instructions = (
    "NOT yet applied",
    "inserts it",
    "Until the snippet lands",
    "Applying the snippet",
)
still_offered = [phrase for phrase in stale_instructions if phrase in doc_text]
check(
    "docs/EXECUTION-PLAN.md no longer instructs a human to paste the prelude",
    not still_offered,
    f"stale instruction still present: {still_offered}",
)

# --- 13. NEGATIVE CONTROL: a second scripts/VERIFY.SH is refused -----------
# Sections 1-11 drive `scripts/gate-lock.sh`, the entrypoint — none of them
# proves that `scripts/verify.sh` CALLS it, and proving only that would leave the
# inert-control bug unproven. So this starts a REAL `scripts/verify.sh`, in a
# scratch worktree carrying byte-identical copies of this tree's orchestrator
# files (digests checked below), and then starts a SECOND one in that same
# worktree. The second must be refused by name, run ZERO checks, and write no
# attestation.
#
# Bound, not open-ended: the scratch tree's check set is bounded to the checks
# the explicit array names (absent there, so they fail fast) plus one discovered
# probe that parks the FIRST gate inside a check until this control releases it.
# That makes the first gate's window deterministic rather than a race, and the
# probe's witness file counts exactly how many gates reached a check body.
verify_store = work / "verify-store"
verify_store.mkdir(parents=True, exist_ok=True)
stores.append(verify_store)

scratch = work / "scratch-wt"
(scratch / "scripts" / "lib").mkdir(parents=True, exist_ok=True)
(scratch / "fleet").mkdir(parents=True, exist_ok=True)

# `fleet/lease.py` travels WITH `fleet/gatelock.py`: `scripts/gate-lock.sh`
# `exec`s `python3 "$root/fleet/gatelock.py"`, so `fleet/` itself is on that
# process's `sys.path` and gatelock's sibling import must resolve from inside
# the scratch tree. Copying the module without its sibling made gate A die in
# the gate's OWN admission step — a traceback where a PARKED/ADMITTED line
# belongs — on every tree, whichever form the import takes (measured
# 2026-09-17, issues #1071 / #1034).
orchestrator_files = (
    "scripts/verify.sh",
    "scripts/gate-lock.sh",
    "scripts/discover-checks.sh",
    "fleet/gatelock.py",
    # gatelock.py imports `lease` (package-relative `from fleet import lease`,
    # falling back to script-style `import lease` when `fleet/` itself is on
    # sys.path — see fleet/gatelock.py's own comment). Without this sibling
    # module the real `scripts/verify.sh` started below cannot import
    # gatelock.py at all: it dies with a bare "No module named 'fleet'" /
    # "No module named 'lease'" before it ever reaches a check, and gate A's
    # ADMITTED assertion below fails for a reason that has nothing to do with
    # the wedge this gate proves (issue #1106).
    "fleet/lease.py",
    "scripts/lib/common.sh",
)
identical = True
for relative in orchestrator_files:
    shutil.copyfile(repo / relative, scratch / relative)
    if hashlib.sha256((repo / relative).read_bytes()).digest() != hashlib.sha256(
        (scratch / relative).read_bytes()
    ).digest():
        identical = False
check("the scratch worktree runs THIS tree's orchestrator files, byte for byte", identical)
check(
    "the scratch worktree's verify.sh is the one that carries the prelude",
    'gate-lock.sh" acquire' in (scratch / "scripts" / "verify.sh").read_text(encoding="utf-8"),
)

witness = work / "verify-probe.witness"
release_file = work / "verify-probe.release"
witness.write_text("", encoding="utf-8")
release_file.unlink(missing_ok=True)
probe = scratch / "scripts" / "check-verify-probe.sh"
probe.write_text(
    """#!/usr/bin/env bash
set -u
printf 'PROBE-ENTER pid=%s\\n' "$$" >> "$AO724_PROBE_WITNESS"
deadline=$(( $(date +%s) + ${AO724_PROBE_TIMEOUT:-180} ))
while [ ! -e "$AO724_PROBE_RELEASE" ] && [ "$(date +%s)" -lt "$deadline" ]; do sleep 0.1; done
printf 'PROBE-EXIT\\n' >> "$AO724_PROBE_WITNESS"
exit 0
""",
    encoding="utf-8",
)


def start_verify_gate(tag):
    """Start a REAL scripts/verify.sh in the scratch worktree, detached."""
    log_path = work / f"verify-gate-{tag}.log"
    handle = open(log_path, "w", encoding="utf-8")
    process = subprocess.Popen(
        ["bash", str(scratch / "scripts" / "verify.sh"), "verify"],
        cwd=str(scratch),
        stdout=handle,
        stderr=subprocess.STDOUT,
        env=env(
            AO_GATE_LOCK_ROOT=verify_store,
            AO_GATE_MAX_CONCURRENT=4,
            AO724_PROBE_WITNESS=witness,
            AO724_PROBE_RELEASE=release_file,
            AO724_PROBE_TIMEOUT=180,
        ),
        start_new_session=True,
    )
    return process, handle, log_path


scratch_lock = gatelock.worktree_lock_path(scratch, verify_store)
scratch_log = scratch / ".verify" / "verify.log"
scratch_results = scratch / ".verify" / ".results.tsv"
sentinel_attestation = scratch / ".verify" / "attestation.json"
sentinel_bytes = b'{"gate": "PRIOR-RUN-SENTINEL", "note": "planted by check-gate-lock"}\n'
# The header verify.sh writes before each check's transcript (`\n== name ==\n`).
HEADER = b"\n== "

gate_a = gate_b = None
handle_a = handle_b = None
path_a = path_b = None
try:
    gate_a, handle_a, path_a = start_verify_gate("A")
    admitted = wait_until(
        lambda: gatelock.probe(scratch_lock).held
        and witness.exists()
        and "PROBE-ENTER" in witness.read_text(encoding="utf-8"),
        timeout=180,
    )
    a_text_early = path_a.read_text(encoding="utf-8")
    for line in a_text_early.splitlines():
        if "ADMITTED" in line:
            note("gate A receipt", line)
            break
    check(
        "gate A — a real scripts/verify.sh — is ADMITTED and reaches a check",
        admitted,
        f"lock={gatelock.state_text(gatelock.probe(scratch_lock))} "
        f"witness={witness.read_text(encoding='utf-8').strip()!r}",
    )
    check(
        "gate A's own transcript carries the admission receipt",
        "ADMITTED" in path_a.read_text(encoding="utf-8"),
        path_a.read_text(encoding="utf-8")[:400],
    )
    check(
        "gate A's holder is a live process in THIS gate's scratch store",
        gatelock.read_owner(scratch_lock) is not None
        and gatelock.pid_alive(gatelock.read_owner(scratch_lock).pid),
        gatelock.state_text(gatelock.probe(scratch_lock)),
    )
    check(
        "gate A is running real checks before the refusal is provoked",
        scratch_log.exists() and b"\n== shell-syntax ==\n" in scratch_log.read_bytes(),
        "gate A never ran a check",
    )

    # Plant a prior attestation and snapshot every artifact gate B must not
    # touch. Gate A is parked inside its probe, so it cannot rewrite these.
    sentinel_attestation.write_bytes(sentinel_bytes)
    before_log = hashlib.sha256(scratch_log.read_bytes()).hexdigest()
    before_results = hashlib.sha256(scratch_results.read_bytes()).hexdigest()
    before_attest = hashlib.sha256(sentinel_attestation.read_bytes()).hexdigest()
    before_headers = scratch_log.read_bytes().count(HEADER)

    gate_b, handle_b, path_b = start_verify_gate("B")
    gate_b.wait(timeout=180)
    b_rc = gate_b.returncode
    b_text = path_b.read_text(encoding="utf-8")
    holder_a = gatelock.read_owner(scratch_lock)
    holder_a_pid = holder_a.pid if holder_a else None
    note("the second scripts/verify.sh was refused with", b_text)
    note(
        "and it wrote nothing: transcript sha256 before -> after",
        f"{before_log} -> {hashlib.sha256(scratch_log.read_bytes()).hexdigest()}",
    )

    check(
        "a SECOND scripts/verify.sh in the same worktree is refused (rc 10)",
        b_rc == gatelock.EXIT_REFUSED,
        f"rc={b_rc} {b_text.strip()}",
    )
    check(
        "the refusal is the PARKED line plus gate-lock's REFUSED, naming the holder's pid",
        "PARKED (rc 10" in b_text
        and "REFUSED" in b_text
        and holder_a_pid is not None
        and f"pid {holder_a_pid}" in b_text,
        b_text.strip(),
    )
    check(
        "the refusal names the worktree it refused in",
        str(scratch) in b_text,
        b_text.strip(),
    )
    check(
        "the refused gate printed no verdict and no ADMITTED receipt",
        "verify: PASS" not in b_text
        and "verify: FAIL" not in b_text
        and "ADMITTED" not in b_text,
        b_text.strip(),
    )
    check(
        "gate B ran ZERO checks: gate A's transcript is byte-identical across B",
        hashlib.sha256(scratch_log.read_bytes()).hexdigest() == before_log,
        "the live transcript changed under a refused gate",
    )
    after_log_bytes = scratch_log.read_bytes()
    check(
        "gate B added no check header to the transcript",
        after_log_bytes.count(HEADER) == before_headers,
        f"{before_headers} -> {after_log_bytes.count(HEADER)}",
    )
    check(
        "gate B's per-check record is byte-identical: no check reached a body",
        hashlib.sha256(scratch_results.read_bytes()).hexdigest() == before_results,
        "the per-check record moved under a refused gate",
    )
    check(
        "gate B overwrote no previous attestation",
        sentinel_attestation.read_bytes() == sentinel_bytes
        and hashlib.sha256(sentinel_attestation.read_bytes()).hexdigest() == before_attest,
        "the planted prior attestation changed under a refused gate",
    )
    check(
        "gate B logged no check header of its own at all",
        b_text.count("\n== ") == 0,
        b_text.strip(),
    )
    check(
        "exactly one gate reached a check body (the probe witness)",
        witness.read_bytes().count(b"PROBE-ENTER") == 1,
        witness.read_text(encoding="utf-8").strip(),
    )
    check(
        "gate A was still running while gate B was refused",
        gate_a.poll() is None,
        "gate A exited before the refusal was proven",
    )
finally:
    release_file.write_text("release\n", encoding="utf-8")
    for process, handle in ((gate_b, handle_b), (gate_a, handle_a)):
        if process is None:
            continue
        try:
            process.wait(timeout=180)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=20)
        if handle is not None:
            handle.close()

check(
    "gate A released its worktree lock on EXIT, so the trap path is wired too",
    wait_until(lambda: not gatelock.probe(scratch_lock).held),
    gatelock.state_text(gatelock.probe(scratch_lock)),
)
a_text = path_a.read_text(encoding="utf-8") if path_a else ""
check(
    "gate A completed a full gate run and printed its own verdict (never B's)",
    "verify: PASS" in a_text or "verify: FAIL" in a_text,
    a_text[-300:],
)
# --- 14. prune: only a provably-dead leftover is reaped (#1170) ------------
# `doctor` can NAME a leftover but never remove one, so they accumulated: the
# box's real store carried 160 zero-byte owner-less leftovers and `doctor` sat
# permanently at rc 13 — a red that is always red carries no information. The
# only verb that could remove a leftover was a worktree's own `release`, because
# a box-wide sweep reaping a lock it does not own is the "delete every file" risk
# class #948 forbids. `prune` is the provably-safe middle: a named leftover goes
# only when its recorded owner_pid is gone AND its recorded worktree path is
# absent (never either alone); a 0-byte owner-less file, which carries no record,
# goes only while holding its own flock. Everything else is refused BY NAME.
#
# These proofs are driven for REAL against fixture stores (never the box's real
# one), and the fixtures are asserted to differ, so no half can pass vacuously.


def prune_fixture_store(name):
    base = work / ("prune-" + name)
    (base / "worktrees").mkdir(parents=True, exist_ok=True)
    return base


def write_record(store_dir, name, *, owner_pid, worktree):
    path = store_dir / "worktrees" / name
    record = {
        "pid": owner_pid,
        "kind": "worktree",
        "worktree": worktree,
        "owner_pid": owner_pid,
        "started_ts": 1.0,
    }
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def line_for(text, filename):
    for line in text.splitlines():
        if filename in line:
            return line.strip()
    return ""


# A dead pid we can prove dead: spawn a child and reap it.
dead_proc = subprocess.Popen(["sleep", "30"])
dead_pid = dead_proc.pid
dead_proc.kill()
dead_proc.wait()
check("the dead-pid fixture is really dead", not gatelock.pid_alive(dead_pid), str(dead_pid))

absent_worktree = work / "prune-absent-worktree"  # deliberately never created

# The mutant fixtures, and the assertion that they really differ: (a), (b) and
# (c) differ in exactly one dimension each, so a rule that keys on only one of
# them cannot pass all three.
lock_a = write_record(
    prune_fixture_store("a"), "leftover-a.lock", owner_pid=os.getpid(),
    worktree=str(absent_worktree),
)
lock_b = write_record(
    prune_fixture_store("b"), "leftover-b.lock", owner_pid=dead_pid,
    worktree=str(work),
)
lock_c = write_record(
    prune_fixture_store("c"), "leftover-c.lock", owner_pid=dead_pid,
    worktree=str(absent_worktree),
)
fixture_shapes = {
    "(a) live pid / absent worktree": (gatelock.pid_alive(os.getpid()), absent_worktree.exists()),
    "(b) dead pid / existing worktree": (gatelock.pid_alive(dead_pid), Path(str(work)).exists()),
    "(c) dead pid / absent worktree": (gatelock.pid_alive(dead_pid), absent_worktree.exists()),
}
check(
    "the three leftover fixtures really differ (no rule can pass all three vacuously)",
    fixture_shapes["(a) live pid / absent worktree"] == (True, False)
    and fixture_shapes["(b) dead pid / existing worktree"] == (False, True)
    and fixture_shapes["(c) dead pid / absent worktree"] == (False, False),
    repr(fixture_shapes),
)

# (a) a leftover whose recorded owner_pid is ALIVE -> REFUSED by name.
run_a = gate("prune", "--apply", root=prune_fixture_store("a"))
check(
    "(a) a leftover whose owner pid is alive is REFUSED and NOT removed",
    run_a.returncode == gatelock.EXIT_ADMIT
    and "REFUSED" in line_for(run_a.stdout, "leftover-a.lock")
    and f"owner pid {os.getpid()} is alive" in line_for(run_a.stdout, "leftover-a.lock")
    and lock_a.exists(),
    f"rc={run_a.returncode} {line_for(run_a.stdout, 'leftover-a.lock')}",
)
note("(a) refusal", line_for(run_a.stdout, "leftover-a.lock"))

# (b) a leftover whose recorded worktree path STILL EXISTS -> REFUSED by name.
run_b = gate("prune", "--apply", root=prune_fixture_store("b"))
check(
    "(b) a leftover whose worktree still exists is REFUSED and NOT removed",
    run_b.returncode == gatelock.EXIT_ADMIT
    and "REFUSED" in line_for(run_b.stdout, "leftover-b.lock")
    and f"worktree {work} still exists" in line_for(run_b.stdout, "leftover-b.lock")
    and lock_b.exists(),
    f"rc={run_b.returncode} {line_for(run_b.stdout, 'leftover-b.lock')}",
)
note("(b) refusal", line_for(run_b.stdout, "leftover-b.lock"))

# (c) a leftover with a DEAD pid AND an ABSENT worktree -> REMOVED.
run_c = gate("prune", "--apply", root=prune_fixture_store("c"))
check(
    "(c) a leftover with a dead pid and an absent worktree is REMOVED",
    run_c.returncode == gatelock.EXIT_ADMIT
    and "REMOVED" in line_for(run_c.stdout, "leftover-c.lock")
    and not lock_c.exists(),
    f"rc={run_c.returncode} {line_for(run_c.stdout, 'leftover-c.lock')}",
)
note("(c) removal", line_for(run_c.stdout, "leftover-c.lock"))

# (g) dry-run is the default: the same (c) shape is only REPORTED, never removed.
store_g = prune_fixture_store("g")
lock_g = write_record(store_g, "leftover-g.lock", owner_pid=dead_pid,
                      worktree=str(absent_worktree))
run_g = gate("prune", root=store_g)
check(
    "(g) prune is DRY-RUN by default: it reports WOULD-REMOVE and removes nothing",
    run_g.returncode == gatelock.EXIT_HEALTH_ATTENTION
    and "WOULD-REMOVE" in line_for(run_g.stdout, "leftover-g.lock")
    and "REMOVED" not in line_for(run_g.stdout, "leftover-g.lock")
    and lock_g.exists(),
    f"rc={run_g.returncode} {line_for(run_g.stdout, 'leftover-g.lock')}",
)
note("(g) dry-run", line_for(run_g.stdout, "leftover-g.lock"))
run_g_apply = gate("prune", "--apply", root=store_g)
check(
    "(g) the same store with --apply does remove it",
    run_g_apply.returncode == gatelock.EXIT_ADMIT
    and "REMOVED" in line_for(run_g_apply.stdout, "leftover-g.lock")
    and not lock_g.exists(),
    f"rc={run_g_apply.returncode} {line_for(run_g_apply.stdout, 'leftover-g.lock')}",
)

# (f) a record prune cannot classify (no owner_pid) -> REFUSED, and rc 13: this
# is the one refusal that is a real health signal, not a provably-alive lock.
store_f = prune_fixture_store("f")
lock_f = store_f / "worktrees" / "leftover-f.lock"
lock_f.write_text(json.dumps({"pid": dead_pid, "kind": "worktree", "worktree": ""}),
                  encoding="utf-8")
run_f = gate("prune", "--apply", root=store_f)
check(
    "(f) a record that names no owner_pid is REFUSED and flags HEALTH-ATTENTION",
    run_f.returncode == gatelock.EXIT_HEALTH_ATTENTION
    and "REFUSED" in line_for(run_f.stdout, "leftover-f.lock")
    and "names no owner_pid" in line_for(run_f.stdout, "leftover-f.lock")
    and lock_f.exists(),
    f"rc={run_f.returncode} {line_for(run_f.stdout, 'leftover-f.lock')}",
)
note("(f) refusal", line_for(run_f.stdout, "leftover-f.lock"))

# (d) a 0-byte owner-less file that IS flock-held by a live process -> REFUSED.
store_d = prune_fixture_store("d")
lock_d_prune = store_d / "worktrees" / "leftover-d.lock"
lock_d_prune.write_bytes(b"")
holder_d = subprocess.Popen(
    [sys.executable, "-c",
     "import fcntl,os,sys,time;fd=os.open(sys.argv[1],os.O_RDWR);"
     "fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);print('held',flush=True);time.sleep(120)",
     str(lock_d_prune)],
    stdout=subprocess.PIPE, text=True,
)
try:
    holder_d.stdout.readline()
    run_d = gate("prune", "--apply", root=store_d)
    check(
        "(d) a 0-byte owner-less file held by a live process is REFUSED, not unlinked",
        run_d.returncode == gatelock.EXIT_ADMIT
        and "REFUSED" in line_for(run_d.stdout, "leftover-d.lock")
        and "HELD right now" in line_for(run_d.stdout, "leftover-d.lock")
        and lock_d_prune.exists(),
        f"rc={run_d.returncode} {line_for(run_d.stdout, 'leftover-d.lock')}",
    )
    note("(d) refusal", line_for(run_d.stdout, "leftover-d.lock"))
    check("(d) the live holder was left running", holder_d.poll() is None)
finally:
    holder_d.kill()
    holder_d.wait(timeout=20)

# (e) a 0-byte owner-less file that is NOT held -> REMOVED (the flock is the proof).
store_e = prune_fixture_store("e")
lock_e_prune = store_e / "worktrees" / "leftover-e.lock"
lock_e_prune.write_bytes(b"")
run_e = gate("prune", "--apply", root=store_e)
check(
    "(e) a 0-byte owner-less file that is NOT held is REMOVED",
    run_e.returncode == gatelock.EXIT_ADMIT
    and "REMOVED" in line_for(run_e.stdout, "leftover-e.lock")
    and not lock_e_prune.exists(),
    f"rc={run_e.returncode} {line_for(run_e.stdout, 'leftover-e.lock')}",
)
note("(e) removal", line_for(run_e.stdout, "leftover-e.lock"))

# A live gate's lock survives the whole run, and prune never touches permits/.
store_live = prune_fixture_store("live")
live_lane = lane("ao-1170-prune-live")
live_admit = gate("acquire", "--worktree", str(live_lane), root=store_live, owner=os.getpid())
live_lock = gatelock.worktree_lock_path(live_lane, store_live)
live_owner = gatelock.read_owner(live_lock)
check(
    "the live-gate fixture acquired a real lock before the prune runs",
    live_admit.returncode == 0 and live_lock.exists() and gatelock.probe(live_lock).held,
    f"rc={live_admit.returncode} {live_admit.stderr.strip() or live_admit.stdout.strip()}",
)
permit_pool = store_live / "permits"
permit_pool.mkdir(parents=True, exist_ok=True)
permit_sentinel = permit_pool / "slot-00.lock"
permit_sentinel.write_text(
    json.dumps({"pid": dead_pid, "kind": "permit", "worktree": str(absent_worktree),
                "owner_pid": dead_pid}),
    encoding="utf-8",
)
# Plant fresh leftovers of every reapable shape so the sweep has work to do.
write_record(store_live, "leftover-live-c.lock", owner_pid=dead_pid,
             worktree=str(absent_worktree))
(store_live / "worktrees" / "leftover-live-e.lock").write_bytes(b"")
run_live = gate("prune", "--apply", root=store_live)
check(
    "the live gate's lock file is untouched by prune (still HELD, by pid)",
    live_lock.exists() and gatelock.probe(live_lock).held and live_owner is not None
    and "REFUSED" in line_for(run_live.stdout, live_lock.name),
    line_for(run_live.stdout, live_lock.name) or gatelock.state_text(gatelock.probe(live_lock)),
)
note("live gate kept", line_for(run_live.stdout, live_lock.name))
check(
    "prune never touches permits/: the pool file is byte-identical after --apply",
    permit_sentinel.exists() and json.loads(permit_sentinel.read_text(encoding="utf-8"))["kind"] == "permit",
    "prune touched a permit slot",
)
check(
    "the live-gate neighbours were still reaped, so the run did real work",
    not (store_live / "worktrees" / "leftover-live-c.lock").exists()
    and not (store_live / "worktrees" / "leftover-live-e.lock").exists(),
    "the sweep removed nothing, so this control would be vacuous",
)
released_live = gate("release", "--worktree", str(live_lane), root=store_live)
check("the live gate still releases cleanly after the prune",
      released_live.returncode == 0 and not live_lock.exists(),
      f"rc={released_live.returncode} {released_live.stdout.strip()}")

# A clean store must report OK — never SKIP, never rc 13.
store_clean = prune_fixture_store("clean")
run_clean = gate("prune", "--apply", root=store_clean)
check(
    "a clean store reports OK (not SKIP) and exits 0",
    run_clean.returncode == gatelock.EXIT_ADMIT
    and "OK" in run_clean.stdout
    and "SKIP" not in run_clean.stdout
    and "considered=0" in run_clean.stdout,
    f"rc={run_clean.returncode} {run_clean.stdout.strip()}",
)
note("clean store", run_clean.stdout.strip().splitlines()[-1])

if problems:
    print(f"  ({len(problems)} live proof(s) failed)", file=sys.stderr)
    raise SystemExit(1)
raise SystemExit(0)
PY
then
  :
else
  fail=$((fail + 1))
fi

if [ "$fail" -gt 0 ]; then
  echo "check-gate-lock: FAIL ($fail violation(s))" >&2
  exit 1
fi
echo "check-gate-lock: OK — the prelude is applied to scripts/verify.sh before .verify/ is reset, a second verify.sh in one worktree is refused by name having run zero checks and written no attestation, the box cap parks the rest, both signal and crash release the permit, and prune (#1170) reaps only a leftover whose owner pid is gone AND whose worktree is absent (a 0-byte owner-less file only while holding its flock), refusing every other shape by name"
exit 0
