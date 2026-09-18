#!/usr/bin/env bash
# check-pr-runner.sh — the shared-services PR runner's controls, provoked
# (issue #1343, parent #1295).
#
# THE DEFECT THIS EXISTS FOR
#   The 2026-09-18 prototype (~/ao-runner nohup scripts on 192.168.168.42)
#   measured ten ways an unattended verify-and-merge loop goes wrong: evidence
#   recorded per PR instead of per head (stale verifies ate ~40% of capacity),
#   EXPIRED builds awaited forever (#1267), green-alone-red-together merges
#   (#1254 step 6, four incidents), a bare `gh pr merge` (#1266), host-env reds
#   counted against Cloud Build greens, worktrees reaped mid-run (#1335), leaked
#   gate-lock permits, racing fetches, a loop nobody could ask "what are you
#   doing", and a required check nothing posts. `fleet/runner/` encodes every
#   one; this gate PROVOKES every one.
#
# WHAT IS MEASURED
#   A. lessons 1-5 + 7 as PLAN FIXTURES (`fleet/runner/fixtures/*.json`) driven
#      through the real cli (`plan --fixture`): each must refuse BY NAME —
#      `stale-head:`, `requeue:expired:`, `merged-tree-stale:`,
#      `via scripts/merge-pr.sh`, `requeue:cannot-assess:`, `green:cloud-build:`,
#      `gatelock-prune-failed:` — and never emit the action the lesson forbids.
#   B. lessons 6-9 as the named negative controls in
#      `fleet/runner/tests/test_transports.py` (held-and-removed worktree,
#      prune-before-run, serialised fetch, status-from-ledger), run with pytest's
#      exit codes mapped INDIVIDUALLY (LESSON-0008: 2 = interrupted is
#      CANNOT-ASSESS, 5 = nothing collected is FAIL) and the rootdir pinned.
#   C. lesson 10: the runner's context, the poster's, the mapper's and the branch
#      protection's are ONE string (reusing `check-gate-status.sh`'s parity).
#   D. TWO MUTANTS of a scratch copy, each of which must RED the same control
#      the real tree passes: (1) the stale-head skip dropped in plan.py -> the
#      old head's build is no longer cancelled; (2) the merged-tree check dropped
#      in merge.py -> a red merged tree reaches the merge verb. A mutation that
#      did not change the file is reported as such (a no-op proves nothing).
#
# Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. No network access.
#
# Usage: bash scripts/check-pr-runner.sh
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-pr-runner: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi
if ! python3 -m pytest --version >/dev/null 2>&1; then
  echo "check-pr-runner: CANNOT-ASSESS — pytest is not available (python3 -m pytest)" >&2
  exit 2
fi

for required in fleet/runner/__init__.py fleet/runner/model.py fleet/runner/evidence.py \
                fleet/runner/plan.py fleet/runner/verify.py fleet/runner/merge.py fleet/runner/cli.py \
                fleet/runner/tests/test_plan.py fleet/runner/tests/test_transports.py \
                fleet/runner/fixtures/lesson-1-stale-head.json scripts/gate-status.sh \
                scripts/gate-status-map.py scripts/merge-pr.sh governance/platform/branch-protection.yaml; do
  if [ ! -f "$required" ]; then
    echo "check-pr-runner: FAIL — $required is missing" >&2
    exit 1
  fi
done

# The scratch tree name follows the sanctioned idiom (no mktemp default template).
scratch="/tmp/ao-pr-runner.$.$(date +%s%N)"
mkdir "$scratch" 2>/dev/null || {
  echo "check-pr-runner: CANNOT-ASSESS — no scratch directory available" >&2
  exit 2
}
TMPD="$scratch"
cleanup() { [ -n "$TMPD" ] && rm -rf "$TMPD" || true; }
trap cleanup EXIT

fail=0
cannot=0
ok()   { printf '  OK    %s\n' "$1"; }
bad()  { printf '  FAIL  %s\n' "$1" >&2; fail=$((fail + 1)); }

# contains <haystack> <needle> — bash-native containment (no pipe, no SIGPIPE).
contains() {
  case "$1" in
    *"$2"*) return 0 ;;
  esac
  return 1
}

# plan_fixture <tree> <fixture> -> prints the plan (the cli's own output)
plan_fixture() {
  env PYTHONDONTWRITEBYTECODE=1 python3 "$1/fleet/runner/cli.py" plan --fixture "$2" 2>&1
}

# expect <label> <output> <must-contain> <must-not-contain|->
# Reports into CONTROL_FAILS so the same assertions can judge a mutant.
CONTROL_FAILS=0
expect() {
  local label=$1 out=$2 want=$3 forbid=$4
  if ! contains "$out" "$want"; then
    printf '  FAIL  %s — did not name %s :: %s\n' "$label" "$want" "$(printf '%s' "$out" | tr '\n' ' ' | cut -c1-200)" >&2
    CONTROL_FAILS=$((CONTROL_FAILS + 1))
    return
  fi
  if [ "$forbid" != "-" ] && contains "$out" "$forbid"; then
    printf '  FAIL  %s — emitted the forbidden %s :: %s\n' "$label" "$forbid" "$(printf '%s' "$out" | tr '\n' ' ' | cut -c1-200)" >&2
    CONTROL_FAILS=$((CONTROL_FAILS + 1))
    return
  fi
  printf '  OK    %s names %s\n' "$label" "$want"
}

FIX="fleet/runner/fixtures"

# fixture_controls <tree> — the lesson 1-5 (+7) assertions against one tree.
fixture_controls() {
  local tree=$1 out
  out="$(plan_fixture "$tree" "$FIX/lesson-1-stale-head.json")"
  expect "lesson-1 a pushed head cancels the stale build" "$out" "cancel-stale #7" "merge "
  expect "lesson-1 the stale build is named" "$out" "stale-head:7:" "-"
  out="$(plan_fixture "$tree" "$FIX/lesson-2-expired.json")"
  expect "lesson-2 expired is re-queued, not awaited" "$out" "requeue:expired:cb-expired:8" "await"
  out="$(plan_fixture "$tree" "$FIX/lesson-3-merged-tree-stale.json")"
  expect "lesson-3 green for an older master tip is refused" "$out" "merged-tree-stale:9:" "merge "
  out="$(plan_fixture "$tree" "$FIX/lesson-4-guarded-verb.json")"
  expect "lesson-4 the merge verb is the guarded entrypoint" "$out" "via scripts/merge-pr.sh" "-"
  out="$(plan_fixture "$tree" "$FIX/lesson-5-cannot-assess.json")"
  expect "lesson-5 cannot-assess is never green" "$out" "requeue:cannot-assess:" "merge "
  out="$(plan_fixture "$tree" "$FIX/lesson-5-ranking.json")"
  expect "lesson-5 a Cloud Build green outranks a host-env red" "$out" "green:cloud-build:12" "verify-red"
  out="$(plan_fixture "$tree" "$FIX/lesson-7-prune-failed.json")"
  expect "lesson-7 a failed gate-lock prune plans no verify" "$out" "gatelock-prune-failed:" "verify "
}

echo "== A. plan fixtures (lessons 1-5, 7) refuse by name =="
CONTROL_FAILS=0
fixture_controls "$root"
if [ "$CONTROL_FAILS" -ne 0 ]; then
  bad "the real planner failed $CONTROL_FAILS fixture control(s)"
fi
real_fixture_fails=$CONTROL_FAILS

# lesson 4, the other half: no module spells the raw merge command.
raw_merge_hits="$(grep -l 'gh pr merge' fleet/runner/plan.py fleet/runner/merge.py fleet/runner/cli.py fleet/runner/verify.py 2>/dev/null || true)"
if [ -n "$raw_merge_hits" ]; then
  bad "lesson-4 a runner module spells the raw merge command: $raw_merge_hits"
else
  ok "lesson-4 no runner module spells the raw merge command (scripts/merge-pr.sh only)"
fi

echo "== B. transport controls (lessons 6-9) via pytest, exit codes mapped individually =="
pinned_ini="$scratch/pytest-pinned.ini"
: >"$pinned_ini"
pytest_pin=(python3 -m pytest -c "$pinned_ini" --rootdir "$root" -p no:cacheprovider)
suite_rc=0
suite_out="$(env PYTHONDONTWRITEBYTECODE=1 "${pytest_pin[@]}" -q fleet/runner/tests 2>&1)" || suite_rc=$?
printf '%s\n' "$suite_out" | tail -2
case "$suite_rc" in
  0) ok "fleet/runner/tests passed (held-and-removed worktree, prune-before-run, serialised fetch, status-from-ledger, PARKED never posted)" ;;
  1) bad "fleet/runner/tests has a failing control (pytest rc 1)" ;;
  2) printf '  CANNOT-ASSESS the runner suite was interrupted (pytest rc 2) — not a pass, not a fail\n'; cannot=$((cannot + 1)) ;;
  5) bad "fleet/runner/tests collected nothing (pytest rc 5) — the controls did not run" ;;
  *) bad "fleet/runner/tests: unrecognised pytest rc $suite_rc" ;;
esac
# The named controls must EXIST by name: a renamed control is a dropped control.
for control in test_a_pushed_head_invalidates_prior_evidence_and_cancels_the_stale_build \
               test_expired_is_requeued_not_awaited \
               test_merge_requires_evidence_for_the_current_master_tip \
               test_merge_action_names_the_guarded_entrypoint_never_gh_pr_merge \
               test_cannot_assess_is_never_counted_green \
               test_worktree_is_held_for_the_whole_run_and_removed_by_the_runner_after \
               test_gatelock_prune_runs_before_any_verify_and_a_failed_prune_plans_none \
               test_fetches_are_serialised_under_one_lock_and_name_explicit_refspecs \
               test_status_answers_what_is_verifying_merged_and_blocked_from_the_ledger \
               test_a_parked_verify_is_never_posted \
               test_poster_protection_mapper_and_runner_name_the_same_context; do
  if ! grep -q "def $control(" fleet/runner/tests/test_plan.py fleet/runner/tests/test_transports.py; then
    bad "named control missing: $control"
  fi
done

echo "== C. lesson 10: one context across runner, poster, mapper and branch protection =="
context_runner="$(python3 - <<'PY'
import sys
sys.path.insert(0, ".")
from fleet.runner.model import GATE_CONTEXT
print(GATE_CONTEXT)
PY
)"
context_poster="$(grep -oE 'CONTEXT="\$\{AO_GATE_CONTEXT:-[^}]+\}"' scripts/gate-status.sh 2>/dev/null | sed 's/.*:-//;s/}"//')"
context_mapper="$(grep -oE '^CONTEXT = "[^"]+"' scripts/gate-status-map.py | sed 's/^CONTEXT = "//;s/"$//')"
context_policy="$(python3 - <<'PY'
import sys
try:
    import yaml
except ImportError:
    sys.exit(0)
policy = yaml.safe_load(open("governance/platform/branch-protection.yaml")) or {}
contexts = policy.get("required_status_contexts") or []
print(" ".join(str(c) for c in contexts))
PY
)"
if [ -z "$context_runner" ] || [ "$context_runner" != "$context_poster" ] || [ "$context_runner" != "$context_mapper" ]; then
  bad "lesson-10 context drift: runner='$context_runner' poster='$context_poster' mapper='$context_mapper'"
elif [ -n "$context_policy" ] && ! contains " $context_policy " " $context_runner "; then
  bad "lesson-10 branch protection requires '$context_policy' but the runner posts '$context_runner' (a required check nothing posts blocks everyone)"
else
  ok "lesson-10 runner, poster, mapper and branch protection name '$context_runner'"
fi

echo "== D. mutants (a scratch copy; the real tree is never touched) =="
mutant_tree="$scratch/tree"
mkdir -p "$mutant_tree/fleet" "$mutant_tree/scripts"
cp -R fleet/runner "$mutant_tree/fleet/runner"
cp scripts/gate-status.sh scripts/gate-status-map.py scripts/merge-pr.sh "$mutant_tree/scripts/"
rm -rf "$mutant_tree/fleet/runner/__pycache__" "$mutant_tree/fleet/runner/tests/__pycache__"

# mutate <file> <sed-expr> <label> -> 0 when the file changed, 1 when the mutation was a no-op
mutate() {
  local file=$1 expr=$2 label=$3 before after
  before="$(sha256sum "$file" | cut -d' ' -f1)"
  sed -i "$expr" "$file"
  after="$(sha256sum "$file" | cut -d' ' -f1)"
  if [ "$before" = "$after" ]; then
    bad "$label: the mutation did not change the file (a no-op mutation proves nothing)"
    return 1
  fi
  return 0
}

# MUTANT 1 — drop the stale-head skip in plan.py: every build is treated as
# being for the current head, so the old head's build is never cancelled.
m1_plan="$mutant_tree/fleet/runner/plan.py"
if mutate "$m1_plan" 's/if current is None or build.sha != current:/if False:/' "MUTANT-1 stale-head-skip-dropped"; then
  CONTROL_FAILS=0
  m1_out="$(plan_fixture "$mutant_tree" "$FIX/lesson-1-stale-head.json")"
  if contains "$m1_out" "cancel-stale #7"; then
    bad "MUTANT-1 stale-head-skip-dropped was NOT caught: the mutant still cancels the stale build"
  else
    ok "MUTANT-1 stale-head-skip-dropped is caught (no cancel-stale for the old head's build)"
  fi
  # restore the scratch copy for mutant 2
  cp fleet/runner/plan.py "$m1_plan"
fi

# MUTANT 2 — drop the merged-tree check in merge.py: a red merged tree no longer
# refuses, so the merge verb is reached. Driven with a fake `sh` that reds the seam.
cat > "$scratch/drive_merge.py" <<'PY'
import sys
from pathlib import Path
tree = Path(sys.argv[1])
sys.path.insert(0, str(tree))
from fleet.runner import merge as merge_mod
from fleet.runner.verify import Ledger, Result
assert merge_mod.__file__.startswith(str(tree)), merge_mod.__file__
repo = tree
(repo / "scripts").mkdir(exist_ok=True)
(repo / "scripts" / "pr-queue.sh").write_text("#!/usr/bin/env bash\n# --check-merged-tree <pr>\n")
calls = []
def sh(argv, **kw):
    calls.append(list(argv))
    if "--check-merged-tree" in argv:
        return Result(1, "", "pr-queue: REFUSED — merged-tree-red:pytest-fleet — master+#14 is red")
    return Result(0)
def git(argv, **kw):
    return Result(0, "c" * 40 + "\n")
out = merge_mod.run_merge(14, "b" * 40, repo=repo, sh=sh, git=git, ledger=Ledger(tree / "l.jsonl"), apply=True, runner_dir=tree)
verb_reached = any(a[1:2] == ["scripts/merge-pr.sh"] for a in calls)
print(f"reason={out.reason} merged={out.merged} verb_reached={verb_reached}")
PY
m2_out="$(env PYTHONDONTWRITEBYTECODE=1 python3 "$scratch/drive_merge.py" "$mutant_tree" 2>&1)"
if ! contains "$m2_out" "reason=merged-tree-red:pytest-fleet merged=False verb_reached=False"; then
  bad "the real merge transport did not refuse a red merged tree by name before the verb :: $m2_out"
else
  ok "the real merge transport refuses merged-tree-red before the verb"
fi
m2_merge="$mutant_tree/fleet/runner/merge.py"
if mutate "$m2_merge" 's/    if seam.rc != 0:/    if False:/' "MUTANT-2 merged-tree-check-dropped"; then
  rm -rf "$mutant_tree/fleet/runner/__pycache__"
  m2_out="$(env PYTHONDONTWRITEBYTECODE=1 python3 "$scratch/drive_merge.py" "$mutant_tree" 2>&1)"
  if contains "$m2_out" "verb_reached=True"; then
    ok "MUTANT-2 merged-tree-check-dropped is caught (the verb was reached on a red merged tree)"
  else
    bad "MUTANT-2 merged-tree-check-dropped was NOT caught :: $m2_out"
  fi
fi

if [ "$fail" -ne 0 ]; then
  echo "check-pr-runner: NOT-OK — $fail finding(s); the runner's controls are not all provable" >&2
  exit 1
fi
if [ "$cannot" -ne 0 ]; then
  echo "check-pr-runner: CANNOT-ASSESS — the transport suite could not run to completion"
  exit 2
fi
echo "check-pr-runner: OK — lessons 1-5,7 refuse by name from fixtures, 6-9 hold under fakes, the context is one string, and both mutants are caught"
exit 0
