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
# Nothing here starts a real `make verify`: a held gate is simulated with the
# entrypoint the gate itself calls. Every subprocess gets
# `AO_GATE_LOCK_ROOT` pointed at a scratch directory, so this gate never touches
# the box's real permit store, the shared checkout's `.fleet/`, or a sibling
# lane's lock state.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-gate-lock.sh
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


def env(**overrides):
    merged = dict(os.environ)
    merged["AO_GATE_LOCK_ROOT"] = str(store)
    merged["PYTHONDONTWRITEBYTECODE"] = "1"
    for key, value in overrides.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = str(value)
    return merged


def gate(*args, cap=None, owner=None, timeout=60):
    overrides = {}
    if cap is not None:
        overrides["AO_GATE_MAX_CONCURRENT"] = cap
    command = ["bash", str(ENTRY), *args]
    if owner is not None:
        command += ["--owner-pid", str(owner)]
    return subprocess.run(
        command, capture_output=True, text=True, env=env(**overrides), timeout=timeout
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


def reap():
    """Leave no holder behind, whatever happened above.

    A record can name a pid this process wrote itself, so a reaper that signals
    anything it finds would kill the gate it is running inside.
    """
    protected = {os.getpid(), os.getppid()}
    for candidate in sorted(store.rglob("*.lock")):
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
needle = "def _try_lock(fd: int) -> bool:\n    try:\n"
mutated = original.replace(
    needle, "def _try_lock(fd: int) -> bool:\n    return True\n    try:\n", 1
)
check(
    "the mutation applied to the module under test",
    mutated != original and needle in original,
    "the mutation target was not found — this control would be vacuous",
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
echo "check-gate-lock: OK — a second gate in one worktree is refused by name, the box cap parks the rest, and both signal and crash release the permit"
exit 0
