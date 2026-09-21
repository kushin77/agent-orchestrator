#!/usr/bin/env bash
# check-gate-lock-stale.sh — a zero-byte owner-less gate lock must not wedge
# lane reclaim (issue #948).
#
# The defect this exists for was measured on #619: a worktree lock file left
# behind as 0 bytes (the normal release path truncates the record; a killed
# holder leaves a stale record that the next release truncated) survived
# `gate-lock release` — which printed "RELEASED … (already free)" — and
# `gate-lock status` — which reported the worktree FREE while omitting the file
# entirely. The two disagreed about what "held" means, and the disagreement was
# invisible from one command, so a lane whose close-out was blocked by it could
# never be reclaimed by automation.
#
# The fix has four halves, and each is proven here against the REAL entrypoint
# (`scripts/gate-lock.sh`, the one `scripts/verify.sh` calls) with the store
# pointed at a scratch directory — this gate never touches the box's real
# permit store or a sibling lane's lock state:
#
#   1. a 0-byte owner-less lock file: `release` removes it (the file and the
#      "already free" verdict must agree) and `status` reports the worktree free
#      — agreement, and `acquire` is ADMITTED, never refused;
#   2. a lock held by a LIVE pid: still HELD, still refused, still blocking —
#      the fix is not "delete everything";
#   3. a lock whose pid is dead: reclaimed/reported by name, not silently;
#   4. NEGATIVE CONTROL: the same 0-byte proof is driven against a MUTANT whose
#      reaper is neutralised and whose status hides the leftover — and it must
#      FAIL, because a control that passes on the broken shape is a formality.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-gate-lock-stale.sh
set -uo pipefail

# The gate of record must not leave bytecode caches in the tree it is judging.
export PYTHONDONTWRITEBYTECODE=1

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

required_files=(
  "fleet/gatelock.py"
  "scripts/gate-lock.sh"
  "fleet/tests/test_gatelock.py"
)
for required in "${required_files[@]}"; do
  if [ ! -f "$required" ]; then
    echo "check-gate-lock-stale: FAIL — $required is missing" >&2
    exit 1
  fi
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-gate-lock-stale: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

work="$(mktemp -d /tmp/ao948-stale.XXXXXX)" || {
  echo "check-gate-lock-stale: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
}
trap 'rm -rf "$work"' EXIT

if python3 - "$root" "$work" <<'PY'
"""Live proofs for the #948 leftover-reaping contract.

Every proof drives `scripts/gate-lock.sh`, the entrypoint `scripts/verify.sh`
calls, with `AO_GATE_LOCK_ROOT` pointed at a scratch store, so this gate never
touches the box's real permit store or a sibling lane's lock state.
"""
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

repo = Path(sys.argv[1]).resolve()
work = Path(sys.argv[2]).resolve()
store = work / "store"
lanes = work / "lanes"
lanes.mkdir(parents=True, exist_ok=True)

ENTRY = repo / "scripts" / "gate-lock.sh"
MODULE = repo / "fleet" / "gatelock.py"

sys.path.insert(0, str(repo / "fleet"))
import gatelock  # noqa: E402

if Path(gatelock.__file__).resolve() != MODULE:
    print("  FAIL  the module under test is not", MODULE, file=sys.stderr)
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


def gate(*args, owner=None, timeout=60):
    command = ["bash", str(ENTRY), *args]
    if owner is not None:
        command += ["--owner-pid", str(owner)]
    return subprocess.run(command, capture_output=True, text=True, env=env(), timeout=timeout)


def lane(name):
    path = lanes / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def lock_of(path):
    return gatelock.worktree_lock_path(path, store)


def wait_until(predicate, timeout=15.0):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def reap():
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


import atexit
atexit.register(reap)

# --- 1. a 0-byte owner-less lock file: release reaps it, status agrees -------
wt_a = lane("ao-948-a")
lock_a = lock_of(wt_a)
lock_a.parent.mkdir(parents=True, exist_ok=True)
lock_a.write_bytes(b"")

released = gate("release", "--worktree", str(wt_a))
check(
    "release reports an already-free 0-byte lock as 'already free'",
    released.returncode == 0 and "already free" in released.stdout,
    f"rc={released.returncode} {released.stdout.strip()}",
)
check(
    "release removes the 0-byte lock file it called 'already free'",
    not lock_a.exists(),
    "the file survived an 'already free' release",
)

lock_a.write_bytes(b"")
status_one = gate("status", "--worktree", str(wt_a))
check(
    "status reports the worktree FREE and names the 0-byte leftover",
    status_one.returncode == 0
    and "FREE" in status_one.stdout
    and "0-byte" in status_one.stdout
    and lock_a.name in status_one.stdout,
    status_one.stdout.strip(),
)
admitted = gate("acquire", "--worktree", str(wt_a), owner=os.getpid())
check(
    "a gate is ADMITTED on the leftover, never refused — reclaim is unblocked",
    admitted.returncode == 0,
    f"rc={admitted.returncode} {admitted.stderr.strip() or admitted.stdout.strip()}",
)
gate("release", "--worktree", str(wt_a))
check(
    "the admitted gate's release leaves no file behind",
    not lock_a.exists(),
    "the release path re-created or left the lock file",
)

# --- 2. a lock held by a LIVE pid is still HELD and still blocks ------------
wt_b = lane("ao-948-b")
held_admit = gate("acquire", "--worktree", str(wt_b), owner=os.getpid())
holder_b = gatelock.read_owner(lock_of(wt_b))
check(
    "the first gate in a worktree is admitted",
    held_admit.returncode == 0 and holder_b is not None and gatelock.pid_alive(holder_b.pid),
    f"rc={held_admit.returncode} {held_admit.stderr.strip()}",
)
refused = gate("acquire", "--worktree", str(wt_b), owner=os.getpid())
check(
    "a second gate is still REFUSED while the live pid holds the lock",
    refused.returncode == gatelock.EXIT_REFUSED,
    f"rc={refused.returncode} {refused.stderr.strip()}",
)
check(
    "the live holder's lock file is still there while held",
    lock_of(wt_b).exists() and gatelock.probe(lock_of(wt_b)).held,
    "the fix removed a live holder's lock",
)
held_status = gate("status", "--worktree", str(wt_b))
check(
    "status reports the live worktree HELD, by pid",
    "HELD" in held_status.stdout and f"pid {holder_b.pid}" in held_status.stdout,
    held_status.stdout.strip(),
)
released_b = gate("release", "--worktree", str(wt_b))
check(
    "release frees the live lock and removes its file",
    released_b.returncode == 0 and not lock_of(wt_b).exists(),
    f"rc={released_b.returncode} {released_b.stdout.strip()}",
)

# --- 3. a lock whose pid is dead is reclaimed/reported by name ---------------
wt_c = lane("ao-948-c")
stale_admit = gate("acquire", "--worktree", str(wt_c), "--issue", "948", owner=os.getpid())
holder_c = gatelock.read_owner(lock_of(wt_c))
check(
    "the dead-pid scenario admitted a gate",
    stale_admit.returncode == 0 and holder_c is not None,
    f"rc={stale_admit.returncode} {stale_admit.stderr.strip()}",
)
if holder_c is not None:
    os.kill(holder_c.pid, signal.SIGKILL)
check(
    "the killed holder leaves a STALE record (pid dead, flock free, bytes kept)",
    wait_until(lambda: not gatelock.probe(lock_of(wt_c)).held and gatelock.probe(lock_of(wt_c)).record_bytes > 0),
    gatelock.state_text(gatelock.probe(lock_of(wt_c))),
)
stale_status = gate("status", "--worktree", str(wt_c))
check(
    "status names the stale lock by pid, not silently",
    "STALE" in stale_status.stdout and f"pid {holder_c.pid}" in stale_status.stdout,
    stale_status.stdout.strip(),
)
stale_release = gate("release", "--worktree", str(wt_c))
check(
    "release reaps the stale lock and names the pid it took it from",
    stale_release.returncode == 0
    and f"pid {holder_c.pid}" in stale_release.stdout
    and not lock_of(wt_c).exists(),
    f"rc={stale_release.returncode} {stale_release.stdout.strip()}",
)

# --- 4. NEGATIVE CONTROL: the proof fails on the pre-fix shape ---------------
mutant_dir = work / "mutant"
mutant_dir.mkdir(exist_ok=True)
mutant_path = mutant_dir / "gatelock_mutant.py"
original = MODULE.read_text(encoding="utf-8")

unlink_block = (
    "        try:\n"
    "            os.unlink(path)\n"
    "        except FileNotFoundError:\n"
    "            pass\n"
    "        except OSError as exc:\n"
    "            raise StoreUnusable(f\"cannot remove {path}: {exc.strerror or exc}\") from exc\n"
)
free_leftover_block = '        return "FREE (a 0-byte owner-less lock file is present — release reaps it)"\n'
check(
    "the mutation targets are present in the module under test",
    unlink_block in original and free_leftover_block in original,
    "a mutation target was not found — the negative control would be vacuous",
)
mutated = original.replace(unlink_block, "        pass\n", 1)
mutated = mutated.replace(free_leftover_block, "        return None\n", 1)
check(
    "the mutation applied",
    mutated != original,
    "the mutant is byte-identical to the module under test",
)
mutant_path.write_text(mutated, encoding="utf-8")
shutil.rmtree(mutant_dir / "__pycache__", ignore_errors=True)
sys.path.insert(0, str(mutant_dir))
import gatelock_mutant as mutant  # noqa: E402

if Path(mutant.__file__).resolve() != mutant_path:
    print("  FAIL  the mutant was not imported from the scratch tree", file=sys.stderr)
    raise SystemExit(1)

wt_d = lane("ao-948-d")
lock_d = mutant.worktree_lock_path(wt_d, store)
lock_d.parent.mkdir(parents=True, exist_ok=True)
lock_d.write_bytes(b"")
mutant_release = mutant.release(str(wt_d), root=store)
mutant_status = mutant.status(str(wt_d), root=store)
check(
    "under the mutant the release still leaves the 0-byte file (the proof fails)",
    lock_d.exists(),
    "the negative control no longer reproduces the pre-fix defect",
)
check(
    "under the mutant status reports a bare FREE without naming the leftover",
    "FREE (a 0-byte owner-less" not in mutant_status,
    "the mutant still names the leftover, so the control is vacuous",
)
mutant_reap = mutant._reap_free_lock(lock_d)
check(
    "under the mutant the reaper does not remove a free file",
    lock_d.exists() and mutant_reap is None,
    "the mutant reaper removed the file",
)

if problems:
    print(f"  ({len(problems)} proof(s) failed)", file=sys.stderr)
    raise SystemExit(1)
raise SystemExit(0)
PY
then
  :
else
  echo "check-gate-lock-stale: FAIL — the live proofs returned non-zero" >&2
  exit 1
fi

echo "check-gate-lock-stale: OK — a 0-byte owner-less lock file is reaped by release and named by status, a live holder still blocks, a dead pid is reclaimed by name, and the negative control fails on the pre-fix shape"
exit 0
