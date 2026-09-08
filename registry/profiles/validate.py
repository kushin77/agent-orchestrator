#!/usr/bin/env python3
"""AgentProfile validator for the agent-orchestrator control plane (issue #9).

Validates AgentProfile documents (registry/profiles/agent-profile.schema.json)
declared as JSON or YAML. Enforces, with REAL exit codes and no network:

  0  every validated profile is valid (and, with --self-test, every broken
     fixture under tests/fixtures is rejected)
  1  at least one profile is invalid, a broken fixture was accepted, the
     schema<->catalog vocabulary parity drifted, or the published-version
     ledger (versions/manifest.yaml) is inconsistent
  2  usage error

Fail-closed doctrine (issue #9): a profile that references an unknown
tool/capability/constraint/guardrail-policy/tier/memory-scope id is rejected by
BOTH the JSON Schema (closed enums in agent-profile.schema.json) and the
code-native membership checks below. The validator never silently skips a check:
if jsonschema or PyYAML is unavailable the run FAILS rather than passing
vacuously (no-false-green).

Usage:
  python3 registry/profiles/validate.py                       # validate all seeds/ + catalog parity + ledger
  python3 registry/profiles/validate.py --self-test           # above + every tests/fixtures/*.yaml must FAIL
  python3 registry/profiles/validate.py <file> [<file> ...]   # validate one or more profile files

Every function is importable (pytest suite in tests/ drives them directly).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys

try:
    import yaml
    HAS_YAML = True
except ImportError:  # pragma: no cover - guarded below
    HAS_YAML = False

try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:  # pragma: no cover - guarded below
    HAS_JSONSCHEMA = False

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SCHEMA = os.path.join(HERE, "agent-profile.schema.json")
DEFAULT_CATALOG = os.path.join(HERE, "catalog.yaml")
DEFAULT_SEEDS = os.path.join(HERE, "seeds")
DEFAULT_MANIFEST = os.path.join(HERE, "versions", "manifest.yaml")
DEFAULT_FIXTURES = os.path.join(HERE, "tests", "fixtures")

ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
PROMPT_REF_RE = re.compile(r"^[a-z0-9][a-z0-9-]*(/[a-z0-9][a-z0-9-]*)*@v[0-9]+$")
SEED_FILE_RE = re.compile(
    r"^(?P<sid>[a-z][a-z0-9-]*)\.(?P<sver>[0-9]+\.[0-9]+\.[0-9]+)\.yaml$"
)

REQUIRED_FIELDS = [
    "id",
    "version",
    "owner",
    "systemPromptRef",
    "toolAllowlist",
    "constraintSet",
    "capabilitySet",
    "defaultModelTier",
    "memoryScope",
    "guardrailPolicyRef",
]

# schema $defs/definitions name -> catalog section name (parity gate)
_DEF_TO_CATALOG = {
    "toolId": "tools",
    "capabilityId": "capabilities",
    "constraintId": "constraints",
    "modelTier": "tiers",
    "memoryScopeValue": "memoryScopes",
}


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

def load_yaml(path):
    """Load a YAML file; raises ValueError on parse errors."""
    if not HAS_YAML:
        raise RuntimeError("PyYAML is not importable; cannot parse %s" % path)
    with open(path, "r", encoding="utf-8") as fh:
        try:
            return yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            raise ValueError("%s: invalid YAML: %s" % (path, exc))


def load_json(path):
    """Load a JSON file; raises ValueError on parse errors."""
    with open(path, "r", encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except json.JSONDecodeError as exc:
            raise ValueError("%s: invalid JSON: %s" % (path, exc))


def _err_prefixed(errors, prefix):
    return ["%s%s" % (prefix, e) for e in errors]


# --------------------------------------------------------------------------
# catalog vocabulary + parity
# --------------------------------------------------------------------------

def catalog_vocab(catalog):
    """Return a dict of known-id sets derived from catalog.yaml."""
    gp = catalog.get("guardrailPolicies") or {}
    atomics = set((gp.get("atomics") or {}).keys())
    bundles = gp.get("bundles") or {}
    return {
        "tools": set((catalog.get("tools") or {}).keys()),
        "capabilities": set((catalog.get("capabilities") or {}).keys()),
        "constraints": set((catalog.get("constraints") or {}).keys()),
        "tiers": set((catalog.get("tiers") or {}).keys()),
        "memoryScopes": set((catalog.get("memoryScopes") or {}).keys()),
        "guardrailPolicies": atomics | set(bundles.keys()),
        "guardrailAtomics": atomics,
        "guardrailBundles": bundles,
    }


def catalog_self_errors(catalog):
    """Structural checks on catalog.yaml itself (can genuinely fail)."""
    errors = []
    if not isinstance(catalog, dict):
        return ["catalog: must be a YAML mapping"]
    vocab = catalog_vocab(catalog)
    for section in ("tools", "capabilities", "constraints", "tiers",
                    "memoryScopes"):
        if not vocab[section]:
            errors.append("catalog: section '%s' is empty" % section)
    gp = catalog.get("guardrailPolicies") or {}
    if not vocab["guardrailAtomics"]:
        errors.append("catalog: guardrailPolicies.atomics is empty")
    for bid, bundle in (gp.get("bundles") or {}).items():
        if not isinstance(bundle, dict):
            errors.append("catalog: bundle '%s' is not a mapping" % bid)
            continue
        policies = bundle.get("policies")
        if not isinstance(policies, list) or not policies:
            errors.append("catalog: bundle '%s' has no 'policies' list" % bid)
            continue
        unknown = [p for p in policies if p not in vocab["guardrailAtomics"]]
        for p in unknown:
            errors.append("catalog: bundle '%s' references unknown atomic "
                          "policy '%s'" % (bid, p))
    return errors


def parity_errors(schema, catalog):
    """Ensure the schema's closed enums equal the catalog vocabulary (no drift)."""
    errors = []
    defs = schema.get("definitions") or {}
    for def_name, section in _DEF_TO_CATALOG.items():
        enum = set((defs.get(def_name) or {}).get("enum") or [])
        catalog_ids = set((catalog.get(section) or {}).keys())
        only_schema = enum - catalog_ids
        only_catalog = catalog_ids - enum
        if only_schema:
            errors.append("parity: schema definition '%s' lists ids absent "
                          "from catalog.%s: %s" % (
                              def_name, section,
                              ", ".join(sorted(only_schema))))
        if only_catalog:
            errors.append("parity: catalog.%s lists ids absent from schema "
                          "definition '%s': %s" % (
                              section, def_name,
                              ", ".join(sorted(only_catalog))))
    gp = schema.get("definitions", {}).get("guardrailPolicyId") or {}
    enum = set(gp.get("enum") or [])
    gp_catalog = catalog_vocab(catalog)["guardrailPolicies"]
    only_schema = enum - gp_catalog
    only_catalog = gp_catalog - enum
    if only_schema:
        errors.append("parity: schema 'guardrailPolicyId' lists ids absent "
                      "from catalog.guardrailPolicies: %s"
                      % ", ".join(sorted(only_schema)))
    if only_catalog:
        errors.append("parity: catalog.guardrailPolicies lists ids absent "
                      "from schema 'guardrailPolicyId': %s"
                      % ", ".join(sorted(only_catalog)))
    return errors


# --------------------------------------------------------------------------
# schema conformance (closed enums reject unknown refs at the schema layer)
# --------------------------------------------------------------------------

def schema_errors(data, schema, label):
    """JSON Schema conformance. Fails closed if jsonschema is unavailable."""
    if not HAS_JSONSCHEMA:
        return ["%s: jsonschema unavailable; schema conformance cannot run "
                "(fail closed)" % label]
    errors = []
    try:
        validator = jsonschema.Draft7Validator(schema)
        for err in validator.iter_errors(data):
            path = "/".join(str(p) for p in err.absolute_path) or "(root)"
            errors.append("%s: schema %s: %s" % (label, path, err.message))
    except Exception as exc:  # pragma: no cover - defensive
        errors.append("%s: schema validation crashed: %s" % (label, exc))
    return errors


# --------------------------------------------------------------------------
# code-native fail-closed membership checks (authoritative layer)
# --------------------------------------------------------------------------

def membership_errors(data, vocab, label):
    """Reject unknown tool/capability/constraint/guardrail/tier/scope refs."""
    errors = []
    if not isinstance(data, dict):
        return ["%s: profile must be a YAML/JSON object" % label]

    for field in REQUIRED_FIELDS:
        if field not in data:
            errors.append("%s: missing required field '%s'" % (label, field))

    pid = data.get("id")
    if isinstance(pid, str) and not ID_RE.match(pid):
        errors.append("%s: id '%s' must match ^[a-z][a-z0-9-]*$"
                      % (label, pid))

    version = data.get("version")
    if isinstance(version, str) and not VERSION_RE.match(version):
        errors.append("%s: version '%s' must be semantic X.Y.Z"
                      % (label, version))

    prompt_ref = data.get("systemPromptRef")
    if isinstance(prompt_ref, str) and not PROMPT_REF_RE.match(prompt_ref):
        errors.append("%s: systemPromptRef '%s' must match "
                      "<module>/<name>@v<n>" % (label, prompt_ref))

    def _check_list(field, known, kind, extra=""):
        values = data.get(field)
        if values is None:
            return
        if not isinstance(values, list):
            errors.append("%s: %s must be an array" % (label, field))
            return
        for value in values:
            if not isinstance(value, str):
                errors.append("%s: %s entries must be strings"
                              % (label, field))
            elif value not in known:
                errors.append("%s: %s references unknown %s '%s' (fail "
                              "closed)%s" % (label, field, kind, value, extra))
        dups = sorted({v for v in values
                       if isinstance(v, str) and values.count(v) > 1})
        for d in dups:
            errors.append("%s: %s contains duplicate id '%s'"
                          % (label, field, d))

    _check_list("toolAllowlist", vocab["tools"], "tool", "")
    _check_list("capabilitySet", vocab["capabilities"], "capability", "")
    _check_list("constraintSet", vocab["constraints"], "constraint", "")
    _check_list("memoryScope", vocab["memoryScopes"], "memory scope",
                " (valid: user, session, repository)")

    tier = data.get("defaultModelTier")
    if isinstance(tier, str) and tier not in vocab["tiers"]:
        errors.append("%s: defaultModelTier '%s' is not a known tier "
                      "(valid: LOW, MED, HIGH, MAX)" % (label, tier))

    gpref = data.get("guardrailPolicyRef")
    if isinstance(gpref, str) and gpref not in vocab["guardrailPolicies"]:
        errors.append("%s: guardrailPolicyRef references unknown policy "
                      "'%s' (fail closed)" % (label, gpref))
    return errors


def validate_profile_data(data, schema, catalog, label):
    """Run schema conformance + code-native membership for one profile."""
    errors = []
    errors.extend(schema_errors(data, schema, label))
    errors.extend(membership_errors(data, catalog_vocab(catalog), label))
    return errors


def validate_seed_file(path, schema, catalog, label=None):
    """Validate one seed file: parse, filename/content consistency, profile."""
    label = label or os.path.relpath(path, HERE)
    errors = []
    try:
        data = load_yaml(path)
    except Exception as exc:
        return ["%s: %s" % (label, exc)]
    base = os.path.basename(path)
    match = SEED_FILE_RE.match(base)
    if not match:
        errors.append("%s: seed filename must be <id>.<version>.yaml"
                      % label)
    else:
        sid, sver = match.group("sid"), match.group("sver")
        if not isinstance(data, dict):
            return ["%s: seed must be a YAML object" % label]
        if data.get("id") != sid:
            errors.append("%s: declared id '%s' does not match filename id "
                          "'%s'" % (label, data.get("id"), sid))
        if data.get("version") != sver:
            errors.append("%s: declared version '%s' does not match filename "
                          "version '%s'" % (label, data.get("version"), sver))
    errors.extend(validate_profile_data(data, schema, catalog, label))
    return errors


# --------------------------------------------------------------------------
# published-version ledger (immutability)
# --------------------------------------------------------------------------

def ledger_errors(seeds_dir, manifest_path):
    """Check the immutable published-version ledger (versions/manifest.yaml).

    Refuses: (a) mutation of a published seed (on-disk sha256 != recorded),
    (b) duplicate (profile, version), (c) seed files missing from the ledger,
    (d) ledger entries whose file is missing or misnamed.

    Entry `file` values are resolved relative to the parent of `seeds_dir`
    (the profiles dir in the real layout), so temp-dir copies of the tree can
    be checked the same way.
    """
    errors = []
    try:
        manifest = load_yaml(manifest_path)
    except Exception as exc:
        return ["ledger: %s" % exc]

    published = manifest.get("published") if isinstance(manifest, dict) else None
    if not isinstance(published, list) or not published:
        return ["ledger: manifest has no non-empty 'published' list"]

    base_dir = os.path.dirname(os.path.abspath(seeds_dir))
    seeds_abs = os.path.normpath(os.path.abspath(seeds_dir))
    by_file = {}
    seen_keys = {}
    for i, entry in enumerate(published):
        if not isinstance(entry, dict):
            errors.append("ledger: entry %d is not a mapping" % i)
            continue
        pid = entry.get("profile")
        ver = entry.get("version")
        rel = entry.get("file")
        sha = entry.get("sha256")
        if not all(isinstance(x, str) and x for x in (pid, ver, rel, sha)):
            errors.append("ledger: entry %d must have string profile, "
                          "version, file, sha256" % i)
            continue
        key = (pid, ver)
        if key in seen_keys:
            errors.append("ledger: duplicate published (profile, version): "
                          "%s %s" % key)
        seen_keys[key] = i

        expected = "%s.%s.yaml" % (pid, ver)
        if os.path.basename(rel) != expected:
            errors.append("ledger: entry file '%s' must be named '%s'"
                          % (rel, expected))
        full = os.path.normpath(os.path.join(base_dir, rel))
        if not full.startswith(seeds_abs + os.sep):
            errors.append("ledger: entry file '%s' must live under seeds/"
                          % rel)
            continue
        if not os.path.exists(full):
            errors.append("ledger: entry file '%s' is missing" % rel)
            continue
        digest = hashlib.sha256(open(full, "rb").read()).hexdigest()
        if digest != sha.lower():
            errors.append("ledger: published profile %s %s is IMMUTABLE but "
                          "its on-disk sha256 changed (file %s)" % (pid, ver, rel))
        by_file.setdefault(rel, []).append(entry)

    # coverage: every seed file under seeds/ must be in the ledger
    seed_names = sorted(n for n in os.listdir(seeds_dir) if n.endswith(".yaml"))
    for name in seed_names:
        rel = "seeds/%s" % name
        if rel not in by_file:
            errors.append("ledger: seed '%s' is not published in the version "
                          "manifest" % rel)
    return errors


# --------------------------------------------------------------------------
# aggregate runs
# --------------------------------------------------------------------------

def coverage_errors(seeds_dir, manifest_path, schema_path, catalog_path,
                    label_base="registry/profiles"):
    """Seeds + schema/catalog parity + ledger. Returns (ok, errors)."""
    errors = []
    schema = load_json(schema_path)
    catalog = load_yaml(catalog_path)
    errors.extend(catalog_self_errors(catalog))
    errors.extend(parity_errors(schema, catalog))
    errors.extend(ledger_errors(seeds_dir, manifest_path))
    for name in sorted(n for n in os.listdir(seeds_dir) if n.endswith(".yaml")):
        errors.extend(validate_seed_file(
            os.path.join(seeds_dir, name), schema, catalog,
            label="%s/seeds/%s" % (label_base, name)))
    return len(errors) == 0, errors


def self_test_errors(fixtures_dir, schema_path, catalog_path):
    """Every broken fixture under tests/fixtures MUST be rejected."""
    errors = []
    schema = load_json(schema_path)
    catalog = load_yaml(catalog_path)
    fixtures = sorted(n for n in os.listdir(fixtures_dir)
                      if n.endswith((".yaml", ".yml", ".json")))
    if not fixtures:
        return ["self-test: no fixtures found under tests/fixtures"]
    for name in fixtures:
        path = os.path.join(fixtures_dir, name)
        try:
            data = load_yaml(path)
        except Exception:
            data = None
        if data is None and name.endswith(".json"):
            try:
                data = load_json(path)
            except Exception:
                data = None
        label = "fixture/%s" % name
        errs = validate_profile_data(data, schema, catalog, label) \
            if isinstance(data, dict) else \
            ["%s: could not parse fixture" % label]
        if not errs:
            errors.append("self-test: fixture '%s' was ACCEPTED (must fail)"
                          % name)
    return errors


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

USAGE = __doc__.split("Usage:")[1].split("\n\n")[0].strip()


def main(argv):
    positional = []
    self_test = False
    schema_path, catalog_path = DEFAULT_SCHEMA, DEFAULT_CATALOG
    seeds_dir, manifest_path = DEFAULT_SEEDS, DEFAULT_MANIFEST
    fixtures_dir = DEFAULT_FIXTURES

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--self-test":
            self_test = True
        elif arg == "--schema":
            i += 1
            schema_path = argv[i]
        elif arg == "--catalog":
            i += 1
            catalog_path = argv[i]
        elif arg == "--seeds":
            i += 1
            seeds_dir = argv[i]
        elif arg == "--manifest":
            i += 1
            manifest_path = argv[i]
        elif arg == "--fixtures":
            i += 1
            fixtures_dir = argv[i]
        elif arg in ("-h", "--help"):
            print(__doc__.rstrip())
            return 0
        elif arg.startswith("-"):
            print("validate.py: unknown option: %s" % arg, file=sys.stderr)
            print("usage: %s" % USAGE, file=sys.stderr)
            return 2
        else:
            positional.append(arg)
        i += 1

    # single-file / file-list mode
    if positional:
        errors = []
        try:
            schema = load_json(schema_path)
            catalog = load_yaml(catalog_path)
        except Exception as exc:
            print("validate.py: %s" % exc, file=sys.stderr)
            return 2
        for path in positional:
            errors.extend(validate_profile_data(
                load_yaml(path), schema, catalog,
                label=os.path.relpath(path, HERE)))
        if errors:
            for e in errors:
                print("  FAIL  %s" % e)
            print("validate: FAIL (%d error(s))" % len(errors))
            return 1
        print("validate: OK (%d file(s))" % len(positional))
        return 0

    # coverage mode (optionally + self-test)
    try:
        ok, errors = coverage_errors(seeds_dir, manifest_path, schema_path,
                                     catalog_path)
    except Exception as exc:
        print("validate.py: %s" % exc, file=sys.stderr)
        return 2

    if self_test:
        try:
            errors.extend(self_test_errors(fixtures_dir, schema_path,
                                           catalog_path))
        except Exception as exc:
            print("validate.py: %s" % exc, file=sys.stderr)
            return 2

    for e in errors:
        print("  FAIL  %s" % e)
    if errors:
        print("validate: FAIL (%d error(s))" % len(errors))
        return 1
    if self_test:
        print("validate: OK (coverage + self-test, all fixtures rejected)")
    else:
        print("validate: OK (coverage: seeds + catalog parity + ledger)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
