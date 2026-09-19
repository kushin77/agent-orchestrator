#!/usr/bin/env bash
# merge-pr.sh — the ONE guarded PR-merge entrypoint (issue #1233, parent #1145).
#
# THE DEFECT THIS EXISTS FOR
#   `gh pr merge --squash` composes the landed commit message out of the PR title
#   and body (`squash_merge_commit_message: PR_BODY`, verified live by
#   `scripts/repo-settings.sh verify`), so a body whose LAST paragraph is not the
#   trailer block lands a commit carrying no `Refs <slug>#<n>`. Then
#   `scripts/check-isolation-landed.sh` reddens on MASTER'S OWN TIP, which makes
#   `make verify` red for every lane on the box. Measured: four recurrences
#   (#960/#976/#996/#991), then eight in one day (#1145) — two of them (#1221,
#   #1225) were lanes landing the unblock while re-creating the defect.
#
#   Every PROGRAMMATIC path already consulted the guard: `scripts/pr-queue.sh`
#   and `governance/lifecycle`'s close-out both call
#   `scripts/check-squash-message.sh`. The RAW `gh pr merge` that the spawn
#   instruction told an agent to run consulted nothing. This script is the single
#   entrypoint that closes that gap, and `governance/spawn/render.py` — the one
#   copy of the instruction every spawned lane receives — now names it.
#
# WHAT IT GUARANTEES
#   * `scripts/check-squash-message.sh --pr <n>` runs FIRST. That script renders
#     the exact message `gh pr merge --squash` would compose and asks the ONE
#     shared predicate (`governance/isolation/trailer.py`) about it.
#   * A refusal (guard rc 1) exits 1 printing `squash-message-would-drop-trailer`.
#     No verdict (guard rc 2, or an unrecognised rc) exits 2. `gh pr merge` is
#     NEVER invoked in either case: a gate that could not reach a verdict is not
#     permission.
#   * DRY RUN BY DEFAULT, mirroring `scripts/pr-queue.sh` (AO_QUEUE_APPLY) and
#     `scripts/land-lane.sh` (AO_LAND_APPLY): without AO_MERGE_APPLY=1 nothing is
#     merged and the planned command is printed instead.
#
# WHAT IT DOES NOT DO (named rather than papered over)
#   It cannot make a deliberate bypass impossible: a human or agent that calls
#   `gh pr merge` directly still bypasses every local control. Only the required
#   status check tracked by #1138 can close that boundary.
#
# Exit contract: 0 OK / 1 NOT-OK (refused; nothing merged) / 2 CANNOT-ASSESS
# (nothing merged).
#
# Usage:
#   bash scripts/merge-pr.sh --pr <number>                  # dry run
#   AO_MERGE_APPLY=1 bash scripts/merge-pr.sh --pr <number>  # execute
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

usage() {
  cat <<'USAGE'
Usage: bash scripts/merge-pr.sh --pr <number>
       AO_MERGE_APPLY=1 bash scripts/merge-pr.sh --pr <number>
Runs scripts/check-squash-message.sh first and refuses by name
(squash-message-would-drop-trailer) before any merge is attempted.
Dry run by default. Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
USAGE
}

pr_number=""
while [ $# -gt 0 ]; do
  case "$1" in
    --pr)
      if [ $# -lt 2 ] || [ -z "${2:-}" ]; then
        echo "merge-pr: CANNOT-ASSESS — --pr needs a PR number" >&2
        exit 2
      fi
      pr_number="$2"
      shift 2
      ;;
    --pr=*)
      pr_number="${1#*=}"
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      printf 'merge-pr: CANNOT-ASSESS — unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ -z "$pr_number" ]; then
  echo "merge-pr: CANNOT-ASSESS — a PR number is required (see --help)" >&2
  exit 2
fi

case "$pr_number" in
  *[!0-9]*)
    printf 'merge-pr: CANNOT-ASSESS — not a PR number: %s\n' "$pr_number" >&2
    exit 2
    ;;
esac

# --- the guard, resolved next to this script so the two cannot drift apart ---
guard="$(dirname "${BASH_SOURCE[0]}")/check-squash-message.sh"
if [ ! -f "$guard" ]; then
  echo "merge-pr: CANNOT-ASSESS — the squash-message guard is missing: $guard" >&2
  exit 2
fi

echo "merge-pr: checking the rendered squash message for #$pr_number (scripts/check-squash-message.sh)"
if bash "$guard" --pr "$pr_number"; then
  guard_rc=0
else
  guard_rc=$?
fi

case "$guard_rc" in
  0) ;;
  1)
    echo "merge-pr: REFUSED — squash-message-would-drop-trailer — #$pr_number's rendered squash message would fail check-isolation-landed after merge; gh pr merge was NOT invoked" >&2
    exit 1
    ;;
  2)
    echo "merge-pr: CANNOT-ASSESS — the squash-message guard reached no verdict for #$pr_number; gh pr merge was NOT invoked" >&2
    exit 2
    ;;
  *)
    printf 'merge-pr: CANNOT-ASSESS — the squash-message guard returned an unrecognised exit code %s for #%s; gh pr merge was NOT invoked\n' \
      "$guard_rc" "$pr_number" >&2
    exit 2
    ;;
esac

if [ "${AO_MERGE_APPLY:-0}" != "1" ]; then
  echo "merge-pr: DRY RUN — #$pr_number carries the ticket trailer; would run: gh pr merge $pr_number --squash --delete-branch (AO_MERGE_APPLY=1 to execute)"
  exit 0
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "merge-pr: CANNOT-ASSESS — gh not found; apply mode needs it to merge" >&2
  exit 2
fi

# --- merged-tree evidence (issue #1254 step 6, same rule as pr-queue.sh) ----
# This is the OTHER path a PR reaches `gh pr merge` from (#1280 made this
# script the guarded entrypoint the single-PR flow uses); the merged-tree
# rule belongs here too, not only in the queue's loop, or a single-PR merge
# through this script would still land on per-head evidence alone. Reuses
# scripts/pr-queue.sh's own merged-tree functions (one predicate, not a
# second copy) via its offline test seam.
pr_view_json="$(gh pr view "$pr_number" --json baseRefName,headRefOid 2>/tmp/mp-view-err.txt)" || {
  printf 'merge-pr: CANNOT-ASSESS — could not read #%s from gh pr view: %s\n' \
    "$pr_number" "$(head -c 200 /tmp/mp-view-err.txt)" >&2
  exit 2
}
pr_base_ref="$(printf '%s' "$pr_view_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("baseRefName",""))' 2>/dev/null)"
pr_head_oid="$(printf '%s' "$pr_view_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("headRefOid",""))' 2>/dev/null)"
if [ -z "$pr_base_ref" ] || [ -z "$pr_head_oid" ]; then
  echo "merge-pr: CANNOT-ASSESS — gh pr view #$pr_number did not report baseRefName/headRefOid" >&2
  exit 2
fi
pr_queue_script="$(dirname "${BASH_SOURCE[0]}")/pr-queue.sh"
if [ ! -f "$pr_queue_script" ]; then
  echo "merge-pr: CANNOT-ASSESS — scripts/pr-queue.sh is missing; cannot judge merged-tree evidence" >&2
  exit 2
fi
if ! bash "$pr_queue_script" --check-merged-tree "$pr_number" --head "$pr_head_oid" --against-base "origin/$pr_base_ref"; then
  echo "merge-pr: REFUSED — merged-tree evidence check failed for #$pr_number; gh pr merge was NOT invoked" >&2
  exit 1
fi

# --- approval record (issue #1272) — behind AO_APPROVAL_REQUIRED, default off
# so this lands without blocking the runner; the runner rung flips it later.
if [ "${AO_APPROVAL_REQUIRED:-0}" = "1" ]; then
  approvals_cli="$(dirname "${BASH_SOURCE[0]}")/../integrations/paperclip/adapters/approvals/record_cli.py"
  if [ ! -f "$approvals_cli" ]; then
    echo "merge-pr: CANNOT-ASSESS — approvals record_cli.py is missing: $approvals_cli" >&2
    exit 2
  fi
  if ! python3 "$approvals_cli" check --scope "merge:pr#$pr_number" >/tmp/mp-approval.txt 2>&1; then
    approval_rc=$?
    cat /tmp/mp-approval.txt >&2
    if [ "$approval_rc" = "2" ]; then
      echo "merge-pr: CANNOT-ASSESS — approval check reached no verdict for #$pr_number; gh pr merge was NOT invoked" >&2
      exit 2
    fi
    echo "merge-pr: REFUSED — approval-missing:merge:pr#$pr_number — gh pr merge was NOT invoked" >&2
    exit 1
  fi
  echo "merge-pr: approval record verified for #$pr_number"
fi

echo "merge-pr: merging #$pr_number (gh pr merge --squash --delete-branch); the message was verified above"

# --- publish the gate of record for the commit that LANDED (issue #1382) ------
# A required status check gates a PULL REQUEST, and a status is posted for a
# commit. A squash landing therefore creates a commit that NO pre-merge status
# can ever describe: the green the gate produced names the PR head, and the head
# is not an ancestor of anything on `master`. Measured 2026-09-19:
# `scripts/check-gate-status.sh` read the live producer state on this repository
# and found `ao/gate-of-record` observed on none of the last 20 commits of
# `master` -- every merge landed a commit the required context said nothing
# about, while branch protection required it.
#
# So the landing seam publishes the context for the commit it landed -- and only
# when it can rest that green on an OBSERVED one:
#
#   * the rc is 0 because the merge-path guards already passed (the rendered
#     squash message carries its ticket trailer, and pr-queue.sh's merged-tree
#     evidence check accepted the tree that is landing);
#   * the EVIDENCE is the gate of record OBSERVED GREEN ON THE PR HEAD, read
#     back with `scripts/gate-status.sh show`. With no observed green there is
#     nothing to rest the landed commit's status on, so NOTHING is published and
#     the refusal is named: an ungated PR must stay unproduced, or this seam
#     would be a green button for any merge that got past the guards.
#   * publishing never changes the merge's outcome. The merge already happened;
#     whether a status could be posted is reported by name, never swallowed.
#
# The status this publishes is therefore a claim about the same TREE the head's
# green was measured on, landing under the same guards that gated the merge --
# not a second, independent verdict on the landed commit.
publish_landed_status() {
  local pr="$1"
  local view view_rc state landed head_oid post_out post_rc post_reason
  view="$(gh pr view "$pr" --json state,mergeCommit,headRefOid 2>/dev/null)"
  view_rc=$?
  if [ "$view_rc" -ne 0 ]; then
    printf 'merge-pr: NOTE -- the gate of record was NOT published for a landed commit: #%s could not be read back (gh pr view exited %s), so which commit landed cannot be named\n' \
      "$pr" "$view_rc"
    return 0
  fi
  state="$(printf '%s' "$view" | python3 -c 'import json,sys; print((json.load(sys.stdin) or {}).get("state") or "")' 2>/dev/null)"
  landed="$(printf '%s' "$view" | python3 -c 'import json,sys; d=json.load(sys.stdin) or {}; m=d.get("mergeCommit") or {}; print((m.get("oid") if isinstance(m, dict) else "") or "")' 2>/dev/null)"
  head_oid="$(printf '%s' "$view" | python3 -c 'import json,sys; print((json.load(sys.stdin) or {}).get("headRefOid") or "")' 2>/dev/null)"
  if [ "$state" != "MERGED" ]; then
    printf 'merge-pr: NOTE -- the gate of record was NOT published for a landed commit: #%s reads back as state %s, so nothing landed to publish for\n' \
      "$pr" "${state:-unreadable}"
    return 0
  fi
  case "$landed" in
    "" | *[!0-9a-f]*)
      printf 'merge-pr: NOTE -- the gate of record was NOT published: gh reported no readable merge commit for #%s (mergeCommit is %s), so the commit that landed cannot be named\n' \
        "$pr" "${landed:-empty}"
      return 0
      ;;
  esac
  if [ "${#landed}" -ne 40 ]; then
    printf 'merge-pr: NOTE -- the gate of record was NOT published: the reported merge commit for #%s is %s characters, not a full sha\n' \
      "$pr" "${#landed}"
    return 0
  fi
  if [ -z "$head_oid" ]; then
    printf 'merge-pr: NOTE -- the gate of record was NOT published for landed commit %s: #%s reports no head commit, so the evidence the landed green would rest on cannot be read\n' \
      "${landed:0:12}" "$pr"
    return 0
  fi
  post_out="$(bash "$root/scripts/gate-status.sh" show --sha "$head_oid" 2>&1)"
  post_rc=$?
  if [ "$post_rc" -ne 0 ]; then
    printf 'merge-pr: NOTE -- NO green was published for the landed commit %s: the gate of record is not observed green on the PR head %s (%s), and a landed green that no run supports is the fabricated-green class this poster refuses -- an ungated merge stays unproduced\n' \
      "${landed:0:12}" "${head_oid:0:12}" "$(printf '%s\n' "$post_out" | tail -n 1)"
    return 0
  fi
  post_out="$(bash "$root/scripts/gate-status.sh" post --sha "$landed" --rc 0 2>&1)"
  post_rc=$?
  if [ "$post_rc" -eq 0 ]; then
    printf '%s\n' "$post_out"
  else
    post_reason="$(printf '%s\n' "$post_out" | tail -n 1)"
    printf 'merge-pr: NOTE -- the gate of record was NOT published for the landed commit %s (the poster exited %s): %s\n' \
      "${landed:0:12}" "$post_rc" "${post_reason:-the poster printed no reason}"
    printf 'merge-pr: NOTE -- the merge stands and its outcome is unchanged; what is missing is the status, and saying so is the point\n'
  fi
  return 0
}

# A non-zero exit here is a refusal to RE-CHECK, not proof that nothing landed:
# `gh pr merge --delete-branch` can exit rc 1 after the merge actually succeeded
# (measured 2026-09-15, #623 — the local branch-prune step collides with the
# shared checkout that holds `master`). Read `gh pr view <n> --json
# state,mergeCommit` before acting on it -- which is what publish_landed_status
# does, in both branches below: a merge that LANDED publishes its status even
# when `gh` exits non-zero afterwards, and a merge that did NOT land publishes
# nothing because the state it reads back is not MERGED.
if gh pr merge "$pr_number" --squash --delete-branch; then
  echo "merge-pr: OK — #$pr_number merged"
  publish_landed_status "$pr_number"
  exit 0
fi
echo "merge-pr: REFUSED — gh pr merge #$pr_number failed; re-check state before retrying" >&2
publish_landed_status "$pr_number"
exit 1
