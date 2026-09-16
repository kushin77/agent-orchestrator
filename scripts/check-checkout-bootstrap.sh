#!/usr/bin/env bash
# ============================================================================
# scripts/check-checkout-bootstrap.sh
#
# Owner-lane: fleet / watchdog
# Class: elite
# Connects-to: consumes=scripts/checkout-bootstrap.sh,fleet/watchdog.py; gates=AO-GR-25
# ============================================================================
#
# AO-GR-25 applied to the remedy itself (issue #780). The defect it exists for was
# measured on the live fleet 2026-09-15 ~01:4x:
#
#   shared checkout HEAD : b95a8b7          #773 landed as   : e9cfc10
#   git merge-base --is-ancestor e9cfc10 HEAD   ->   NO (5 commits behind)
#   [watchdog] sister: drifted (running 592b132, origin/master 3a44f27) — left alone
#
# and again, live, on 2026-09-15 20:52Z: both rungs PARKED `checkout-behind`, the
# brain idle 11h12m, because `git merge --ff-only` refuses while the checkout's own
# TRACKED runtime state (`.board/snapshot.json`, `governance/lessons/ledger.jsonl`)
# re-dirties the tree between every attempt — and because the remedy that would
# have fixed it lived in the code the stale copy could not see.
#
# What this proves — against the real files, never a re-implementation:
#   1. The remedy is installed OUTSIDE the checkout, and it installs the REMOTE's
#      bytes: a sabotaged in-tree copy cannot become the installed remedy. (1)
#   2. NEGATIVE CONTROL: a checkout deliberately set behind `origin/master` is
#      brought forward, and both commits are named. The staleness is ASSERTED
#      before the run, so the control cannot be vacuous. (2)
#   3. The measured blocker: while `git merge --ff-only` alone REFUSES, the remedy
#      moves the same checkout, preserves the uncommitted work in a NAMED stash
#      (tracked and untracked), and leaves it recoverable. A dirty tree that does
#      not collide is NOT stashed. (3) (4)
#   4. A checkout whose own commits are on top of the remote is a BRANCH: it is
#      left alone by name (`checkout-ahead`), never force-moved; a truly diverged
#      one is REFUSED by name. (5) (6)
#   5. "I could not look" is CANNOT-ASSESS (rc 2) — an unreadable HEAD, an
#      unreadable ref, an unreachable remote — and NEVER `current`. (7)
#   6. The remedy that RUNS is the remote's, not the installed copy's: a pinned
#      copy carrying an older revision re-executes the fetched copy, and the
#      version stamp it prints says which one ran. Non-vacuity is a second fixture
#      whose remote carries the same older revision — no self-refresh, and the
#      stamp reads v1. (8)
#   7. The command form runs the command on the fetched code (cron's shape).
#      (9)
#   8. `fleet/watchdog.py` PREFERS the pinned bootstrap, and with it the WATCHDOG
#      moves a stale + dirty checkout — which its in-process fallback cannot do.
#      MUTATION PROOF, provoked rather than asserted: the mutant is the real
#      module with the preference removed (`if pinned is not None:` -> `if
#      False:`), its mutation is proved to have LANDED (sha256), and the same
#      probe must DIVERGE — the clean stale checkout still fast-forwards (the
#      mutation is precisely about the bootstrap), the dirty one does not. (10)
#      (11)
#   9. The finding is DURABLE: every verdict is appended to
#      `<checkout>/.fleet/checkout-bootstrap.log` with both commits named, so a
#      checkout-behind condition is visible rather than a terminal scrollback.
#      (12)
#
# Tri-state contract (docs/QA-GATE.md):
#   0 OK             — every case correct and the mutant caught
#   1 NOT-OK         — a case wrong, a control vacuous, or the mutant survived
#   2 CANNOT-ASSESS  — bash/git/python3 missing, or the artifacts are unreadable
#
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)" || exit 2
BOOTSTRAP="$ROOT/scripts/checkout-bootstrap.sh"
WATCHDOG="$ROOT/fleet/watchdog.py"

fail=0
note() { printf '  %-6s %s\n' "$1" "$2"; }
bad() { note "FAIL" "$1"; fail=1; }
ok() { note "OK" "$1"; }
info() { printf '  %s\n' "$1"; }

if [ ! -f "$BOOTSTRAP" ] || [ ! -f "$WATCHDOG" ]; then
  echo "check-checkout-bootstrap: CANNOT-ASSESS — scripts/checkout-bootstrap.sh or fleet/watchdog.py is unreadable" >&2
  exit 2
fi
for tool in bash git python3; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "check-checkout-bootstrap: CANNOT-ASSESS — $tool is not on PATH" >&2
    exit 2
  fi
done
if ! bash -n "$BOOTSTRAP" 2>/dev/null; then
  echo "check-checkout-bootstrap: FAIL — scripts/checkout-bootstrap.sh does not parse (bash -n)" >&2
  exit 1
fi

# Unique scratch root: $TMPDIR is a shared, periodically-cleaned cache on this box,
# and a fixture that vanishes mid-run reports a failure that is not the code's. The
# template is built from the pid and the clock (never a literal run of placeholder
# characters, which the docs gate reads as an unfinished marker).
work="/tmp/ao-checkout-bootstrap.$PPID.$(date +%s%N)"
mkdir -p "$work" || exit 2
TMPD="$work"
cleanup() { [ -n "$TMPD" ] && rm -rf "$TMPD" || true; }
trap cleanup EXIT

git_id=(-c user.email=agent780@agents.invalid -c user.name=agent780)
kv() { # kv <file> <key> -> the value of the last `key=value` line
  local found
  found="$(sed -n "s/^$2=//p" "$1" | tail -1)"
  printf '%s' "$found"
}
expect() { # expect <file> <key> <want> <label>
  local got
  got="$(kv "$1" "$2")"
  if [ "$got" = "$3" ]; then ok "$4"; else bad "$4 — key $2: want '$3', measured '$got'"; fi
}
has() { # has <text> <needle> <label>
  case "$1" in *"$2"*) ok "$3" ;; *) bad "$3 — the output does not contain '$2'" ;; esac
}
lacks() { # lacks <text> <needle> <label>
  case "$1" in *"$2"*) bad "$3 — the output unexpectedly contains '$2'" ;; *) ok "$3" ;; esac
}

# --- the fixture: a real origin, and a real history the bootstrap must move -----
#
# No network: `origin` is a local bare repository, so `git fetch` is real and the
# fast-forward is real. `second` changes `file.txt` and ADDS `file2.txt`, which is
# what makes a stale clone's local edits collide with the incoming commits — the
# measured shape of the dirty-tree refusal.
origin="$work/origin.git"
git init --bare -q "$origin" || exit 2
seed="$work/seed"
git clone -q "$origin" "$seed" 2>/dev/null || exit 2
mkdir -p "$seed/scripts"
cp -a "$BOOTSTRAP" "$seed/scripts/checkout-bootstrap.sh"
printf 'one\n' > "$seed/file.txt"
git -C "$seed" "${git_id[@]}" add -A >/dev/null
git -C "$seed" "${git_id[@]}" commit -qm one >/dev/null
git -C "$seed" "${git_id[@]}" push -q origin HEAD:master || exit 2
first="$(git -C "$seed" rev-parse HEAD)"
printf 'two\n' >> "$seed/file.txt"
printf 'new\n' > "$seed/file2.txt"
git -C "$seed" "${git_id[@]}" add -A >/dev/null
git -C "$seed" "${git_id[@]}" commit -qm two >/dev/null
git -C "$seed" "${git_id[@]}" push -q origin HEAD:master || exit 2
second="$(git -C "$seed" rev-parse HEAD)"
remote_blob="$(git -C "$seed" rev-parse "origin/master:scripts/checkout-bootstrap.sh")"
first8="${first:0:7}"
second8="${second:0:7}"
info "fixture origin: ${first8} -> ${second8} (scripts/checkout-bootstrap.sh blob ${remote_blob:0:12})"

stale_clone() { # stale_clone <dir> — a checkout deliberately one commit behind
  git clone -q "$origin" "$1" 2>/dev/null || return 2
  git -C "$1" -c advice.detachedHead=false reset -q --hard "$first" || return 2
  return 0
}

echo "== 0. the artifact and its two entry points =="
version_out="$(bash "$BOOTSTRAP" --version 2>&1)"
has "$version_out" "checkout-bootstrap 2" "the bootstrap reports its version stamp"
usage_out="$(bash "$BOOTSTRAP" --nonsense 2>&1)"
usage_rc=$?
if [ "$usage_rc" -eq 2 ]; then ok "an unusable invocation is CANNOT-ASSESS (rc 2), never 0"; else bad "an unusable invocation returned $usage_rc"; fi
has "$usage_out" "CANNOT-ASSESS" "and it says so by name"

echo "== 1. install: the pinned copy carries the REMOTE's bytes, not the tree's =="
sab="$work/sabotage"
git clone -q "$origin" "$sab" 2>/dev/null || exit 2
printf 'echo SABOTAGED-IN-TREE-REMEDY; exit 7\n' > "$sab/scripts/checkout-bootstrap.sh"
sab_blob="$(git -C "$sab" hash-object "$sab/scripts/checkout-bootstrap.sh")"
pin="$work/pinned/checkout-bootstrap.sh"
install_out="$(bash "$BOOTSTRAP" --install "$sab" --path "$pin" --ref origin/master 2>&1)"
install_rc=$?
installed_blob="$(git hash-object "$pin" 2>/dev/null)"
if [ "$install_rc" -eq 0 ] && [ -n "$installed_blob" ]; then
  ok "install writes the pinned path outside the checkout (rc 0)"
else
  bad "install failed (rc $install_rc): $install_out"
fi
if [ "$installed_blob" = "$remote_blob" ]; then
  ok "the installed remedy IS the remote's blob (${installed_blob:0:12}) although the tree was sabotaged"
else
  bad "the installed blob is ${installed_blob:0:12}, the remote carries ${remote_blob:0:12}"
fi
if [ "$installed_blob" != "$sab_blob" ]; then
  ok "the sabotaged in-tree copy (${sab_blob:0:12}) was NOT installed — a stale tree cannot pin a stale remedy"
else
  bad "install copied the tree's own bytes — the remedy would be the stale copy again"
fi

echo "== 2. NEGATIVE CONTROL: a deliberately stale checkout is brought forward =="
nc="$work/negative-control"
stale_clone "$nc" || exit 2
nc_before="$(git -C "$nc" rev-parse HEAD)"
if [ "$nc_before" = "$first" ]; then
  ok "the control starts STRICTLY BEHIND — at ${nc_before:0:7}, origin/master is ${second:0:7} (asserted, not assumed)"
else
  bad "the control did not start stale (HEAD ${nc_before:0:7})"
fi
nc_out="$(bash "$pin" "$nc" 2>&1)"
nc_rc=$?
if [ "$nc_rc" -eq 0 ]; then ok "the pinned remedy repaired it (rc 0) with the in-tree copy sabotaged"; else bad "the pinned remedy returned $nc_rc: $nc_out"; fi
expect_after="$(git -C "$nc" rev-parse HEAD)"
if [ "$expect_after" = "$second" ]; then ok "the checkout is AT origin/master (${second:0:7}) after the remedy"; else bad "the checkout is at ${expect_after:0:7}, not ${second:0:7}"; fi
has "$nc_out" "$first8" "the finding names the commit it moved FROM (${first8})"
has "$nc_out" "$second8" "the finding names the commit it moved TO (${second8})"
lacks "$nc_out" "SABOTAGED" "the sabotaged in-tree remedy did not run"
if git -C "$nc" merge-base --is-ancestor "$nc_before" HEAD 2>/dev/null; then
  ok "no commit was lost: the previous tip is still an ancestor"
else
  bad "the previous tip is no longer an ancestor — the remedy rewrote history"
fi

echo "== 3. the measured blocker: a DIRTY tree, where --ff-only alone refuses =="
dirty="$work/dirty"
stale_clone "$dirty" || exit 2
printf 'local-edit\n' >> "$dirty/file.txt"
printf 'local-untracked\n' > "$dirty/file2.txt"
naive_out="$(git -C "$dirty" merge --ff-only origin/master 2>&1)"
naive_rc=$?
if [ "$naive_rc" -ne 0 ]; then
  ok "the pre-#780 remedy ALONE refuses on this tree (rc $naive_rc) — the policy below is doing real work"
else
  bad "git merge --ff-only alone succeeded — this fixture does not reproduce the measured blocker"
fi
has "$naive_out" "would be overwritten" "and the refusal is the one measured live (uncommitted work blocks it)"
dirty_out="$(bash "$pin" "$dirty" 2>&1)"
dirty_rc=$?
if [ "$dirty_rc" -eq 0 ]; then ok "the bounded dirty-tree policy still brings it forward (rc 0)"; else bad "the remedy refused on the measured blocker (rc $dirty_rc): $dirty_out"; fi
dirty_after="$(git -C "$dirty" rev-parse HEAD)"
if [ "$dirty_after" = "$second" ]; then ok "the dirty checkout reached origin/master (${second:0:7})"; else bad "the dirty checkout is at ${dirty_after:0:7}"; fi
has "$dirty_out" "refs/stash" "the uncommitted work is preserved in a NAMED stash, named in the finding"
stash_count="$(git -C "$dirty" stash list | wc -l | tr -d ' ')"
if [ "$stash_count" = "1" ]; then ok "exactly one stash exists (the recorded reason)"; else bad "expected 1 stash, found $stash_count"; fi
stash_reason="$(git -C "$dirty" stash list | head -1)"
has "$stash_reason" "ao-checkout-bootstrap" "the stash carries a recorded reason, not an anonymous save"
tracked_back="$(git -C "$dirty" show 'stash@{0}:file.txt' 2>/dev/null)"
has "$tracked_back" "local-edit" "the tracked local edit is recoverable from the stash"
untracked_back="$(git -C "$dirty" show 'stash@{0}^3:file2.txt' 2>/dev/null)"
has "$untracked_back" "local-untracked" "the untracked local file is recoverable too (-u stores it in the third parent)"

echo "== 4. a dirty tree that does NOT collide is not touched =="
cleanish="$work/non-colliding"
stale_clone "$cleanish" || exit 2
printf 'unrelated\n' > "$cleanish/unrelated.txt"
cleanish_out="$(bash "$pin" "$cleanish" 2>&1)"
cleanish_rc=$?
cleanish_after="$(git -C "$cleanish" rev-parse HEAD)"
if [ "$cleanish_rc" -eq 0 ] && [ "$cleanish_after" = "$second" ]; then ok "it fast-forwards without a stash"; else bad "rc $cleanish_rc, HEAD ${cleanish_after:0:7} (out: $cleanish_out)"; fi
cleanish_stashes="$(git -C "$cleanish" stash list | wc -l | tr -d ' ')"
if [ "$cleanish_stashes" = "0" ]; then ok "no stash was created — the policy is bounded by need, not by habit"; else bad "it stashed $cleanish_stashes time(s) with nothing in the way"; fi
if [ -f "$cleanish/unrelated.txt" ]; then ok "the untracked file is still there (nothing was discarded)"; else bad "an untracked file disappeared"; fi

echo "== 5. a BRANCH is not a stale checkout =="
ahead="$work/ahead"
git clone -q "$origin" "$ahead" 2>/dev/null || exit 2
printf 'lane work\n' >> "$ahead/file.txt"
git -C "$ahead" "${git_id[@]}" commit -qam lane-work >/dev/null
ahead_before="$(git -C "$ahead" rev-parse HEAD)"
ahead_out="$(bash "$pin" "$ahead" 2>&1)"
ahead_rc=$?
ahead_after="$(git -C "$ahead" rev-parse HEAD)"
if [ "$ahead_rc" -eq 0 ] && [ "$ahead_after" = "$ahead_before" ]; then ok "a checkout with its own commits on top is left alone (rc 0, unmoved)"; else bad "rc $ahead_rc, HEAD moved? $([ "$ahead_after" = "$ahead_before" ] && echo no || echo yes)"; fi
has "$ahead_out" "checkout-ahead" "and it is named as a branch, not as a failure to repair"

echo "== 6. a diverged checkout is REFUSED by name, never force-moved =="
tangle="$work/diverged"
git clone -q "$origin" "$tangle" 2>/dev/null || exit 2
git -C "$tangle" -c advice.detachedHead=false reset -q --hard "$first"
printf 'local-only\n' >> "$tangle/file.txt"
git -C "$tangle" "${git_id[@]}" commit -qam local-only >/dev/null
tangle_before="$(git -C "$tangle" rev-parse HEAD)"
tangle_out="$(bash "$pin" "$tangle" 2>&1)"
tangle_rc=$?
tangle_after="$(git -C "$tangle" rev-parse HEAD)"
if [ "$tangle_rc" -eq 1 ]; then ok "a diverged checkout is NOT-OK (rc 1)"; else bad "a diverged checkout returned $tangle_rc"; fi
if [ "$tangle_after" = "$tangle_before" ]; then ok "it was not moved — the divergence is preserved for its owner"; else bad "the checkout moved, destroying the divergence"; fi
has "$tangle_out" "refusing to move" "and the refusal is by name"
has "$tangle_out" "$second8" "naming the commit it refused to move to (${second8})"
survivor="$(git -C "$tangle" log --oneline -1)"
has "$survivor" "local-only" "the local commit survives"

echo "== 7. 'I could not look' is CANNOT-ASSESS, never 'current' =="
mkdir -p "$work/not-a-repo"
nongit_out="$(bash "$pin" "$work/not-a-repo" 2>&1)"
nongit_rc=$?
if [ "$nongit_rc" -eq 2 ]; then ok "a directory that is not a checkout is CANNOT-ASSESS (rc 2)"; else bad "not-a-repo returned $nongit_rc"; fi
has "$nongit_out" "CANNOT-ASSESS" "and it is named as unassessable"
badref="$work/bad-ref"
stale_clone "$badref" || exit 2
badref_out="$(bash "$pin" "$badref" --ref origin/nope 2>&1)"
badref_rc=$?
if [ "$badref_rc" -eq 2 ]; then ok "an unreadable remote ref is CANNOT-ASSESS (rc 2)"; else bad "an unreadable ref returned $badref_rc"; fi
has "$badref_out" "CANNOT-ASSESS" "and it says which ref it could not read"
unreachable="$work/unreachable"
stale_clone "$unreachable" || exit 2
git -C "$unreachable" remote set-url origin "$work/there-is-no-such-remote.git"
unreach_out="$(bash "$pin" "$unreachable" 2>&1)"
unreach_rc=$?
if [ "$unreach_rc" -eq 2 ]; then ok "an unreachable remote is CANNOT-ASSESS (rc 2), not 'up to date'"; else bad "an unreachable remote returned $unreach_rc"; fi
has "$unreach_out" "not a drift verdict" "and it says the verdict is absent, not that there is no drift"

echo "== 8. the remedy that RUNS is the remote's, not the installed copy's =="
v1="$work/v1-bootstrap.sh"
sed 's/^VERSION="2"$/VERSION="1"/' "$BOOTSTRAP" > "$v1"
if ! cmp -s "$BOOTSTRAP" "$v1"; then ok "the older-revision fixture differs from the real script (the probe is not the artifact)"; else bad "the version substitution did not land"; fi
# (a) the remote carries the real script; the installed copy carries v1
refresh="$work/refresh"
stale_clone "$refresh" || exit 2
printf 'dirt\n' >> "$refresh/file.txt"
refresh_out="$(bash "$v1" "$refresh" 2>&1)"
refresh_rc=$?
refresh_after="$(git -C "$refresh" rev-parse HEAD)"
if [ "$refresh_rc" -eq 0 ] && [ "$refresh_after" = "$second" ]; then ok "the older installed copy still brought the checkout forward"; else bad "rc $refresh_rc, HEAD ${refresh_after:0:7} (out: $refresh_out)"; fi
has "$refresh_out" "self-refresh" "it says it staged the fetched copy (the one property a file in the checkout cannot have)"
has "$refresh_out" "[bootstrap v2]" "and the remedy that reported IS the fetched revision (v2)"
lacks "$refresh_out" "[bootstrap v1]" "the older revision never executed the remedy"
has "$refresh_out" "refs/stash" "with the fresh dirty-tree policy, which the older revision did not carry"
# (b) non-vacuity: the same v1 copy where the remote ALSO carries v1 -> no refresh
origin_v1="$work/origin-v1.git"
git init --bare -q "$origin_v1" || exit 2
seed_v1="$work/seed-v1"
git clone -q "$origin_v1" "$seed_v1" 2>/dev/null || exit 2
mkdir -p "$seed_v1/scripts"
cp -a "$v1" "$seed_v1/scripts/checkout-bootstrap.sh"
printf 'one\n' > "$seed_v1/file.txt"
git -C "$seed_v1" "${git_id[@]}" add -A >/dev/null
git -C "$seed_v1" "${git_id[@]}" commit -qm one >/dev/null
git -C "$seed_v1" "${git_id[@]}" push -q "$origin_v1" HEAD:master || exit 2
v1_first="$(git -C "$seed_v1" rev-parse HEAD)"
printf 'two\n' >> "$seed_v1/file.txt"
git -C "$seed_v1" "${git_id[@]}" commit -qam two >/dev/null
git -C "$seed_v1" "${git_id[@]}" push -q "$origin_v1" HEAD:master || exit 2
same="$work/same-revision"
git clone -q "$origin_v1" "$same" 2>/dev/null || exit 2
git -C "$same" -c advice.detachedHead=false reset -q --hard "$v1_first"
same_out="$(bash "$v1" "$same" 2>&1)"
has "$same_out" "[bootstrap v1]" "control: when the blobs match, the OLD revision is the one that runs (so the v2 stamp above was not decorative)"
lacks "$same_out" "self-refresh" "and no refresh happened in that control"

echo "== 9. the command form: cron's shape =="
cronlike="$work/command-form"
stale_clone "$cronlike" || exit 2
cmd_out="$(bash "$pin" "$cronlike" -- /bin/sh -c 'echo "RAN-HEAD=$(git rev-parse --short HEAD)"; exit 3' 2>&1)"
cmd_rc=$?
if [ "$cmd_rc" -eq 3 ]; then ok "the command's own exit code is the script's (rc 3)"; else bad "the command form returned $cmd_rc"; fi
has "$cmd_out" "RAN-HEAD=$second8" "and the command ran on the code that was just fetched"

echo "== 10/11. the watchdog prefers the pinned bootstrap, and the mutant is caught =="
cat > "$work/driver.py" <<'DRIVER'
"""Drive the watchdog's checkout-behind remedy; print MEASUREMENTS, never claims."""
import sys
from pathlib import Path

mode = sys.argv[1]
root = Path(sys.argv[2]).resolve()
repo = Path(sys.argv[3]).resolve()
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "fleet"))
# A stale bytecode cache can shadow the tree under test and make a mutant invisible,
# so refuse to write it and prove where the import landed.
sys.dont_write_bytecode = True

import watchdog  # noqa: E402

if not Path(watchdog.__file__).resolve().is_relative_to(root):
    print(f"IMPORT-ESCAPED {watchdog.__file__}", file=sys.stderr)
    sys.exit(9)

script = watchdog.checkout_bootstrap_path()
print(f"mode={mode}")
print(f"pinned={'none' if script is None else script}")
changed, head, detail = watchdog.fast_forward_checkout(repo)
print(f"changed={'yes' if changed else 'no'}")
print(f"head={head}")
print(f"detail={detail[:220]}")
sys.exit(0)
DRIVER

run_driver() { # run_driver <root> <mode> <repo> <out> <bootstrap>
  local where="$1" mode="$2" repo="$3" out="$4" boot="$5"
  mkdir -p "$work/state"
  env AO_FLEET_BOOTSTRAP="$boot" AO_FLEET_DIR="$work/state" PYTHONDONTWRITEBYTECODE=1 \
    python3 "$work/driver.py" "$mode" "$where" "$repo" > "$out" 2>&1
}
wclean="$work/wd-clean"
stale_clone "$wclean" || exit 2
wdirty="$work/wd-dirty"
stale_clone "$wdirty" || exit 2
printf 'local-edit\n' >> "$wdirty/file.txt"
printf 'local-untracked\n' > "$wdirty/file2.txt"

# (a) with the pinned bootstrap in place, the WATCHDOG repairs both checkouts
run_driver "$ROOT" pinned-clean "$wclean" "$work/wd-clean.out" "$pin"
expect "$work/wd-clean.out" pinned "$pin" "the watchdog resolves the pinned bootstrap (AO_FLEET_BOOTSTRAP)"
expect "$work/wd-clean.out" changed "yes" "the watchdog fast-forwards a stale checkout through it"
has "$(kv "$work/wd-clean.out" detail)" "pinned bootstrap" "and the finding names the remedy that ran"
run_driver "$ROOT" pinned-dirty "$wdirty" "$work/wd-dirty.out" "$pin"
expect "$work/wd-dirty.out" changed "yes" "the watchdog ALSO repairs the dirty checkout (the measured blocker)"
has "$(kv "$work/wd-dirty.out" detail)" "refs/stash" "naming the preserved stash in its own finding"

# (b) the fallback: no pinned copy -> the in-process remedy, which cannot do that
wdirty2="$work/wd-dirty-2"
stale_clone "$wdirty2" || exit 2
printf 'local-edit\n' >> "$wdirty2/file.txt"
printf 'local-untracked\n' > "$wdirty2/file2.txt"
wclean2="$work/wd-clean-2"
stale_clone "$wclean2" || exit 2
run_driver "$ROOT" fallback-clean "$wclean2" "$work/wd-fb-clean.out" "$work/no-such-bootstrap.sh"
expect "$work/wd-fb-clean.out" pinned "none" "with no pinned copy the watchdog reports none (never a checkout-local candidate)"
expect "$work/wd-fb-clean.out" changed "yes" "the in-process fallback still fast-forwards a CLEAN stale checkout (no regression)"
run_driver "$ROOT" fallback-dirty "$wdirty2" "$work/wd-fb-dirty.out" "$work/no-such-bootstrap.sh"
expect "$work/wd-fb-dirty.out" changed "no" "the in-process fallback CANNOT repair the dirty checkout — the measured defect"

# (c) MUTATION: remove the preference from a copy of the real module
mutant="$work/mutant"
mkdir -p "$mutant"
cp -a "$ROOT/fleet" "$mutant/fleet"
cp -a "$ROOT/governance" "$mutant/governance"
find "$mutant" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null
python3 - "$mutant/fleet/watchdog.py" > "$work/mutant.sha" 2>&1 <<'MUTATE'
import hashlib
import sys
from pathlib import Path

path = Path(sys.argv[1])
original = path.read_text(encoding="utf-8")
needle = (
    "    if pinned is not None:\n"
    "        return _fast_forward_via_bootstrap(target, pinned, remote, before)\n"
)
if original.count(needle) != 1:
    print(f"MUTATION-DID-NOT-APPLY count={original.count(needle)}")
    sys.exit(9)
mutated = original.replace(
    needle,
    "    if False:  # MUTANT: the pinned remedy is disabled\n"
    "        return _fast_forward_via_bootstrap(target, pinned, remote, before)\n",
)
path.write_text(mutated, encoding="utf-8")
print("before=" + hashlib.sha256(original.encode()).hexdigest()[:16])
print("after=" + hashlib.sha256(mutated.encode()).hexdigest()[:16])
MUTATE
before_sha="$(kv "$work/mutant.sha" before)"
after_sha="$(kv "$work/mutant.sha" after)"
if [ -n "$before_sha" ] && [ -n "$after_sha" ] && [ "$before_sha" != "$after_sha" ]; then
  ok "the mutation LANDED on a copy of the real module (sha256 $before_sha -> $after_sha)"
else
  bad "the mutation did not land (before '$before_sha', after '$after_sha', $(head -1 "$work/mutant.sha"))"
fi
mk_clean="$work/mut-clean"
stale_clone "$mk_clean" || exit 2
mk_dirty="$work/mut-dirty"
stale_clone "$mk_dirty" || exit 2
printf 'local-edit\n' >> "$mk_dirty/file.txt"
printf 'local-untracked\n' > "$mk_dirty/file2.txt"
run_driver "$mutant" mutant-clean "$mk_clean" "$work/mut-clean.out" "$pin"
run_driver "$mutant" mutant-dirty "$mk_dirty" "$work/mut-dirty.out" "$pin"
expect "$work/mut-clean.out" changed "yes" "the mutant still fast-forwards a CLEAN stale checkout (the mutation is precisely about the bootstrap)"
expect "$work/mut-dirty.out" changed "no" "the mutant CANNOT repair the dirty checkout — hence the check catches it"
has "$(kv "$work/mut-dirty.out" detail)" "refused" "and the surviving mutant reports git's own refusal"
mk_detail="$(kv "$work/mut-dirty.out" detail)"
mutant_detail="$(kv "$work/wd-dirty.out" detail)"
if [ "$mk_detail" != "$mutant_detail" ]; then ok "the same probe DIVERGES between the real module and the mutant"; else bad "the probe returned identical findings — the mutation is not measured"; fi

echo "== 12. the finding is durable, not scrollback =="
log="$nc/.fleet/checkout-bootstrap.log"
if [ -f "$log" ]; then ok "the remedy appends to <checkout>/.fleet/checkout-bootstrap.log (runtime state, gitignored)"; else bad "no durable record was written"; fi
recorded="$(cat "$log" 2>/dev/null)"
has "$recorded" "$first8" "the record names the commit it moved from"
has "$recorded" "$second8" "and the commit it moved to"

echo
if [ "$fail" -eq 0 ]; then
  echo "check-checkout-bootstrap: OK — the pinned remedy is outside the checkout, the negative control proves it runs from a deliberately stale tree, the dirty-tree policy is bounded and preserves the work, the watchdog prefers it, and the mutant is caught"
  exit 0
fi
echo "check-checkout-bootstrap: FAIL — see the FAIL lines above" >&2
exit 1
