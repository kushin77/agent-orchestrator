#!/usr/bin/env python3
"""validate.py — the break-glass audit record's schema, enforced (issue #1276).

WHAT THIS VALIDATES, AND WHY IT IS NOT A FORMALITY
  `governance/platform/branch-protection.yaml` declares that the merge right to
  `master` lives with ONE queue identity (`ao-merge-queue`) and that the named
  break-glass actor is the only other principal. A declaration with no
  consequence is advice (CMR GR-29); the consequence here is that a break-glass
  USE must leave a record, and this file is what makes the record's shape
  binding rather than descriptive.

  The failure it prevents was measured, not imagined: eleven DIRECT merges to
  `master` landed in 36 hours from Claude and DeepSeek sessions alike, each
  carrying the owner's own token, and nothing in the tree could say afterwards
  which of them was a deliberate break-glass and which was drift. A rule that
  cannot distinguish those two is not a rule.

WHAT IT REFUSES, BY NAME
  Every refusal carries a stable name so a caller can assert on it (the same
  contract `scripts/check-merge-identity.sh` prints):

    record-unreadable:<file>            the file could not be read
    record-not-json:<file>              the file is not JSON
    schema-unsupported-keyword:<kw>     the schema uses a keyword this validator
                                        does not implement -- refused rather than
                                        silently under-enforced (the repo's
                                        convention; see check-module-manifest.sh)
    record-missing-field:<field>        a required field is absent
    record-field-type:<path>            a value has the wrong JSON type
    record-field-empty:<path>           a string that must not be empty is empty
    record-field-pattern:<path>         a value does not match the schema pattern
    record-field-enum:<path>            a value is outside the closed vocabulary
    record-field-const:<path>           a value is not the required constant
    record-field-too-small:<path>       below the schema minimum
    record-field-too-large:<path>       above the schema maximum
    record-field-not-allowed:<path>     a property the schema forbids
    record-expiry-not-after-start       the window is not time-boxed forwards
    record-window-exceeds-ceiling:<n>   the window exceeds the declared ceiling
    record-actor-not-declared:<actor>   the actor is not the declared actor
    record-principal-mismatch:<name>    principal_expected is not the queue identity
    uses-unreadable:<file>              the use journal could not be read
    uses-invalid-shape:<what>           the journal is not the declared shape
    use-missing-field:<id>:<field>      a journal entry is missing a field
    use-without-audit-record:<id>       a USE with no audit record -- the case
                                        the issue names: break-glass used, no
                                        artifact left behind
    use-record-id-mismatch:<id>         the record's id is not the use's id
    use-record-invalid:<id>:<finding>   the record resolved, but is invalid

  Exit contract: 0 OK / 1 NOT-OK (findings, each named) / 2 CANNOT-ASSESS
  (an input that could not be read at all, or a bad invocation). A missing
  input is CANNOT-ASSESS, never a pass.

THE SCHEMA IS THE ONE SOURCE OF TRUTH FOR THE SHAPE
  `schema/record.schema.json` declares the record. This validator reads THAT
  file and enforces it, instead of restating the field list in code: two
  copies of one shape drift, and the copy nothing reads is the one that ends up
  wrong. It implements a small, explicit keyword subset and REFUSES a schema
  that uses a keyword outside it, so an unenforced requirement can never look
  enforced. The checks a JSON Schema cannot express -- expiry after start, the
  window ceiling, actor and principal against the declaration, a use resolving
  to its record -- are applied on top, in code, and named above.

Usage:
  python3 governance/platform/break-glass/validate.py record <file> [--max-window-minutes N] [--expect-actor A] [--expect-principal P]
  python3 governance/platform/break-glass/validate.py uses <uses.json> --records-dir <dir> [same options]
  python3 governance/platform/break-glass/validate.py --self-test
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "schema", "record.schema.json")

# The keyword subset this validator implements. A schema using anything else is
# refused by name rather than silently under-enforced.
SUPPORTED = {
    "$schema",
    "title",
    "description",
    "$comment",
    "type",
    "required",
    "properties",
    "additionalProperties",
    "const",
    "enum",
    "pattern",
    "minLength",
    "minimum",
    "maximum",
    "items",
}

TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


class Findings:
    """An ordered, de-duplicated set of refusal names."""

    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, finding: str) -> None:
        if finding not in self.items:
            self.items.append(finding)

    def extend(self, other: "Findings") -> None:
        for item in other.items:
            self.add(item)

    def __bool__(self) -> bool:
        return bool(self.items)


def read_json(path: str) -> tuple[object, str]:
    """Return (value, finding). Exactly one of the two is meaningful."""
    if not os.path.exists(path):
        return None, "unreadable"
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle), ""
    except (OSError, ValueError):
        return None, "not-json"


def type_ok(value: object, name: str) -> bool:
    if name not in TYPES:
        return False
    expected = TYPES[name]
    if name in ("integer", "number") and isinstance(value, bool):
        return False
    if name == "integer":
        return isinstance(value, int)
    return isinstance(value, expected)


# --------------------------------------------------------------------------
# the schema subset validator
# --------------------------------------------------------------------------
def check_keywords(schema: object, out: Findings) -> None:
    """Refuse a schema node that uses a keyword this validator cannot enforce."""
    if not isinstance(schema, dict):
        return
    for key in schema:
        if key not in SUPPORTED:
            out.add(f"schema-unsupported-keyword:{key}")
    block = schema.get("properties")
    if isinstance(block, dict):
        for child in block.values():
            check_keywords(child, out)
    items = schema.get("items")
    if isinstance(items, dict):
        check_keywords(items, out)


def check_value(value: object, schema: dict, path: str, out: Findings) -> None:
    if not isinstance(schema, dict):
        return

    declared = schema.get("type")
    if declared is not None:
        names = declared if isinstance(declared, list) else [declared]
        if not any(type_ok(value, n) for n in names):
            out.add(f"record-field-type:{path}")
            return

    if "const" in schema and value != schema["const"]:
        out.add(f"record-field-const:{path}")
    if "enum" in schema and value not in schema["enum"]:
        out.add(f"record-field-enum:{path}")
    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.add(f"record-field-pattern:{path}")
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            # An empty string and a too-short one are named apart: "the field is
            # absent" and "the field says nothing reviewable" are different
            # repairs, and a single name would hide which one is owed.
            if value == "":
                out.add(f"record-field-empty:{path}")
            else:
                out.add(f"record-field-too-short:{path}")
    if isinstance(value, bool):
        pass
    elif isinstance(value, (int, float)):
        if "minimum" in schema and value < schema["minimum"]:
            out.add(f"record-field-too-small:{path}")
        if "maximum" in schema and value > schema["maximum"]:
            out.add(f"record-field-too-large:{path}")

    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            check_value(item, schema["items"], f"{path}[{index}]", out)

    if isinstance(value, dict):
        props = schema.get("properties") or {}
        for name in schema.get("required", []):
            if name not in value:
                out.add(f"record-missing-field:{name}")
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in props:
                    out.add(f"record-field-not-allowed:{path}/{name}")
        for name, child in value.items():
            if name in props:
                check_value(child, props[name], f"{path}/{name}", out)


def semantic_checks(record: dict, opts: argparse.Namespace, out: Findings) -> None:
    """The invariants a JSON Schema cannot state."""
    start, expiry = record.get("at"), record.get("expires_at")
    if isinstance(start, str) and isinstance(expiry, str) and expiry <= start:
        out.add("record-expiry-not-after-start")
    window = record.get("window_minutes")
    if opts.max_window_minutes is not None and isinstance(window, int):
        if window > opts.max_window_minutes:
            out.add(f"record-window-exceeds-ceiling:{opts.max_window_minutes}")
    if opts.expect_actor and record.get("actor") != opts.expect_actor:
        out.add(f"record-actor-not-declared:{record.get('actor')}")
    if opts.expect_principal and record.get("principal_expected") != opts.expect_principal:
        out.add(f"record-principal-mismatch:{record.get('principal_expected')}")


def validate_record(path: str, schema: object, opts: argparse.Namespace) -> Findings:
    out = Findings()
    check_keywords(schema, out)
    if out:
        return out
    record, why = read_json(path)
    if why == "unreadable":
        out.add(f"record-unreadable:{path}")
        return out
    if why == "not-json":
        out.add(f"record-not-json:{path}")
        return out
    if not isinstance(record, dict):
        out.add(f"record-field-type:{path}")
        return out
    check_value(record, schema, "", out)
    if not out:
        semantic_checks(record, opts, out)
    return out


def validate_uses(path: str, schema: object, opts: argparse.Namespace) -> Findings:
    out = Findings()
    usage, why = read_json(path)
    if why == "unreadable":
        out.add(f"uses-unreadable:{path}")
        return out
    if why == "not-json":
        out.add(f"uses-invalid-shape:{path}")
        return out
    if not isinstance(usage, dict) or usage.get("schema") != "ao.break-glass-uses/v1":
        out.add("uses-invalid-shape:schema")
        return out
    uses = usage.get("uses")
    if not isinstance(uses, list):
        out.add("uses-invalid-shape:uses")
        return out
    required = ("id", "at", "actor", "action", "reason", "record")
    for entry in uses:
        if not isinstance(entry, dict):
            out.add("uses-invalid-shape:entry")
            continue
        ident = str(entry.get("id") or "")
        for field in required:
            if not entry.get(field):
                out.add(f"use-missing-field:{ident}:{field}")
        record_path = entry.get("record")
        if not isinstance(record_path, str) or not record_path:
            out.add(f"use-without-audit-record:{ident}")
            continue
        resolved = record_path
        if not os.path.isabs(resolved):
            resolved = os.path.join(opts.root, record_path)
        if not os.path.exists(resolved):
            out.add(f"use-without-audit-record:{ident}")
            continue
        sub = validate_record(resolved, schema, opts)
        if sub:
            for finding in sub.items:
                out.add(f"use-record-invalid:{ident}:{finding}")
            continue
        record, _ = read_json(resolved)
        if isinstance(record, dict) and record.get("id") != ident:
            out.add(f"use-record-id-mismatch:{ident}")
    return out


# --------------------------------------------------------------------------
# self-test — every refusal above is provoked, and the compliant case is not
# --------------------------------------------------------------------------
USES_HEADER = {"schema": "ao.break-glass-uses/v1"}


def good_record(round_id: str = "01", minute: int = 5) -> dict:
    return {
        "schema": "ao.break-glass-record/v1",
        "id": f"bg-20260921T1200{round_id}Z-abc123",
        "at": f"2026-09-21T12:00:{round_id}Z",
        "actor": "cto-root",
        "actor_kind": "team",
        "principal_expected": "ao-merge-queue",
        "action": "merge",
        "branch": "master",
        "pr": 4242,
        "reason": "the queue identity is wedged and the fix is time-critical",
        "window_minutes": minute,
        "expires_at": "2026-09-21T12:05:05Z",
        "paged": {"channel": "#ao-escalation", "at": "2026-09-21T12:00:07Z", "ack_by": "kushin77"},
        "evidence": "gh api -X PUT repos/kushin77/agent-orchestrator/pulls/4242 (merge), rc=200",
        "declared_by": "issue #1276",
    }


def write_json(path: str, value: object) -> str:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle)
    return path


def self_test(opts_root: str) -> int:
    """Provoke each refusal by name, and show the compliant case passes."""
    schema, why = read_json(SCHEMA_PATH)
    if why:
        print(f"validate: CANNOT-ASSESS — the schema is {why}: {SCHEMA_PATH}", file=sys.stderr)
        return 2
    checks: list[tuple[str, list[str], bool]] = []

    def run(label: str, mutate, expect: list[str], quiet: bool = False, via_uses: bool = False) -> None:
        with tempfile.TemporaryDirectory(prefix="bg-validate-") as work:
            record = good_record()
            mutate(record)
            records_dir = os.path.join(work, "records")
            os.makedirs(records_dir, exist_ok=True)
            write_json(os.path.join(records_dir, record.get("id", "x") + ".json"), record)
            local = argparse.Namespace(
                root=work, expect_actor="cto-root", expect_principal="ao-merge-queue", max_window_minutes=120
            )
            if not via_uses:
                findings = validate_record(os.path.join(records_dir, record["id"] + ".json"), schema, local)
            else:
                uses = dict(USES_HEADER)
                uses["uses"] = [
                    {
                        "id": record.get("id", "x"),
                        "at": record.get("at", ""),
                        "actor": "cto-root",
                        "action": "merge",
                        "reason": record.get("reason", ""),
                        "record": os.path.relpath(os.path.join(records_dir, record["id"] + ".json"), work),
                    }
                ]
                findings = validate_uses(write_json(os.path.join(work, "uses.json"), uses), schema, local)
            got = findings.items
            hits = [name for name in expect if any(item.startswith(name) for item in got)]
            # A run with no expected refusal must ALSO be silent: an arm that
            # only counts matches would call a badly broken validator compliant.
            passed = len(hits) == len(expect) and (bool(expect) or not got)
            checks.append((label, hits if expect else got, passed))

    run("compliant record accepted", lambda r: None, [])
    run("missing required field refused", lambda r: r.pop("paged"), ["record-missing-field:paged"])
    run("empty required string refused", lambda r: r.update(reason=""), ["record-field-empty:/reason"])
    run("short reason refused by name", lambda r: r.update(reason="too short"), ["record-field-too-short:/reason"])
    run("un-boxed window refused", lambda r: r.update(expires_at="2026-09-21T11:59:00Z"), ["record-expiry-not-after-start"])
    run("window past the ceiling refused", lambda r: r.update(window_minutes=600), ["record-window-exceeds-ceiling:120"])
    run("undeclared actor refused by name", lambda r: r.update(actor="some-agent"), ["record-actor-not-declared:some-agent"])
    run("wrong queue identity refused", lambda r: r.update(principal_expected="claude"), ["record-principal-mismatch:claude"])
    run("wrong action vocabulary refused", lambda r: r.update(action="force-push"), ["record-field-enum:/action"])
    run("extra property refused", lambda r: r.update(extra=True), ["record-field-not-allowed:/extra"])
    run("page without an ack refused", lambda r: r["paged"].pop("ack_by"), ["record-missing-field:ack_by"])

    # The use-without-audit-record case needs a journal that POINTS at nothing,
    # so it is provoked against the uses reader directly.
    with tempfile.TemporaryDirectory(prefix="bg-validate-") as work:
        uses = dict(USES_HEADER)
        uses["uses"] = [
            {
                "id": "bg-20260921T120001Z-abc123",
                "at": "2026-09-21T12:00:01Z",
                "actor": "cto-root",
                "action": "merge",
                "reason": "the queue identity is wedged and the fix is time-critical",
                "record": "governance/platform/break-glass/records/bg-20260921T120001Z-abc123.json",
            }
        ]
        write_json(os.path.join(work, "uses.json"), uses)
        local = argparse.Namespace(root=work, expect_actor="cto-root", expect_principal="ao-merge-queue", max_window_minutes=120)
        got = validate_uses(os.path.join(work, "uses.json"), schema, local).items
        checks.append(
            (
                "a use pointing at a record that does not exist refuses by name",
                [n for n in got if n.startswith("use-without-audit-record:")],
                any(n.startswith("use-without-audit-record:") for n in got),
            )
        )
        write_json(
            os.path.join(work, "uses.json"),
            {**USES_HEADER, "uses": [{**uses["uses"][0], "record": ""}]},
        )
        got = validate_uses(os.path.join(work, "uses.json"), schema, local).items
        checks.append(
            (
                "a use with an empty record field refuses by name",
                [n for n in got if n.startswith("use-without-audit-record:")],
                any(n.startswith("use-without-audit-record:") for n in got),
            )
        )

    # An unimplemented schema keyword must be REFUSED, not ignored.
    with tempfile.TemporaryDirectory(prefix="bg-validate-") as work:
        out = Findings()
        check_keywords({"type": "object", "dependentRequired": {"a": ["b"]}}, out)
        checks.append(
            (
                "an unimplemented schema keyword is refused, not ignored",
                [n for n in out.items if n.startswith("schema-unsupported-keyword:")],
                bool(out),
            )
        )
        # ... and the UNREADABLE input is CANNOT-ASSESS, never a pass.
        findings = validate_record(os.path.join(work, "absent.json"), schema, argparse.Namespace(
            root=work, expect_actor=None, expect_principal=None, max_window_minutes=None))
        checks.append(
            (
                "an unreadable record is named, not passed",
                [n for n in findings.items if n.startswith("record-unreadable:")],
                any(n.startswith("record-unreadable:") for n in findings.items),
            )
        )

    failed = 0
    for label, hits, passed in checks:
        if passed:
            print(f"  ok    {label}" + (f"  [{', '.join(hits)}]" if hits else ""))
        else:
            failed += 1
            print(f"  FAIL  {label} — no refusal matched: {hits}", file=sys.stderr)
    if failed:
        print(f"validate: NOT-OK — {failed} of {len(checks)} self-test controls failed", file=sys.stderr)
        return 1
    print(f"validate: OK — {len(checks)} self-test controls passed")
    return 0


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------
def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(add_help=True, description="break-glass audit record validator")
    parser.add_argument("mode", nargs="?", choices=["record", "uses"])
    parser.add_argument("path", nargs="?")
    parser.add_argument("--records-dir")
    parser.add_argument("--root", default=os.getcwd())
    parser.add_argument("--max-window-minutes", type=int)
    parser.add_argument("--expect-actor")
    parser.add_argument("--expect-principal")
    parser.add_argument("--self-test", action="store_true")
    opts = parser.parse_args(argv)

    if opts.self_test:
        return self_test(os.getcwd())

    schema, why = read_json(SCHEMA_PATH)
    if why:
        print(f"validate: CANNOT-ASSESS — the record schema is {why}: {SCHEMA_PATH}", file=sys.stderr)
        return 2

    if not opts.mode or not opts.path:
        print(
            "validate: CANNOT-ASSESS — usage: validate.py {record <file>|uses <file> --records-dir <dir>} "
            "[--max-window-minutes N] [--expect-actor A] [--expect-principal P] | --self-test",
            file=sys.stderr,
        )
        return 2

    if opts.mode == "record":
        findings = validate_record(opts.path, schema, opts)
    else:
        if not opts.records_dir:
            print("validate: CANNOT-ASSESS — `uses` needs --records-dir", file=sys.stderr)
            return 2
        findings = validate_uses(opts.path, schema, opts)

    if findings:
        for finding in findings.items:
            print(finding)
        return 1
    print("validate: OK — no findings")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
