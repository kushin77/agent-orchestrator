#!/usr/bin/env bash
# scan-pr-failures.sh — file an issue for a pull request whose checks failed.
#
# THE CODE-NATIVE REPLACEMENT FOR `.github/workflows/ci-failure-scanner.yml`
# (issue #812, GR-15). The retired workflow did exactly this, in Actions:
#
#     gh pr list --state open --limit 50 --json number,statusCheckRollup \
#       --jq '[.[] | select(.statusCheckRollup | any(.conclusion == "FAILURE")) | .number]'
#
# Those are `gh` commands. GR-15 says automation in this fleet is code-native
# `make` targets run by the ops runner and cron, so the workflow was a rule
# violation that bought nothing the shell could not do. This is the same scan,
# same behaviour, no Actions.
#
# It also scans the SAME surface the gate of record now publishes to: ADR-0028
# posts the gate's outcome as a commit status, and `statusCheckRollup` is where
# commit statuses and check runs both appear. So once the poster runs from the
# runner, this scanner sees a red gate of record without knowing anything about
# it.
#
# DRY-RUN BY DEFAULT. Filing issues is a write to the board, and a scanner that
# writes on every cron tick is a nuisance generator; `--apply` is required.
#
#   bash scripts/scan-pr-failures.sh            # report only
#   bash scripts/scan-pr-failures.sh --apply    # file issues
#
# SCHEDULED (issue #1207). This script is the `scan-pr-failures` job in
# `config/fleet-jobs.json` (marker `ao-fleet-scan-pr-failures`), which
# `fleet/cron.py` renders and reconciles — the schedule it never had. It is
# declared `enabled: false` because `--apply` writes to the board (GR-5), so a
# principal enables it by flipping that flag; the installed crontab is never
# hand-edited.
#
# Exit codes: 0 OK (including "found nothing", which is the normal case) /
#             1 NOT-OK / 2 CANNOT-ASSESS (gh absent or the API refused).
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

REPO="${AO_REPO:-kushin77/agent-orchestrator}"
LIMIT="${AO_SCAN_PR_LIMIT:-50}"
APPLY=0
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "scan-pr-failures: unknown argument: $arg" >&2; exit 1 ;;
  esac
done

command -v gh >/dev/null 2>&1 || { echo "scan-pr-failures: CANNOT-ASSESS — gh not found" >&2; exit 2; }

# The same selector the workflow used. `statusCheckRollup` carries both commit
# statuses (what ADR-0028 posts) and check runs, so this reads either.
prs="$(gh pr list --repo "$REPO" --state open --limit "$LIMIT" \
        --json number,statusCheckRollup \
        --jq '[.[] | select(.statusCheckRollup | any(.conclusion == "FAILURE")) | .number] | .[]' 2>/tmp/spf-err.txt)" || {
  echo "scan-pr-failures: CANNOT-ASSESS — the PR scan failed: $(head -c 200 /tmp/spf-err.txt)" >&2
  exit 2
}

if [ -z "$prs" ]; then
  echo "scan-pr-failures: OK — no open PR has a failing check"
  exit 0
fi

filed=0
for pr in $prs; do
  # Do not file a second issue for the same PR: the same guard the workflow had.
  existing="$(gh issue list --repo "$REPO" --state all --limit 500 --json number,body \
      --jq "[.[] | select(.body | contains(\"PR #${pr}\")) | .number] | first // empty" 2>/dev/null)"
  if [ -n "$existing" ]; then
    echo "scan-pr-failures: PR #$pr already has issue #$existing — skipping"
    continue
  fi
  if [ "$APPLY" -eq 0 ]; then
    echo "scan-pr-failures: DRY-RUN — PR #$pr has a failing check and no issue (would file)"
    continue
  fi
  body="$(printf 'A check on PR #%s concluded FAILURE.\n\nFiled by `scripts/scan-pr-failures.sh` — the code-native replacement for the retired\n`ci-failure-scanner.yml` workflow (#812, GR-15).\n\nPR #%s\n' "$pr" "$pr")"
  if gh issue create --repo "$REPO" \
       --title "PR #$pr has a failing check" \
       --body "$body" \
       --label type:bug --label priority:P2 >/dev/null 2>/tmp/spf-err.txt; then
    filed=$((filed + 1))
    echo "scan-pr-failures: filed an issue for PR #$pr"
  else
    echo "scan-pr-failures: could not file for PR #$pr — $(head -c 160 /tmp/spf-err.txt)" >&2
  fi
done

if [ "$APPLY" -eq 0 ]; then
  echo "scan-pr-failures: DRY-RUN — re-run with --apply to file"
else
  echo "scan-pr-failures: OK — $filed issue(s) filed"
fi
exit 0
