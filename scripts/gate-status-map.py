#!/usr/bin/env python3
"""Map a gate of record outcome to a GitHub commit-status state (ADR-0028).

The gate of record is a tri-state (`scripts/verify.sh` and every check under it):
0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. GitHub's commit-status API accepts four
states: error / failure / pending / success. This module is the ONLY place that
translation happens, and it lives in its own file so the gate that proves the
translation can drive the very same function the poster uses -- a proof that
exercises a copy proves nothing about the path that runs.

Why the mapping is not a one-liner:

    rc 2 (CANNOT-ASSESS) must map to `error`, NOT to `success` and NOT to
    `failure`.

Folding "I could not check" into `success` is the defect class this repository
has repeatedly fixed (#739: an unreadable HEAD read back as `healthy`, silently
disabling drift detection). Folding it into `failure` is the opposite error: it
trains an operator to ignore a red gate. The three outcomes have to stay
distinguishable or the status carries no information.

    an UNKNOWN outcome must be REFUSED, never defaulted.

A mapper that returns `success` for anything it does not recognise is a control
that cannot fail. So an unrecognised rc is a refusal (exit 2), not a guess.

Usage:
    gate-status-map.py <rc>          -> prints the state, exit 0
    gate-status-map.py --self-test   -> asserts the whole table, exit 0/1
"""
from __future__ import annotations

import sys

# The gate's tri-state, mapped to the status vocabulary. Exhaustive by
# construction: anything absent from this table is a refusal.
MAP = {
    0: "success",   # verify: PASS
    1: "failure",   # verify: FAIL
    2: "error",     # CANNOT-ASSESS -- published as an error, never a pass
}

# Contexts GitHub accepts as a status state. Used to validate, not to guess.
VALID_STATES = frozenset({"error", "failure", "pending", "success"})

CONTEXT = "ao/gate-of-record"


def map_rc(rc: int) -> str:
    """Return the status state for a gate exit code, or raise on an unknown one."""
    if rc not in MAP:
        raise ValueError(
            f"gate exit code {rc!r} is not a gate of record outcome "
            f"(expected 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS); refusing to guess a status"
        )
    state = MAP[rc]
    if state not in VALID_STATES:
        raise ValueError(f"mapped state {state!r} is not a valid commit status")
    return state


def summarize(rc: int) -> str:
    """A human description, so the status is readable without the gate log."""
    return {
        0: "make verify: PASS",
        1: "make verify: FAIL",
        2: "make verify: CANNOT-ASSESS (not a pass)",
    }.get(rc, f"make verify: unknown outcome ({rc})")


def self_test() -> int:
    """Assert the whole table. Called by scripts/check-gate-status.sh."""
    problems = []

    expected = {0: "success", 1: "failure", 2: "error"}
    for rc, want in expected.items():
        got = map_rc(rc)
        if got != want:
            problems.append(f"rc {rc} mapped to {got!r}, expected {want!r}")

    # The three outcomes must be DISTINGUISHABLE. A mapper is useless if two of
    # them collapse into the same state -- that is exactly how a CANNOT-ASSESS
    # becomes a false green.
    states = {map_rc(rc) for rc in expected}
    if len(states) != 3:
        problems.append(f"the three gate outcomes collapse into {len(states)} state(s): {sorted(states)}")

    # CANNOT-ASSESS must never be a pass. This is the #739 defect class.
    if map_rc(2) == "success":
        problems.append("CANNOT-ASSESS maps to success -- a false green, the #739 defect class")

    # Unknown outcomes must be REFUSED, not defaulted.
    for bogus in (3, 99, -1):
        try:
            got = map_rc(bogus)
            problems.append(f"unknown rc {bogus} was NOT refused (returned {got!r})")
        except ValueError:
            pass

    for problem in problems:
        print(f"  FAIL  {problem}", file=sys.stderr)
    if problems:
        return 1
    print("  OK  the mapping is exhaustive, distinguishable, and refuses the unknown")
    for rc, want in sorted(expected.items()):
        print(f"      rc {rc} -> {want}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] == "--self-test":
        return self_test()
    if len(argv) != 2:
        print("usage: gate-status-map.py <rc> | --self-test", file=sys.stderr)
        return 2
    try:
        rc = int(argv[1])
    except ValueError:
        print(f"gate-status-map: CANNOT-ASSESS -- {argv[1]!r} is not an exit code", file=sys.stderr)
        return 2
    try:
        print(map_rc(rc))
    except ValueError as exc:
        print(f"gate-status-map: REFUSED -- {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
