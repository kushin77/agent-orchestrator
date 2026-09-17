#!/usr/bin/env bash
# merge-pr.sh — the GUARDED squash-merge entrypoint: the one command a lane is
# told to run, and the only place the repository performs a squash merge for a
# lane (issue #1145, parent #878).
#
# THE DEFECT THIS EXISTS FOR
#   `gh pr merge --squash` composes the landed commit message as
#   `"<title> (#N)\n\n<PR BODY>"`. If the PR body has no TRAILING TRAILER
#   paragraph, the landed commit carries no `Refs <slug>#<n>` and
#   `scripts/check-isolation-landed.sh` reddens on **master's own tip** -- which
#   makes `make verify` red for every lane on the box, not just the one that
#   merged. Measured, and still escalating, on 2026-09-17:
#
#     pristine origin/master = 04ad55a
#     $ bash scripts/check-isolation-landed.sh
#       isolation-enforce: NOT-OK -- 4 unrecorded and 0 stale baseline entr(ies)
#     rc=1
#
#   `scripts/check-squash-message.sh` (#1001) renders that exact composed message
#   and refuses by name, and every PROGRAMMATIC merge path already consults it:
#   `scripts/pr-queue.sh`, `governance/lifecycle`'s close-out, and
#   `governance/landing`'s driver (which composes a trailer-bearing body itself).
#   What none of them covered is the RAW path: `governance/spawn/render.py`, the
#   single copy of the instruction prose every spawned agent receives, told the
#   agent to run `gh pr merge <n> --squash --delete-branch` directly. Measured:
#   the trailers were lost by merges the owner performed directly
#   (`merged_by: kushin77`) -- PR #1150, #1171, #1185, #1188, #1191 -- exactly
#   the shape that instruction produces. The producer was the instruction.
#
# WHAT THIS IS
#   One entrypoint that runs the guard FIRST and merges only on a green verdict
#   -- so a lane that follows its instruction cannot produce a trailer-less
#   squash, and the instruction can name a single command. It is a REFUSAL, not
#   advice: on a NOT-OK verdict `gh pr merge` is never invoked.
#
#   DRY RUN BY DEFAULT, mirroring `scripts/pr-queue.sh` / `scripts/land-lane.sh`:
#   without an explicit opt-in it runs the guard, prints the command it *would*
#   run, and changes nothing. To merge for real:
#
#     bash scripts/merge-pr.sh --pr <n> --apply
#     AO_MERGE_APPLY=1 bash scripts/merge-pr.sh --pr <n>
#
# Exit-code contract (repository tri-state): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# A CANNOT-ASSESS is never a merge: an unreadable guard is an unknown verdict.
#
# Usage: bash scripts/merge-pr.sh --pr <number> [--apply] [--delete-branch]
set -uo pipefail

# Resolved from THIS file so a scratch copy (the gate's own fixture) resolves its
# sibling guard and can be provoked offline with a stub `gh` on PATH.
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

pr=""
apply=0
delete_branch=0
while [ $# -gt 0 ]; do
  case "$1" in
    --pr) pr="${2:-}"; shift 2 ;;
    --pr=*) pr="${1#*=}"; shift ;;
    --apply) apply=1; shift ;;
    --delete-branch) delete_branch=1; shift ;;
    -h|--help) sed -n '2,44p' "$0"; exit 0 ;;
    *) printf 'merge-pr: CANNOT-ASSESS — unknown argument: %s (see --help)\n' "$1" >&2; exit 2 ;;
  esac
done

if [ -z "$pr" ]; then
  printf 'merge-pr: CANNOT-ASSESS — usage: bash scripts/merge-pr.sh --pr <number> [--apply] [--delete-branch]\n' >&2
  exit 2
fi
case "$pr" in
  ''|*[!0-9]*) printf 'merge-pr: CANNOT-ASSESS — --pr must be a number, got %s\n' "$pr" >&2; exit 2 ;;
esac

if [ "${AO_MERGE_APPLY:-0}" = "1" ]; then
  apply=1
fi

guard="$root/scripts/check-squash-message.sh"

# --- 1. the guard, BEFORE anything is merged --------------------------------
if [ ! -f "$guard" ]; then
  printf 'merge-pr: CANNOT-ASSESS — the guard is missing: %s\n' "${guard#"$root"/}" >&2
  exit 2
fi

guard_out=""
guard_rc=0
guard_out="$(bash "$guard" --pr "$pr" 2>&1)"
guard_rc=$?

case "$guard_rc" in
  0) : ;;
  1)
    printf 'merge-pr: REFUSED — squash-message-would-drop-trailer (PR #%s)\n' "$pr" >&2
    printf '%s\n' "$guard_out" | sed 's/^/    /' >&2
    printf '  the composed squash message would land without its ticket trailer, reddening\n' >&2
    printf '  check-isolation-landed on master for every lane. Put `Refs kushin77/agent-orchestrator#<n>`\n' >&2
    printf '  in the PR body as its TRAILING paragraph (`gh pr edit` is broken here; patch it with\n' >&2
    printf '  `gh api -X PATCH repos/kushin77/agent-orchestrator/pulls/%s -F body=@file`) and re-run.\n' "$pr" >&2
    exit 1
    ;;
  *)
    printf 'merge-pr: CANNOT-ASSESS — the guard reached no verdict (rc=%s, never a merge)\n' "$guard_rc" >&2
    printf '%s\n' "$guard_out" | sed 's/^/    /' >&2
    exit 2
    ;;
esac

# --- 2. the merge, only on a green verdict ----------------------------------
merge_args=(pr merge "$pr" --squash)
if [ "$delete_branch" -eq 1 ]; then
  merge_args+=(--delete-branch)
fi
rendered="gh ${merge_args[*]}"

if [ "$apply" -ne 1 ]; then
  echo "merge-pr: DRY-RUN — guard OK for PR #$pr; would run: $rendered"
  echo "merge-pr: DRY-RUN — nothing was merged (add --apply or set AO_MERGE_APPLY=1)"
  exit 0
fi

if ! command -v gh >/dev/null 2>&1; then
  printf 'merge-pr: CANNOT-ASSESS — gh (GitHub CLI) not found; nothing was merged\n' >&2
  exit 2
fi

echo "merge-pr: guard OK for PR #$pr; merging ($rendered)"
if gh "${merge_args[@]}"; then
  echo "merge-pr: OK — PR #$pr squash-merged"
  exit 0
fi
printf 'merge-pr: REFUSED — gh pr merge failed for #%s (nothing swallowed; stopping)\n' "$pr" >&2
exit 1
