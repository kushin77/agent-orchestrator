#!/usr/bin/env python3
"""Compare a DECLARED branch-protection policy against the LIVE protection.

Exit 0 when every declared field matches, 1 on any drift, 2 when it cannot tell.

This lives in its own file on purpose. The gate that proves branch protection
cannot silently vanish must exercise **the same comparator** `branch-protection.sh
verify` uses -- a provocation that drives a different code path proves nothing
about the path that actually runs. Measured lesson: a mutation harness that
asserted its own mutant had been caught while grepping a string that a *passing*
run also printed certified nothing at all.

Usage:
    branch-protection-compare.py <declared.json> <live.json>

declared.json is `{"want": {...}, "has": {...}}` (see branch-protection.sh);
live.json is the raw protection object GitHub returns.
"""
from __future__ import annotations

import json
import sys


def flatten(document: dict) -> dict:
    """Reduce GitHub's protection shape to comparable scalars.

    Most fields arrive as ``{"enabled": bool}``; the three nullable policy fields
    (status checks, reviews, restrictions) arrive as ``null``. Anything else is
    already a scalar. The comparison must not invent a difference merely because
    the wire format is nested.
    """
    out: dict = {}
    for key, value in (document or {}).items():
        if isinstance(value, dict) and "enabled" in value:
            out[key] = value["enabled"]
        else:
            out[key] = value
    return out


def _normalize_status_checks(value: object) -> object:
    """Reduce ``required_status_checks`` to the fields the declaration owns.

    GitHub's wire shape carries server-generated fields (``url``,
    ``contexts_url``, ``checks``) alongside the two the declaration actually
    controls (``strict``, ``contexts``). Comparing the raw dicts would report
    drift on every read purely from those generated fields, which is a false
    positive, not a control — so this keeps only ``strict``/``contexts`` and
    sorts the contexts so their ORDER cannot manufacture a difference either.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        return value
    contexts = value.get("contexts") or []
    return {"strict": bool(value.get("strict", False)), "contexts": sorted(contexts)}


def compare(declared: dict, live: dict) -> list[tuple[str, object, object]]:
    """Return the (field, declared, live) triples that disagree."""
    want = declared.get("want", {})
    has = declared.get("has", {})
    got = flatten(live)
    drift = []
    for field, expected in want.items():
        actual = got.get(has.get(field, field))
        if field == "required_status_checks":
            actual = _normalize_status_checks(actual)
            expected = _normalize_status_checks(expected)
        if actual != expected:
            drift.append((field, expected, actual))
    return drift


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: branch-protection-compare.py <declared.json> <live.json>", file=sys.stderr)
        return 2
    try:
        declared = json.load(open(sys.argv[1]))
        live = json.load(open(sys.argv[2]))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"branch-protection-compare: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return 2

    if live.get("__unprotected__"):
        print("branch-protection: NOT-OK — the branch is NOT PROTECTED at all", file=sys.stderr)
        return 1

    drift = compare(declared, live)
    if drift:
        for field, expected, actual in drift:
            print(f"  DRIFT {field}: declared={expected!r} live={actual!r}")
        print(f"branch-protection: NOT-OK — {len(drift)} field(s) drifted from the declaration")
        return 1

    print(f"  OK  all {len(declared.get('want', {}))} declared field(s) match the live protection")
    return 0


if __name__ == "__main__":
    sys.exit(main())
