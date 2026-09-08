#!/usr/bin/env bash
# qa-loop.sh — continuous fix -> verify -> re-check loop for the QA gate (issue #29).
#
# Runs `make gate` (scripts/gate.sh). On a failure it applies the documented
# mechanical auto-fixes (only classes that are safe to apply blind) and
# re-runs the gate, until the gate is green or no auto-fix can make progress.
# A failure the loop cannot fix is reported for manual work — the loop never
# loosens a check to get green (no-false-green, AO-GR-4).
#
# AUTO-FIX CLASSES (see also `--list-fixes`):
#   TRAILING-WS   docs-lint reports `FAIL <file> (trailing whitespace)`; the
#                 loop strips trailing blank space from those files. Only
#                 files the current change-set already touches are edited —
#                 never files another lane owns.
#
# MODES:
#   --once        run the gate exactly once and exit with its result
#   --list-fixes  list the auto-fix classes (no gate run)
#   (default)     loop until green, or until no auto-fix makes progress
#   --max-iters N cap the loop at N gate runs (default 8)
#   --verbose     print full gate output on every iteration
#
# Exit codes: 0 = gate green; 1 = gate red and not auto-fixable (manual work
# required); 2 = gate reached no determinate verdict (CANNOT-ASSESS).
#
# Usage:
#   bash scripts/qa-loop.sh
#   bash scripts/qa-loop.sh --once
#   bash scripts/qa-loop.sh --list-fixes
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

max_iters=8
verbose=0
mode="loop"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --once) mode="once"; shift ;;
    --list-fixes) mode="list-fixes"; shift ;;
    --verbose) verbose=1; shift ;;
    --max-iters)
      [ "$#" -ge 2 ] || { echo "qa-loop: --max-iters needs a value" >&2; exit 2; }
      max_iters="$2"; shift 2 ;;
    --max-iters=*) max_iters="${1#*=}"; shift ;;
    *) shift ;;
  esac
done

list_fixes() {
  echo "qa-loop auto-fix classes (mechanically safe to apply blind):"
  echo ""
  echo "  TRAILING-WS  docs-lint flags 'FAIL <file> (trailing whitespace)'."
  echo "               qa-loop strips trailing blank space from exactly those"
  echo "               files, restricted to files the current change-set"
  echo "               already touches (never another lane's files)."
  echo ""
  echo "Failures outside these classes are reported for manual work — qa-loop"
  echo "never loosens a check to make the gate green (no-false-green, AO-GR-4)."
  exit 0
}

run_gate() { # -> rc (0/1/2); full transcript in .verify/gate.log
  bash scripts/gate.sh gate
  return $?
}

# local_change_set — files this worktree has modified/added (tracked diff +
# untracked). Auto-fixes never touch anything outside this set.
local_change_set() {
  {
    git diff --name-only HEAD 2>/dev/null
    git ls-files --others --exclude-standard 2>/dev/null
  } | LC_ALL=C sort -u
}

fix_trailing_ws() { # <gate-log> <change-set> -> count fixed
  local log="$1" cs="$2"
  local count=0 f
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    f="$(printf '%s' "$f" | sed -E 's/^ *FAIL  +([^ ]+) \(trailing whitespace\).*/\1/')"
    # line was not a trailing-ws finding if the sed produced the full line back
    case "$f" in
      *"trailing whitespace"*) continue ;;
    esac
    case "$f" in
      /*|./*) : ;;          # already absolute or repo-root-relative
      *) f="./$f" ;;
    esac
    if [ -f "$f" ] && printf '%s\n' "$cs" | grep -qx "${f#./}"; then
      sed -i 's/[[:blank:]]*$//' "$f" 2>/dev/null && count=$((count + 1))
    fi
  done < <(grep -E 'FAIL  .*\(trailing whitespace\)' "$log" 2>/dev/null)
  printf '%s' "$count"
}

if [ "$mode" = "list-fixes" ]; then
  list_fixes
fi

if [ "$mode" = "once" ]; then
  run_gate
  exit $?
fi

echo "qa-loop: begin (max $max_iters iteration(s))"
iter=1
while [ "$iter" -le "$max_iters" ]; do
  echo ""
  echo "===== qa-loop iteration $iter ====="
  run_gate
  rc=$?
  if [ "$rc" -eq 0 ]; then
    echo ""
    echo "qa-loop: GREEN after $iter iteration(s)"
    exit 0
  fi

  cs="$(local_change_set)"
  fixed="$(fix_trailing_ws "$root/.verify/gate.log" "$cs")"
  if [ "$fixed" -gt 0 ]; then
    echo "qa-loop: applied TRAILING-WS fix to $fixed file(s) in the current change-set; re-running"
    iter=$((iter + 1))
    continue
  fi

  echo ""
  echo "qa-loop: FAILED (gate exit $rc) and no auto-fix could make progress."
  echo "  Remaining failures require manual work — qa-loop never loosens a check."
  echo "  Full transcript: .verify/gate.log"
  echo "  Re-run with --once after fixing, or inspect the failing signal(s) above."
  exit 1
done

echo "qa-loop: stopped after $max_iters iterations without green" >&2
exit 1
