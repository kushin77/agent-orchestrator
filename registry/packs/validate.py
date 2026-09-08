#!/usr/bin/env python3
"""AgentPack registry validator for the agent-orchestrator control plane (#40).

Validates AgentPack documents (registry/packs/agent-pack.schema.json) declared
as YAML or JSON and the published release archive. Enforces, with REAL exit
codes and no network:

  0  every release pack is valid (schema + contents integrity + ledger +
     catalog parity + attestation signature) and, with --self-test, every
     broken fixture under tests/fixtures is rejected
  1  at least one pack is invalid, a broken fixture was accepted, a release
     attestation does not verify, the published-version ledger
     (versions/manifest.yaml) is inconsistent, or the schema<->catalog
     vocabulary drifted
  2  usage error

Fail-closed doctrine (issue #40): an invalid pack FAILS publish and a release
whose attestation does not verify against the committed public key
(publisher-key.pem) FAILS the gate. If jsonschema, PyYAML or cryptography is
unavailable the relevant check FAILS rather than passing vacuously
(no-false-green) — a pack is never trusted because a verifier went missing.

Usage:
  python3 registry/packs/validate.py                  # coverage: releases + ledger + parity + attestation
  python3 registry/packs/validate.py --self-test      # above + every tests/fixtures/* must FAIL
  python3 registry/packs/validate.py <file> [...]     # validate one or more pack files

Every function is importable (pytest suite in tests/ drives them directly).
"""

from __future__ import annotations

import base64
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
# `packs` lives under registry/ (one level above this file); make the package
# importable whether validate.py runs as a script or under pytest.
_PACKS_ROOT = os.path.dirname(HERE)
if _PACKS_ROOT not in sys.path:
    sys.path.insert(0, _PACKS_ROOT)
from packs import attestation  # noqa: E402
DEFAULT_SCHEMA = os.path.join(HERE, "agent-pack.schema.json")
DEFAULT_CATALOG = os.path.join(HERE, "pack-catalog.yaml")
DEFAULT_RELEASES = os.path.join(HERE, "releases")
DEFAULT_MANIFEST = os.path.join(HERE, "versions", "manifest.yaml")
DEFAULT_KEY = os.path.join(HERE, "publisher-key.pem")
DEFAULT_FIXTURES = os.path.join(HERE, "tests", "fixtures")

ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
UPSTREAM_RE = re.compile(r"^(https?|git)://")
PUBLISHER_RE = re.compile(r"^[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*$")
RELEASE_FILE_RE = re.compile(
    r"^(?P<pid>[a-z][a-z0-9-]*)\.(?P<pver>[0-9]+\.[0-9]+\.[0-9]+)\.yaml$")

REQUIRED_FIELDS = [
    "schema", "id", "version", "name", "category", "upstream", "publisher",
    "lifecycle", "contents", "attestation",
]
LIFECYCLE_STATES = ("planned", "live", "paused", "retired")

# schema definitions name -> catalog section name (parity gate)
_DEF_TO_CATALOG = {
    "categoryId": "categories",
    "artifactType": "artifactTypes",
}


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

def load_yaml(path):
    if not HAS_YAML:
        raise RuntimeError("PyYAML is not importable; cannot parse %s" % path)
    with open(path, "r", encoding="utf-8") as fh:
        try:
            return yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            raise ValueError("%s: invalid YAML: %s" % (path, exc))


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except json.JSONDecodeError as exc:
            raise ValueError("%s: invalid JSON: %s" % (path, exc))


def load_public_key(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------------------
# catalog vocabulary + parity
# --------------------------------------------------------------------------

def catalog_vocab(catalog):
    return {
        "categories": set((catalog.get("categories") or {}).keys()),
        "artifactTypes": set((catalog.get("artifactTypes") or {}).keys()),
        "lifecycleStates": set(catalog.get("lifecycleStates") or []),
    }


def catalog_self_errors(catalog):
    errors = []
    if not isinstance(catalog, dict):
        return ["catalog: must be a YAML mapping"]
    vocab = catalog_vocab(catalog)
    if not vocab["categories"]:
        errors.append("catalog: 'categories' is empty")
    if not vocab["artifactTypes"]:
        errors.append("catalog: 'artifactTypes' is empty")
    if not vocab["lifecycleStates"]:
        errors.append("catalog: 'lifecycleStates' is empty")
    return errors


def parity_errors(schema, catalog):
    """Schema definitions enum == catalog vocabulary (no drift)."""
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
    lc_def = set((defs.get("lifecycleState") or {}).get("enum") or [])
    lc_cat = set(catalog.get("lifecycleStates") or [])
    if lc_def != lc_cat:
        errors.append("parity: lifecycleState enum %s does not equal "
                      "catalog.lifecycleStates %s"
                      % (sorted(lc_def), sorted(lc_cat)))
    return errors


# --------------------------------------------------------------------------
# schema conformance + code-native membership + contents integrity
# --------------------------------------------------------------------------

def schema_errors(data, schema, label):
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


def membership_errors(data, vocab, label):
    """Code-native fail-closed checks (authoritative second layer)."""
    errors = []
    if not isinstance(data, dict):
        return ["%s: pack must be a YAML/JSON object" % label]

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
    if data.get("schema") != "agent-pack/v1":
        errors.append("%s: schema must be 'agent-pack/v1'" % label)
    category = data.get("category")
    if isinstance(category, str) and category not in vocab["categories"]:
        errors.append("%s: category '%s' is not a known pack category "
                      "(valid: %s)" % (label, category,
                                        ", ".join(sorted(vocab["categories"]))))
    lifecycle = data.get("lifecycle")
    if isinstance(lifecycle, str) and lifecycle not in LIFECYCLE_STATES:
        errors.append("%s: lifecycle '%s' must be one of %s"
                      % (label, lifecycle, ", ".join(LIFECYCLE_STATES)))
    upstream = data.get("upstream")
    if isinstance(upstream, str) and not UPSTREAM_RE.match(upstream):
        errors.append("%s: upstream '%s' must be an http(s):// or git:// URI"
                      % (label, upstream))
    publisher = data.get("publisher")
    if isinstance(publisher, str) and not PUBLISHER_RE.match(publisher):
        errors.append("%s: publisher '%s' must be namespace/team"
                      % (label, publisher))

    contents = data.get("contents")
    if not isinstance(contents, dict) or not contents:
        errors.append("%s: contents must be a non-empty mapping" % label)
    else:
        for type_, entries in contents.items():
            if type_ not in vocab["artifactTypes"]:
                errors.append("%s: contents bundles unknown artifact type "
                              "'%s' (fail closed)" % (label, type_))
                continue
            if not isinstance(entries, list) or not entries:
                errors.append("%s: contents.%s must be a non-empty array"
                              % (label, type_))
                continue
            seen_refs = set()
            for entry in entries:
                if not isinstance(entry, dict):
                    errors.append("%s: contents.%s entries must be objects"
                                  % (label, type_))
                    continue
                ref = entry.get("ref")
                data_b64 = entry.get("data")
                sha = entry.get("sha256")
                if not isinstance(ref, str) or not ref:
                    errors.append("%s: contents.%s entry missing 'ref'"
                                  % (label, type_))
                elif ref in seen_refs:
                    errors.append("%s: contents.%s duplicate artifact ref '%s'"
                                  % (label, type_, ref))
                else:
                    seen_refs.add(ref)
                if not isinstance(data_b64, str) or not data_b64:
                    errors.append("%s: contents.%s/%s missing base64 'data'"
                                  % (label, type_, ref))
                if not isinstance(sha, str) or not SHA_RE.match(sha or ""):
                    errors.append("%s: contents.%s/%s sha256 must be 64 hex"
                                  % (label, type_, ref))
                    continue
                # contents integrity: decoded data must hash to declared sha
                try:
                    raw = base64.b64decode(data_b64, validate=True)
                except Exception:
                    errors.append("%s: contents.%s/%s data is not valid base64"
                                  % (label, type_, ref))
                    continue
                if hashlib.sha256(raw).hexdigest() != sha.lower():
                    errors.append("%s: contents.%s/%s data hash does not match "
                                  "declared sha256 (tampered content)"
                                  % (label, type_, ref))

    deps = data.get("dependencies") or []
    if not isinstance(deps, list):
        errors.append("%s: dependencies must be an array" % label)
    else:
        seen = set()
        for dep in deps:
            if not isinstance(dep, dict):
                errors.append("%s: dependency entries must be objects" % label)
                continue
            dep_id = dep.get("id")
            dep_ver = dep.get("version")
            if not isinstance(dep_id, str) or not ID_RE.match(dep_id or ""):
                errors.append("%s: dependency id '%s' must match ^[a-z]"
                              "[a-z0-9-]*$" % (label, dep_id))
            if not isinstance(dep_ver, str) or not VERSION_RE.match(
                    dep_ver or ""):
                errors.append("%s: dependency version '%s' must be X.Y.Z"
                              % (label, dep_ver))
            key = (dep_id, dep_ver)
            if key in seen:
                errors.append("%s: duplicate dependency %s@%s"
                              % (label, dep_id, dep_ver))
            seen.add(key)

    att = data.get("attestation")
    if not isinstance(att, dict):
        errors.append("%s: attestation must be an object" % label)
    else:
        for field in ("kid", "alg", "signedAt", "signature"):
            if not isinstance(att.get(field), str) or not att.get(field):
                errors.append("%s: attestation missing '%s'" % (label, field))
        if att.get("alg") != attestation.ALG:
            errors.append("%s: attestation.alg must be '%s'"
                          % (label, attestation.ALG))
    return errors


def attestation_errors(data, public_key, label):
    """Release attestation MUST verify against the publisher public key.

    Fails closed when cryptography is unavailable or the public key is
    missing: a release whose signature cannot be verified is never trusted.
    """
    if public_key is None:
        return ["%s: publisher public key is missing; attestation cannot be "
                "verified (fail closed)" % label]
    if not attestation.HAS_CRYPTO:
        return ["%s: cryptography unavailable; attestation cannot be verified "
                "(fail closed)" % label]
    if not attestation.verify_pack(data, public_key):
        return ["%s: attestation signature does not verify against the "
                "publisher public key" % label]
    return []


def validate_pack_data(data, schema, catalog, label):
    """Schema conformance + code-native membership for one pack doc."""
    errors = []
    errors.extend(schema_errors(data, schema, label))
    errors.extend(membership_errors(data, catalog_vocab(catalog), label))
    return errors


def validate_release_file(path, schema, catalog, public_key, label=None):
    """Validate one release snapshot: parse, filename/content consistency,
    schema+membership, contents integrity (in membership) and attestation."""
    label = label or os.path.relpath(path, HERE)
    errors = []
    try:
        data = load_yaml(path)
    except Exception as exc:
        return ["%s: %s" % (label, exc)]
    base = os.path.basename(path)
    match = RELEASE_FILE_RE.match(base)
    if not match:
        errors.append("%s: release filename must be <id>.<version>.yaml"
                      % label)
    else:
        pid, pver = match.group("pid"), match.group("pver")
        if not isinstance(data, dict):
            return ["%s: release must be a YAML object" % label]
        if data.get("id") != pid:
            errors.append("%s: declared id '%s' does not match filename id "
                          "'%s'" % (label, data.get("id"), pid))
        if data.get("version") != pver:
            errors.append("%s: declared version '%s' does not match filename "
                          "version '%s'" % (label, data.get("version"), pver))
    errors.extend(validate_pack_data(data, schema, catalog, label))
    errors.extend(attestation_errors(data, public_key, label))
    return errors


# --------------------------------------------------------------------------
# published-version ledger (immutability)
# --------------------------------------------------------------------------

def ledger_errors(releases_dir, manifest_path):
    """Refuse: mutation of a published release, duplicate (pack, version),
    release files missing from the ledger, ledger entries whose file is
    missing or misnamed."""
    errors = []
    try:
        manifest = load_yaml(manifest_path)
    except Exception as exc:
        return ["ledger: %s" % exc]

    published = manifest.get("published") if isinstance(manifest, dict) else None
    if not isinstance(published, list) or not published:
        return ["ledger: manifest has no non-empty 'published' list"]

    base_dir = os.path.dirname(os.path.abspath(releases_dir))
    releases_abs = os.path.normpath(os.path.abspath(releases_dir))
    by_file = {}
    seen_keys = {}
    for i, entry in enumerate(published):
        if not isinstance(entry, dict):
            errors.append("ledger: entry %d is not a mapping" % i)
            continue
        pid = entry.get("pack")
        ver = entry.get("version")
        rel = entry.get("file")
        sha = entry.get("sha256")
        if not all(isinstance(x, str) and x for x in (pid, ver, rel, sha)):
            errors.append("ledger: entry %d must have string pack, version, "
                          "file, sha256" % i)
            continue
        key = (pid, ver)
        if key in seen_keys:
            errors.append("ledger: duplicate published (pack, version): %s %s"
                          % key)
        seen_keys[key] = i

        expected = "%s.%s.yaml" % (pid, ver)
        if os.path.basename(rel) != expected:
            errors.append("ledger: entry file '%s' must be named '%s'"
                          % (rel, expected))
        full = os.path.normpath(os.path.join(base_dir, rel))
        if not full.startswith(releases_abs + os.sep):
            errors.append("ledger: entry file '%s' must live under releases/"
                          % rel)
            continue
        if not os.path.exists(full):
            errors.append("ledger: entry file '%s' is missing" % rel)
            continue
        digest = hashlib.sha256(open(full, "rb").read()).hexdigest()
        if digest != sha.lower():
            errors.append("ledger: published pack %s %s is IMMUTABLE but its "
                          "on-disk sha256 changed (file %s)" % (pid, ver, rel))
        by_file.setdefault(rel, []).append(entry)

    release_names = sorted(n for n in os.listdir(releases_dir)
                           if n.endswith(".yaml"))
    for name in release_names:
        rel = "releases/%s" % name
        if rel not in by_file:
            errors.append("ledger: release '%s' is not published in the "
                          "version manifest" % rel)
    return errors


# --------------------------------------------------------------------------
# aggregate runs
# --------------------------------------------------------------------------

def coverage_errors(releases_dir, manifest_path, schema_path, catalog_path,
                    public_key_path, label_base="registry/packs"):
    """Releases + schema/catalog parity + ledger + attestation."""
    errors = []
    schema = load_json(schema_path)
    catalog = load_yaml(catalog_path)
    public_key = load_public_key(public_key_path)
    errors.extend(catalog_self_errors(catalog))
    errors.extend(parity_errors(schema, catalog))
    errors.extend(ledger_errors(releases_dir, manifest_path))
    names = sorted(n for n in os.listdir(releases_dir) if n.endswith(".yaml"))
    if not names:
        errors.append("coverage: no release packs under releases/")
    for name in names:
        errors.extend(validate_release_file(
            os.path.join(releases_dir, name), schema, catalog, public_key,
            label="%s/releases/%s" % (label_base, name)))
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
        errs = validate_pack_data(data, schema, catalog, label) \
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
    releases_dir, manifest_path = DEFAULT_RELEASES, DEFAULT_MANIFEST
    key_path, fixtures_dir = DEFAULT_KEY, DEFAULT_FIXTURES

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
        elif arg == "--releases":
            i += 1
            releases_dir = argv[i]
        elif arg == "--manifest":
            i += 1
            manifest_path = argv[i]
        elif arg == "--key":
            i += 1
            key_path = argv[i]
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
            public_key = load_public_key(key_path)
        except Exception as exc:
            print("validate.py: %s" % exc, file=sys.stderr)
            return 2
        for path in positional:
            errors.extend(validate_release_file(
                path, schema, catalog, public_key,
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
        ok, errors = coverage_errors(releases_dir, manifest_path, schema_path,
                                     catalog_path, key_path)
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
        print("validate: OK (coverage: releases + ledger + parity + "
              "attestation)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
