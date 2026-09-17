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
#     immediately (no loop swallowing a refusal).
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
while [ $# -gt 0 ]; do
  case "$1" in
    --base) base="${2:-master}"; shift 2 ;;
    --base=*) base="${1#*=}"; shift ;;
    --help | -h)
      cat <<'USAGE'
Usage: bash scripts/pr-queue.sh [--base BASE]
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
    cat "$root/scripts/lib/gate-paths.txt"
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
    --json number,title,isDraft,mergeable,mergeStateStatus,files,body,headRefName
}

# classify.py — the ONE classifier, used by the plan, the self-test and the
# apply-mode re-check, so what is proven offline is the code path that runs.
classify_py() {
  python3 - "$@" <<'PY'
import fnmatch
import json
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


def pre_existing_red(body):
    lines = (body or "").splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("## pre-existing red"):
            for later in lines[i + 1:]:
                text = later.strip()
                if not text:
                    continue
                if text.startswith("#"):
                    return None
                return text
            return None
    return None


def classify(pr):
    files = [f.get("path", "") for f in (pr.get("files") or [])]
    is_draft = bool(pr.get("isDraft"))
    mergeable = pr.get("mergeable")
    mstate = pr.get("mergeStateStatus")
    body = pr.get("body")

    if is_draft and not include_drafts:
        return "draft", "draft PR excluded (set AO_QUEUE_INCLUDE_DRAFTS=1 to admit)"
    if mergeable == "CONFLICTING" or mstate in ("DIRTY", "CONFLICTING"):
        return "conflict", "conflicting with the base branch; skipped, not fought"
    red = pre_existing_red(body)
    if red is not None and not red.strip().lower().startswith("none"):
        return "pre-existing-red", f"declares pre-existing red: {red}"
    if mergeable is None or mstate is None:
        return "unknown", "gh reported no mergeable/mergeStateStatus for this PR"
    touched = sorted({f for f in files if any(fnmatch.fnmatch(f, g) for g in gate_globs)})
    if touched:
        return "gate-changing", "touches gate path(s): " + ", ".join(touched)
    return "ready", "no gate path touched; mergeable"


order = {"ready": 0, "gate-changing": 1, "draft": 2, "conflict": 3, "pre-existing-red": 4, "unknown": 5}
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
  echo "pr-queue: merging #$number (gh pr merge --squash)"
  if ! gh pr merge "$number" --squash; then
    echo "pr-queue: REFUSED — gh pr merge #$number failed; stopping (no loop swallowing a refusal)" >&2
    exit 1
  fi
done

echo "pr-queue: OK — apply run complete, no refusal"
exit 0
