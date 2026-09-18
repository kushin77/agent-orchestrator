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
#   AO_QUEUE_VERIFY_MERGED=1      let the queue itself run scripts/verify.sh in a
#                                 detached scratch worktree of master+PR-head when
#                                 no CI status evidence exists for the head (see
#                                 merged-tree evidence, below)
#
# MERGED-TREE EVIDENCE (issue #1254 step 6, child of #1254). Measured
# 2026-09-18: four times, two PRs each green ALONE were red TOGETHER —
# #1110+#1115 (AO_FROZEN_CLOCK vs the env-surface gate), #1309+#1287
# (board.freshness vs control-mapping), #1300 (box-local quarantine), #1246
# (RCA doc ids) — because merges were judged on PER-PR-HEAD build results, so
# the first build of the ACTUAL merged tree was the NEXT PR's, which
# inherited the red silently. Before `gh pr merge`, the queue now requires
# evidence that a verify ran green on a tree equal to origin/master's CURRENT
# tip (re-read immediately before merging) plus this PR's head:
#   (a) the PR head's merge-base IS the current master tip, and the gate of
#       record's own commit status (scripts/gate-status.sh show --sha <head>)
#       reads success; or
#   (b) with AO_QUEUE_VERIFY_MERGED=1, a local `scripts/verify.sh verify` run
#       in a detached scratch worktree merging master's tip with the PR head
#       (honouring scripts/verify.sh's own gate-lock; a PARKED/CANNOT-ASSESS
#       run is not evidence either way).
# Otherwise the merge is refused BY NAME: `merged-tree-unverified:<pr>`
# (evidence is stale or absent — the base moved, or no CI status and (b) was
# not opted into) or `merged-tree-red:<check>` (the merged tree reds).
# Refusing prints the remedy (update-branch / re-run) and the queue continues
# with the NEXT candidate; after every successful merge the tip moved, so the
# next candidate is re-judged against the NEW tip, exactly like the existing
# gate-regression re-check below.
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
check_merged_tree_number=""
check_merged_tree_head=""
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
    # Test seam / merge-pr.sh's own seam (issue #1254 step 6) for the
    # merged-tree check: drives merged_tree_evidence_by_ref() directly, offline or
    # from the single-PR merge-pr.sh entrypoint, against real refs — see
    # that function's header for the negative-control invocation.
    --check-merged-tree)
      if [ $# -lt 2 ] || [ -z "${2:-}" ]; then
        echo "pr-queue: CANNOT-ASSESS — --check-merged-tree needs a PR number" >&2
        exit 2
      fi
      check_merged_tree_number="$2"; shift 2 ;;
    --head)
      if [ $# -lt 2 ] || [ -z "${2:-}" ]; then
        echo "pr-queue: CANNOT-ASSESS — --head needs a head OID" >&2
        exit 2
      fi
      check_merged_tree_head="$2"; shift 2 ;;
    --against-base)
      if [ $# -lt 2 ] || [ -z "${2:-}" ]; then
        echo "pr-queue: CANNOT-ASSESS — --against-base needs a base ref/OID" >&2
        exit 2
      fi
      check_gate_regression_base="$2"; shift 2 ;;
    --help | -h)
      cat <<'USAGE'
Usage: bash scripts/pr-queue.sh [--base BASE]
       bash scripts/pr-queue.sh --check-gate-regression <head-oid> [--against-base REF]   (reads touched paths on stdin)
       bash scripts/pr-queue.sh --check-merged-tree <pr-number> --head <head-oid> --against-base <base-ref>
Env: AO_QUEUE_APPLY, AO_QUEUE_INCLUDE_DRAFTS, AO_QUEUE_GATE_PATHS, AO_QUEUE_FIXTURE, AO_QUEUE_VERIFY_MERGED
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
#
# The PR JSON arrives on STDIN, never in argv (issue #1056). At the fleet's
# current queue size a `gh pr list --json` payload is larger than Linux's
# per-argument limit (MAX_ARG_STRLEN, ~128KB), so passing it as an argv entry
# died with `python3: Argument list too long` (rc 126) — the tool worked only
# while the queue was small. Only the small scalars stay in argv; the program
# rides in `-c` so stdin is free for the payload.
classify_py() {
  # usage: <pr-json on stdin> classify_py <gate-globs> <include-drafts> [only-number]
  python3 -c "$(cat <<'PY'
import fnmatch
import json
import re
import sys

gate_globs_raw, include_drafts_raw = sys.argv[1], sys.argv[2]
only_number = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None
gate_globs = [g for g in gate_globs_raw.split() if g.strip()]
include_drafts = include_drafts_raw == "1"
prs_json = sys.stdin.read()

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
)" "$@"
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

# merged_tree_local_verify — evidence source (b): materialize origin/master's
# CURRENT tip in a detached scratch worktree, merge the PR head into it, and
# run scripts/verify.sh verify there (it applies its own gate-lock, so this
# never runs concurrently with another verify on the box). A clean merge that
# passes is evidence the MERGED tree is green, not just either side alone.
merged_tree_local_verify() { # <pr-number> <head-oid> <tip>
  local number="$1" head_oid="$2" tip="$3" wt logf verify_rc failed_names name
  wt="$(mktemp -d "${TMPDIR:-/tmp}/pr-queue-merged-tree.XXXXXX" 2>/dev/null)" || {
    echo "pr-queue: REFUSED — merged-tree-unverified:$number — could not allocate a scratch directory for the merge-tree verify" >&2
    return 1
  }
  rmdir "$wt"
  if ! git worktree add --detach --quiet "$wt" "$tip" >/dev/null 2>&1; then
    echo "pr-queue: REFUSED — merged-tree-unverified:$number — could not materialize master tip $tip as a scratch worktree" >&2
    return 1
  fi
  if ! ( cd "$wt" && git -c user.email=pr-queue@local -c user.name=pr-queue merge --no-commit --no-ff "$head_oid" ) >/dev/null 2>&1; then
    ( cd "$wt" && git merge --abort ) >/dev/null 2>&1
    git worktree remove --force "$wt" >/dev/null 2>&1; rm -rf "$wt" 2>/dev/null
    echo "pr-queue: REFUSED — merged-tree-unverified:$number — master($tip)+#$number($head_oid) does not merge cleanly; cannot verify a tree that does not exist" >&2
    return 1
  fi
  logf="$(mktemp "${TMPDIR:-/tmp}/pr-queue-merged-tree-log.XXXXXX" 2>/dev/null)"
  # AO_QUEUE_VERIFY_CMD / AO_QUEUE_VERIFY_ATTESTATION are a test seam ONLY
  # (default: the real gate) — check-pr-queue-squash-guard.sh's negative
  # controls swap in a fast fake command + fixture attestation so the red
  # and green merged-tree paths are provable offline without paying for a
  # real `scripts/verify.sh verify` run per assertion.
  ( cd "$wt" && bash -c "${AO_QUEUE_VERIFY_CMD:-"scripts/verify.sh verify"}" ) >"$logf" 2>&1
  verify_rc=$?
  failed_names=""
  local attestation_rel="${AO_QUEUE_VERIFY_ATTESTATION:-.verify/attestation.json}"
  local attestation_path="$attestation_rel"
  case "$attestation_rel" in
    /*) ;; # already absolute (a test seam pointing at a fixture) — use as-is
    *) attestation_path="$wt/$attestation_rel" ;;
  esac
  if [ -f "$attestation_path" ]; then
    failed_names="$(python3 -c "
import json
try:
    data = json.load(open('$attestation_path'))
except Exception:
    raise SystemExit(0)
print(' '.join(c.get('name', '') for c in data.get('checks', []) if c.get('verdict') == 'FAIL'))
" 2>/dev/null)"
  fi
  git worktree remove --force "$wt" >/dev/null 2>&1
  rm -rf "$wt" 2>/dev/null
  case "$verify_rc" in
    0)
      echo "pr-queue: merged-tree evidence for #$number — local scratch verify PASSED on master($tip)+#$number($head_oid); log at $logf"
      rm -f "$logf" 2>/dev/null
      return 0
      ;;
    10 | 11 | 12)
      echo "pr-queue: REFUSED — merged-tree-unverified:$number — the local merge-tree verify was PARKED/CANNOT-ASSESS (gate-lock rc $verify_rc); that is not evidence either way; log at $logf" >&2
      return 1
      ;;
    *)
      name="${failed_names%% *}"
      [ -n "$name" ] || name="verify"
      echo "pr-queue: REFUSED — merged-tree-red:$name — master($tip)+#$number($head_oid) is red on ${failed_names:-$name} (remedy: update-branch / re-run CI); log at $logf" >&2
      return 1
      ;;
  esac
}

# merged_tree_evidence_by_ref — the gate itself, keyed off an ALREADY
# RESOLVABLE base ref (a plain branch name is fetched from origin first; a
# ref already in the form "origin/<branch>" — e.g. from merge-pr.sh, which
# resolved baseRefName itself — is used as-is). No `gh pr merge` is reached
# without this returning 0. Named refusals only (never a bare non-zero).
merged_tree_evidence_by_ref() { # <pr-number> <head-oid> <base-ref>
  local number="$1" head_oid="$2" base_ref="$3" tip mb
  if [ -z "$head_oid" ]; then
    echo "pr-queue: REFUSED — merged-tree-unverified:$number — no head OID to judge" >&2
    return 1
  fi
  case "$base_ref" in
    origin/*) git fetch --quiet origin "${base_ref#origin/}" >/dev/null 2>&1 || true ;;
    *) git fetch --quiet origin "$base_ref" >/dev/null 2>&1 || true; base_ref="origin/$base_ref" ;;
  esac
  tip="$(git rev-parse "$base_ref" 2>/dev/null)"
  if [ -z "$tip" ]; then
    echo "pr-queue: REFUSED — merged-tree-unverified:$number — could not read the current tip of $base_ref" >&2
    return 1
  fi
  git fetch --quiet origin "$head_oid" >/dev/null 2>&1 || true
  mb="$(git merge-base "$tip" "$head_oid" 2>/dev/null)"
  if [ "$mb" != "$tip" ]; then
    echo "pr-queue: REFUSED — merged-tree-unverified:$number — #$number's merge-base is not the CURRENT master tip ($tip); evidence would be stale (remedy: update-branch / re-run, then re-plan)" >&2
    return 1
  fi
  if command -v gh >/dev/null 2>&1 && [ -f "$(dirname "${BASH_SOURCE[0]}")/gate-status.sh" ]; then
    if bash "$(dirname "${BASH_SOURCE[0]}")/gate-status.sh" show --sha "$head_oid" >/dev/null 2>&1; then
      echo "pr-queue: merged-tree evidence for #$number — gate-of-record CI status success at $head_oid, merge-base == current tip $tip"
      return 0
    fi
  fi
  if [ "${AO_QUEUE_VERIFY_MERGED:-0}" = "1" ]; then
    merged_tree_local_verify "$number" "$head_oid" "$tip"
    return $?
  fi
  echo "pr-queue: REFUSED — merged-tree-unverified:$number — no green gate-of-record CI status found for $head_oid, and AO_QUEUE_VERIFY_MERGED is not set to run a local merge-tree verify (remedy: update-branch / re-run CI, or set AO_QUEUE_VERIFY_MERGED=1)" >&2
  return 1
}

if [ -n "$check_merged_tree_number" ]; then
  if [ -z "$check_merged_tree_head" ] || [ -z "$check_gate_regression_base" ]; then
    echo "pr-queue: CANNOT-ASSESS — --check-merged-tree needs --head <oid> and --against-base <ref>" >&2
    exit 2
  fi
  # --against-base here names the BASE REF (e.g. origin/master), not a bare
  # branch name, so pass it through untouched to merge-base/rev-parse rather
  # than merged_tree_evidence_by_ref's own "origin/<name>" fetch — the caller
  # (merge-pr.sh) already resolved it.
  merged_tree_evidence_by_ref "$check_merged_tree_number" "$check_merged_tree_head" "$check_gate_regression_base"
  exit $?
fi

if [ -n "$check_gate_regression_head" ]; then
  gate_regression_check "$check_gate_regression_head" "$check_gate_regression_base"
  exit $?
fi

gate_globs_text="$(gate_paths)"
include_drafts="${AO_QUEUE_INCLUDE_DRAFTS:-0}"

prs_json="$(fetch_prs)" || exit $?

plan_output="$(printf '%s' "$prs_json" | classify_py "$gate_globs_text" "$include_drafts")"
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
  # Same stdin route as the plan above, so the offline proof and the live
  # re-check exercise one code path.
  current_class="$(printf '%s' "$recheck_json" | classify_py "$gate_globs_text" "$include_drafts" "$number")"
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
  # Merged-tree evidence (issue #1254 step 6): required for EVERY PR, not
  # just scripts/-touching ones — a merge-order defect (two green heads,
  # red together) is not confined to gate files. Runs after the
  # squash-message guard (a message-shape refusal should not spend a
  # scratch worktree) and before the scripts/-scoped gate-regression check.
  if ! merged_tree_evidence_by_ref "$number" "$number_head_oid" "$base"; then
    exit 1
  fi
  # gate_regression_check's own scoping (no scripts/* file touched -> return 0
  # immediately, no worktree) must be applied BEFORE the merge-base lookup
  # below, not just inside the function: a PR that touches nothing under
  # scripts/ has no reason to need `origin/$base` fetched or a merge-base
  # computed at all, and a queue offline/fixture context (no real PR head to
  # fetch) must not be refused over a check this PR was never going to need
  # (measured: broke check-pr-queue-squash-guard.sh's non-scripts/ fixture).
  number_any_scripts=0
  while IFS= read -r ao_f; do
    case "$ao_f" in
      scripts/*) number_any_scripts=1 ;;
    esac
  done <<<"$number_files"
  if [ "$number_any_scripts" -eq 1 ]; then
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
  fi
  echo "pr-queue: merging #$number (gh pr merge --squash)"
  if ! gh pr merge "$number" --squash; then
    echo "pr-queue: REFUSED — gh pr merge #$number failed; stopping (no loop swallowing a refusal)" >&2
    exit 1
  fi
done

echo "pr-queue: OK — apply run complete, no refusal"
exit 0
