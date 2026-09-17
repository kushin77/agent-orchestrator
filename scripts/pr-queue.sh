#!/usr/bin/env bash
# pr-queue.sh — ordered serial squash-merge queue, codifying the by-hand
# PR-queue-clearing procedure done on 2026-09-17 (10 PRs) as a system
# (issue #1053, parent #878).
#
# DRY RUN BY DEFAULT, mirroring scripts/land-lane.sh / AO_LAND_APPLY: without
# an explicit opt-in this prints the ordered plan and changes nothing. To
# execute for real:
#
#   AO_QUEUE_APPLY=1 bash scripts/pr-queue.sh
#
# Rules measured on the night of 2026-09-17 (see docs/PR-QUEUE.md):
#   - a PR touching a GATE PATH (scripts/verify.sh, scripts/gate.sh,
#     scripts/merge-gate.sh, scripts/check-*.sh,
#     scripts/gate-coverage-baseline.txt, or the AO_QUEUE_GATE_PATHS /
#     scripts/lib/gate-paths.txt override) is classified gate-changing and
#     ordered LAST, after every ready PR;
#   - a draft PR is excluded from the merge order unless
#     AO_QUEUE_INCLUDE_DRAFTS=1 admits it;
#   - a PR whose body's `## Pre-existing red` section is not `None` is
#     reported as pre-existing-red and NEVER auto-merged;
#   - a conflicting PR is reported as conflict and skipped, not fought;
#   - mergeability is re-read after EVERY merge (master moves; another
#     session can merge mid-run) — apply mode re-fetches and reclassifies
#     each remaining PR immediately before merging it;
#   - one call to `gh pr merge --squash` per PR, and a refusal stops the run
#     immediately (no loop swallowing a refusal);
#   - a PR touching anything under scripts/ gets its GATE-REGRESSION checked
#     before it is merged (issue #1145): the PR's head commit is materialized
#     into a scratch worktree, where a curated set of fast whole-tree content
#     scanners (GATE_REGRESSION_SCRIPTS — verdict-contains, shell-patterns,
#     shell-syntax, python-syntax, secrets, json, docs; not every
#     scripts/check-*.sh — see that array's own comment for why) is run bare.
#     A scanner that goes from clean at the merge base to red at head refuses
#     the merge BY NAME. This closes the gap that let #1118 merge clean
#     through the queue while reddening check-verdict-contains.sh — the
#     queue proved the merge message, never the work. If the PR's head
#     commit cannot be materialized, the merge is refused rather than
#     silently skipped (an audit that could not run is not evidence the diff
#     is safe).
#
# Env vars (see docs/PR-QUEUE.md):
#   AO_QUEUE_APPLY=1              execute merges serially (default: plan only)
#   AO_QUEUE_INCLUDE_DRAFTS=1     admit draft PRs into the plan
#   AO_QUEUE_GATE_PATHS="g1 g2"   override the gate-path glob list (space-separated)
#   AO_QUEUE_FIXTURE=<json|path>  feed `gh pr list --json ...`-shaped input
#                                 (a literal JSON array, or a path to a file
#                                 holding one) for offline runs — no `gh` call
#
# Exit contract: 0 the plan/apply ran to completion with nothing refused,
# 1 an apply-mode merge was refused, 2 CANNOT-ASSESS (no gh, no fixture, bad
# input).
#
# Usage: bash scripts/pr-queue.sh [--base BASE]
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

base="master"
check_gate_regression_head=""
check_gate_regression_base=""
while [ $# -gt 0 ]; do
  case "$1" in
    --base) base="${2:-master}"; shift 2 ;;
    --base=*) base="${1#*=}"; shift ;;
    # Test seam for the gate-regression check (issue #1145): drives
    # gate_regression_check() directly, offline, against a real commit —
    # see that function's header for the negative-control invocation.
    --check-gate-regression)
      if [ $# -lt 2 ] || [ -z "${2:-}" ]; then
        echo "pr-queue: CANNOT-ASSESS — --check-gate-regression needs a head OID" >&2
        exit 2
      fi
      check_gate_regression_head="$2"; shift 2 ;;
    --against-base)
      if [ $# -lt 2 ] || [ -z "${2:-}" ]; then
        echo "pr-queue: CANNOT-ASSESS — --against-base needs a base ref/OID" >&2
        exit 2
      fi
      check_gate_regression_base="$2"; shift 2 ;;
    --help | -h)
      cat <<'USAGE'
Usage: bash scripts/pr-queue.sh [--base BASE]
       bash scripts/pr-queue.sh --check-gate-regression <head-oid>   (reads touched paths on stdin)
Env: AO_QUEUE_APPLY, AO_QUEUE_INCLUDE_DRAFTS, AO_QUEUE_GATE_PATHS, AO_QUEUE_FIXTURE
See docs/PR-QUEUE.md.
USAGE
      exit 0
      ;;
    *)
      echo "pr-queue: CANNOT-ASSESS — unknown argument: $1 (see --help)" >&2
      exit 2
      ;;
  esac
done

DEFAULT_GATE_PATHS="scripts/verify.sh scripts/gate.sh scripts/merge-gate.sh scripts/check-*.sh scripts/gate-coverage-baseline.txt"

gate_paths() {
  if [ -n "${AO_QUEUE_GATE_PATHS+set}" ]; then
    printf '%s\n' "${AO_QUEUE_GATE_PATHS}"
    return 0
  fi
  if [ -f "$root/scripts/lib/gate-paths.txt" ]; then
    # Blank lines and `#` comments are ignored (the file's own header says
    # so, and scripts/check-pr-contract.sh's reader does strip them) —
    # without this, a comment line like "scripts/pr-queue.sh" in prose
    # becomes a glob (measured: this is the only reason #1135 was classified
    # gate-changing).
    grep -vE '^[[:space:]]*(#|$)' "$root/scripts/lib/gate-paths.txt"
    return 0
  fi
  printf '%s\n' "$DEFAULT_GATE_PATHS"
}

fetch_prs() {
  if [ -n "${AO_QUEUE_FIXTURE:-}" ]; then
    if [ -f "$AO_QUEUE_FIXTURE" ]; then
      cat "$AO_QUEUE_FIXTURE"
    else
      printf '%s' "$AO_QUEUE_FIXTURE"
    fi
    return 0
  fi
  if ! command -v gh >/dev/null 2>&1; then
    echo "pr-queue: CANNOT-ASSESS — gh not found and no AO_QUEUE_FIXTURE for an offline run" >&2
    exit 2
  fi
  gh pr list --state open --base "$base" --limit 200 \
    --json number,title,isDraft,mergeable,mergeStateStatus,files,body,headRefName,headRefOid
}

# classify.py — the ONE classifier, used by the plan, the self-test and the
# apply-mode re-check, so what is proven offline is the code path that runs.
classify_py() {
  python3 - "$@" <<'PY'
import fnmatch
import json
import re
import sys

gate_globs_raw, include_drafts_raw, prs_json = sys.argv[1], sys.argv[2], sys.argv[3]
only_number = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else None
gate_globs = [g for g in gate_globs_raw.split() if g.strip()]
include_drafts = include_drafts_raw == "1"

try:
    prs = json.loads(prs_json)
except json.JSONDecodeError as exc:
    print(f"pr-queue: CANNOT-ASSESS — the PR list is not valid JSON: {exc}", file=sys.stderr)
    sys.exit(2)


# Exact-heading match, reconciled with scripts/check-pr-contract.sh's own
# predicate (`^##+[ \t]*Pre-existing red[ \t]*$`) rather than the queue's
# former ad hoc prefix match — a heading like "## Pre-existing red /
# environment notes" (#1127) must be treated the SAME way by both readers.
PRE_EXISTING_RED_HEADING = re.compile(r"(?im)^##+[ \t]*Pre-existing red[ \t]*$")


def pre_existing_red(body):
    """Returns (found, text): found=False means no exact heading at all —
    the queue treats that as UNSAFE-TO-MERGE (see classify()), not as an
    implicit "None". A body can decline gate-changing entirely and still
    slip an unreviewed regression past a queue that reads "no section" as
    "nothing to declare"."""
    text = body or ""
    match = PRE_EXISTING_RED_HEADING.search(text)
    if not match:
        return False, None
    lines = text[match.end():].splitlines()
    for later in lines:
        stripped = later.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            return True, None
        return True, stripped
    return True, None


def classify(pr):
    files = [f.get("path", "") for f in (pr.get("files") or [])]
    is_draft = bool(pr.get("isDraft"))
    mergeable = pr.get("mergeable")
    mstate = pr.get("mergeStateStatus")
    body = pr.get("body")

    if is_draft and not include_drafts:
        return "draft", "draft PR excluded (set AO_QUEUE_INCLUDE_DRAFTS=1 to admit)"
    # gh reports mergeability as the JSON string "UNKNOWN" while GitHub is
    # still computing it, not just JSON null — treating only null as unknown
    # (measured: #1142 came back mergeable="UNKNOWN"/mergeStateStatus="UNKNOWN"
    # from `gh pr list` while the single-PR endpoint said mergeable:false,
    # dirty) misclassifies it as mergeable and can order it into the merge
    # order ahead of PRs GitHub will actually refuse.
    if mergeable in (None, "UNKNOWN") or mstate in (None, "UNKNOWN"):
        return "unknown", "gh reported no settled mergeable/mergeStateStatus for this PR"
    if mergeable == "CONFLICTING" or mstate in ("DIRTY", "CONFLICTING"):
        return "conflict", "conflicting with the base branch; skipped, not fought"
    found, red = pre_existing_red(body)
    if not found:
        return (
            "unclear-pre-existing-red",
            "no exact '## Pre-existing red' heading found; cannot confirm nothing is being hidden",
        )
    if red is not None and not red.strip().lower().startswith("none"):
        return "pre-existing-red", f"declares pre-existing red: {red}"
    touched = sorted({f for f in files if any(fnmatch.fnmatch(f, g) for g in gate_globs)})
    if touched:
        return "gate-changing", "touches gate path(s): " + ", ".join(touched)
    return "ready", "no gate path touched; mergeable"


order = {
    "ready": 0,
    "gate-changing": 1,
    "draft": 2,
    "conflict": 3,
    "pre-existing-red": 4,
    "unclear-pre-existing-red": 5,
    "unknown": 6,
}
rows = []
for pr in prs:
    cls, reason = classify(pr)
    rows.append((pr.get("number"), cls, reason))
rows.sort(key=lambda r: (order.get(r[1], 9), r[0] if r[0] is not None else 0))

if only_number is not None:
    # Single-PR re-check mode (apply-mode re-classification before a merge):
    # print just the class, or nothing if the PR is no longer in the list
    # (merged/closed elsewhere mid-run).
    want = int(only_number)
    for number, cls, _reason in rows:
        if number == want:
            print(cls)
            break
    sys.exit(0)

print(f"{'number':>8}  {'class':<16}  reason")
for number, cls, reason in rows:
    print(f"{number:>8}  {cls:<16}  {reason}")

merge_order = [str(r[0]) for r in rows if r[1] in ("ready", "gate-changing")]
print("MERGE_ORDER:" + (" " + " ".join(merge_order) if merge_order else ""))
PY
}

# pr_head_and_files_py — the head OID and changed-file list for one PR
# number, from the SAME `gh pr list --json ...` payload fetch_prs already
# pulled (never a second `gh pr view` call, which could observe a different
# moment than the classification just made). Prints the head OID on the
# first line, one changed path per line after it. Empty output if the PR is
# no longer in the list.
pr_head_and_files_py() { # <prs-json> <number>
  python3 - "$1" "$2" <<'PY'
import json
import sys

prs = json.loads(sys.argv[1])
want = int(sys.argv[2])
for pr in prs:
    if pr.get("number") == want:
        print(pr.get("headRefOid") or "")
        for f in pr.get("files") or []:
            path = f.get("path", "")
            if path:
                print(path)
        break
PY
}

# gate_regression_check — the seam issue #1145 exists for. A PR touching
# anything under scripts/ gets its head commit materialized into a scratch
# worktree, where GATE_REGRESSION_SCRIPTS is run bare (no args) under a
# per-script timeout; one that hangs past the timeout is skipped, not
# treated as a failure (its verdict is unknown, not red). Only a script
# that exits 1 (NOT-OK) at head is a candidate.
#
# A candidate is checked AGAINST THE MERGE BASE before it refuses anything
# (a second, smaller worktree, built only when something is already red —
# the common case touches nothing here): master itself can be red on an
# UNRELATED gate right now (the issue names #1144/check-isolation-landed),
# and refusing every scripts/ PR because of a pre-existing, already-known
# red would just get this check disabled. Only a 0-at-base -> 1-at-head
# FLIP is a regression the PR's own diff is responsible for; a gate that
# was already red at the merge base is reported but does not block.
#
# Reads touched files from stdin (one path per line) so it can be driven
# directly, offline, against REAL historical commits for a negative
# control:
#   printf 'scripts/prune-worktrees.sh\n' | \
#     bash scripts/pr-queue.sh --check-gate-regression 2df367d --base be94933
# proves the check refuses by name (check-verdict-contains: rc 0 at
# be94933 -> rc 1 at 2df367d) at the exact commit that made master red
# (#1118); omitting --base (so head IS its own base) proves it does not
# false-red a tree that was already clean.
GATE_REGRESSION_TIMEOUT="${AO_QUEUE_GATE_TIMEOUT:-30}"

# The gate set this check runs, not "every scripts/check-*.sh" (measured:
# globbing all ~160 and running each bare took over ten minutes and did not
# finish — several gates make real network/fleet calls with their own
# multi-second waits when run outside their intended context, which is not
# safe or affordable to do serially inside a merge loop). These seven are the
# generic, offline, whole-tree CONTENT scanners — no PR/range argument, no
# network, no fleet state — and #1118's regression (a bash idiom bug in an
# unrelated file, caught by check-verdict-contains.sh, a file neither of
# #1118's own changed files) is exactly the class of thing a repo-wide
# content scanner catches and a file-path glob (gate-paths.txt) cannot: this
# list, not "which check-*.sh path matched", is the actual "affected gates"
# set for a scripts/ diff.
GATE_REGRESSION_SCRIPTS=(
  check-verdict-contains.sh
  check-shell-patterns.sh
  check-shell-syntax.sh
  check-python-syntax.sh
  check-secrets.sh
  check-json.sh
  check-docs.sh
)

gate_regression_worktree() { # <ref> <out-var-name>
  local ref="$1" var="$2" wt
  wt="$(mktemp -d "${TMPDIR:-/tmp}/pr-queue-gate-regression.XXXXXX" 2>/dev/null)" || return 1
  rmdir "$wt"
  git worktree add --detach --quiet "$wt" "$ref" >/dev/null 2>&1 || return 1
  printf -v "$var" '%s' "$wt"
}

gate_regression_run_all() { # <worktree> <out-array-name (assoc: script -> rc)>
  local wt="$1" name rc
  local -n results_ref="$2"
  for name in "${GATE_REGRESSION_SCRIPTS[@]}"; do
    [ -f "$wt/scripts/$name" ] || continue
    ( cd "$wt" && timeout "$GATE_REGRESSION_TIMEOUT" bash "scripts/$name" ) >/dev/null 2>&1
    rc=$?
    results_ref["$name"]="$rc"
  done
}

gate_regression_check() { # <head-oid> <base-ref-or-empty>
  local head_oid="$1" base_ref="${2:-}" touched f any_scripts=0

  touched="$(cat)"
  while IFS= read -r f; do
    case "$f" in
      scripts/*) any_scripts=1 ;;
    esac
  done <<<"$touched"
  if [ "$any_scripts" -eq 0 ]; then
    return 0
  fi
  if [ -z "$head_oid" ]; then
    echo "pr-queue: gate-regression CANNOT-ASSESS — no head OID to materialize" >&2
    return 1
  fi
  [ -n "$base_ref" ] || base_ref="$head_oid^"

  git fetch --quiet origin "$head_oid" >/dev/null 2>&1 || true

  local head_wt=""
  if ! gate_regression_worktree "$head_oid" head_wt; then
    echo "pr-queue: gate-regression CANNOT-ASSESS — could not materialize head $head_oid as a scratch worktree (fetched into this clone?)" >&2
    return 1
  fi

  declare -A head_rc=()
  gate_regression_run_all "$head_wt" head_rc
  git worktree remove --force "$head_wt" >/dev/null 2>&1
  rm -rf "$head_wt" 2>/dev/null

  local candidates=() name
  for name in "${!head_rc[@]}"; do
    [ "${head_rc[$name]}" = "1" ] && candidates+=("$name")
  done

  if [ "${#candidates[@]}" -eq 0 ]; then
    return 0
  fi

  local base_wt=""
  if ! gate_regression_worktree "$base_ref" base_wt; then
    echo "pr-queue: gate-regression CANNOT-ASSESS — head $head_oid reds ${candidates[*]}, but the merge base ($base_ref) could not be materialized to tell a regression from a pre-existing red" >&2
    return 1
  fi

  local regressed=() reported=()
  for name in "${candidates[@]}"; do
    ( cd "$base_wt" && timeout "$GATE_REGRESSION_TIMEOUT" bash "scripts/$name" ) >/dev/null 2>&1
    if [ $? -eq 1 ]; then
      reported+=("$name (already red at $base_ref, pre-existing — not this PR's fault)")
    else
      regressed+=("$name")
    fi
  done
  git worktree remove --force "$base_wt" >/dev/null 2>&1
  rm -rf "$base_wt" 2>/dev/null

  if [ "${#reported[@]}" -gt 0 ]; then
    printf 'pr-queue: gate-regression NOTE — %s\n' "${reported[*]}" >&2
  fi
  if [ "${#regressed[@]}" -gt 0 ]; then
    echo "pr-queue: gate-regression REFUSED — ${regressed[*]} pass at $base_ref but go red at $head_oid" >&2
    return 1
  fi
  return 0
}

if [ -n "$check_gate_regression_head" ]; then
  gate_regression_check "$check_gate_regression_head" "$check_gate_regression_base"
  exit $?
fi

gate_globs_text="$(gate_paths)"
include_drafts="${AO_QUEUE_INCLUDE_DRAFTS:-0}"

prs_json="$(fetch_prs)" || exit $?

plan_output="$(classify_py "$gate_globs_text" "$include_drafts" "$prs_json")"
plan_rc=$?
if [ "$plan_rc" -ne 0 ]; then
  printf '%s\n' "$plan_output" >&2
  exit "$plan_rc"
fi

echo "pr-queue: base=$base"
printf '%s\n' "$plan_output"

merge_order_line="$(printf '%s\n' "$plan_output" | grep '^MERGE_ORDER:')"
# shellcheck disable=SC2206
merge_order=(${merge_order_line#MERGE_ORDER:})

if [ "${AO_QUEUE_APPLY:-0}" != "1" ]; then
  echo "pr-queue: DRY RUN — ${#merge_order[@]} PR(s) would be merged, in the order above (AO_QUEUE_APPLY=1 to execute)"
  exit 0
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "pr-queue: CANNOT-ASSESS — gh not found; apply mode needs it to merge" >&2
  exit 2
fi

echo "pr-queue: APPLY — merging ${#merge_order[@]} PR(s) serially, re-checking after each merge"
for number in "${merge_order[@]}"; do
  [ -n "$number" ] || continue
  # Re-read mergeability immediately before acting on it: master moves, and
  # another session may have merged this PR (or made it conflict) already.
  recheck_json="$(fetch_prs)" || exit $?
  current_class="$(classify_py "$gate_globs_text" "$include_drafts" "$recheck_json" "$number")"
  if [ "$current_class" != "ready" ] && [ "$current_class" != "gate-changing" ]; then
    if [ -z "$current_class" ]; then
      echo "pr-queue: SKIP #$number — no longer open (merged or closed elsewhere mid-run)"
    else
      echo "pr-queue: SKIP #$number — reclassified as '$current_class' on re-check; not merging"
    fi
    continue
  fi
  if ! bash "$(dirname "${BASH_SOURCE[0]}")/check-squash-message.sh" --pr "$number"; then
    echo "pr-queue: REFUSED — squash-message-would-drop-trailer — #$number's rendered squash message would fail check-isolation-landed after merge; stopping (no loop swallowing a refusal)" >&2
    exit 1
  fi
  head_and_files="$(pr_head_and_files_py "$recheck_json" "$number")"
  number_head_oid="$(printf '%s\n' "$head_and_files" | head -n1)"
  number_files="$(printf '%s\n' "$head_and_files" | tail -n +2)"
  # The MERGE BASE, not master's current tip: the tip moves as the queue
  # works through the plan, and a two-dot-shaped comparison against the
  # tip is exactly the range-computation defect issue #1145 also names
  # (in check-pr-contract.sh, out of this lane's scope — see the PR body).
  git fetch --quiet origin "$base" >/dev/null 2>&1 || true
  git fetch --quiet origin "$number_head_oid" >/dev/null 2>&1 || true
  number_merge_base="$(git merge-base "origin/$base" "$number_head_oid" 2>/dev/null)"
  if [ -z "$number_merge_base" ]; then
    echo "pr-queue: REFUSED — gate-regression CANNOT-ASSESS — could not compute the merge base of #$number's head ($number_head_oid) with origin/$base; stopping (no loop swallowing a refusal)" >&2
    exit 1
  fi
  if ! printf '%s\n' "$number_files" | gate_regression_check "$number_head_oid" "$number_merge_base"; then
    echo "pr-queue: REFUSED — gate-regression — #$number's diff would red a check-*.sh gate that passes on master today; stopping (no loop swallowing a refusal)" >&2
    exit 1
  fi
  echo "pr-queue: merging #$number (gh pr merge --squash)"
  if ! gh pr merge "$number" --squash; then
    echo "pr-queue: REFUSED — gh pr merge #$number failed; stopping (no loop swallowing a refusal)" >&2
    exit 1
  fi
done

echo "pr-queue: OK — apply run complete, no refusal"
exit 0
