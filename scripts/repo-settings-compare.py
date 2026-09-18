#!/usr/bin/env python3
"""Compare a DECLARED repo-settings policy against the LIVE repo settings.

Exit 0 when every declared field matches, 1 on any drift, 2 when it cannot tell.

This lives in its own file on purpose, mirroring scripts/branch-protection-
compare.py: the gate that proves the merge-message policy cannot silently
drift must exercise the SAME comparator `scripts/repo-settings.sh verify`
uses — a provocation that drives a different code path proves nothing about
the path that actually runs.

Usage:
    repo-settings-compare.py <declared.json> <live.json>

declared.json is `{"want": {...}}` (see repo-settings.sh); live.json is the
raw object `gh api repos/<owner>/<repo>` returns (a flat dict of scalars for
every field this policy declares — no nesting to unwrap, unlike branch
protection's `{"enabled": bool}` shape).
"""
from __future__ import annotations

import json
import sys


def compare(declared: dict, live: dict) -> list[tuple[str, object, object]]:
    """Return the (field, declared, live) triples that disagree."""
    want = declared.get("want", {})
    drift = []
    for field, expected in want.items():
        actual = live.get(field)
        if actual != expected:
            drift.append((field, expected, actual))
    return drift


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: repo-settings-compare.py <declared.json> <live.json>", file=sys.stderr)
        return 2
    try:
        declared = json.load(open(sys.argv[1]))
        live = json.load(open(sys.argv[2]))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"repo-settings-compare: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return 2

    drift = compare(declared, live)
    if drift:
        for field, expected, actual in drift:
            print(f"  DRIFT {field}: declared={expected!r} live={actual!r}")
        print(f"repo-settings: NOT-OK — {len(drift)} field(s) drifted from the declaration")
        return 1

    print(f"  OK  all {len(declared.get('want', {}))} declared field(s) match the live settings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
