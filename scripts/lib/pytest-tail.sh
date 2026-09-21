#!/usr/bin/env bash
# scripts/lib/pytest-tail.sh — the ONE bounded inline echo of a red pytest
# suite's transcript (issue #1661). SOURCED, never executed.
#
# WHY THIS IS A LIB AND NOT TWO COPIES
#   A red suite must NAME its failing test, or the venue's evidence cannot be
#   attributed to a lane: #1661 measured the composite printing a transcript PATH
#   whose transcript the ephemeral build workspace then destroyed, so the failing
#   test was UNNAMEABLE from the venue's own evidence and the red could not be
#   attributed to a lane at all. The first pass added the echo to
#   `scripts/run-pytest-suites.sh` — but that sweep is reached by `make gate` and
#   NOT by the gate of record, and the line the venue actually prints,
#   `FAIL <suite> (pytest exit N — <log>)`, comes from `judge()` in
#   `scripts/check-pytest-suites.sh`. An echo in one caller and not the other
#   leaves the venue exactly as unnameable as before, so BOTH callers source this
#   file and call the one function: one rule, one implementation — the same
#   single-sourcing move #1593 was repaired with.
#
# WHAT IS ECHOED, AND WHY IT IS BOUNDED
#   The `FAILED <test>` / `ERROR <test>` ids pytest printed (at most
#   `PYTEST_FAILED_IDS`, default 20) and the last `PYTEST_TAIL_LINES` lines of the
#   transcript (default 25), every line truncated to 240 columns. Both axes are
#   bounded because the recipient is a gate log: an unbounded tail from a
#   6000-line transcript is a flood, and `.verify/` is ephemeral, so this echo IS
#   the last surviving evidence of why the suite was red. The bounds are resolved
#   HERE and nowhere else, so the two callers cannot drift; a caller's own
#   self-test can still override them through the environment.
#
# WHAT IT IS NOT
#   A GREEN suite never reaches this function: the echo IS the finding, so
#   printing it on a pass would bury the evidence it exists to surface.
#
# THE TRANSCRIPT KEY IS SHARED
#   Both callers retain a suite's transcript at
#   `.verify/pytest-suites/<suite with / -> _>.log` and pass that path in, so the
#   published line, the retained transcript and the assertion in
#   `scripts/check-pytest-log-tail.sh` all name the same file.
#
# Usage: source scripts/lib/pytest-tail.sh; pytest_emit_suite_tail <suite> <log>

pytest_tail_lines="${PYTEST_TAIL_LINES:-25}"
pytest_failed_ids="${PYTEST_FAILED_IDS:-20}"

# pytest_emit_suite_tail <suite> <log> — publish WHY <suite> is red, from the
# transcript <log>, on stderr (the channel both callers report a finding on).
# Always returns 0: it reports, it never decides the suite's verdict.
pytest_emit_suite_tail() {
  local suite="$1" log="$2"
  local ids
  if [ ! -s "$log" ]; then
    printf '    ---- %s: no transcript retained at %s ----\n' "$suite" "$log" >&2
    return 0
  fi
  ids="$(grep -m "$pytest_failed_ids" -E '^(FAILED|ERROR) ' "$log" 2>/dev/null | cut -c1-240 || true)"
  if [ -n "$ids" ]; then
    printf '    ---- %s: failing test id(s), first %s ----\n' "$suite" "$pytest_failed_ids" >&2
    printf '%s\n' "$ids" >&2
  else
    printf '    ---- %s: pytest printed no FAILED <test> line; the transcript tail follows ----\n' \
      "$suite" >&2
  fi
  printf '    ---- %s: last %s line(s) of %s ----\n' "$suite" "$pytest_tail_lines" "$log" >&2
  tail -n "$pytest_tail_lines" "$log" 2>/dev/null | cut -c1-240 >&2
  printf '    ---- end %s ----\n' "$suite" >&2
}
