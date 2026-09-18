#!/usr/bin/env bash
# check-pr-queue-squash-guard.sh — scripts/pr-queue.sh must refuse an apply-mode
# merge whose rendered squash message would drop the ticket trailer (issue
# #1102, parent #878).
#
# THE DEFECT THIS EXISTS FOR
#   scripts/pr-queue.sh's apply mode called `gh pr merge --squash` with nothing
#   checking the message that call composes; check-squash-message.sh existed
#   but nothing called it, so the same defect that produced #960/#976/#996/#991
#   could still land through the queue. This gate PROVES pr-queue.sh now calls
#   `scripts/check-squash-message.sh --pr <n>` immediately before `gh pr merge`
#   and stops (never calls `gh pr merge`) on a NOT-OK verdict.
#
# HOW
#   Runs scripts/pr-queue.sh in apply mode (AO_QUEUE_APPLY=1) against a
#   one-PR AO_QUEUE_FIXTURE, offline (no real `gh`, no network): a scratch
#   PATH puts a fake `gh` in front of the real one (its `pr merge` subcommand
#   just records that it was called and exits 0; its `pr view` subcommand is
#   never reached because AO_QUEUE_FIXTURE supplies the list) and a fake
#   `check-squash-message.sh` stands in for the real one so the verdict is
#   deterministic and no `gh pr view`/scratch-git-repo classification runs.
#
# Exit contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-pr-queue-squash-guard.sh
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

target="scripts/pr-queue.sh"
[ -f "$target" ] || { echo "check-pr-queue-squash-guard: CANNOT-ASSESS — $target is missing" >&2; exit 2; }

FAILED=0
fail() { printf '  FAIL  %s\n' "$*" >&2; FAILED=$((FAILED + 1)); }
ok() { printf '  ok    %s\n' "$*"; }

TMPD=""
cleanup() { [ -n "$TMPD" ] && rm -rf "$TMPD" || true; }
trap cleanup EXIT
TMPD="$(mktemp -d /tmp/ao1102-pr-queue-squash-guard.XXXXXX)" || {
  echo "check-pr-queue-squash-guard: CANNOT-ASSESS — no scratch directory" >&2
  exit 2
}

fixture='[
  {"number":10,"title":"ready one","isDraft":false,"mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","files":[{"path":"README.md"}],"body":"## Pre-existing red\n\nNone — no failing gate is claimed to be pre-existing.\n"}
]'
printf '%s' "$fixture" > "$TMPD/fixture.json"

fakebin="$TMPD/bin"
mkdir -p "$fakebin"

# fake `gh pr merge` records the call; never called on a refused merge.
cat > "$fakebin/gh" <<'GH'
#!/usr/bin/env bash
if [ "$1" = "pr" ] && [ "$2" = "merge" ]; then
  echo "$3" >> "$AO_TEST_GH_MERGE_CALLS"
  exit 0
fi
echo "fake gh: unexpected invocation: $*" >&2
exit 1
GH
chmod +x "$fakebin/gh"

run_case() { # <verdict-rc> <label>
  local verdict_rc="$1" label="$2"
  local calls="$TMPD/merge-calls-$label.txt"
  : > "$calls"

  local squash_stub="$TMPD/check-squash-message-$label.sh"
  cat > "$squash_stub" <<EOF
#!/usr/bin/env bash
exit $verdict_rc
EOF
  chmod +x "$squash_stub"
  # pr-queue.sh resolves the checker relative to its OWN dirname; stand a
  # whole scratch scripts/ dir next to a copy so the relative resolution
  # picks up the stub instead of the real checker.
  local scratch_scripts="$TMPD/scripts-$label"
  mkdir -p "$scratch_scripts"
  cp "$root/$target" "$scratch_scripts/pr-queue.sh"
  cp "$squash_stub" "$scratch_scripts/check-squash-message.sh"

  AO_TEST_GH_MERGE_CALLS="$calls" PATH="$fakebin:$PATH" \
    env AO_QUEUE_APPLY=1 AO_QUEUE_FIXTURE="$TMPD/fixture.json" \
    bash "$scratch_scripts/pr-queue.sh" > "$TMPD/out-$label.txt" 2>&1
  echo $?
}

echo "== check-pr-queue-squash-guard: apply-mode merge gated on check-squash-message.sh =="

# --- NOT-OK verdict: merge must be refused, gh pr merge never called --------
rc="$(run_case 1 notok)"
if [ "$rc" -eq 1 ]; then
  ok "NOT-OK verdict: pr-queue.sh exits 1 (refused)"
else
  fail "NOT-OK verdict: pr-queue.sh exit=$rc, expected 1"
fi
if grep -q "squash-message-would-drop-trailer" "$TMPD/out-notok.txt"; then
  ok "NOT-OK verdict: refusal is named squash-message-would-drop-trailer"
else
  fail "NOT-OK verdict: refusal is not named squash-message-would-drop-trailer"
fi
if [ -s "$TMPD/merge-calls-notok.txt" ]; then
  fail "NOT-OK verdict: gh pr merge was called although the squash-message check refused"
else
  ok "NOT-OK verdict: gh pr merge was never called"
fi

# --- OK verdict: merge must proceed -----------------------------------------
rc="$(run_case 0 ok)"
if [ "$rc" -eq 0 ]; then
  ok "OK verdict: pr-queue.sh exits 0"
else
  fail "OK verdict: pr-queue.sh exit=$rc, expected 0"
fi
if grep -q "^10$" "$TMPD/merge-calls-ok.txt"; then
  ok "OK verdict: gh pr merge was called for PR #10"
else
  fail "OK verdict: gh pr merge was not called for PR #10"
fi

echo ""
if [ "$FAILED" -eq 0 ]; then
  echo "check-pr-queue-squash-guard: OK — refused NOT-OK without merging, merged on OK"
  exit 0
fi
echo "check-pr-queue-squash-guard: NOT-OK — $FAILED finding(s)" >&2
exit 1
