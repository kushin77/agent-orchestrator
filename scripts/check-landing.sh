#!/usr/bin/env bash
# check-landing.sh — the landing driver's refusal, proved and not asserted (#764).
#
# THE DEFECT THIS EXISTS FOR
#   Landing a verified lane was a manual sequence, so "done" ended with "…and
#   then a human pushes and opens the PR". The replacement (`make land`) is the
#   one automation whose failure mode is catastrophic: a driver that merges
#   without green evidence, or that merges *again* on a re-run, is worse than no
#   driver at all. Asserting "it refuses" in prose proves nothing, so this gate
#   provokes every refusal against a SCRATCH fixture — a temp git repository with
#   a bare origin, a stub `gh`, a stub contract and a stub closure CLI — and
#   requires the named refusal back. It never touches a real repository.
#
# WHAT IT PROVES (each control can genuinely fail, in both directions)
#   1. static: the declared entry point is `make land` -> scripts/land-lane.sh;
#      the apply opt-in is one place (`--apply` / AO_LAND_APPLY=1) and is not
#      unconditional; the driver contains no `--force`/`--force-with-lease`
#      anywhere; landing automation added no workflow file (GR-15).
#   2. a RED attestation is REFUSED (rc 1) — naming `attestation-not-green` —
#      with nothing pushed, opened, merged or deleted.
#   3. a GREEN attestation that names NO commit is REFUSED (rc 1), naming
#      `attestation-does-not-name-a-commit`: an attestation names a COMMIT.
#   4. a GREEN attestation naming ANOTHER commit is REFUSED (rc 1), naming
#      `attestation-names-another-commit`.
#   5. NO attestation is CANNOT-ASSESS (rc 2) — never a pass.
#   6. a GREEN attestation naming the head is GRANTED in dry run (rc 0): the
#      ordered plan is printed, and nothing remote changes (no push, no PR).
#   7. apply, end to end over the stubs, performs the ordered effects —
#      push -> open PR -> contract -> merge -> delete branch -> closure — with the
#      push proved server-side by the origin's own pre-receive hook and the branch
#      delete proved to follow the merge; then a SECOND run reports the terminal
#      state (rc 0) with no second pull request and no second merge.
#   8. the merge-boundary re-check is real: a contract that writes a *stale*
#      attestation (green, naming the parent commit) is refused AFTER the push,
#      with no merge.
#   9. dry run writes NOTHING: the lane's file listing is identical before and
#      after, and the pre-flight evidence the driver judges is a file the fixture
#      owns (so cases 2-6 share one untouched lane and differ only in evidence).
#
# NON-VACUITY
#   Cases 2-6 run the SAME argv against the SAME untouched lane, and differ only
#   in the attestation file handed to `--attestation`; the gate asserts the lane's
#   tree hash and head commit are identical across them. So a refusal cannot be
#   an artifact of a different fixture, and case 6's grant proves the matcher is
#   not simply always refusing. A refusal that is a Python traceback or an
#   argparse error is rejected by name — "it failed" is not "it refused".
#
# Exit-code contract (the repo's honesty tri-state, issue #28): 0 OK /
# 1 NOT-OK / 2 CANNOT-ASSESS. Offline, deterministic, no network, no containers.
#
# Usage: bash scripts/check-landing.sh
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

cli="governance/landing/cli.py"
entry="scripts/land-lane.sh"
makefile="Makefile"

FAILED=0
fail() { printf '  FAIL  %s\n' "$*" >&2; FAILED=$((FAILED + 1)); }
ok() { printf '  ok    %s\n' "$*"; }
cannot_assess() { printf 'check-landing: CANNOT-ASSESS — %s\n' "$*" >&2; exit 2; }

# --- staging (a gap here is CANNOT-ASSESS: the control cannot be set up) ------
command -v python3 >/dev/null 2>&1 || cannot_assess "python3 not found"
command -v git >/dev/null 2>&1 || cannot_assess "git not found"
for required in "$cli" "$entry" "$makefile" \
                governance/merge/engine.py governance/merge/gate.py governance/merge/model.py \
                scripts/merge-gate.sh governance/lifecycle/cli.py; do
  [ -f "$required" ] || cannot_assess "$required is missing — the landing surface is incomplete"
done

work="/tmp/check-landing.$$.$(date +%s)"
mkdir -p "$work" || cannot_assess "cannot create the scratch root $work"
EVENTS="$work/events.log"
export EVENTS
: > "$EVENTS"

# The fixtures OWN their environment. An ambient GH_PR_STATE (or a leftover
# PATH entry) from a caller would silently change what the stub answers, and a
# control that reads its answer from the caller's shell proves nothing — the
# shape of an exported AO_FLEET_DIR that reds five sibling tests in any worktree.
unset GH_PR_STATE GH_MERGE_COMMIT GH_PR_HEAD CONTRACT_MODE

# --- 1. static assertions ----------------------------------------------------
echo "== check-landing: the declared, code-native path =="
if grep -qE '^land:' "$makefile" && grep -q 'scripts/land-lane.sh' "$makefile"; then
  ok "make land is declared and invokes scripts/land-lane.sh"
else
  fail "$makefile declares no 'land:' target invoking scripts/land-lane.sh"
fi
land_line="$(grep -n '^land:' "$makefile" | head -1 | cut -d: -f1)"
phony_line="$(grep -n '^\.PHONY: land' "$makefile" | head -1 | cut -d: -f1)"
if [ -n "$land_line" ] && [ -n "$phony_line" ]; then
  ok "the land target is declared .PHONY (a file named land cannot shadow it)"
else
  fail "the land target is not declared .PHONY — a stray file named 'land' would shadow it"
fi
apply_lines="$(grep -n 'AO_LAND_APPLY' "$entry" | head -1 | cut -d: -f1)"
optin_line="$(grep -n 'passthru+=(--apply)' "$entry" | head -1 | cut -d: -f1)"
if [ -n "$apply_lines" ] && [ -n "$optin_line" ] && [ "$optin_line" -gt "$apply_lines" ]; then
  ok "the apply opt-in is unconditional-proof: --apply is appended only after the AO_LAND_APPLY test"
else
  fail "$entry appends --apply without gating it on AO_LAND_APPLY — a bare make land would merge"
fi
# Scoped to the driver's ARGUMENTS, parsed from the AST: the module docstrings
# say "no --force" in prose, and a gate that failed on its own documentation
# would be a formality that fired for the wrong reason.
force_problems="$(python3 - <<'PY'
"""Any literal force flag passed as an argument in the landing driver."""
import ast
import pathlib

problems = []
for path in sorted(pathlib.Path("governance/landing").glob("*.py")):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if node.value.startswith("--force") or node.value in ("-f", "-F"):
            problems.append(f"{path}:{node.lineno} passes {node.value!r}")
print("\n".join(problems))
PY
)"
if [ -n "$force_problems" ]; then
  fail "the landing driver passes a force flag as an argument: $force_problems"
else
  ok "no force flag is passed anywhere in the landing driver (AST-parsed, so prose cannot satisfy it)"
fi
if grep -q '"push", "-u", "origin"' governance/landing/ports.py; then
  ok "the only push is 'git push -u origin <branch>' (a rejected push stops the landing)"
else
  fail "the push argv is not the plain 'git push -u origin <branch>'"
fi
if compgen -G ".github/workflows/*land*" >/dev/null; then
  fail "a landing workflow file exists — automation here is code-native make + cron (GR-15)"
else
  ok "no landing workflow file: the automation path is make + scripts/ (GR-15)"
fi
bash "$entry" >/dev/null 2>&1
entry_rc=$?
if [ "$entry_rc" -eq 2 ]; then
  ok "$entry with no issue exits 2 (usage), never a silent default"
else
  fail "$entry with no issue exited $entry_rc, expected 2"
fi

# --- the fixture kit --------------------------------------------------------
write_gh_stub() { # <bin-dir>
  mkdir -p "$1"
  cat > "$1/gh" <<'STUB'
#!/usr/bin/env bash
# Fixture stand-in for `gh`: records every call, answers the landing driver's
# reads and writes, and never reaches GitHub. It is stateful in exactly one way —
# a created pull request stays visible to the next read, which is what lets the
# post-contract re-check read the commit the pull request points at.
printf 'gh %s-%s\n' "$1" "${2:-}" >> "${EVENTS:?}"
state="$(cd "$(dirname "$0")/.." && pwd)"
case "$1 $2" in
  "pr list")
    if [ -f "$state/pr-opened" ] || [ -n "${GH_PR_STATE:-}" ]; then
      # `mergeCommit` is an OBJECT in `gh pr list --json` output — the fixture
      # mirrors the real shape, so a driver that read it wrongly would fail here.
      if [ -n "${GH_MERGE_COMMIT:-}" ]; then merge="{\"oid\":\"$GH_MERGE_COMMIT\"}"; else merge=null; fi
      printf '[{"number":4242,"state":"%s","headRefOid":"%s","baseRefName":"master","mergeCommit":%s,"title":"fixture","url":"https://example.invalid/pull/4242"}]\n' \
        "${GH_PR_STATE:-OPEN}" "${GH_PR_HEAD:-}" "$merge"
    else
      printf '[]\n'
    fi ;;
  "pr create") : > "$state/pr-opened"; printf 'https://example.invalid/pull/4242\n' ;;
  "pr view")   printf '{"state":"MERGED","mergeCommit":{"oid":"%s"}}\n' "${GH_MERGE_COMMIT:-merged-unknown}" ;;
  "pr merge")  printf 'merged (fixture)\n' ;;
  *)           printf '[]\n' ;;
esac
exit 0
STUB
  chmod +x "$1/gh"
}

write_contract_stub() { # <lane>
  mkdir -p "$1/scripts"
  cat > "$1/scripts/merge-gate.sh" <<'STUB'
#!/usr/bin/env bash
# Fixture stand-in for scripts/merge-gate.sh: writes an attestation, and nothing
# else. The real contract (issue #29) is not what this control is testing — the
# landing driver's USE of it is.
printf 'contract AO_PR_NUMBER=%s\n' "${AO_PR_NUMBER:-unset}" >> "${EVENTS:?}"
lane="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$lane/.verify"
head="$(git -C "$lane" rev-parse HEAD)"
case "${CONTRACT_MODE:-green}" in
  stale) commit="$(git -C "$lane" rev-parse HEAD~1)"; result=PASS;   rc=0 ;;
  red)   commit="$head";                            result=NOT-OK; rc=1 ;;
  *)     commit="$head";                            result=PASS;   rc=0 ;;
esac
printf '{"gate":"merge-gate","result":"%s","exit_code":%s,"commit":"%s","branch":"issue-764","timestamp":"fixture"}\n' \
  "$result" "$rc" "$commit" > "$lane/.verify/merge-attestation.json"
exit "$rc"
STUB
}

write_lifecycle_stub() { # <lane>
  mkdir -p "$1/governance/lifecycle"
  cat > "$1/governance/lifecycle/cli.py" <<'STUB'
#!/usr/bin/env python3
"""Fixture stand-in for governance/lifecycle/cli.py (#269) inside a landing fixture.

The landing driver JOINS the real closure CLI; this stub exists so the control
can prove the join — that the step is invoked, in order, with the right issue,
and that its exit code is carried through — without reaching GitHub.
"""
import os
import sys

with open(os.environ["EVENTS"], "a", encoding="utf-8") as handle:
    handle.write("lifecycle " + " ".join(sys.argv[1:]) + "\n")
print("lifecycle-close (fixture): the closure steps, driven by governance/lifecycle/cli.py")
sys.exit(0)
STUB
}

install_pre_receive_hook() { # <bare-origin>
  cat > "$1/hooks/pre-receive" <<'HOOK'
#!/usr/bin/env bash
# Server-side proof that a push actually reached the remote.
while read -r _old new ref; do
  printf 'push-received %s %s\n' "$ref" "$new" >> "${EVENTS:?}"
done
exit 0
HOOK
  chmod +x "$1/hooks/pre-receive"
}

lane_new() { # <dir> — a lane worktree, its bare origin, its stubs
  local dir="$1" origin="$1.origin.git" lane="$1/lane"
  mkdir -p "$lane"
  git init -q --bare "$origin"
  git init -q -b master "$lane"
  git -C "$lane" remote add origin "$origin"
  printf 'base\n' > "$lane/base.txt"
  git -C "$lane" add base.txt
  git -C "$lane" -c user.name=landing-fixture -c user.email=land@example.invalid commit -qm "chore(fixture): base"
  git -C "$lane" push -q origin master
  git -C "$lane" checkout -qb issue-764
  printf 'lane change\n' > "$lane/lane.txt"
  git -C "$lane" add lane.txt
  git -C "$lane" -c user.name=landing-fixture -c user.email=land@example.invalid commit -qm "feat(landing): fixture lane commit"
  write_gh_stub "$dir/bin"
  write_contract_stub "$lane"
  write_lifecycle_stub "$lane"
  install_pre_receive_hook "$origin"
}

lane_head() { git -C "$1/lane" rev-parse HEAD; }
lane_tree_hash() { git -C "$1/lane" rev-parse 'HEAD^{tree}'; }
origin_has_branch() { git -C "$1.origin.git" rev-parse --verify --quiet "refs/heads/$2" >/dev/null; }
lane_listing() { (cd "$1/lane" && git status --porcelain -uall | LC_ALL=C sort); }
event_count() { grep -c "$1" "$EVENTS" 2>/dev/null || true; }

attest() { # <file> <result> <rc> <commit-json>
  mkdir -p "$(dirname "$1")"
  printf '{"gate":"merge-gate","result":"%s","exit_code":%s,"commit":%s,"branch":"issue-764","timestamp":"fixture"}\n' \
    "$2" "$3" "$4" > "$1"
}

run_driver() { # <out-file> <lane> <args…>  -> rc in $?
  local out="$1" lane="$2"; shift 2
  python3 "$cli" land --issue 764 --root "$lane" "$@" > "$out" 2>&1
}

assert_named() { # <out-file> <code> <case>
  if grep -q "$2" "$1"; then
    ok "$3: refused by name ($2)"
  else
    fail "$3: the refusal does not name $2 — output: $(head -3 "$1" | tr '\n' ' ')"
  fi
  if grep -q 'Traceback\|unrecognized arguments\|usage:' "$1"; then
    fail "$3: the refusal is a crash or an argparse error, not a refusal"
  fi
}

assert_no_remote_change() { # <lane> <case>
  if [ "$(event_count 'gh pr-create')" = "0" ] && [ "$(event_count 'gh pr-merge')" = "0" ] \
     && [ "$(event_count 'contract')" = "0" ] && [ "$(event_count 'lifecycle')" = "0" ]; then
    ok "$2: no PR was created, nothing was merged, no contract ran, no closure ran"
  else
    fail "$2: a refusing run performed a mutating call: $(grep -E 'gh pr-create|gh pr-merge|contract|lifecycle' "$EVENTS" | tr '\n' ' ')"
  fi
  if origin_has_branch "$1" issue-764; then
    fail "$2: the lane branch reached the remote — a refusal must not push"
  else
    ok "$2: the remote has no lane branch (the refusal happened before any push)"
  fi
}

before() { # <needle-a> <needle-b> <case>
  local a b
  a="$(grep -n "$1" "$EVENTS" | head -1 | cut -d: -f1)"
  b="$(grep -n "$2" "$EVENTS" | head -1 | cut -d: -f1)"
  if [ -n "$a" ] && [ -n "$b" ] && [ "$a" -lt "$b" ]; then
    ok "$3: '$1' precedes '$2'"
  else
    fail "$3: the order is wrong or an event is missing ('$1'=$a, '$2'=$b)"
  fi
}

# --- 2..6. the refusal controls, over ONE untouched lane ---------------------
echo "== check-landing: the evidence controls (one lane, five attestations) =="
fx="$work/evidence"
lane_new "$fx"
export PATH="$fx/bin:$PATH"
head="$(lane_head "$fx")"
tree_before="$(lane_tree_hash "$fx")"
listing_before="$(lane_listing "$fx")"

attest "$work/att/red.json" "NOT-OK" 1 "\"$head\""
: > "$EVENTS"
run_driver "$work/out-red.txt" "$fx/lane" --attestation "$work/att/red.json"; red_rc=$?
[ "$red_rc" -eq 1 ] || fail "red attestation: rc=$red_rc, expected 1 (NOT-OK)"
assert_named "$work/out-red.txt" "attestation-not-green" "red attestation"
assert_no_remote_change "$fx" "red attestation"

attest "$work/att/unnamed.json" "PASS" 0 "null"
run_driver "$work/out-unnamed.txt" "$fx/lane" --attestation "$work/att/unnamed.json"; unnamed_rc=$?
[ "$unnamed_rc" -eq 1 ] || fail "unnamed attestation: rc=$unnamed_rc, expected 1"
assert_named "$work/out-unnamed.txt" "attestation-does-not-name-a-commit" "green attestation with no commit"
assert_no_remote_change "$fx" "unnamed attestation"

attest "$work/att/other.json" "PASS" 0 "\"$(git -C "$fx/lane" rev-parse HEAD~1)\""
run_driver "$work/out-other.txt" "$fx/lane" --attestation "$work/att/other.json"; other_rc=$?
[ "$other_rc" -eq 1 ] || fail "stale attestation: rc=$other_rc, expected 1"
assert_named "$work/out-other.txt" "attestation-names-another-commit" "green attestation naming another commit"
assert_no_remote_change "$fx" "stale attestation"

run_driver "$work/out-absent.txt" "$fx/lane" --attestation "$work/att/missing.json"; absent_rc=$?
[ "$absent_rc" -eq 2 ] || fail "absent attestation: rc=$absent_rc, expected 2 (CANNOT-ASSESS, never a pass)"
assert_named "$work/out-absent.txt" "attestation-absent" "no attestation"
assert_no_remote_change "$fx" "no attestation"

attest "$work/att/green.json" "PASS" 0 "\"$head\""
run_driver "$work/out-green.txt" "$fx/lane" --attestation "$work/att/green.json"; green_rc=$?
[ "$green_rc" -eq 0 ] || fail "green attestation: rc=$green_rc, expected 0 (grantable in dry run)"
if grep -q 'DRY RUN' "$work/out-green.txt" && grep -q 'gh pr merge <n> --squash' "$work/out-green.txt"; then
  ok "green attestation: granted in dry run, with the ordered plan printed"
else
  fail "green attestation: the dry-run plan is not visible in the output"
fi
assert_no_remote_change "$fx" "green attestation (dry run)"

# non-vacuity: cases 2-6 shared one untouched lane, and differ only in evidence.
tree_after="$(lane_tree_hash "$fx")"
listing_after="$(lane_listing "$fx")"
if [ "$tree_before" = "$tree_after" ] && [ "$listing_before" = "$listing_after" ]; then
  ok "dry run wrote nothing: the lane tree and its file listing are byte-identical across all five cases"
else
  fail "the lane changed during the dry-run controls (tree $tree_before -> $tree_after)"
fi
if [ "$(lane_head "$fx")" = "$head" ]; then
  ok "the lane head is unchanged across the five cases — the refusal is attributable to the evidence alone"
else
  fail "the lane head moved during the controls; the comparison is not controlled"
fi
if grep -q "mergeable=True" "$work/out-green.txt" && ! grep -q "mergeable=True" "$work/out-red.txt"; then
  ok "the merge verdict is consulted and differs by evidence (governance/merge, not a local rule)"
else
  fail "the merge verdict does not distinguish the green from the red case"
fi

# --- 7..8. apply: the order, the server-side push proof, the stale refusal ---
echo "== check-landing: apply over the stubs (order, push proof, stale refusal) =="
ap="$work/apply"
lane_new "$ap"
export PATH="$ap/bin:$PATH"
export GH_PR_HEAD="$(lane_head "$ap")"
export CONTRACT_MODE=green
: > "$EVENTS"
AO_LAND_APPLY=1 bash "$entry" --issue 764 --root "$ap/lane" > "$work/out-apply.txt" 2>&1; apply_rc=$?
[ "$apply_rc" -eq 0 ] || fail "apply: rc=$apply_rc, expected 0 — output: $(tail -5 "$work/out-apply.txt" | tr '\n' ' ')"
if [ "$(event_count "push-received refs/heads/issue-764 $(lane_head "$ap")")" = "1" ]; then
  ok "apply: the lane branch really reached the remote (the origin's own pre-receive hook recorded it)"
else
  fail "apply: no server-side push of the lane branch was recorded"
fi
if [ "$(event_count "contract AO_PR_NUMBER=4242")" = "1" ]; then
  ok "apply: the pre-merge contract ran with the PR context (AO_PR_NUMBER=4242)"
else
  fail "apply: the contract did not run at the PR boundary: $(grep contract "$EVENTS" | tr '\n' ' ')"
fi
before "contract AO_PR_NUMBER=4242" "gh pr-merge" "apply"
before "gh pr-create" "gh pr-merge" "apply"
before "gh pr-merge" "lifecycle close --issue 764" "apply"
before "gh pr-merge" "push-received refs/heads/issue-764 0000" "apply"
if [ "$(event_count "push-received refs/heads/issue-764 0000")" = "1" ] && ! origin_has_branch "$ap" issue-764; then
  ok "apply: the source branch was deleted after the merge (the origin recorded the delete)"
else
  fail "apply: the source branch was not deleted server-side"
fi
if grep -q 'TERMINAL\|landing:' "$work/out-apply.txt"; then
  ok "apply: the run reported its own outcome"
else
  fail "apply: the run printed no landing outcome"
fi

# idempotence: the same lane, now merged, re-landed.
export GH_PR_STATE=MERGED
export GH_MERGE_COMMIT="$(lane_head "$ap")"
: > "$EVENTS"
AO_LAND_APPLY=1 bash "$entry" --issue 764 --root "$ap/lane" > "$work/out-again.txt" 2>&1; again_rc=$?
[ "$again_rc" -eq 0 ] || fail "idempotence: the second run exited $again_rc, expected 0 (a no-op, not an error)"
if grep -q 'TERMINAL' "$work/out-again.txt"; then
  ok "idempotence: the second run reports the terminal state"
else
  fail "idempotence: the second run does not report a terminal state"
fi
if [ "$(event_count 'gh pr-create')" = "0" ] && [ "$(event_count 'gh pr-merge')" = "0" ]; then
  ok "idempotence: the second run created no pull request and merged nothing (no second PR)"
else
  fail "idempotence: the second run created or merged something: $(grep -E 'gh pr-create|gh pr-merge' "$EVENTS" | tr '\n' ' ')"
fi
if [ "$(event_count 'gh pr-list')" -ge 1 ]; then
  ok "idempotence: the second run read the pull request state before deciding (it inspected, then stopped)"
else
  fail "idempotence: the second run did not even read the pull request state"
fi
unset GH_PR_STATE GH_MERGE_COMMIT

# the merge-boundary re-check: a contract that writes a STALE attestation.
st="$work/stale"
lane_new "$st"
export PATH="$st/bin:$PATH"
export GH_PR_HEAD="$(lane_head "$st")"
export CONTRACT_MODE=stale
: > "$EVENTS"
AO_LAND_APPLY=1 bash "$entry" --issue 764 --root "$st/lane" > "$work/out-stale.txt" 2>&1; stale_rc=$?
[ "$stale_rc" -eq 1 ] || fail "stale contract: rc=$stale_rc, expected 1 (refused at the merge boundary)"
assert_named "$work/out-stale.txt" "attestation-names-another-commit" "stale contract output"
if [ "$(event_count 'gh pr-merge')" = "0" ]; then
  ok "stale contract: nothing was merged although the push and the PR had happened"
else
  fail "stale contract: the driver merged on evidence naming another commit"
fi
if [ "$(event_count 'gh pr-create')" = "1" ]; then
  ok "stale contract: the refusal is reported after the push and the PR, not before them (honest detail)"
else
  fail "stale contract: expected exactly one pull request to have been opened"
fi
unset CONTRACT_MODE

# --- summary (a red run names its count; a green run says what it proved) ----
if [ "$FAILED" -gt 0 ]; then
  printf 'check-landing: FAIL (%s finding(s)) — scratch kept at %s\n' "$FAILED" "$work" >&2
  exit 1
fi
rm -rf "$work"
echo "check-landing: OK — the landing driver refuses a lane without green commit-named evidence (and grants one with it), lands in order over the fixture, is idempotent on a terminal lane, and defaults to a dry run that writes nothing"
exit 0
