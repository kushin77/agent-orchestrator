#!/usr/bin/env bash
# check-pytest-log-tail.sh — the publication an unattributable red depends on is
# PROVEN on every gate run, not merely claimed (issue #1661).
#
# WHY THIS EXISTS
#   #1661 measured the composite printing a failing suite's transcript PATH and
#   nothing else, with the transcript destroyed together with the ephemeral build
#   workspace — so the failing test was UNNAMEABLE from the venue's own evidence,
#   the red could not be attributed to a lane, and 21 verified lanes could not
#   land on it. `scripts/run-pytest-suites.sh` now publishes the failing test ids
#   and a BOUNDED tail of the transcript it retains. An echo nobody exercises is a
#   formality, and a gate that cannot fail is not a gate (AO-GR-4), so this check
#   drives the runner's own `--self-test` on every run — the publication is
#   therefore always exercisable AND always exercised:
#     * a deliberately RED suite is NAMED, and the run is rc 1;
#     * the transcript is retained at the shared
#       `.verify/pytest-suites/<suite with / -> _>.log` key that
#       `scripts/check-pytest-suites.sh` also uses;
#     * the echo TRACKS its bound — a 500-failure transcript is echoed within it,
#       and a SMALLER bound echoes fewer lines from the SAME transcript, so the
#       bound is load-bearing rather than met by a short log;
#     * a GREEN suite stays QUIET: no id block, no tail;
#     * an ALL-SKIPPED suite is CANNOT-ASSESS, never PASS (pytest exits 0 there,
#       so this is the arm that would otherwise let "nothing ran" read as green).
#
#   The provocation builds a scratch tree and drives a COPY of the runner through
#   its ORDINARY path, so what is proven is the code that shipped, never a
#   reimplementation of it. The same delegation shape
#   `scripts/check-marker-scan.sh` uses for `scripts/check-docs.sh --self-test`.
#
#   It is BOUNDED: a slow box must not turn into a verdict, so an overrunning
#   self-test is CANNOT-ASSESS by name (never a red, never a quiet pass), and the
#   bound is overridable through `PYTEST_LOG_TAIL_SELFTEST_TIMEOUT` (default
#   120s — roughly six times the ~20s a green run costs on an idle box, so a
#   busy box caps what this check can add to the gate instead of adding it).
#
# EXIT CONTRACT (guardrails/honesty tri-state, consumed not redefined)
#   0  OK              every arm of the publication held
#   1  NOT-OK          an arm failed — the echo the gate depends on is broken
#   2  CANNOT-ASSESS   python3 is missing, the anchor script is absent, the
#                      self-test could not be built, or it exceeded its bound
#
# Offline, deterministic, no network, no containers.
#
# Usage: bash scripts/check-pytest-log-tail.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if [ ! -f scripts/run-pytest-suites.sh ]; then
  echo "check-pytest-log-tail: CANNOT-ASSESS — scripts/run-pytest-suites.sh is missing, so there is no publication to prove" >&2
  exit 2
fi

selftest_bound="${PYTEST_LOG_TAIL_SELFTEST_TIMEOUT:-120}"

rc=0
timeout "$selftest_bound" bash scripts/run-pytest-suites.sh --self-test || rc=$?

case "$rc" in
  0)
    echo "check-pytest-log-tail: OK — the runner names a red suite (ids plus a bounded transcript tail), stays quiet on a green one, and never reads an all-skipped one as PASS"
    exit 0
    ;;
  2)
    echo "check-pytest-log-tail: CANNOT-ASSESS — the runner's --self-test could not be built (rc 2)" >&2
    exit 2
    ;;
  124 | 137)
    echo "check-pytest-log-tail: CANNOT-ASSESS — the runner's --self-test exceeded its ${selftest_bound}s bound; a slow box is not a verdict" >&2
    exit 2
    ;;
  *)
    echo "check-pytest-log-tail: NOT-OK — the runner's --self-test is red (rc $rc): a failing suite would not be nameable from the gate's own output" >&2
    exit 1
    ;;
esac
