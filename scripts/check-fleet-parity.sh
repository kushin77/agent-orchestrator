#!/usr/bin/env bash
# check-fleet-parity.sh — the fleet acceptance test as a runnable instrument (#799).
#
# THE DEFECT THIS EXISTS FOR
#   The acceptance test ("give the fleet a single GitHub issue and have it complete
#   e2e, at the quality of this terminal") was scored ON 2026-09-15 BY AN OPERATOR
#   READING LOGS. The verdict — "no work product, and an unbounded loop" — was
#   reached by grepping `.fleet/sister.log` by hand and counting
#   `executing directive` 13 times, and the harness that produced it could not be
#   run on the next lane. A verdict only a human can produce is not a repeatable
#   instrument: its dimensions are whatever the reader happened to think of, and
#   nobody can re-run it.
#
# WHAT THIS RUNS
#   `scripts/fleet-parity/judge.py`. With `--issue <n>` it COLLECTS a subject from
#   the real artifacts (the lane worktree's attestation, the merged pull request,
#   the commit, the board snapshot, the repo's own checkers) and JUDGES ten
#   dimensions from it, printing one verdict per dimension and a tri-state
#   aggregate. With no arguments — the form `make verify` runs, because
#   `scripts/discover-checks.sh` auto-wires this file the moment it lands — it runs
#   the PROBE SUITE: fifteen planted subjects, each a one-field mutation of a
#   healthy subject, each asserted to move exactly the dimension it names.
#
#   The split is the whole design. A dimension that cannot be read from the
#   subject reports CANNOT-ASSESS and must NAME what could not be read; an OK
#   verdict with no evidence behind it cannot even be constructed. So "no claim
#   stated without its measurement" is not a convention this harness follows, it
#   is a shape it cannot express — and "every dimension is provoked" is measured
#   by RUNNING the plants, not asserted in prose.
#
# HERMETIC AND FAST
#   The no-argument run touches no network and no live fleet: it reads two JSON
#   fixtures and judges them. That is deliberate — a gate that needs a live fleet
#   cannot be a gate of record. The network-bearing half runs only when an
#   operator asks for a specific issue, and an unreadable input there becomes
#   CANNOT-ASSESS, never OK and never FAIL.
#
# A LANE THAT IS SIMPLY INCOMPLETE MUST NOT FAIL THIS CHECK
#   `make verify` runs this inside every lane's composite gate, so it must not
#   fail on work that is merely unfinished: an absent dimension is CANNOT-ASSESS
#   (exit 2, recorded as SKIP) and FAIL is reserved for a MEASURED violation.
#
# Exit-code contract (the repo tri-state): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.

set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 1

if [ ! -f scripts/fleet-parity/judge.py ]; then
  echo "check-fleet-parity: CANNOT-ASSESS — scripts/fleet-parity/judge.py is missing" >&2
  exit 2
fi

if [ "$#" -eq 0 ]; then
  # The form `make verify` runs: prove the instrument, hermetically.
  python3 scripts/fleet-parity/judge.py
  rc=$?
  case "$rc" in
    0) echo "check-fleet-parity: OK — every dimension is provable (see the grid above)" ;;
    1) echo "check-fleet-parity: NOT-OK — the instrument is not provoked" >&2 ;;
    *) echo "check-fleet-parity: CANNOT-ASSESS — the probe suite could not run (rc=$rc)" >&2 ;;
  esac
  exit "$rc"
fi

# Every other form is passed through to the instrument, which decides the verdict.
python3 scripts/fleet-parity/judge.py "$@"
rc=$?
case "$rc" in
  0) echo "check-fleet-parity: OK — every dimension measured and holding" ;;
  1) echo "check-fleet-parity: NOT-OK — a measured violation (named above)" >&2 ;;
  2) echo "check-fleet-parity: CANNOT-ASSESS — no verdict may be claimed (named above)" >&2 ;;
  *) echo "check-fleet-parity: CANNOT-ASSESS — the instrument exited $rc" >&2 ;;
esac
exit "$rc"
