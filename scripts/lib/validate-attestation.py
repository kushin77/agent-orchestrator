#!/usr/bin/env python3
"""validate-attestation.py — validate a .verify/attestation.json against its
schema (issue #882).

Prefers `jsonschema` when it is importable (the general validator); falls back
to a small hand-rolled STRICT validator that checks exactly the same required
shape as `governance/isolation/attestation.schema.json` so the gate is never
disabled by a missing optional dependency (no-false-green doctrine, GR-12).

The negative control this exists to refuse: a fabricated attestation that
marks a FAILING check OK. That is not a schema-shape defect (the fabricated
document is otherwise well-formed), so both the shape validation AND a
semantic cross-check are performed here: a check whose recorded `rc` is
non-zero and non-two (the tri-state's genuine-failure code) must never carry
verdict OK, and the top-level `overall_verdict` must be the worst of the
per-check verdicts.

The same class, one level out (issue #1199): a run that records a SKIP must
ACCOUNT for it in `skip_ratchet` -- a skip nothing names is a standing gap the
board cannot read, and a record claiming `verdict: OK` while it carries
unbudgeted skips, stale exemptions or findings of its own is a false green of
exactly the same kind.

Usage:
  python3 scripts/lib/validate-attestation.py <attestation.json> <schema.json>

Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
"""
from __future__ import annotations

import json
import sys

_VERDICT_RANK = {"OK": 0, "WARN": 1, "FAIL": 2}


def _fail(msg: str) -> None:
    print(f"validate-attestation: NOT-OK — {msg}", file=sys.stderr)


def _cannot_assess(msg: str) -> None:
    print(f"validate-attestation: CANNOT-ASSESS — {msg}", file=sys.stderr)


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _hand_validate_shape(doc: dict) -> list[str]:
    """The same required shape as the schema, checked by hand. Returns findings."""
    findings: list[str] = []
    if not isinstance(doc, dict):
        return ["attestation-not-an-object"]
    for key in ("run_id", "git_sha", "overall_verdict", "checks"):
        if key not in doc:
            findings.append(f"missing-field:{key}")
    if "run_id" in doc and not (isinstance(doc["run_id"], str) and doc["run_id"]):
        findings.append("run_id-not-a-nonempty-string")
    if "overall_verdict" in doc and doc["overall_verdict"] not in _VERDICT_RANK:
        findings.append("overall_verdict-not-tri-state")
    checks = doc.get("checks")
    if not isinstance(checks, list) or not checks:
        findings.append("checks-not-a-nonempty-array")
        checks = []
    for i, chk in enumerate(checks):
        if not isinstance(chk, dict):
            findings.append(f"check[{i}]-not-an-object")
            continue
        for key in ("name", "verdict", "rc", "duration", "evidence_tail"):
            if key not in chk:
                findings.append(f"check[{i}]-missing-field:{key}")
        if "verdict" in chk and chk["verdict"] not in _VERDICT_RANK:
            findings.append(f"check[{i}]-verdict-not-tri-state")
        if "rc" in chk and not isinstance(chk["rc"], int):
            findings.append(f"check[{i}]-rc-not-an-integer")
        if "duration" in chk and not isinstance(chk["duration"], (int, float)):
            findings.append(f"check[{i}]-duration-not-a-number")
        if "evidence_tail" in chk and not isinstance(chk["evidence_tail"], str):
            findings.append(f"check[{i}]-evidence_tail-not-a-string")
    return findings


def _semantic_findings(doc: dict) -> list[str]:
    """The no-false-green cross-check the schema alone cannot express."""
    findings: list[str] = []
    checks = doc.get("checks") if isinstance(doc.get("checks"), list) else []
    worst = "OK"
    skipped: list[str] = []
    for i, chk in enumerate(checks):
        if not isinstance(chk, dict):
            continue
        rc = chk.get("rc")
        verdict = chk.get("verdict")
        if isinstance(rc, int) and rc not in (0, 2) and verdict == "OK":
            findings.append(f"check[{i}]-red-reported-ok:{chk.get('name', '?')}")
        if isinstance(rc, int) and rc == 2 and verdict == "OK":
            findings.append(f"check[{i}]-cannot-assess-reported-ok:{chk.get('name', '?')}")
        if isinstance(rc, int) and rc == 2:
            skipped.append(str(chk.get("name", "?")))
        if verdict in _VERDICT_RANK and _VERDICT_RANK[verdict] > _VERDICT_RANK[worst]:
            worst = verdict
    overall = doc.get("overall_verdict")
    if overall in _VERDICT_RANK and _VERDICT_RANK[overall] < _VERDICT_RANK[worst]:
        findings.append(f"overall-verdict-better-than-worst-check:{overall}<{worst}")

    # The skip ratchet (issue #1199): a skip the record does not account for is
    # the same class of false green as a red reported OK -- the verdict line and
    # the counters would say "N skipped" while nothing named WHICH standing gap
    # that is. The record must therefore account for exactly the skips this run
    # recorded, and may not claim OK while it carries findings of its own.
    ratchet = doc.get("skip_ratchet")
    if skipped and not isinstance(ratchet, dict):
        findings.append(
            "skip-ratchet-missing:%d check(s) recorded SKIP with no skip_ratchet record"
            % len(skipped)
        )
    elif isinstance(ratchet, dict):
        named = {
            str(e.get("check"))
            for e in ratchet.get("standing_skips", [])
            if isinstance(e, dict)
        }
        unbudgeted = {
            str(n) for n in ratchet.get("unbudgeted_skips", []) if isinstance(n, str)
        }
        for name in skipped:
            if name not in named and name not in unbudgeted:
                findings.append(f"skip-not-accounted:{name}")
        for name in named | unbudgeted:
            if name not in skipped:
                findings.append(f"skip-ratchet-accounts-for-a-check-that-did-not-skip:{name}")
        stale = ratchet.get("stale_entries")
        findings_list = ratchet.get("findings")
        if ratchet.get("verdict") == "OK" and (
            unbudgeted or (isinstance(stale, list) and stale) or (isinstance(findings_list, list) and findings_list)
        ):
            findings.append("skip-ratchet-verdict-ok-while-carrying-findings")
    skipped_count = doc.get("skipped")
    if isinstance(skipped_count, int) and skipped_count != len(skipped):
        findings.append(
            f"skipped-count-disagrees-with-the-checks:{skipped_count}!={len(skipped)}"
        )
    return findings


def _validate_with_jsonschema(doc: dict, schema: dict) -> list[str]:
    import jsonschema  # type: ignore

    validator = jsonschema.Draft7Validator(schema)
    return [
        f"{'/'.join(str(p) for p in err.path) or '<root>'}: {err.message}"
        for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
    ]


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: validate-attestation.py <attestation.json> <schema.json>", file=sys.stderr)
        return 2

    attestation_path, schema_path = argv[1], argv[2]

    try:
        doc = _load(attestation_path)
    except (OSError, json.JSONDecodeError) as exc:
        _cannot_assess(f"cannot read/parse {attestation_path}: {exc}")
        return 2

    try:
        schema = _load(schema_path)
    except (OSError, json.JSONDecodeError) as exc:
        _cannot_assess(f"cannot read/parse {schema_path}: {exc}")
        return 2

    shape_findings: list[str]
    try:
        import jsonschema  # noqa: F401  (import-only probe)

        shape_findings = _validate_with_jsonschema(doc, schema)
        engine = "jsonschema"
    except ImportError:
        shape_findings = _hand_validate_shape(doc)
        engine = "hand-validator (jsonschema not installed)"

    semantic_findings = _semantic_findings(doc)
    findings = shape_findings + semantic_findings

    if findings:
        for f in findings:
            print(f"  FAIL  {f}", file=sys.stderr)
        _fail(f"{len(findings)} finding(s) via {engine}")
        return 1

    print(f"validate-attestation: OK — {attestation_path} conforms to {schema_path} ({engine})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
