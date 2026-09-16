#!/usr/bin/env bash
# check-lifecycle-reclaim.sh — the close-out's three measured wedges (#834).
#
# Closing out #287 reached `OK` only after three hand-workarounds, each of which
# made the close-out report NOT-OK for a reason unrelated to the work:
#
#   1. `consume-directive` could not consume a directive that lives only in
#      `.fleet/sent/`, because the invariant read the brain's own mailbox while the
#      remedy operated on the *inbox* — a mailbox a brain-minted order never enters.
#      Repaired by #821, which gave `governance/lifecycle/directive.py` ownership of
#      the `sent` -> `done` move. What was missing is a control at the DRIVER's own
#      step, which is what this gate adds: the close-out's step 4, not the module.
#   2. `_lane_records` kept ONE record per issue and the later-sorted one won, so a
#      dead record (worktree removed) shadowed the live lane and `record-verification`
#      refused `no lane worktree for #287` while the live lane existed, audited clean,
#      and held the verified commit.
#   3. `reclaim-lane` refused a worktree whose only dirty path was machine-managed
#      board state — and the close-out's own step 2 dirties exactly that file, because
#      `make verify` runs `fleet/tests/test_brain.py`, which drives the real brain loop
#      whose `advance_epic_focus()` rewrites the checkout's `.board/focus.json`. The
#      close-out was racy against the fleet that owns the file, and against itself.
#
# Every claim above is PROVOKED here against a real repository — real worktrees, a
# real removed one, the driver's own operations — never asserted:
#
#   * wedge 1: a sent-only order for a landed issue is retired by the driver's step,
#     and an order whose change has NOT landed is refused by name and stays in `sent`
#     (the negative control: the move must not become "consume anything");
#   * wedge 2: the dead record is made the one that SORTS LAST (the accident, not a
#     fixture choice), and the driver must still run the gate in the LIVE worktree and
#     journal the attestation; a lane whose only record has no worktree must be refused
#     naming `worktree-missing`; and reclaiming the lane must leave no record behind;
#   * wedge 3: `isolation cli close` reclaims a lane dirty only in `.board/focus.json`
#     and REPORTS what it ignored; a lane dirty in its own file is still refused, and
#     the refusal names that file;
#   * MUTANTS: each fix is reverted in a copy of the tree and the gate's own controls
#     are required to go RED — the lane selector back to "later record wins", and the
#     machine-managed set emptied. A control whose fix can be removed without a
#     failure is not a control (GR-12).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-lifecycle-reclaim.sh
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

lifecycle_cli="governance/lifecycle/cli.py"
isolation_cli="governance/isolation/cli.py"
suites="scripts/pytest-suites.txt"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-lifecycle-reclaim: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi
if ! command -v git >/dev/null 2>&1; then
  echo "check-lifecycle-reclaim: CANNOT-ASSESS — git not found" >&2
  exit 2
fi
for required in "$lifecycle_cli" "$isolation_cli" "$suites"; do
  if [ ! -f "$required" ]; then
    echo "check-lifecycle-reclaim: FAIL — $required is missing" >&2
    exit 1
  fi
done

fail=0
cannot=0

contains() { # contains <haystack> <needle>
  case "$1" in
    *"$2"*) return 0 ;;
    *) return 1 ;;
  esac
}

ok() { # ok <label>
  echo "  OK    $1"
}

bad() { # bad <label> <why>
  echo "  FAIL  $1 ($2)" >&2
  fail=$((fail + 1))
}

work="/tmp/lifecycle-reclaim.$$.$(date +%s)"
if ! mkdir -p "$work" 2>/dev/null; then
  echo "check-lifecycle-reclaim: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$work"' EXIT

export GIT_CONFIG_GLOBAL=/dev/null

# --- 0. the wedges are declared where the next lane will look for them -------
# The three wedges are the issue's own findings; the contract they belong to must
# be declared, or the next lane rediscovers them. The markers are substance, not
# wording.
declare -a declarations=(
  "governance/lifecycle/cli.py|def select_lane|worktree-missing|dead record"
  "governance/lifecycle/audit.py|worktree-missing|LANE_NOT_RECLAIMED"
  "governance/isolation/worktree.py|MACHINE_MANAGED_PATHS|.board/focus.json|#834"
  "governance/isolation/cli.py|ignored machine-managed state"
)
missing_declarations() {
  local file="$1" marker missing=0
  shift
  for marker in "$@"; do
    if ! grep -qF -- "$marker" "$file"; then
      printf '  FAIL  %s (missing declaration: %s)\n' "$file" "$marker" >&2
      missing=1
    fi
  done
  return "$missing"
}
for entry in "${declarations[@]}"; do
  IFS='|' read -r -a parts <<< "$entry"
  if missing_declarations "${parts[0]}" "${parts[@]:1}"; then
    ok "${parts[0]} declares the wedge it closes"
  else
    fail=$((fail + 1))
  fi
done

# --- 1. the scratch repository, and the module trees ------------------------
# The driver shells out to the repository's own CLIs and to `git worktree`, so the
# scratch repository carries copies of the two modules. The `repo` tree is the
# repository under test; the `mutant` tree is the same copy with one fix reverted.
scratch="$work/repo"
lanes="$work/lanes"
mutant="$work/mutant"
mutant_scratch="$work/mutant-repo"

git init -q -b master "$scratch" >/dev/null 2>&1 || {
  echo "check-lifecycle-reclaim: CANNOT-ASSESS — cannot create a scratch repository" >&2
  exit 2
}
git -C "$scratch" config user.name "Gate Human"
git -C "$scratch" config user.email "gate-human@example.com"
echo seed > "$scratch/seed.txt"
git -C "$scratch" add seed.txt >/dev/null 2>&1
git -C "$scratch" commit -q -m seed >/dev/null 2>&1
mkdir -p "$scratch/.fleet/sent" "$scratch/.fleet/done" "$scratch/.fleet/lanes"

copy_tree() { # copy_tree <destination>
  mkdir -p "$1/governance" "$1/fleet"
  cp -r governance/isolation "$1/governance/isolation"
  cp -r governance/lifecycle "$1/governance/lifecycle"
  # The verify port reads the admission control's exit vocabulary from
  # `fleet/gatelock.py` (#840), so the copy has to carry it: a mutant tree that
  # cannot be imported proves nothing, and this gate would fail for a reason that
  # has nothing to do with the wedge under test.
  cp fleet/gatelock.py "$1/fleet/gatelock.py"
}
copy_tree "$scratch"
copy_tree "$mutant"
git init -q -b master "$mutant_scratch" >/dev/null 2>&1
git -C "$mutant_scratch" config user.name "Gate Human"
git -C "$mutant_scratch" config user.email "gate-human@example.com"
echo seed > "$mutant_scratch/seed.txt"
git -C "$mutant_scratch" add seed.txt >/dev/null 2>&1
git -C "$mutant_scratch" commit -q -m seed >/dev/null 2>&1
mkdir -p "$mutant_scratch/.fleet/sent" "$mutant_scratch/.fleet/done" "$mutant_scratch/.fleet/lanes"

cat > "$work/driver.py" <<'PY'
"""Drive the close-out's own operations against a real repository (#834).

The driver uses the REAL modules from the tree it is pointed at, provisions REAL
worktrees, removes one, and runs the driver's own steps — so what the gate reads
is the behaviour of the code under test, not a description of it.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

tree, scratch, lanes, shim = (Path(argument).resolve() for argument in sys.argv[1:5])
sys.path.insert(0, str(tree))

from governance.isolation.identity import mint  # noqa: E402
from governance.isolation.worktree import provision, write_record  # noqa: E402
from governance.lifecycle.cli import GhOps, _lane_records, lane_view, read_journal, select_lane  # noqa: E402

ISSUE = 834
mounts = scratch.parent / "mounts"
mounts.write_text("/dev/root / ext4 rw,relatime 0 0\n", encoding="utf-8")
os.environ["PATH"] = f"{shim}:{os.environ['PATH']}"

#: The shim records where the gate ran; a stale log would answer for this run.
gate_log = scratch.parent / "make.log"
gate_log.unlink(missing_ok=True)


def emit(key: str, value) -> None:
    print(f"{key}={value}")


def head_of(worktree: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()


# --- wedge 1: the driver's own step retires a sent-only order ---------------
sent = scratch / ".fleet" / "sent"
done = scratch / ".fleet" / "done"
order = sent / "brain-directive-834.json"
order.write_text(json.dumps({"id": "brain-directive-834", "task": {"issue": ISSUE}}), encoding="utf-8")
landed = {"items": [{"issue": ISSUE, "state": "closed", "pr": {"state": "merged"}}]}
try:
    emit("CONSUMED", GhOps(root=scratch, record=landed).consume_directive("brain-directive-834"))
except Exception as exc:  # noqa: BLE001 - the refusal IS the measurement
    emit("CONSUMED", f"REFUSED {type(exc).__name__}: {exc}")
emit("IN_DONE", (done / "brain-directive-834.json").exists())
emit("IN_SENT_AFTER", order.exists())

# the negative control: an order whose change has NOT landed must be refused by
# name and must stay in `sent` — otherwise the move is a "consume anything" verb.
order.write_text(json.dumps({"id": "brain-directive-834", "task": {"issue": ISSUE}}), encoding="utf-8")
live = {"items": [{"issue": ISSUE, "state": "open", "pr": {}}]}
try:
    emit("LIVE_CONSUME", GhOps(root=scratch, record=live).consume_directive("brain-directive-834"))
except Exception as exc:  # noqa: BLE001
    emit("LIVE_CONSUME", f"REFUSED {type(exc).__name__}: {exc}")
emit("LIVE_STILL_SENT", order.exists())

# --- wedge 2: the live lane, when a dead record sorts last -------------------
made = []
for suffix in ("a", "b"):
    identity = mint(ISSUE, f"gate-{suffix}", "wedges", suffix=suffix, worktree_root=lanes)
    provision(identity, scratch, base="HEAD", mounts=mounts)
    write_record(identity, scratch)
    made.append(identity)
live_lane, dead_lane = sorted(made, key=lambda identity: identity.session_id)
subprocess.run(["git", "-C", str(scratch), "worktree", "remove", str(dead_lane.worktree)], check=True, capture_output=True)
records = _lane_records(scratch).get(ISSUE) or []
emit("LANE_RECORDS", len(records))
emit("DEAD_SORTS_LAST", "yes" if dead_lane.session_id > live_lane.session_id else "no")
emit("LIVE", live_lane.session_id)
emit("DEAD", dead_lane.session_id)
emit("LIVE_WORKTREE", live_lane.worktree)
emit("DEAD_WORKTREE", dead_lane.worktree)
emit("CHOSEN", (select_lane(records) or {}).get("session_id", "(none)"))
emit("VIEW_DEAD", ",".join(lane_view(records).get("dead") or []))

head = head_of(live_lane.worktree)
try:
    emit("VERIFY", GhOps(root=scratch).record_verification(ISSUE, head))
except Exception as exc:  # noqa: BLE001
    emit("VERIFY", f"REFUSED {type(exc).__name__}: {exc}")
emit("GATE_RAN_IN", gate_log.read_text(encoding="utf-8").strip() if gate_log.exists() else "(not run)")
try:
    emit("JOURNAL", read_journal(ISSUE, scratch)["verify"]["commit"])
except Exception as exc:  # noqa: BLE001
    emit("JOURNAL", f"(none: {exc})")

# reclaiming the lane must leave no record behind, or the next audit re-reports it.
try:
    emit("RECLAIM", GhOps(root=scratch).reclaim_lane((select_lane(records) or {}).get("session_id", "")))
except Exception as exc:  # noqa: BLE001
    emit("RECLAIM", f"REFUSED {type(exc).__name__}: {exc}")
emit("RECORDS_AFTER", len(_lane_records(scratch).get(ISSUE) or []))
emit("WORKTREE_AFTER", live_lane.worktree.exists())

# the vacuity control: a lane whose ONLY record has no worktree is refused by name.
# "no lane worktree for #N" is what sent #287's lane hunting for a tree it had
# provisioned itself; `worktree-missing` says what actually happened.
sole = mint(ISSUE, "gate-sole", "wedges", suffix="c", worktree_root=lanes)
provision(sole, scratch, base="HEAD", mounts=mounts)
write_record(sole, scratch)
shutil.rmtree(sole.worktree)
try:
    emit("SOLE_VERIFY", GhOps(root=scratch).record_verification(ISSUE, "f" * 40))
except Exception as exc:  # noqa: BLE001
    emit("SOLE_VERIFY", f"REFUSED {type(exc).__name__}: {exc}")
PY

mkdir -p "$work/bin"
cat > "$work/bin/make" <<'SH'
#!/bin/sh
# The gate's stand-in for `make verify`: it records WHERE it ran, which is how the
# control tells the live worktree from the dead one.
echo "$PWD $*" >> "$(dirname "$0")/../make.log"
exit 0
SH
chmod +x "$work/bin/make"
: > "$work/make.log"

run_driver() { # run_driver <tree> <scratch> <lanes> <output-file> -> rc
  ( cd "$1" && env PYTHONDONTWRITEBYTECODE=1 python3 "$work/driver.py" "$1" "$2" "$3" "$work/bin" ) > "$4" 2>&1
}

field() { # field <file> <key>
  sed -n "s/^$2=//p" "$1" | head -1
}

# --- 2. the wedges, provoked against the live tree ---------------------------
echo "== the close-out's wedges, provoked =="
if ! run_driver "$root" "$scratch" "$lanes" "$work/out.txt"; then
  echo "  FAIL  the driver could not run against the repository (rc=$?)" >&2
  sed 's/^/        /' "$work/out.txt" >&2
  fail=$((fail + 1))
else
  # wedge 1
  if contains "$(field "$work/out.txt" CONSUMED)" "consumed 1 directive"; then
    ok "a sent-only order is retired by the driver's own step (wedge 1)"
  else
    bad "a sent-only order is retired by the driver's own step" "$(field "$work/out.txt" CONSUMED)"
  fi
  if [ "$(field "$work/out.txt" IN_DONE)" = "True" ] && [ "$(field "$work/out.txt" IN_SENT_AFTER)" = "False" ]; then
    ok "the order reached .fleet/done/ and left .fleet/sent/"
  else
    bad "the order reached .fleet/done/" "IN_DONE=$(field "$work/out.txt" IN_DONE) IN_SENT=$(field "$work/out.txt" IN_SENT_AFTER)"
  fi
  if contains "$(field "$work/out.txt" LIVE_CONSUME)" "has not landed"; then
    ok "an order whose change has not landed is refused by name (negative control)"
  else
    bad "an order whose change has not landed is refused by name" "$(field "$work/out.txt" LIVE_CONSUME)"
  fi
  if [ "$(field "$work/out.txt" LIVE_STILL_SENT)" = "True" ]; then
    ok "the refused order stayed in .fleet/sent/ — live work cannot be retired"
  else
    bad "the refused order stayed in .fleet/sent/" "it was moved"
  fi

  # wedge 2
  if [ "$(field "$work/out.txt" LANE_RECORDS)" = "2" ] && [ "$(field "$work/out.txt" DEAD_SORTS_LAST)" = "yes" ]; then
    ok "the fixture reproduces the accident: two records, the dead one sorts last"
  else
    bad "the fixture reproduces the accident" "records=$(field "$work/out.txt" LANE_RECORDS) dead-last=$(field "$work/out.txt" DEAD_SORTS_LAST)"
  fi
  if [ "$(field "$work/out.txt" CHOSEN)" = "$(field "$work/out.txt" LIVE)" ]; then
    ok "the live lane is the lane the driver resolves (wedge 2)"
  else
    bad "the live lane is the lane the driver resolves" "chosen=$(field "$work/out.txt" CHOSEN) live=$(field "$work/out.txt" LIVE)"
  fi
  if contains "$(field "$work/out.txt" GATE_RAN_IN)" "$(field "$work/out.txt" LIVE_WORKTREE)" \
    && ! contains "$(field "$work/out.txt" GATE_RAN_IN)" "$(field "$work/out.txt" DEAD_WORKTREE)"; then
    ok "the gate ran in the LIVE worktree, never the dead one"
  else
    bad "the gate ran in the LIVE worktree" "ran in: $(field "$work/out.txt" GATE_RAN_IN)"
  fi
  if [ "$(field "$work/out.txt" JOURNAL)" = "$(git -C "$scratch" rev-parse HEAD)" ]; then
    ok "the attestation is journalled against the verified commit"
  else
    bad "the attestation is journalled against the verified commit" "journal=$(field "$work/out.txt" JOURNAL)"
  fi
  if [ "$(field "$work/out.txt" RECORDS_AFTER)" = "0" ] && [ "$(field "$work/out.txt" WORKTREE_AFTER)" = "False" ]; then
    ok "reclaiming the lane leaves no record behind — the item can reach OK"
  else
    bad "reclaiming the lane leaves no record behind" "records=$(field "$work/out.txt" RECORDS_AFTER) worktree=$(field "$work/out.txt" WORKTREE_AFTER)"
  fi
  if contains "$(field "$work/out.txt" SOLE_VERIFY)" "worktree-missing"; then
    ok "a lane whose only record has no worktree is refused naming worktree-missing"
  else
    bad "a lane whose only record has no worktree is refused naming worktree-missing" "$(field "$work/out.txt" SOLE_VERIFY)"
  fi
fi

# --- 3. wedge 3, through the real isolation CLI ------------------------------
echo "== the reclaim, and the board state it must not refuse on =="
open_lane() { # open_lane <lane-name> <branch-suffix> -> "<sid> <worktree>"
  # One issue = one branch, so a second lane for the same issue must say which
  # session it is (`issue-834-board`) rather than silently share the branch.
  local payload sid wt
  payload="$(python3 "$isolation_cli" open --issue 834 --agent gate --lane "$1" --suffix "$2" \
    --main "$scratch" --root "$lanes" --base HEAD --allow-tmpfs-root 2>/dev/null)" || return 1
  sid="$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin)["identity"]["session_id"])')" || return 1
  wt="$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin)["identity"]["worktree"])')" || return 1
  printf '%s %s\n' "$sid" "$wt"
}

board_lane="$(open_lane board-state-dirty board)" || board_lane=""
if [ -z "$board_lane" ]; then
  echo "  CANNOT-ASSESS  a lane could not be provisioned, so the reclaim was not exercised" >&2
  cannot=$((cannot + 1))
else
  read -r board_sid board_wt <<< "$board_lane"
  # the gate itself dirties this file: commit it, then rewrite it as the fleet does
  mkdir -p "$board_wt/.board"
  printf '{"version": 1, "active_epic": 707}\n' > "$board_wt/.board/focus.json"
  git -C "$board_wt" add .board/focus.json >/dev/null 2>&1
  git -C "$board_wt" commit -q -m "board state" >/dev/null 2>&1
  printf '{"version": 1, "active_epic": 160}\n' > "$board_wt/.board/focus.json"

  out="$(python3 "$isolation_cli" close --session "$board_sid" --main "$scratch" 2>&1)"
  rc=$?
  if [ "$rc" -eq 0 ] && [ ! -d "$board_wt" ] && [ ! -f "$scratch/.fleet/lanes/$board_sid.json" ]; then
    ok "a lane dirty only in machine-managed board state is reclaimed (wedge 3)"
  else
    bad "a lane dirty only in machine-managed board state is reclaimed" "rc=$rc: $out"
  fi
  if contains "$out" "ignored machine-managed state: .board/focus.json"; then
    ok "the reclaim REPORTS what it ignored, instead of discarding it silently"
  else
    bad "the reclaim reports what it ignored" "$out"
  fi
fi

wip_lane="$(open_lane lane-work-dirty wip)" || wip_lane=""
if [ -z "$wip_lane" ]; then
  echo "  CANNOT-ASSESS  a lane could not be provisioned, so the refusal was not exercised" >&2
  cannot=$((cannot + 1))
else
  read -r wip_sid wip_wt <<< "$wip_lane"
  printf 'half-finished\n' > "$wip_wt/wip.txt"
  out="$(python3 "$isolation_cli" close --session "$wip_sid" --main "$scratch" 2>&1)"
  rc=$?
  if [ "$rc" -eq 1 ] && contains "$out" "wip.txt" && [ -d "$wip_wt" ] && [ -f "$scratch/.fleet/lanes/$wip_sid.json" ]; then
    ok "a lane dirty in its own file is refused, and the refusal names the file"
  else
    bad "a lane dirty in its own file is refused and named" "rc=$rc: $out"
  fi
fi

# --- 4. the audit reports a dead record, by name -----------------------------
echo "== the audit names a dead lane record =="
dead_record="$work/dead-record.json"
python3 - "$dead_record" <<'PY'
import json, sys
item = {
    "issue": 834, "title": "wedge", "state": "closed",
    "pr": {"number": 1, "state": "merged", "branch": "issue-834", "head_commit": "a" * 40},
    "branch_deleted": True, "claim": {"agent": None, "live": False},
    "directive": {}, "verify": {"ok": True, "commit": "a" * 40}, "closing_evidence": True,
    "lane": {"session_id": "d9caf9d11ee2", "worktree": "/home/akushnir/ao-worktrees/ao-287-d9caf9d1",
             "present": True, "worktree_exists": False},
}
open(sys.argv[1], "w").write(json.dumps({"scope": "gate fixture", "items": [item]}, indent=2) + "\n")
PY
printf '{"version": 1, "quarantine": []}\n' > "$work/empty-baseline.json"
out="$(python3 "$lifecycle_cli" audit --record "$dead_record" --baseline "$work/empty-baseline.json" 2>&1)"
rc=$?
if [ "$rc" -eq 1 ] && contains "$out" "LANE_NOT_RECLAIMED" && contains "$out" "worktree-missing"; then
  ok "a dead lane record is a finding, named worktree-missing"
else
  bad "a dead lane record is a finding" "rc=$rc: $out"
fi

# --- 5. the mutants: each fix removed, and the controls must go RED ----------
# A control whose fix can be removed without a failure is not a control.
echo "== mutants (each fix reverted; the controls must fail) =="

python3 - "$mutant/$lifecycle_cli" <<'PY'
import sys, pathlib
path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
start = text.index("def select_lane(")
end = text.index("def lane_view(")
mutant = (
    "def select_lane(records: list[dict] | None, commit: str = \"\") -> dict | None:\n"
    '    """MUTANT (#834): the pre-fix accident — one record per issue, later sorted wins."""\n'
    "    candidates = list(records or [])\n"
    "    if not candidates:\n"
    "        return None\n"
    "    return candidates[-1]\n\n\n"
)
path.write_text(text[:start] + mutant + text[end:], encoding="utf-8")
print("  OK    the lane selector is reverted to the pre-fix accident (later record wins)")
PY

if ! run_driver "$mutant" "$mutant_scratch" "$work/mutant-lanes" "$work/mutant-out.txt"; then
  echo "  FAIL  the mutated driver could not run at all (a mutation that breaks the tree proves nothing)" >&2
  sed 's/^/        /' "$work/mutant-out.txt" >&2
  fail=$((fail + 1))
elif [ "$(field "$work/mutant-out.txt" CHOSEN)" = "$(field "$work/mutant-out.txt" DEAD)" ] \
  && contains "$(field "$work/mutant-out.txt" VERIFY)" "worktree-missing"; then
  ok "with the selector reverted the driver picks the DEAD record and the verification is refused"
else
  bad "with the selector reverted the driver picks the dead record" \
    "chosen=$(field "$work/mutant-out.txt" CHOSEN) dead=$(field "$work/mutant-out.txt" DEAD) verify=$(field "$work/mutant-out.txt" VERIFY)"
fi

python3 - "$mutant/governance/isolation/worktree.py" <<'PY'
import sys, pathlib
path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
before = 'MACHINE_MANAGED_PATHS: tuple[str, ...] = (".board/focus.json",)'
after = "MACHINE_MANAGED_PATHS: tuple[str, ...] = ()"
if before not in text:
    raise SystemExit("the declared machine-managed set moved; the mutant cannot apply")
path.write_text(text.replace(before, after), encoding="utf-8")
print("  OK    the machine-managed set is emptied (the pre-fix default, for #834)")
PY

mutant_lane="$(python3 "$mutant/$isolation_cli" open --issue 834 --agent gate --lane mutant-board --suffix mutant \
  --main "$scratch" --root "$lanes" --base HEAD --allow-tmpfs-root 2>/dev/null)" || mutant_lane=""
if [ -z "$mutant_lane" ]; then
  echo "  CANNOT-ASSESS  the mutant lane could not be provisioned, so the mutant reclaim was not exercised" >&2
  cannot=$((cannot + 1))
else
  read -r mut_sid mut_wt <<< "$(printf '%s' "$mutant_lane" | python3 -c 'import json,sys; d=json.load(sys.stdin)["identity"]; print(d["session_id"], d["worktree"])')"
  mkdir -p "$mut_wt/.board"
  printf '{"version": 1, "active_epic": 707}\n' > "$mut_wt/.board/focus.json"
  git -C "$mut_wt" add .board/focus.json >/dev/null 2>&1
  git -C "$mut_wt" commit -q -m "board state" >/dev/null 2>&1
  printf '{"version": 1, "active_epic": 160}\n' > "$mut_wt/.board/focus.json"
  out="$(python3 "$mutant/$isolation_cli" close --session "$mut_sid" --main "$scratch" 2>&1)"
  rc=$?
  if [ "$rc" -eq 1 ] && contains "$out" "uncommitted work" && [ -d "$mut_wt" ]; then
    ok "with the machine-managed set emptied the same lane is refused (the control depends on the fix)"
  else
    bad "with the machine-managed set emptied the same lane is refused" "rc=$rc: $out"
  fi
fi

if [ "$cannot" -gt 0 ]; then
  echo "check-lifecycle-reclaim: CANNOT-ASSESS ($cannot control(s) could not be measured)" >&2
  exit 2
fi
if [ "$fail" -gt 0 ]; then
  echo "check-lifecycle-reclaim: FAIL ($fail violation(s))" >&2
  exit 1
fi
echo "check-lifecycle-reclaim: OK — every wedge is provoked against a real repository, and each fix is proved load-bearing by a mutant"
exit 0
