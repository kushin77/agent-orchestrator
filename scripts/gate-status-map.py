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

A DESCRIPTION can also carry a DETAIL (#1407), so a red PR page can name the
check that failed instead of reading the same fixed string on every red:

    summarize(1)                     -> "make verify: FAIL"      (unchanged)
    summarize(1, "check-reconcile")  -> "make verify: FAIL -- check-reconcile"

The detail is a SUFFIX on the description and nothing else. The outcome is
`map_rc`'s, and no detail is an input to it, so no detail can turn a
CANNOT-ASSESS into a pass -- the #739 false-green class. It is BOUNDED here,
because GitHub caps a status description at DESCRIPTION_MAX characters and
would truncate the overflow itself, at a position this module does not choose.

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

# GitHub caps a commit-status `description` at 140 characters and truncates the
# overflow ITSELF, so an appended detail without a bound loses its TAIL wherever
# the API happens to cut -- not the least useful part. The bound is applied here
# instead, deliberately, and the cut is marked.
DESCRIPTION_MAX = 140

# Between the fixed outcome string and a supplied detail. Both are ASCII, so the
# description a reader sees is the description this module built.
DETAIL_SEP = " -- "

# Appended when a detail had to be cut, so a truncated description never reads as
# a complete (and therefore wrong) check name.
DETAIL_CUT = "\u2026"


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


def summarize(rc: int, detail: str | None = None) -> str:
    """A human description, so the status is readable without the gate log.

    Called with ONE argument this returns exactly the string it always has. A
    `detail` is appended to that string -- never substituted for it, and never a
    second source of truth for the outcome, which is `map_rc`'s and is not an
    input here at all.
    """
    base = {
        0: "make verify: PASS",
        1: "make verify: FAIL",
        2: "make verify: CANNOT-ASSESS (not a pass)",
    }.get(rc, f"make verify: unknown outcome ({rc})")
    if not detail:
        return base
    return _append_detail(base, detail)


def _append_detail(base: str, detail: str) -> str:
    """`base` with `detail` appended, bounded to what the status API accepts.

    A status description is ONE line of at most DESCRIPTION_MAX characters, and
    a detail harvested from a run (a failing check name, a log line) arrives with
    newlines and indentation, so the whitespace is collapsed first. The fixed
    outcome string is never the part that is cut: it is what makes the
    description mean anything, and the head of a check name is the identifying
    part, so the TAIL is what gives way.
    """
    flat = " ".join(str(detail).split())
    if not flat:
        return base
    room = DESCRIPTION_MAX - len(base) - len(DETAIL_SEP)
    if room < 1:
        # Unreachable for the outcome strings above, and the self-test proves it
        # stays unreachable: a future edit that shorts them is caught there, by
        # name, rather than here. Stated rather than left to crash, because a
        # description must never exceed the API's cap whatever the inputs are.
        return base[:DESCRIPTION_MAX]
    if len(flat) <= room:
        return base + DETAIL_SEP + flat
    return base + DETAIL_SEP + flat[: room - 1] + DETAIL_CUT


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

    # The DETAIL seam (#1407). Both halves are asserted, because the second is
    # the one that would read as a green: a detail that can displace the outcome
    # string, or turn rc 2 into a pass, is the #739 defect class.
    for rc in expected:
        if summarize(rc) != summarize(rc, None) or summarize(rc) != summarize(rc, ""):
            problems.append(f"rc {rc}: a no-detail call no longer returns the pinned string")
    wanted = "make verify: FAIL -- check-reconcile"
    if summarize(1, "check-reconcile") != wanted:
        problems.append(f"a detail is not appended to the outcome string: {summarize(1, 'check-reconcile')!r} != {wanted!r}")
    for rc in expected:
        if not summarize(rc, "success PASS").startswith(summarize(rc)):
            problems.append(f"rc {rc}: a detail displaced the outcome string it is a suffix of")
    if "CANNOT-ASSESS" not in summarize(2, "success PASS"):
        problems.append("a detail naming a pass hid the CANNOT-ASSESS outcome on rc 2 -- the #739 false green")
    cut = summarize(2, "c" * 500)
    if len(cut) > DESCRIPTION_MAX:
        problems.append(f"a 500-character detail produced a {len(cut)}-character description (cap {DESCRIPTION_MAX})")
    if not cut.startswith(summarize(2)) or not cut.endswith(DETAIL_CUT):
        problems.append("a truncated description neither keeps its outcome string nor says it was truncated")
    if summarize(1, "check-a\n  check-b") != "make verify: FAIL -- check-a check-b":
        problems.append(f"a multi-line detail is not flattened to one line: {summarize(1, 'check-a\n  check-b')!r}")
    for rc in expected:
        if DESCRIPTION_MAX - len(summarize(rc)) - len(DETAIL_SEP) < 1:
            problems.append(f"rc {rc}'s outcome string leaves no room for a detail inside the API's cap")

    for problem in problems:
        print(f"  FAIL  {problem}", file=sys.stderr)
    if problems:
        return 1
    print("  OK  the mapping is exhaustive, distinguishable, and refuses the unknown")
    for rc, want in sorted(expected.items()):
        print(f"      rc {rc} -> {want}")
    print("  OK  a detail is appended, bounded and flattened, and can never displace the outcome")
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
