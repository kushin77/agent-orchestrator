#!/usr/bin/env bash
#
# check-orphan-handoff.sh — an orphaned claim is handed to the reconciler (#694).
#
# THE DEFECT THIS EXISTS FOR
#   `fleet/terminal.py::held_action` returns `orphaned` for a claim held by
#   someone else with no live run. The loop answered that with an escalation and
#   an attempt count -- "escalating, left pending" -- and the claim stayed
#   wedged, because the reconciler that OWNS orphan teardown (issue #304:
#   per-session heartbeat, TTL sweep, worktree/branch teardown, lock release)
#   was never asked to do its job. A claim whose lane is already gone is exactly
#   the wedge that worker exists to clear.
#
# WHAT IT CHECKS
#   * the loop CALLS the handoff, and calls it BEFORE the escalation, so a
#     reconciler that can end the orphan ends it while a reconciler that cannot
#     still leaves the bounded escalation intact;
#   * the handoff is BOUNDED (one sweep per episode), or it would itself be the
#     runaway the guard exists to bound;
#   * the sweep is asked to ACT (`--apply`): a plan releases no claim;
#   * the "ended" vocabulary is IMPORTED from the reconciler's own module rather
#     than restated, so the two sides cannot drift;
#   * the interface the loop depends on still exists (`reconcile sweep --apply`);
#   * the tests that prove the behaviour are present.
#
# It also proves IT CAN FAIL: it re-runs every assertion against a copy of the
# tree with the handoff call removed and requires that run to fail. A gate that
# cannot fail is a formality.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-orphan-handoff.sh [--root DIR]
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)" || exit 2
while [ $# -gt 0 ]; do
  case "$1" in
    --root) root="${2:?--root needs a directory}"; shift 2 ;;
    *) printf 'check-orphan-handoff: unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

LOOP="$root/fleet/terminal.py"
TESTS="$root/fleet/tests/test_orphan_handoff.py"
RECONCILE_CLI="$root/governance/reconcile/cli.py"

if [ ! -r "$LOOP" ]; then
  echo "check-orphan-handoff: CANNOT-ASSESS -- $LOOP is not readable" >&2
  exit 2
fi


count_of() { # count_of <file> <fixed-string>
  awk -v needle="$2" 'index($0, needle) { n++ } END { print n + 0 }' "$1"
}

line_of() { # line_of <file> <fixed-string> -> first line number, or 0
  awk -v needle="$2" 'index($0, needle) { print NR; exit } END { print 0 }' "$1" | head -1
}

# --- the assertions, factored so the vacuity control can run the same ones ----
# Every assertion appends a finding to `findings` rather than exiting, so one run
# reports everything wrong at once.
findings=""
assert_loop() { # assert_loop <root> <label>
  local r="$1" label="$2" loop="$1/fleet/terminal.py"
  local handoff_call escalate_call handoff_line escalate_line n

  if [ ! -r "$loop" ]; then
    findings="$findings"$'\n'"  $label: $loop is not readable"
    return
  fi
  # 1. the handoff exists and is called from the loop
  if [ "$(count_of "$loop" 'def hand_orphan_to_reconciler(')" -eq 1 ]; then
    printf '  OK    the handoff is defined (fleet/terminal.py: hand_orphan_to_reconciler)\n'
  else
    findings="$findings"$'\n'"  $label: hand_orphan_to_reconciler is not defined exactly once"
  fi

  handoff_call='ended, detail = hand_orphan_to_reconciler(directive_id, issue, holder)'
  escalate_call='guard_retire(directive_id, issue, f"orphaned claim held by {holder}'
  n="$(count_of "$loop" "$handoff_call")"
  if [ "$n" -eq 1 ]; then
    printf '  OK    the orphaned branch calls it (fleet/terminal.py: hand_orphan_to_reconciler call)\n'
  else
    findings="$findings"$'\n'"  $label: the orphaned branch does not hand over (found $n call(s))"
  fi

  # 2. ... and BEFORE the escalation, so the escalation is the fallback it used to be
  handoff_line="$(line_of "$loop" "$handoff_call")"
  escalate_line="$(line_of "$loop" "$escalate_call")"
  if [ "$handoff_line" -gt 0 ] && [ "$escalate_line" -gt 0 ] && [ "$handoff_line" -lt "$escalate_line" ]; then
    printf '  OK    the handoff precedes the escalation (line %s before line %s)\n' "$handoff_line" "$escalate_line"
  else
    findings="$findings"$'\n'"  $label: the handoff does not precede the escalation (handoff=$handoff_line escalate=$escalate_line)"
  fi

  # 3. the bounded form: the episode mark is consulted, so a poll cannot re-sweep
  if [ "$(count_of "$loop" 'orphan_handoff_done(directive_id)')" -ge 1 ] \
     && [ "$(count_of "$loop" 'already handed over once for this episode')" -ge 1 ]; then
    printf '  OK    the handoff is bounded to one sweep per episode (fleet/terminal.py: orphan_handoff_done)\n'
  else
    findings="$findings"$'\n'"  $label: the handoff is not bounded -- every cycle could sweep"
  fi

  # 4. the sweep is asked to ACT
  if [ "$(count_of "$loop" '"sweep", "--apply", "--json"')" -eq 1 ]; then
    printf '  OK    the sweep is asked to act, not to plan (fleet/terminal.py: --apply)\n'
  else
    findings="$findings"$'\n'"  $label: the sweep is not invoked with --apply, so nothing is released"
  fi

  # 5. the vocabulary is imported from its owner, not restated
  if grep -qE 'from governance\.reconcile\.sweep import (PARKED|RECLAIMED)' "$loop"; then
    printf '  OK    "ended" is read from the reconciler own vocabulary (fleet/terminal.py: governance.reconcile.sweep)\n'
  else
    findings="$findings"$'\n'"  $label: the ended-vocabulary is not imported from governance.reconcile.sweep"
  fi

  # 6. the pre-#694 behaviour survives for a handoff that does not end the orphan
  if contains "$(cat "$loop")" 'escalating, left'; then
    printf '  OK    a handoff that does not end the orphan still escalates (the old path is preserved)\n'
  else
    findings="$findings"$'\n'"  $label: the escalation was removed, so a shelved orphan would go silent"
  fi

  # 7. the interface the loop depends on still exists
  local help_out=""
  if [ -r "$r/governance/reconcile/cli.py" ]; then
    help_out="$(python3 "$r/governance/reconcile/cli.py" sweep --help 2>/dev/null)"
  fi
  # Bash-native (#868): the piped form of this test is the #852 idiom, and in this
  # polarity a false negative would report the interface as MISSING when it is there.
  if contains "$help_out" '--apply'; then
    printf '  OK    reconcile sweep still offers --apply (the interface the loop depends on)\n'
  else
    findings="$findings"$'\n'"  $label: reconcile sweep no longer offers --apply"
  fi

  # 8. the tests that prove the behaviour are present
  if [ -r "$r/fleet/tests/test_orphan_handoff.py" ]; then
    printf '  OK    the behaviour tests are present (fleet/tests/test_orphan_handoff.py)\n'
  else
    findings="$findings"$'\n'"  $label: fleet/tests/test_orphan_handoff.py is missing"
  fi

  # Findings go to stderr and the STATUS carries the verdict, so the vacuity
  # control can read it across a subshell boundary.
  if [ -n "$findings" ]; then
    printf '%s\n' "$findings" | sed '/^$/d' >&2
    return 1
  fi
  return 0
}

printf 'orphan-handoff: the loop hands an orphaned claim to the reconciler (#694)\n'
printf '\n== the loop ==\n'
findings=""
if ! assert_loop "$root" "loop"; then
  printf '\ncheck-orphan-handoff: FAIL -- the handoff is not wired\n' >&2
  exit 1
fi

# --- the vacuity control: the same assertions must FAIL on a loop that does not
# hand over. Without this the check above cannot be shown to be able to fail.
printf '\n== vacuity control: remove the handoff and require a failure ==\n'
tmp="$(mktemp -d "/tmp/ao877-orphan-handoff.$(printf 'X%.0s' 1 2 3 4 5 6)")" || { echo "check-orphan-handoff: CANNOT-ASSESS -- mktemp failed" >&2; exit 2; }
trap 'rm -rf "$tmp"' EXIT

mkdir -p "$tmp/mut/fleet/tests" "$tmp/mut/governance/reconcile"
cp "$LOOP" "$tmp/mut/fleet/terminal.py"
cp -f "$TESTS" "$tmp/mut/fleet/tests/test_orphan_handoff.py" 2>/dev/null || true
cp -f "$RECONCILE_CLI" "$tmp/mut/governance/reconcile/cli.py" 2>/dev/null || true

python3 - "$tmp/mut/fleet/terminal.py" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
src = path.read_text()
needle = "ended, detail = hand_orphan_to_reconciler(directive_id, issue, holder)\n"
if src.count(needle) != 1:
    print(f"CONTROL-SETUP-BROKEN: anchor appears {src.count(needle)} times", file=sys.stderr)
    raise SystemExit(3)
path.write_text(src.replace(needle, ""))
PY
control_setup=$?
if [ "$control_setup" -ne 0 ]; then
  printf '  FAIL  the control could not be built (rc=%s)\n' "$control_setup" >&2
  exit 2
fi

if assert_loop "$tmp/mut" "control" > "$tmp/control.out" 2> "$tmp/control.err"; then
  printf '  FAIL  the assertions PASS on a loop with no handoff -- this check cannot fail\n' >&2
  exit 1
fi
printf '  OK    the control is detected -- the same assertions fail without the handoff\n'
sed -n '1,3p' "$tmp/control.err" | sed 's/^/        /'

printf '\ncheck-orphan-handoff: OK -- the loop hands an orphaned claim to the reconciler before escalating (bounded to one sweep per episode, --apply, vocabulary imported from its owner), and the assertions are proven able to fail\n'
exit 0
