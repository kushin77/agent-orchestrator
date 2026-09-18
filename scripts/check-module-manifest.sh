#!/usr/bin/env bash
# check-module-manifest.sh — this repository's OWN root `module.json`, validated
# against the `cmr.module/v1` schema it declares (issue #1163).
#
# THE DEFECT THIS EXISTS FOR
#   `docs/MODULE-ADMISSION.md` requires every child module to publish a
#   `module.json` that is "valid against `cmr.module/v1`" (§1 point 1, §3 point 1),
#   and this repository's own root manifest declares exactly that identity. Nothing
#   validated it. Every `scripts/check-*.sh` that mentions `module.json` READS A
#   FIELD out of it (`os_apps`, `submodules[]`, the feature flags, the class
#   ceilings); `check-module-admission.sh` audits the parent-side *register*
#   against `docs/MODULE-ADMISSION.md`, which is a different question. So a
#   malformed root manifest — an unknown `schema` identity, a required key absent,
#   a malformed register entry — satisfied every gate in the repo. Filed and
#   measured as #1163; recorded as ABSENT in
#   `docs/SYSTEM-APP-GOVERNANCE-E2E-GAP-ANALYSIS.md` (§4, §5).
#
# WHAT IT VALIDATES (two halves, one subject)
#   1. THE WHOLE DOCUMENT against `cmr.module/v1`, not fields: `type`, `const`,
#      `enum`, `required`, `properties`, `additionalProperties`, `items`,
#      `pattern`, `minLength`, `maxLength`, `minItems`, `maxItems`,
#      `uniqueItems`, and local `$ref`/`$defs` targets.
#   2. THE REPO-LOCAL EXTENSION the schema does not cover — the shape of a
#      `submodules[]` register entry, declared in `docs/MODULE-ADMISSION.md` §2.
#      The register's *semantics* (admission states, reference integrity, the
#      dependency pins) stay with `scripts/check-module-admission.sh`; this check
#      owns the entry's SHAPE, so a malformed entry is refused by name.
#
# THE SCHEMA SOURCE, HONESTLY
#   `cmr.module/v1` is defined once, in the pinned hub submodule:
#   `vendor/CMR/catalog/schemas/module.schema.json`. A `git worktree add` does not
#   initialise submodules, so in a lane worktree that path is EMPTY — and a
#   validator that silently skips when its schema is missing is *precisely* the
#   formality this issue is about. Resolution order, printed on every run:
#     1. `--schema FILE`
#     2. `$AO_MODULE_MANIFEST_SCHEMA`
#     3. `vendor/CMR/catalog/schemas/module.schema.json`  — the live pinned copy
#     4. `governance/modules/cmr.module.v1.schema.json`   — a pinned local copy,
#        byte-identical to the vendored file apart from one `$comment` that
#        records this provenance, so the gate has a real input offline
#   No readable schema => CANNOT-ASSESS (rc 2). Never a pass.
#
# NO THIRD-PARTY VALIDATOR
#   The subset validator below is stdlib-only, which is this repository's stated
#   convention for its own schemas (`governance/modules/schema.py`: "no
#   third-party validator ... so the gate is offline and deterministic"). It
#   REFUSES a schema that uses a keyword it does not implement, rather than
#   silently under-enforcing it: an ignored keyword is a requirement nobody
#   measures, which is worse than no schema at all.
#
# THE GATE PROVES ITSELF ON EVERY RUN (AO-GR-4)
#   Before it reads this repository it plants, in a scratch tree, each shape it
#   claims to refuse — an unknown `schema` identity, a missing required key, a
#   malformed `submodules[]` entry, a manifest that is not an object — and
#   requires the refusal BY NAME. The halves that stop it matching everything are
#   proven too: a valid-but-minimal manifest is accepted and refused nothing; each
#   plant yields ONLY its own refusal; and a malformed manifest under `vendor/`
#   is neither read nor reported, because this gate has exactly one subject.
#   The provocation drives the SAME function (assess) the repository run uses.
#
# EXIT CONTRACT (the repository's honesty tri-state)
#   0  OK              schema-valid, register shape holds, provocation passed
#   1  NOT-OK          a refusal by name (or the provocation itself failed)
#   2  CANNOT-ASSESS   no python3, no readable schema (or manifest), a schema
#                      keyword this validator does not implement, a `$ref` it
#                      cannot resolve, or a bad invocation — never a pass
#
# Usage:
#   bash scripts/check-module-manifest.sh                 gate: provocation + this repo
#   bash scripts/check-module-manifest.sh --self-test     the provocation alone
#   bash scripts/check-module-manifest.sh --no-controls   this repo alone
#   bash scripts/check-module-manifest.sh --root DIR      resolve module.json under DIR
#   bash scripts/check-module-manifest.sh --manifest FILE --schema FILE
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)" || exit 2
scan_root="$root"

mode="gate"
controls=1
manifest=""
schema=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --root) scan_root="${2:-}"; shift 2 ;;
    --manifest) manifest="${2:-}"; shift 2 ;;
    --schema) schema="${2:-}"; shift 2 ;;
    --self-test) mode="selftest"; shift ;;
    --no-controls) controls=0; shift ;;
    -h|--help) sed -n '2,80p' "$0"; exit 0 ;;
    *) printf 'check-module-manifest: unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

if [ ! -d "$scan_root" ]; then
  printf 'check-module-manifest: CANNOT-ASSESS — --root %s is not a directory\n' "$scan_root" >&2
  exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
  printf 'check-module-manifest: CANNOT-ASSESS — python3 not found\n' >&2
  exit 2
fi

# One global scratch and one EXIT trap (SHELL-PATTERNS SP-1). The X-run is
# assembled by printf, so no literal marker token sits in this source (SP-9).
TMPD=""
cleanup() { [ -n "$TMPD" ] && rm -rf "$TMPD" || true; }
trap cleanup EXIT
TMPD="$(mktemp -d "/tmp/ao1163-module-manifest.$(printf 'X%.0s' 1 2 3 4 5 6)")" || {
  printf 'check-module-manifest: CANNOT-ASSESS — mktemp failed\n' >&2
  exit 2
}

python3 - "$mode" "$controls" "$scan_root" "$manifest" "$schema" "$TMPD" <<'PY'
"""Validate a cmr.module/v1 manifest, and the register shape under it.

Exit-code contract mirrors the shell wrapper: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
Nothing here is a third-party import: the subset validator is local, and a
schema that uses a keyword it cannot enforce is CANNOT-ASSESS rather than a
silent pass.
"""

import json
import os
import re
import sys
from pathlib import Path

MODE, CONTROLS_ARG, ROOT_ARG, MANIFEST_ARG, SCHEMA_ARG, TMPD_ARG = sys.argv[1:7]
CONTROLS = CONTROLS_ARG == "1"
ROOT = Path(ROOT_ARG)
TMPD = Path(TMPD_ARG)

SCHEMA_ID = "cmr.module/v1"

#: (relative path, how to describe it) in resolution order after the explicit
#: override and the environment variable.
SOURCES = (
    ("vendor/CMR/catalog/schemas/module.schema.json", "vendored pinned hub submodule"),
    ("governance/modules/cmr.module.v1.schema.json", "pinned local copy"),
)

#: Every keyword this validator implements. A schema using anything else is
#: refused, never silently under-enforced.
IMPLEMENTED = frozenset({
    "$schema", "$id", "$comment", "title", "description",
    "$defs", "$ref",
    "type", "required", "properties", "additionalProperties",
    "items", "enum", "const", "pattern", "minLength", "maxLength",
    "minItems", "maxItems", "uniqueItems",
})

#: The shape of one `submodules[]` entry, per docs/MODULE-ADMISSION.md section 2.
REGISTER_ENTRY_KEYS = ("id", "repo", "admission", "request", "evidence")

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}

_NAMES = {
    dict: "an object", list: "an array", str: "a string",
    bool: "a boolean", int: "a number", float: "a number", type(None): "null",
}


class CannotAssess(Exception):
    """An input this gate is not able to assess. Never a pass."""


class Finding(object):
    """One refusal, named."""

    def __init__(self, name, where, detail):
        self.name = name
        self.where = where or "(document)"
        self.detail = detail

    def line(self):
        return "  REFUSED  %s  %s: %s" % (self.name, self.where, self.detail)


def type_name(value):
    return _NAMES.get(type(value), type(value).__name__)


def cannot_assess(message):
    """Report an unassessable input. stdout is flushed first: with the streams
    redirected to one file, an unflushed stdout would put this line ABOVE the
    report it belongs under."""
    sys.stdout.flush()
    print("check-module-manifest: CANNOT-ASSESS — %s" % message, file=sys.stderr)


def json_equal(left, right):
    """JSON equality, so ``True`` is not ``1`` and ``False`` is not ``0``."""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return len(left) == len(right) and all(
            key in right and json_equal(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            json_equal(one, two) for one, two in zip(left, right)
        )
    return left == right


def load_json(path, label):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise CannotAssess("the %s %s is unreadable: %s" % (label, path, exc))
    try:
        return json.loads(text)
    except ValueError as exc:
        raise CannotAssess("the %s %s is not JSON: %s" % (label, path, exc))


# --------------------------------------------------------------------------- #
# the schema
# --------------------------------------------------------------------------- #
def unsupported_keywords(schema):
    """Every keyword this validator does not implement, with where it sits."""
    found = []

    def walk(node, where):
        if isinstance(node, dict):
            for keyword, value in node.items():
                if keyword not in IMPLEMENTED:
                    found.append((keyword, where))
                    continue
                if keyword in ("properties", "$defs") and isinstance(value, dict):
                    for name, sub in value.items():
                        walk(sub, "%s/%s/%s" % (where, keyword, name))
                elif keyword in ("items", "additionalProperties") and isinstance(value, dict):
                    walk(value, "%s/%s" % (where, keyword))
        elif isinstance(node, list):
            for index, sub in enumerate(node):
                walk(sub, "%s/%d" % (where, index))

    walk(schema, "#")
    return found


def resolve_ref(ref, schema, where):
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise CannotAssess("the schema refers to %r at %s, which is not a local pointer"
                           % (ref, where))
    node = schema
    for part in ref[2:].split("/"):
        if not isinstance(node, dict) or part not in node:
            raise CannotAssess("the schema refers to %r at %s, which does not exist"
                               % (ref, where))
        node = node[part]
    return node


# --------------------------------------------------------------------------- #
# the manifest
# --------------------------------------------------------------------------- #
def validate(value, rule, schema, path, out):
    """Apply one schema rule to one value, appending named findings."""
    if not isinstance(rule, dict):
        return
    if "$ref" in rule:
        validate(value, resolve_ref(rule["$ref"], schema, path or "#"), schema, path, out)

    kinds = rule.get("type")
    if kinds is not None:
        names = kinds if isinstance(kinds, list) else [kinds]
        for name in names:
            if name not in _TYPES:
                raise CannotAssess("the schema declares the unknown type %r at %s"
                                   % (name, path or "#"))
        if not any(_TYPES[name](value) for name in names):
            out.append(Finding("manifest-schema-violation", path,
                               "expected %s, found %s" % (" or ".join(names), type_name(value))))
            return

    if "const" in rule and not json_equal(value, rule["const"]):
        if path == "schema":
            out.append(Finding("schema-not-cmr-module-v1", "schema",
                               "declares %r, not %r" % (value, rule["const"])))
        else:
            out.append(Finding("manifest-schema-violation", path,
                               "%r does not equal the required constant %r"
                               % (value, rule["const"])))

    if "enum" in rule and not any(json_equal(value, one) for one in rule["enum"]):
        out.append(Finding("manifest-schema-violation", path,
                           "%r is not one of %s" % (value, rule["enum"])))

    if isinstance(value, str):
        if "minLength" in rule and len(value) < rule["minLength"]:
            out.append(Finding("manifest-schema-violation", path,
                               "is %d character(s) long, the schema requires at least %d"
                               % (len(value), rule["minLength"])))
        if "maxLength" in rule and len(value) > rule["maxLength"]:
            out.append(Finding("manifest-schema-violation", path,
                               "is %d character(s) long, the schema allows at most %d"
                               % (len(value), rule["maxLength"])))
        if "pattern" in rule and not re.search(rule["pattern"], value):
            out.append(Finding("manifest-schema-violation", path,
                               "%r does not match the required pattern %s"
                               % (value, rule["pattern"])))

    if isinstance(value, list):
        if "minItems" in rule and len(value) < rule["minItems"]:
            out.append(Finding("manifest-schema-violation", path,
                               "has %d item(s), the schema requires at least %d"
                               % (len(value), rule["minItems"])))
        if "maxItems" in rule and len(value) > rule["maxItems"]:
            out.append(Finding("manifest-schema-violation", path,
                               "has %d item(s), the schema allows at most %d"
                               % (len(value), rule["maxItems"])))
        if rule.get("uniqueItems"):
            seen = []
            for index, item in enumerate(value):
                if any(json_equal(item, other) for other in seen):
                    out.append(Finding("manifest-schema-violation", "%s/%d" % (path, index),
                                       "%r is a duplicate of an earlier item, and the "
                                       "schema requires unique items" % (item,)))
                    break
                seen.append(item)
        if isinstance(rule.get("items"), dict):
            for index, item in enumerate(value):
                validate(item, rule["items"], schema, "%s/%d" % (path, index), out)

    if isinstance(value, dict):
        for key in rule.get("required") or ():
            if key not in value:
                out.append(Finding("manifest-required-key-missing", path or "(document)",
                                   "the required key %r is absent (at /%s%s)"
                                   % (key, path + "/" if path else "", key)))
        properties = rule.get("properties") or {}
        extra = rule.get("additionalProperties", True)
        for key, item in value.items():
            child = "%s/%s" % (path, key) if path else key
            if key in properties:
                validate(item, properties[key], schema, child, out)
            elif extra is False:
                out.append(Finding("manifest-unexpected-key", child,
                                   "the schema closes this object and does not declare %r" % (key,)))
            elif isinstance(extra, dict):
                validate(item, extra, schema, child, out)


def check_register(manifest, out):
    """The submodules[] entry shape (docs/MODULE-ADMISSION.md section 2)."""
    if "submodules" not in manifest:
        return 0
    entries = manifest["submodules"]
    if not isinstance(entries, list):
        out.append(Finding("submodules-not-a-register", "submodules",
                           "the register must be an array of entries, found %s"
                           % type_name(entries)))
        return 0
    for index, entry in enumerate(entries):
        where = "submodules/%d" % index
        if not isinstance(entry, dict):
            out.append(Finding("submodules-entry-malformed", where,
                               "a register entry must be an object, found %s"
                               % type_name(entry)))
            continue
        identifier = entry.get("id")
        label = identifier if isinstance(identifier, str) and identifier else "(unnamed entry)"
        for key in REGISTER_ENTRY_KEYS:
            held = entry.get(key)
            if not isinstance(held, str) or not held:
                out.append(Finding(
                    "submodules-entry-malformed", where,
                    "%s: the register key %r is missing, or is not a non-empty string "
                    "(docs/MODULE-ADMISSION.md section 2)" % (label, key)))
    return len(entries)


def assess(manifest_path, schema_path):
    """Assess one manifest against one schema. Never raises: rc 2 is a result."""
    result = {"rc": 2, "findings": [], "entries": 0, "reason": None,
              "manifest": str(manifest_path), "schema": str(schema_path)}
    try:
        schema = load_json(schema_path, "schema")
        if not isinstance(schema, dict):
            raise CannotAssess("the schema %s is not a JSON object" % schema_path)
        unsupported = unsupported_keywords(schema)
        if unsupported:
            keyword, where = unsupported[0]
            raise CannotAssess(
                "the schema %s uses the keyword %r at %s, which this validator does not "
                "implement — an unenforced keyword is a requirement nobody measures"
                % (schema_path, keyword, where))
        manifest = load_json(manifest_path, "manifest")
    except CannotAssess as exc:
        result["reason"] = str(exc)
        return result

    findings = []
    if not isinstance(manifest, dict):
        findings.append(Finding("manifest-not-an-object", "(document)",
                                "a module manifest must be a JSON object, found %s"
                                % type_name(manifest)))
    else:
        validate(manifest, schema, schema, "", findings)
        result["entries"] = check_register(manifest, findings)

    result["findings"] = findings
    result["rc"] = 1 if findings else 0
    if not findings and isinstance(manifest, dict):
        result["identity"] = manifest.get("schema")
        result["id"] = manifest.get("id")
        result["type"] = manifest.get("type")
    return result


# --------------------------------------------------------------------------- #
# resolution
# --------------------------------------------------------------------------- #
def resolve_manifest(root, argument):
    return Path(argument) if argument else Path(root) / "module.json"


def resolve_schema(root, argument):
    """(path, label) or (None, None). An absent schema is never a pass."""
    if argument:
        return Path(argument), "--schema"
    from_env = os.environ.get("AO_MODULE_MANIFEST_SCHEMA")
    if from_env:
        return Path(from_env), "AO_MODULE_MANIFEST_SCHEMA"
    for relative, label in SOURCES:
        candidate = Path(root) / relative
        if candidate.is_file():
            return candidate, label
    return None, None


def report(result, label=None):
    for finding in result["findings"]:
        print(finding.line())


# --------------------------------------------------------------------------- #
# the provocation
# --------------------------------------------------------------------------- #
def new_case(scratch, name, schema_text):
    where = scratch / name
    where.mkdir(parents=True, exist_ok=True)
    (where / "module.schema.json").write_text(schema_text, encoding="utf-8")
    return where


def selftest(real_schema_path):
    """Plant each refused shape. Returns (passed, total)."""
    print("== provocation — the gate proves itself before it reads this repository ==")
    real_schema_text = Path(real_schema_path).read_text(encoding="utf-8")
    scratch = TMPD / "provocation"
    scratch.mkdir(parents=True, exist_ok=True)

    clean = {
        "schema": SCHEMA_ID, "id": "probe", "name": "probe", "type": "service",
        "source": {"repo": "kushin77/probe"}, "governance": {"branch_protection": "standard"},
    }
    unknown = dict(clean, schema="cmr.module/v2")
    missing = dict(clean)
    del missing["governance"]
    malformed = dict(clean, submodules=[{"id": "probe-child"}])
    not_object = ["clean", "but", "an", "array"]

    base = new_case(scratch, "base", real_schema_text)
    cases = [
        ("clean — a valid-but-minimal manifest is accepted and refused nothing",
         base, clean, 0, None),
        ("plant unknown-schema — the declared identity is refused by name",
         base, unknown, 1, "schema-not-cmr-module-v1"),
        ("plant missing-required-key — the absent key is refused by name",
         base, missing, 1, "manifest-required-key-missing"),
        ("plant malformed-submodules — the register entry is refused by name",
         base, malformed, 1, "submodules-entry-malformed"),
        ("plant manifest-not-an-object — a non-object document is refused by name",
         base, not_object, 1, "manifest-not-an-object"),
    ]

    passed = 0
    total = 0
    for label, where, document, want_rc, want_name in cases:
        total += 1
        target = where / "module.json"
        target.write_text(json.dumps(document, indent=2), encoding="utf-8")
        result = assess(target, where / "module.schema.json")
        names = [finding.name for finding in result["findings"]]
        problems = []
        if result["rc"] != want_rc:
            problems.append("rc %s, expected %s (%s)" % (result["rc"], want_rc,
                                                         result["reason"] or "no reason"))
        if want_name is None:
            if names:
                problems.append("refused %s, expected nothing" % ",".join(names))
        elif want_name not in names:
            problems.append("did not refuse %s (refused %s)" % (want_name, names or "nothing"))
        elif set(names) != {want_name}:
            # The half that stops a rule matching everything: a plant yields ONLY
            # its own refusal, so no detector can be inert while the gate is green.
            problems.append("refused %s, expected only %s" % (names, want_name))
        if problems:
            print("  FAIL  %s — %s" % (label, "; ".join(problems)))
        else:
            got = "nothing" if not names else ",".join(names)
            print("  OK    %s (rc %s, refused %s)" % (label, result["rc"], got))
            passed += 1

    # An unimplemented schema keyword is CANNOT-ASSESS, never a pass.
    total += 1
    loose = new_case(scratch, "loose", json.dumps({
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object", "oneOf": [{"required": ["id"]}],
    }))
    (loose / "module.json").write_text(json.dumps(clean), encoding="utf-8")
    result = assess(loose / "module.json", loose / "module.schema.json")
    if result["rc"] == 2 and "oneOf" in (result["reason"] or ""):
        print("  OK    unimplemented-keyword — a keyword this validator cannot enforce is "
              "CANNOT-ASSESS (rc 2), never a pass")
        passed += 1
    else:
        print("  FAIL  unimplemented-keyword — rc %s, reason %r"
              % (result["rc"], result["reason"]))

    # An absent schema, and an absent manifest, are both CANNOT-ASSESS.
    for label, manifest_path, schema_path in (
        ("missing-schema", base / "module.json", base / "no.schema.json"),
        ("missing-manifest", base / "no.module.json", base / "module.schema.json"),
    ):
        total += 1
        result = assess(manifest_path, schema_path)
        if result["rc"] == 2:
            print("  OK    %s — an unreadable input is CANNOT-ASSESS (rc 2), never a pass"
                  % label)
            passed += 1
        else:
            print("  FAIL  %s — rc %s (%s)" % (label, result["rc"], result["reason"]))

    # Scoping: the gate has exactly one subject. A malformed manifest under
    # vendor/ is neither read nor reported when the subject is the root manifest.
    total += 1
    planted_root = scratch / "scoped"
    (planted_root / "vendor" / "planted").mkdir(parents=True, exist_ok=True)
    (planted_root / "module.json").write_text(json.dumps(clean), encoding="utf-8")
    planted = planted_root / "vendor" / "planted" / "module.json"
    planted.write_text(json.dumps(dict(clean, schema="cmr.module/nope")), encoding="utf-8")
    as_subject = assess(planted, base / "module.schema.json")
    as_root = assess(resolve_manifest(planted_root, ""), base / "module.schema.json")
    if as_subject["rc"] == 1 and as_root["rc"] == 0:
        print("  OK    scoping — the planted vendor/ manifest WOULD be refused (rc 1) as a "
              "subject, and the root run is unaffected (rc 0): the gate does not walk the "
              "repository")
        passed += 1
    else:
        print("  FAIL  scoping — the planted vendor/ manifest gave rc %s as a subject, and "
              "the root run gave rc %s (expected 1 and 0)"
              % (as_subject["rc"], as_root["rc"]))

    return passed, total


def main():
    if MODE == "selftest":
        schema_path, label = resolve_schema(ROOT, SCHEMA_ARG)
        if schema_path is None:
            cannot_assess("no readable schema under %s, and --schema was not given" % ROOT)
            return 2
        passed, total = selftest(schema_path)
        print("provocation: %s (%d of %d)"
              % ("PASS" if passed == total else "FAIL", passed, total))
        return 0 if passed == total else 1

    print("check-module-manifest: this repository's root module.json against %s (issue #1163)"
          % SCHEMA_ID)
    print()

    controls_ok = True
    if CONTROLS:
        schema_for_controls, _ = resolve_schema(ROOT, SCHEMA_ARG)
        if schema_for_controls is None:
            print("  FAIL  provocation could not run — no readable schema under %s" % ROOT)
            controls_ok = False
        else:
            passed, total = selftest(schema_for_controls)
            print("provocation: %s (%d of %d)"
                  % ("PASS" if passed == total else "FAIL", passed, total))
            controls_ok = passed == total
        print()

    schema_path, label = resolve_schema(ROOT, SCHEMA_ARG)
    manifest_path = resolve_manifest(ROOT, MANIFEST_ARG)

    print("== this repository ==")
    print("  manifest: %s" % manifest_path)
    if schema_path is None:
        print("  schema:   (none readable)")
    else:
        print("  schema:   %s  [%s]" % (schema_path, label))
        if label == "pinned local copy":
            print("  note:     the vendored schema %s is not readable here (a git worktree "
                  "does not initialise the submodule); the pinned local copy is used, and it "
                  "records its own provenance" % SOURCES[0][0])

    if controls_ok is False:
        print("check-module-manifest: NOT-OK — the provocation failed, so this gate cannot "
              "be trusted for this repository")
        return 1

    if schema_path is None:
        cannot_assess(
            "no readable %s schema: neither %s nor %s exists under %s, and --schema was "
            "not given (a validator that skips when its schema is missing is the "
            "formality #1163 is about)"
            % (SCHEMA_ID, SOURCES[0][0], SOURCES[1][0], ROOT))
        return 2

    result = assess(manifest_path, schema_path)
    if result["rc"] == 2:
        cannot_assess(result["reason"])
        return 2

    if result["rc"] == 1:
        report(result)
        print("check-module-manifest: NOT-OK — %d refusal(s): %s"
              % (len(result["findings"]),
                 ",".join(sorted({finding.name for finding in result["findings"]}))))
        return 1

    print("  OK    %s: schema-valid (%s, id=%s, type=%s)"
          % (manifest_path.name, result.get("identity"), result.get("id"), result.get("type")))
    print("  OK    submodules: %d register entr(ies) match the shape declared in "
          "docs/MODULE-ADMISSION.md section 2" % result["entries"])
    print("check-module-manifest: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
PY
