#!/usr/bin/env python3
"""Control-verb vocabulary — validator and cross-reference (issue #553, EPIC #551).

`validate` is the check: it loads ``verbs.yaml``, enforces the closed sets the
schema declares, and — the part that matters — cross-references the four CLI
files that own the local levers, so the registry cannot drift from what the
fleet can actually do:

    fleet/control.py            18 verbs
    fleet/channel.py            13 verbs
    governance/dispatch/cli.py   8 verbs
    governance/reconcile/cli.py  5 verbs
    governance/lifecycle/cli.py  4 verbs

Two directions are checked, and both matter:

  * MISSING  — a surface verb with no registry entry. This is the contract-first
    enforcement: a producer lane that adds a verb to a lever file without landing
    the matching ``verbs.yaml`` + schema entry first is refused BY NAME.
  * ABSENT   — a registry entry whose local verb has disappeared. This is rot:
    the registry would describe a lever that no longer exists.

``SOURCES`` is the anti-vacuity FLOOR, not a closed set: it names the canonical
verbs each lever file must keep providing, and the gate refuses a canonical verb
that vanishes from BOTH the registry and the surface (a silent loss must never
read as a pass). A producer extends the surface only by landing a registry entry
first — never by editing ``SOURCES``.

Exit contract (this repo's tri-state): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
CANNOT-ASSESS must never read as a pass.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "control-plane" / "control" / "verbs.yaml"
SCHEMA = ROOT / "control-plane" / "control" / "schema" / "verbs.schema.json"

# The registry may declare a verb that is served BY the registry itself (the
# vocabulary is part of the API), so that one source is exempt from the
# "the file must contain this subcommand" direction.
SELF_SOURCE = "control-plane/control/verbs.yaml"

# file -> the canonical local verbs this file MUST keep providing. This is the
# anti-vacuity FLOOR, not a closed set: a canonical verb that vanishes from BOTH
# the surface and the registry is refused. A producer adds a verb only by landing
# the matching verbs.yaml entry first (contract-first) — never by editing this map.
SOURCES: dict[str, set[str]] = {
    "fleet/control.py": {
        "start", "status", "refresh", "update", "poke", "pause", "resume",
        "stop", "kill", "restart", "halt", "override", "debug", "watch",
        "health", "cron", "live", "attach",
    },
    "fleet/channel.py": {
        "verify", "send", "status", "report", "wait", "watch", "escalate",
        "listen", "order", "brain-inbox", "brain-outbox", "head-commit",
        "consume", "log", "follow", "kb", "steer",
    },
    "governance/dispatch/cli.py": {
        "audit", "eligible", "claim", "release", "status", "held", "reap",
        "snapshot",
    },
    "governance/reconcile/cli.py": {"stamp", "clear", "status", "sweep", "watch"},
    "governance/lifecycle/cli.py": {"audit", "status", "close", "collect"},
}

# How to read each file's verb list. A registration TUPLE for control.py (it
# builds its subparsers in a loop) and add_parser() calls for the rest.
ADD_PARSER = re.compile(r"""add_parser\(\s*["']([^"']+)["']""")
CONTROL_TUPLE = re.compile(r"""^\s*\(\s*["']([a-z][a-z0-9-]*)["']\s*,\s*cmd_""", re.M)

REQUIRED_SCAFFOLD = {401, 403, 503}


def load_registry() -> dict:
    with REGISTRY.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def verbs_in(path: str) -> set[str]:
    text = (ROOT / path).read_text(encoding="utf-8")
    if path == "fleet/control.py":
        found = set(CONTROL_TUPLE.findall(text))
    else:
        found = set(ADD_PARSER.findall(text))
    return found


def validate_schema(doc: dict) -> list[str]:
    """Closed-set and field-level checks. Returns findings (empty == OK)."""
    findings: list[str] = []

    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    if doc.get("schema") != schema["properties"]["schema"]["const"]:
        findings.append(
            f"schema: expected {schema['properties']['schema']['const']!r}, got {doc.get('schema')!r}"
        )

    classes = set((doc.get("effect_classes") or {}).keys())
    want_classes = set(schema["properties"]["effect_classes"]["required"])
    if classes != want_classes:
        findings.append(
            f"effect_classes: expected exactly {sorted(want_classes)}, got {sorted(classes)}"
        )

    refusals = {int(k) for k in (doc.get("refusals") or {})}
    if not refusals:
        findings.append("refusals: the closed refusal-code set is empty")

    seen: dict[str, int] = {}
    for i, v in enumerate(doc.get("verbs") or []):
        where = f"verbs[{i}] ({v.get('id', '?')})"

        for field in (
            "id", "source", "local", "effect_class",
            "capability", "audit", "idempotent", "exposed", "refusals",
        ):
            if field not in v:
                findings.append(f"{where}: missing required field {field!r}")
        if findings and "missing required field 'id'" in findings[-1]:
            continue

        vid = v.get("id")
        if vid is not None:
            if not re.match(r"^[a-z][a-z0-9-]*\.[a-z][a-z0-9-]*$", str(vid)):
                findings.append(f"{where}: id must be `<family>.<action>`, got {vid!r}")
            seen[vid] = seen.get(vid, 0) + 1

        ec = v.get("effect_class")
        if ec not in classes:
            findings.append(
                f"{where}: effect_class {ec!r} is not one of the closed set {sorted(classes)}"
            )

        cap = v.get("capability")
        if cap is not None and not re.match(r"^[a-z][a-z0-9-]*:[a-z][a-z0-9-]*$", str(cap)):
            findings.append(f"{where}: capability must be `resource:action`, got {cap!r}")

        # ADR-0025 D3: exactly one audit record per applied command; reads are
        # not control acts and must not claim one.
        audit = v.get("audit")
        if ec == "read" and audit is not None:
            findings.append(f"{where}: a read verb must have audit: null, got {audit!r}")
        if ec in ("hold", "stop", "irreversible") and not audit:
            findings.append(f"{where}: effect_class {ec!r} requires an audit action")

        bad = [c for c in (v.get("refusals") or []) if int(c) not in refusals]
        if bad:
            findings.append(
                f"{where}: refusal code(s) {bad} are outside the closed set {sorted(refusals)}"
            )
        if not bad:
            codes = {int(c) for c in (v.get("refusals") or [])}
            missing = sorted(REQUIRED_SCAFFOLD - codes)
            if missing and not v.get("note"):
                findings.append(
                    f"{where}: refusals omit {missing} (every verb must carry the "
                    "401/403/503 scaffold unless it says why in `note`)"
                )

        if v.get("exposed") is False and not v.get("why_not_exposed"):
            findings.append(f"{where}: exposed: false requires why_not_exposed")
        if v.get("exposed") is True and v.get("why_not_exposed"):
            findings.append(f"{where}: why_not_exposed is set but the verb is exposed")

    dupes = sorted(k for k, n in seen.items() if n > 1)
    if dupes:
        findings.append(f"duplicate verb id(s): {dupes}")

    return findings


def cross_reference(doc: dict) -> list[str]:
    """Both directions against the real CLI files."""
    findings: list[str] = []
    entries = doc.get("verbs") or []

    declared: dict[str, set[str]] = {}
    for v in entries:
        src, local = v.get("source"), v.get("local")
        if src and local:
            declared.setdefault(str(src), set()).add(str(local))

    for path, canonical in SOURCES.items():
        actual = verbs_in(path)
        if not actual:
            findings.append(
                f"CANNOT-ASSESS: {path} yielded no verbs — the reader is wrong, not the registry"
            )
            continue
        declared_here = declared.get(path, set())
        # Contract-first floor (anti-vacuity): the registry must still declare
        # every canonical verb for this file. A canonical verb that vanishes from
        # BOTH the surface and the registry is refused here, so a silent loss
        # cannot pass the gate vacuously.
        lost = sorted(canonical - declared_here)
        if lost:
            findings.append(
                f"{path}: the registry no longer declares the canonical verb(s) {lost} "
                "— a canonical verb is never silently retired; restore it in verbs.yaml"
            )
        missing = sorted(actual - declared_here)
        for name in missing:
            findings.append(
                f"MISSING: {path} declares the verb {name!r}, which the registry does not declare "
                "— land the matching verbs.yaml + schema entry first (contract-first)"
            )

    for path in declared:
        if path == SELF_SOURCE:
            continue
        if path not in SOURCES:
            findings.append(
                f"ABSENT: the registry names source {path!r}, which is not a known lever file"
            )
            continue
        absent = sorted(declared[path] - verbs_in(path))
        for name in absent:
            findings.append(
                f"ABSENT: the registry declares {name!r} from {path}, which no longer provides it"
            )

    return findings


def cmd_validate(args: argparse.Namespace) -> int:
    if not REGISTRY.exists():
        print(f"control-verbs: CANNOT-ASSESS — {REGISTRY} is missing", file=sys.stderr)
        return 2
    try:
        doc = load_registry()
    except yaml.YAMLError as exc:
        print(f"control-verbs: CANNOT-ASSESS — {REGISTRY} is not valid YAML: {exc}", file=sys.stderr)
        return 2

    findings = validate_schema(doc) + cross_reference(doc)
    entries = doc.get("verbs") or []
    exposed = sum(1 for v in entries if v.get("exposed"))
    held = sum(1 for v in entries if not v.get("exposed"))

    if findings:
        for f in findings:
            print(f"  FAIL  {f}", file=sys.stderr)
        print(
            f"control-verbs: FAIL — {len(findings)} finding(s) over {len(entries)} verb(s)",
            file=sys.stderr,
        )
        return 1

    print(
        f"  OK    {len(entries)} verb(s) declared: {exposed} exposed, {held} withheld with a reason"
    )
    for path in SOURCES:
        print(f"  OK    {path} -> {len(verbs_in(path))} local verb(s), all declared")
    print("control-verbs: OK — the vocabulary is closed and matches the levers it describes")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="control-verbs", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate", help="validate the registry and cross-reference the levers")
    v.set_defaults(func=cmd_validate)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
