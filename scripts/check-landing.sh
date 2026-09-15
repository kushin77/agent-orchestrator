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
#  10. the attribution is a MEASUREMENT, in both directions and in the open: a
#      suite failing in the lane AND on clean master is attributed by name and
#      landed; the same lane against a baseline that passes it is REFUSED by
#      name; a suite absent from the baseline is refused (no grandfathering); a
#      red `verify` is refused even though every suite red is pre-existing; and a
#      red with no measured baseline is CANNOT-ASSESS, never a grant.
#  11. the comparison cannot be a constant: the gate PROVOKES both rewrites
#      (always-grant, always-refuse) against a scratch copy of the module and
#      requires each to change the answer, asserting the mutated module's
#      `__file__` so a vacuous import cannot fake it.
#  12. the driver consumes it end to end over the stubs — landing the
#      attributable lane, refusing the lane-caused one, refusing the red
#      `verify`, and declaring the measured red in the PR body in the shape the
#      repo's OWN scripts/check-pr-contract.sh accepts (consumed, not duplicated).
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
# AO_LAND_APPLY belongs here for the same reason, and is the sharpest case: the
# driver is invoked as `AO_LAND_APPLY=1` in production, so that is exactly the
# variable set when this check runs inside the contract's own verify. Inheriting
# it turned every dry-run control below into an apply run — rc 0 where 1 or 2 was
# required, mutating calls recorded, no plan printed — and so reddened the gate
# of record for the very lane that added it.
unset GH_PR_STATE GH_MERGE_COMMIT GH_PR_HEAD CONTRACT_MODE AO_LAND_APPLY

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
# The real contract writes its OWN per-signal record (the `checks` array). The
# attribution reads it to decide which red may be re-scored — so the fixture can
# ask for it with CONTRACT_SIGNALS="verify=0,drift=0,tests=1,...".
if [ -n "${CONTRACT_SIGNALS:-}" ]; then
  python3 - "$lane/.verify/merge-attestation.json" "$CONTRACT_SIGNALS" <<'PY'
import json
import sys

path, spec = sys.argv[1], sys.argv[2]
payload = json.load(open(path, encoding="utf-8"))
payload["checks"] = [
    {"name": name, "rc": int(rc), "status": "OK" if int(rc) == 0 else "NOT-OK"}
    for name, _, rc in (part.partition("=") for part in spec.split(","))
]
with open(path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle)
    handle.write("\n")
PY
fi
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

# A sweep record in the exact shape scripts/run-pytest-suites.sh writes (#29).
# Rows are <suite>=<STATUS> pairs, so a control states its failing set literally.
sweep() { # <file> <sha> <suite=STATUS>…
  python3 - "$1" "$2" "${@:3}" <<'PY'
import json
import sys

out, sha, rows = sys.argv[1], sys.argv[2], sys.argv[3:]
suites = []
for row in rows:
    name, _, status = row.partition("=")
    suites.append({"suite": name, "status": status, "rc": 0 if status == "OK" else 1, "detail": ""})
with open(out, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "gate": "pytest-suites",
            "sha": sha,
            "declared": len(suites),
            "auto_registered": 0,
            "passed": 0,
            "failed": 0,
            "no_verdict": 0,
            "suites": suites,
        },
        handle,
    )
    handle.write("\n")
PY
}

# A merge attestation carrying the contract's own per-signal record (#29).
attest_signals() { # <file> <commit> <verify> <drift> <tests> <negative> <policy>
  python3 - "$@" <<'PY'
import json
import sys

out, commit, *rest = sys.argv[1:]
names = ("verify", "drift", "tests", "negative-controls", "policy-schema")
codes = [int(value) for value in rest]
rc = 1 if any(codes) else 0
with open(out, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "gate": "merge-gate",
            "result": "NOT-OK" if rc else "PASS",
            "exit_code": rc,
            "commit": commit,
            "branch": "issue-764",
            "timestamp": "fixture",
            "checks": [
                {"name": name, "rc": code, "status": "OK" if code == 0 else "NOT-OK"}
                for name, code in zip(names, codes)
            ],
        },
        handle,
    )
    handle.write("\n")
PY
}

run_driver() { # <out-file> <lane> <args…>  -> rc in $?
  local out="$1" lane="$2"; shift 2
  # Every caller is a DRY-RUN scenario, so the apply opt-in is stripped for the
  # child whether or not the caller's shell exported it (see the hygiene note
  # above). The mode is then asserted on the banner: a run that reaches `land:`
  # and is not a dry run is a wrong-mode run, and saying so once beats the six
  # confusing downstream failures it causes (#764).
  env -u AO_LAND_APPLY python3 "$cli" land --issue 764 --root "$lane" "$@" > "$out" 2>&1
  local rc=$?
  if grep -q '^land: ' "$out" && ! grep -q 'mode=DRY RUN' "$out"; then
    fail "wrong mode: expected a dry run — $(grep -m1 '^land: ' "$out")"
  fi
  return "$rc"
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

# --- 9. the wiring: the driver CONSUMES the attribution ----------------------
echo "== check-landing: pre-existing suite reds are attributed by measurement =="
attr_module="governance/landing/attribution.py"
cli_dir="governance/landing"
if [ -f "$attr_module" ]; then
  ok "the attribution module exists ($attr_module)"
else
  fail "$attr_module is missing — the driver has no way to measure a pre-existing red"
fi
# Structural half of the wiring proof. The #724 lesson is exact here: a control
# that only drives the attribution CLI cannot see that the DRIVER never calls it,
# and a control that only reads the driver cannot see that the CLI is inert. Both
# halves are asserted, and the functional half is below.
if grep -q 'from governance.landing import attribution as attribution_mod' "$cli_dir/engine.py"; then
  ok "the driver imports the attribution module (not a look-alike)"
else
  fail "governance/landing/engine.py does not import the attribution module — a measurement nobody runs is not a gate"
fi
if grep -q '"attribution"' "$cli_dir/engine.py" && grep -q 'pre_existing' "$cli_dir/engine.py"; then
  ok "the driver records the attributed set on the result (pre_existing), so it cannot be dropped silently"
else
  fail "the driver does not carry the attributed suite names in its result"
fi
if grep -q '"--baseline"' "$cli_dir/cli.py" && grep -q 'AO_LAND_BASELINE_RECORD' "$attr_module"; then
  ok "a recorded baseline can be supplied (--baseline / AO_LAND_BASELINE_RECORD) and is measured otherwise"
else
  fail "there is no seam to supply a recorded baseline, so the controls could not stay offline"
fi

# --- 10. the attribution, both directions, in the open -----------------------
# A lane, its origin and its stubs; its commit carries the lane's ticket trailer
# so the contract's own body check can be run against the body the driver writes.
at="$work/pre-existing"
lane_new "$at"
export PATH="$at/bin:$PATH"
git -C "$at/lane" -c user.name=landing-fixture -c user.email=land@example.invalid commit --amend -qm \
  "feat(landing): fixture lane commit

Refs kushin77/agent-orchestrator#764"
at_head="$(lane_head "$at")"
mkdir -p "$at/lane/.verify"
# The lane's own sweep: two suites red. The baselines differ ONLY in which of
# them is red on clean master — so the verdict must differ, and a comparison
# replaced by a constant cannot produce both answers.
sweep "$at/lane/.verify/test-results.json" "$at_head" "governance/modules=FAIL" "telemetry/chat=FAIL" "portal=OK"
sweep "$at/master-agrees.json" "$at_head" "governance/modules=FAIL" "telemetry/chat=FAIL" "portal=OK"
sweep "$at/master-passes-chat.json" "$at_head" "governance/modules=FAIL" "portal=OK"
sweep "$at/master-plus-new.json" "$at_head" "governance/modules=FAIL" "telemetry/chat=FAIL" "portal=OK"

attest_signals "$at/lane/.verify/merge-attestation.json" "$at_head" 0 0 1 0 0

attribution_run() { # <out> <baseline>
  python3 -m governance.landing.attribution status --root "$at/lane" --commit "$at_head" \
    --baseline "$2" --json > "$1" 2>&1
  echo $?
}

out="$work/attrib-attributable.json"
rc="$(attribution_run "$out" "$at/master-agrees.json")"
if [ "$rc" = "0" ] && grep -q '"grant": true' "$out" && grep -q 'governance/modules' "$out" \
   && grep -q 'telemetry/chat' "$out"; then
  ok "attributable: the lane's two reds are BOTH failing on clean master — granted, and both NAMED (rc 0)"
else
  fail "attributable: expected a grant naming both suites (rc=$rc): $(head -c 400 "$out")"
fi
if grep -q '"lane_caused": \[\]' "$out"; then
  ok "attributable: nothing is named lane-caused, so the grant is not a blanket waiver"
else
  fail "attributable: the grant names a lane-caused suite"
fi

out="$work/attrib-lane-caused.json"
rc="$(attribution_run "$out" "$at/master-passes-chat.json")"
if [ "$rc" = "1" ] && grep -q '"grant": false' "$out" && grep -q '"lane_caused": \[' "$out" \
   && grep -q 'telemetry/chat' "$out"; then
  ok "LANE-CAUSED: a suite that fails here and PASSES on clean master is REFUSED BY NAME (rc 1)"
else
  fail "lane-caused: expected a named refusal (rc=$rc): $(head -c 400 "$out")"
fi
if grep -q 'governance/modules' "$out"; then
  ok "lane-caused: the pre-existing red is still reported alongside the refusal (never silently dropped)"
else
  fail "lane-caused: the refusal dropped the pre-existing red from its report"
fi

out="$work/attrib-new-suite.json"
# A suite the lane fails that the baseline does not even report -> not grandfathered.
sweep "$at/lane-new.json" "$at_head" "governance/modules=FAIL" "governance/landing=FAIL"
python3 -m governance.landing.attribution status --root "$at/lane" --commit "$at_head" \
  --lane-record "$at/lane-new.json" --baseline "$at/master-agrees.json" --json > "$out" 2>&1
rc=$?
if [ "$rc" = "1" ] && grep -q 'governance/landing' "$out" && grep -q '"grant": false' "$out"; then
  ok "no grandfathering: a NEW failing suite absent from the baseline is refused immediately (rc 1)"
else
  fail "no grandfathering: a newly failing suite was absorbed (rc=$rc): $(head -c 400 "$out")"
fi

out="$work/attrib-red-verify.json"
attest_signals "$at/att-red-verify.json" "$at_head" 1 0 1 0 0
cp "$at/att-red-verify.json" "$at/lane/.verify/merge-attestation.json"
rc="$(attribution_run "$out" "$at/master-agrees.json")"
if [ "$rc" = "1" ] && grep -q 'verify' "$out"; then
  ok "the gate of record stays strict: a red verify is refused even though every suite red is pre-existing (rc 1)"
else
  fail "red verify: expected a refusal naming verify (rc=$rc): $(head -c 400 "$out")"
fi
if grep -q 'master-agrees.json' "$out"; then
  fail "red verify: a sweep was consulted for a signal that is never attributable"
else
  ok "red verify: refused from the contract's own signal record, before any sweep was read"
fi

out="$work/attrib-no-baseline.json"
attest_signals "$at/lane/.verify/merge-attestation.json" "$at_head" 0 0 1 0 0
python3 -m governance.landing.attribution status --root "$at/lane" --commit "$at_head" \
  --baseline "$at/no-such-baseline.json" --json > "$out" 2>&1
rc=$?
if [ "$rc" = "2" ] && grep -q '"grant": false' "$out"; then
  ok "a red with no measured baseline is CANNOT-ASSESS (rc 2), never a grant"
else
  fail "no baseline: expected CANNOT-ASSESS (rc=$rc): $(head -c 400 "$out")"
fi

# --- 11. the comparison is a measurement, provoked ---------------------------
# The strongest form of "not a claim" is a MUTANT: replace the comparison with a
# constant and require the control above to change its answer. Both constant
# directions are provoked, so neither an always-grant nor an always-refuse
# rewrite can survive, and the module under test is proven by __file__ to be the
# mutant copy rather than the real one (a vacuous import would fake this).
prove_mutant() { # <name> <python-replacement-src> <python-replacement-dst> <baseline> <expected-rc> <expected-rc-mutant>
  local name="$1" src="$2" dst="$3" baseline="$4" want="$5" mutant_want="$6"
  local mut="$work/mutant-$name"
  mkdir -p "$mut/governance/landing" "$mut/governance/merge"
  cp "$cli_dir/__init__.py" "$cli_dir/attribution.py" "$cli_dir/evidence.py" "$mut/governance/landing/"
  cp governance/merge/gate.py "$mut/governance/merge/"
  if ! python3 - "$mut/governance/landing/attribution.py" "$src" "$dst" <<'PY'
import sys

path, src, dst = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(path, encoding="utf-8").read()
if text.count(src) != 1:
    print(f"the mutation target appears {text.count(src)} times, not once", file=sys.stderr)
    raise SystemExit(1)
open(path, "w", encoding="utf-8").write(text.replace(src, dst))
PY
  then
    fail "$name: the mutation could not be applied — the control is inert"
    return
  fi
  local resolved
  resolved="$(cd "$work" && PYTHONPATH="$mut" python3 -c 'import governance.landing.attribution as a; print(a.__file__)')"
  if [ "${resolved#"$mut"}" = "$resolved" ]; then
    fail "$name: the mutated module did not resolve under $mut (got $resolved) — the control would be vacuous"
    return
  fi
  local out="$work/mutant-$name.json" rc
  # (1) the UNMUTATED module, on exactly the fixture the mutant will see: the
  # answer the control claims. Asserted, not assumed — otherwise "the mutant
  # changed the answer" is unfalsifiable.
  python3 -m governance.landing.attribution status --root "$at/lane" --commit "$at_head" \
    --baseline "$baseline" --json > "$out.real" 2>&1
  rc=$?
  if [ "$rc" != "$want" ]; then
    fail "$name: the unmutated module answered rc=$rc, expected rc=$want — the control's premise is wrong"
    return
  fi
  # (2) the mutant, resolved from the scratch tree (proven by __file__ above).
  (cd "$work" && PYTHONPATH="$mut" python3 "$mut/governance/landing/attribution.py" status \
     --root "$at/lane" --commit "$at_head" --baseline "$baseline" --json) > "$out" 2>&1
  rc=$?
  if [ "$rc" = "$mutant_want" ]; then
    ok "$name: the same lane + the same baseline give rc $want unmutated and rc $rc mutated — the verdict is read from the baseline, not asserted"
  else
    fail "$name: expected the mutant to answer rc=$mutant_want, got rc=$rc ($(head -c 200 "$out"))"
  fi
}

# rewrite the comparison to "everything is attributable" (a constant grant)
prove_mutant constant-grant \
  'lane_caused = tuple(sorted(lane.failures() - baseline.failures()))' \
  'lane_caused = tuple()' \
  "$at/master-passes-chat.json" 1 0

# rewrite the comparison to "nothing can pass" (a constant refuse)
prove_mutant constant-refuse \
  'lane_caused = tuple(sorted(lane.failures() - baseline.failures()))' \
  'lane_caused = tuple(sorted(lane.failures()))' \
  "$at/master-agrees.json" 0 1

# --- 12. the driver consumes it, end to end ---------------------------------
export GH_PR_HEAD="$at_head"
rm -f "$at/lane/.verify/merge-attestation.json"
export CONTRACT_MODE=red
export CONTRACT_SIGNALS="verify=0,drift=0,tests=1,negative-controls=0,policy-schema=0"
: > "$EVENTS"
AO_LAND_APPLY=1 bash "$entry" --issue 764 --root "$at/lane" --baseline "$at/master-agrees.json" \
  > "$work/out-attributed.txt" 2>&1; attr_rc=$?
if [ "$attr_rc" -eq 0 ] && grep -q 'landing: MERGED' "$work/out-attributed.txt"; then
  ok "the driver LANDS a lane whose contract is red only on measured pre-existing suites (rc 0, MERGED)"
else
  fail "attributed landing: rc=$attr_rc (expected 0): $(tail -c 500 "$work/out-attributed.txt")"
fi
if grep -q 'pre-existing red (measured on clean master' "$work/out-attributed.txt" \
   && grep -q 'governance/modules' "$work/out-attributed.txt"; then
  ok "the attributed suites are REPORTED BY NAME in the landing output"
else
  fail "the landing output does not name the pre-existing reds"
fi
body="$at/lane/.verify/landing-764-pr-body.md"
if [ -f "$body" ] && grep -qE '^Reproduce:[[:space:]]*`[^`]+`' "$body" && grep -q '^```' "$body" \
   && grep -q 'governance/modules' "$body"; then
  ok "the PR body DECLARES the red in the repo's own '## Pre-existing red' shape (a Reproduce: line + a fenced block)"
else
  fail "the PR body does not declare the pre-existing red in the contract's shape"
fi
# ...and the repo's EXISTING mechanism accepts that declaration: the real
# check-pr-contract gate is run against the body the driver wrote.
if bash scripts/check-pr-contract.sh --body-file "$body" --range master..issue-764 --repo "$at/lane" \
     > "$work/out-pr-contract.txt" 2>&1; then
  ok "check-pr-contract ACCEPTS the driver's declaration (the existing mechanism is consumed, not replaced)"
else
  fail "check-pr-contract refused the driver's own PR body: $(tail -c 300 "$work/out-pr-contract.txt")"
fi

# the lane-caused direction: the SAME lane, a baseline that passes one of them.
rm -f "$at/lane/.verify/merge-attestation.json"
: > "$EVENTS"
AO_LAND_APPLY=1 bash "$entry" --issue 764 --root "$at/lane" --baseline "$at/master-passes-chat.json" \
  > "$work/out-lane-caused.txt" 2>&1; caused_rc=$?
if [ "$caused_rc" -eq 1 ] && grep -q 'telemetry/chat' "$work/out-lane-caused.txt"; then
  ok "the driver REFUSES a lane-caused suite BY NAME (rc 1)"
else
  fail "lane-caused landing: rc=$caused_rc (expected 1): $(tail -c 500 "$work/out-lane-caused.txt")"
fi
if [ "$(event_count 'gh pr-merge')" = "0" ]; then
  ok "lane-caused: nothing was merged, although the contract's red was otherwise attributable"
else
  fail "lane-caused: the driver merged past a lane-caused failure"
fi

# the gate-of-record direction: a red verify refuses even with the same baseline.
rm -f "$at/lane/.verify/merge-attestation.json"
export CONTRACT_SIGNALS="verify=1,drift=0,tests=1,negative-controls=0,policy-schema=0"
: > "$EVENTS"
AO_LAND_APPLY=1 bash "$entry" --issue 764 --root "$at/lane" --baseline "$at/master-agrees.json" \
  > "$work/out-red-verify.txt" 2>&1; verify_rc=$?
if [ "$verify_rc" -eq 1 ] && grep -q 'verify' "$work/out-red-verify.txt"; then
  ok "the driver REFUSES when the gate of record is red, however pre-existing the suite reds are (rc 1)"
else
  fail "red verify through the driver: rc=$verify_rc (expected 1): $(tail -c 500 "$work/out-red-verify.txt")"
fi
if [ "$(event_count 'gh pr-merge')" = "0" ]; then
  ok "red verify: nothing was merged"
else
  fail "red verify: the driver merged past a red gate of record"
fi

# the dry run: the SAME lane, granted, with nothing remote changing.
rm -f "$at/lane/.verify/landing-764-report.md" "$at/lane/.verify/landing-764.json"
export CONTRACT_SIGNALS="verify=0,drift=0,tests=1,negative-controls=0,policy-schema=0"
attest_signals "$at/lane/.verify/merge-attestation.json" "$at_head" 0 0 1 0 0
before_listing="$(lane_listing "$at")"
: > "$EVENTS"
run_driver "$work/out-attributed-dry.txt" "$at/lane" --baseline "$at/master-agrees.json"; dry_rc=$?
if [ "$dry_rc" -eq 0 ] && grep -q 'DRY RUN — the lane is grantable' "$work/out-attributed-dry.txt"; then
  ok "a dry run on the same attributable red is GRANTABLE (rc 0) — the pre-flight path measures too, it does not guess"
else
  fail "attributed dry run: rc=$dry_rc (expected 0): $(tail -c 400 "$work/out-attributed-dry.txt")"
fi
if [ "$(event_count 'gh pr-create')" = "0" ] && [ "$(event_count 'gh pr-merge')" = "0" ] \
   && [ "$(event_count 'contract')" = "0" ] && [ "$(event_count 'lifecycle')" = "0" ]; then
  ok "attributed dry run: no PR was created, nothing was merged, no contract ran, no closure ran"
else
  fail "attributed dry run: a mutating call happened: $(grep -E 'gh pr-create|gh pr-merge|contract|lifecycle' "$EVENTS" | tr '\n' ' ')"
fi
# NB: the earlier refusals happen AFTER their push, so the origin legitimately
# holds this branch; what a dry run must not do is change the LANE.
if [ "$(lane_listing "$at")" = "$before_listing" ]; then
  ok "attributed dry run: the lane's file listing is unchanged (a dry run wrote nothing)"
else
  fail "attributed dry run: the lane listing changed"
fi
unset CONTRACT_MODE CONTRACT_SIGNALS GH_PR_HEAD

# --- summary (a red run names its count; a green run says what it proved) ----
if [ "$FAILED" -gt 0 ]; then
  printf 'check-landing: FAIL (%s finding(s)) — scratch kept at %s\n' "$FAILED" "$work" >&2
  exit 1
fi
rm -rf "$work"
echo "check-landing: OK — the landing driver refuses a lane without green commit-named evidence (and grants one with it), lands in order over the fixture, is idempotent on a terminal lane, and defaults to a dry run that writes nothing; it attributes pre-existing suite reds ONLY by measuring them against clean master — naming what is pre-existing, refusing a lane-caused suite by name, keeping the gate of record strict, grandfathering nothing, and failing both constant mutants of that comparison"
exit 0
