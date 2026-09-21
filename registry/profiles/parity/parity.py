#!/usr/bin/env python3
"""AgentIdentity parity engine — one shared agent-identity schema (issue #346).

---knowledge---
module_id: registry.profiles.parity.parity
system: registry
app: profiles
solution_class: enterprise
patterns: [drift-detection, schema-parity, both-directions, no-hand-maintained-snapshot]
derives_from: null
owner_sme: sync-sme
tier: L1
interfaces: [evaluate, identity_findings, schema_parity_findings, seed_findings, closed_vocab_fields, CannotAssess]
invariants: "agent-identity drift must be impossible to land SILENTLY, in BOTH directions, with no hand-maintained snapshot anywhere"
gotchas: ""
related: ["#346"]
do_not_duplicate: null
---knowledge---

The point of this module is to make AGENT-IDENTITY DRIFT IMPOSSIBLE TO LAND
SILENTLY, in BOTH directions, with no hand-maintained snapshot anywhere:

  * A shared, versioned schema (registry/profiles/agent-identity.schema.json)
    is the single source of truth for the agent record — the UNION of the
    shared-frontend TeamAgent wire fields and the agent-orchestrator
    AgentProfile contract fields, with a CLOSED vocabulary on every enum.
  * The schema's closed vocabularies must EQUAL the vocabularies this repo
    already declares (agent-profile.schema.json definitions + catalog.yaml).
    Widen either side without reconciling the shared schema and the parity
    check reports drift — that is the "a field change in one repo fails the
    other's gate until reconciled" property, checked offline.
  * Every seed under registry/profiles/seeds/ is PROJECTED to an identity view
    (a documented, mechanical projection — id -> name, transport -> provider,
    defaultModelTier -> modelTier, capabilitySet -> capabilities, absent status
    -> registered) and that projection is validated against the shared schema.
    The seed files are the input; nothing is snapshotted or hand-maintained.

Findings carry a STABLE CODE so a caller (or a test) can assert the reason:

  AI-SCHEMA-DRIFT    a shared-schema vocabulary/field set disagrees with this
                     repo's declared vocabulary/field set (either direction)
  AI-MISSING-FIELD   a required field is absent from the identity view
  AI-EXTRA-FIELD     a field the shared schema does not declare
  AI-OUT-OF-VOCAB    a value outside a closed vocabulary (incl. free-text
                     capabilities, which are refused, never accepted)
  AI-WRONG-TYPE      a value of the wrong JSON type
  AI-SHAPE           any other constraint (pattern/format/length) violation
  AI-CANNOT-ASSESS   an input the check needs is absent or unreadable

Exit-code contract (guardrails/honesty tri-state, issue #28):
0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. CANNOT-ASSESS is never reported as a pass,
and a missing jsonschema/PyYAML FAILS CLOSED rather than passing vacuously.

Usage: see registry/profiles/parity/cli.py
"""

from __future__ import annotations

import json
import os

try:
    import yaml
    HAS_YAML = True
except ImportError:  # pragma: no cover - guarded by CannotAssess below
    yaml = None
    HAS_YAML = False

try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:  # pragma: no cover - guarded by CannotAssess below
    jsonschema = None
    HAS_JSONSCHEMA = False

OK = 0
NOT_OK = 1
CANNOT_ASSESS = 2
RESULT_NAMES = {OK: "OK", NOT_OK: "NOT-OK", CANNOT_ASSESS: "CANNOT-ASSESS"}

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILES_DIR = os.path.dirname(HERE)

DEFAULT_SCHEMA = os.path.join(PROFILES_DIR, "agent-identity.schema.json")
DEFAULT_PROFILE_SCHEMA = os.path.join(PROFILES_DIR, "agent-profile.schema.json")
DEFAULT_CATALOG = os.path.join(PROFILES_DIR, "catalog.yaml")
DEFAULT_SEEDS = os.path.join(PROFILES_DIR, "seeds")

SCHEMA_VERSION = 1

# --- the identity axis (the shared-frontend TeamAgent fields) ----------------
# Declared here as the INDEPENDENT ORACLE of the union: the shared schema must
# require exactly the AgentProfile required set plus these five fields. Adding a
# required field to either repo without reconciling the shared schema fails.
IDENTITY_REQUIRED = ("name", "provider", "status", "modelTier", "capabilities")

# The two declared status vocabularies the single closed enum must span. A
# repo that adds a status widens its own set and the shared enum disagrees.
AGENT_ORCHESTRATOR_STATUS = ("registered", "active", "paused", "retired")
SHARED_FRONTEND_STATUS = ("active", "paused", "retired")
STATUS_UNION = tuple(sorted(set(AGENT_ORCHESTRATOR_STATUS)
                            | set(SHARED_FRONTEND_STATUS)))

# --- the documented projection (AgentProfile seed -> AgentIdentity view) -----
DEFAULT_PROVIDER = "unspecified"
DEFAULT_STATUS = "registered"
TRANSPORT_TO_PROVIDER = {
    "deepseek": "deepseek",   # the DeepSeek API path
    "api": "anthropic",       # the Anthropic API path
    "session": "anthropic",   # the running CLI session
}

# --- stable finding codes ---------------------------------------------------
F_MISSING = "AI-MISSING-FIELD"
F_EXTRA = "AI-EXTRA-FIELD"
F_VOCAB = "AI-OUT-OF-VOCAB"
F_TYPE = "AI-WRONG-TYPE"
F_SHAPE = "AI-SHAPE"
F_DRIFT = "AI-SCHEMA-DRIFT"
F_CANNOT = "AI-CANNOT-ASSESS"

# Vocabularies the shared schema must reproduce from this repo, keyed by the
# shared schema's definition name and the catalog section it must equal.
PINNED_DEFINITIONS = (
    ("modelTier", "tiers"),
    ("capabilityId", "capabilities"),
    ("toolId", "tools"),
    ("constraintId", "constraints"),
    ("memoryScopeValue", "memoryScopes"),
    ("guardrailPolicyId", "guardrailPolicies"),
)

# jsonschema validators already covered by the code-native layer (so a single
# defect is reported once, with a stable code, rather than twice with two
# different message shapes).
_COVERED_VALIDATORS = {"type", "enum", "required", "additionalProperties"}


class CannotAssess(Exception):
    """An input the check needs is absent, unreadable or unverifiable."""


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

def load_json(path):
    """Load a JSON document; raise CannotAssess when it cannot be read."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise CannotAssess("%s is unreadable: %s" % (path, exc))


def load_yaml(path):
    """Load a YAML document; raise CannotAssess when it cannot be read."""
    if not HAS_YAML:
        raise CannotAssess("PyYAML is not importable; cannot parse %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except (OSError, yaml.YAMLError) as exc:
        raise CannotAssess("%s is unreadable: %s" % (path, exc))


def finding(code, label, field, detail):
    """Render one stable finding line: <label>: <CODE> field=<f>: <detail>."""
    return "%s: %s field=%s: %s" % (label, code, field, detail)


def type_name(value):
    """A JSON-shaped name for a Python value (for honest type findings)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__


# --------------------------------------------------------------------------
# catalog vocabulary
# --------------------------------------------------------------------------

def catalog_vocab(catalog):
    """Known-id sets derived from catalog.yaml (independent of validate.py)."""
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
    }


# --------------------------------------------------------------------------
# the projection: AgentProfile seed -> AgentIdentity view
# --------------------------------------------------------------------------

def project_identity(profile):
    """Project an AgentProfile record onto the shared AgentIdentity view.

    Mechanical and documented (docs/AGENT-IDENTITY.md): a field the record
    already declares is NEVER rewritten — only absent fields are filled, so a
    declared (and therefore checkable) value can never be masked by a default.
    """
    record = dict(profile) if isinstance(profile, dict) else {}

    if "name" not in record and isinstance(profile.get("id"), str):
        record["name"] = profile["id"]

    if "provider" not in record:
        transport = profile.get("transport")
        if isinstance(transport, str):
            record["provider"] = TRANSPORT_TO_PROVIDER.get(
                transport, DEFAULT_PROVIDER)
        else:
            record["provider"] = DEFAULT_PROVIDER

    if "modelTier" not in record and isinstance(
            profile.get("defaultModelTier"), str):
        record["modelTier"] = profile["defaultModelTier"]

    if "status" not in record:
        record["status"] = DEFAULT_STATUS

    if "capabilities" not in record and isinstance(
            profile.get("capabilitySet"), list):
        record["capabilities"] = list(profile["capabilitySet"])

    return record


# --------------------------------------------------------------------------
# closed-vocabulary introspection (data-driven, from the shared schema)
# --------------------------------------------------------------------------

def _definition_enum(schema, ref):
    """Resolve a local `#/definitions/<name>` $ref to its enum, if any."""
    if not isinstance(ref, str) or not ref.startswith("#/definitions/"):
        return None
    return (schema.get("definitions") or {}).get(ref.rsplit("/", 1)[-1], {}).get("enum")


def closed_vocab_fields(schema):
    """field -> (kind, allowed ids) for every closed-vocabulary property.

    `kind` is "scalar" for an enum property and "list" for an array whose items
    are an enum or a $ref to an enum definition. Vocabulary membership is thus
    read from the shared schema itself rather than restated here.
    """
    out = {}
    for name, spec in sorted((schema.get("properties") or {}).items()):
        if not isinstance(spec, dict):
            continue
        if spec.get("enum"):
            out[name] = ("scalar", list(spec["enum"]))
            continue
        items = spec.get("items")
        if not isinstance(items, dict):
            continue
        allowed = None
        if items.get("enum"):
            allowed = list(items["enum"])
        elif isinstance(items.get("$ref"), str) and "$ref" in items:
            allowed = _definition_enum(schema, items["$ref"])
        if allowed is not None:
            out[name] = ("list", allowed)
    # A property that only $refs an enum definition is also a closed scalar.
    for name, spec in sorted((schema.get("properties") or {}).items()):
        if name in out or not isinstance(spec, dict):
            continue
        allowed = _definition_enum(schema, spec.get("$ref"))
        if allowed is not None:
            out[name] = ("scalar", list(allowed))
    return out


# --------------------------------------------------------------------------
# code-native findings (deterministic, no jsonschema message shapes)
# --------------------------------------------------------------------------

def required_findings(record, schema, label):
    """Absent required fields and undeclared extra fields."""
    findings = []
    for field in schema.get("required") or []:
        if field not in record:
            findings.append(finding(
                F_MISSING, label, field,
                "required field absent from the identity view"))
    if schema.get("additionalProperties") is False:
        allowed = set(schema.get("properties") or {})
        for field in sorted(set(record) - allowed):
            findings.append(finding(
                F_EXTRA, label, field,
                "field is not declared by the shared schema"))
    return findings


def vocab_findings(record, schema, label):
    """Closed-vocabulary membership — free text is refused, never accepted."""
    findings = []
    for field, (kind, allowed) in sorted(closed_vocab_fields(schema).items()):
        if field not in record:
            continue
        value = record[field]
        if kind == "scalar":
            if not isinstance(value, str):
                findings.append(finding(
                    F_TYPE, label, field,
                    "expected a string, got %s" % type_name(value)))
            elif value not in allowed:
                findings.append(finding(
                    F_VOCAB, label, field,
                    "'%s' is not a closed-vocabulary id; allowed: %s"
                    % (value, ", ".join(allowed))))
            continue
        if not isinstance(value, list):
            findings.append(finding(
                F_TYPE, label, field,
                "expected an array, got %s" % type_name(value)))
            continue
        for item in value:
            if not isinstance(item, str):
                findings.append(finding(
                    F_TYPE, label, field,
                    "entries must be strings, got %s" % type_name(item)))
            elif item not in allowed:
                findings.append(finding(
                    F_VOCAB, label, field,
                    "'%s' is free text or unknown; allowed: %s"
                    % (item, ", ".join(allowed))))
    return findings


def shape_findings(record, schema, label):
    """Everything jsonschema enforces beyond type/enum/required/extra."""
    if not HAS_JSONSCHEMA:
        raise CannotAssess(
            "jsonschema is not importable; schema conformance cannot run "
            "(fail closed)")
    findings = []
    for err in jsonschema.Draft7Validator(schema).iter_errors(record):
        if err.validator in _COVERED_VALIDATORS:
            continue
        field = ".".join(str(p) for p in err.absolute_path) or "(root)"
        findings.append(finding(F_SHAPE, label, field, err.message))
    return findings


def identity_findings(record, schema, label):
    """All findings for one identity view, in a deterministic order."""
    findings = []
    findings.extend(required_findings(record, schema, label))
    findings.extend(vocab_findings(record, schema, label))
    findings.extend(shape_findings(record, schema, label))
    return sorted(findings)


# --------------------------------------------------------------------------
# schema <-> schema parity (the anti-fork check, both directions)
# --------------------------------------------------------------------------

def _set_drift(findings, label, field, left_name, left, right_name, right):
    only_left = sorted(set(left) - set(right))
    only_right = sorted(set(right) - set(left))
    if only_left:
        findings.append(finding(
            F_DRIFT, label, field,
            "%s lists ids absent from %s: %s"
            % (left_name, right_name, ", ".join(only_left))))
    if only_right:
        findings.append(finding(
            F_DRIFT, label, field,
            "%s lists ids absent from %s: %s"
            % (right_name, left_name, ", ".join(only_right))))


def schema_parity_findings(identity_schema, profile_schema, catalog):
    """The shared schema must equal this repo's declared contract, both ways."""
    findings = []
    label = "shared-schema"
    idef = identity_schema.get("definitions") or {}
    pdef = profile_schema.get("definitions") or {}
    vocab = catalog_vocab(catalog)

    version = identity_schema.get("x-schema-version")
    if version != SCHEMA_VERSION:
        findings.append(finding(
            F_DRIFT, label, "x-schema-version",
            "expected %r, got %r" % (SCHEMA_VERSION, version)))

    # 1. every pinned vocabulary: shared == agent-profile.schema.json == catalog.
    for def_name, section in PINNED_DEFINITIONS:
        shared = (idef.get(def_name) or {}).get("enum") or []
        profile = (pdef.get(def_name) or {}).get("enum") or []
        catalog_ids = vocab[section]
        _set_drift(findings, label, def_name,
                   "shared schema", shared,
                   "agent-profile.schema.json", profile)
        _set_drift(findings, label, def_name,
                   "shared schema", shared,
                   "catalog.yaml." + section, catalog_ids)

    # 2. the status enum is exactly the declared union of both repos' sets, and
    #    it therefore cannot NARROW below the other repo's vocabulary (a shared
    #    value the other repo does not carry is the point of the superset).
    shared_status = (idef.get("statusValue") or {}).get("enum") or []
    _set_drift(findings, label, "status",
               "shared schema", shared_status,
               "the declared status union", STATUS_UNION)
    narrowed = sorted(set(SHARED_FRONTEND_STATUS) - set(shared_status))
    if narrowed:
        findings.append(finding(
            F_DRIFT, label, "status",
            "shared schema narrowed below the shared-frontend TeamAgent "
            "vocabulary; missing: %s" % ", ".join(narrowed)))

    # 3. the required set is exactly AgentProfile's required set plus the
    #    identity fields (a field added on either side fails until reconciled).
    shared_required = identity_schema.get("required") or []
    profile_required = profile_schema.get("required") or []
    _set_drift(findings, label, "required",
               "shared schema", shared_required,
               "agent-profile.schema.json + identity fields",
               list(profile_required) + list(IDENTITY_REQUIRED))

    # 4. every property this repo's profile schema declares is declared here,
    #    and every identity field is a declared property.
    shared_props = set(identity_schema.get("properties") or {})
    missing = sorted((set(profile_schema.get("properties") or {})
                      | set(IDENTITY_REQUIRED)) - shared_props)
    if missing:
        findings.append(finding(
            F_DRIFT, label, "properties",
            "shared schema does not declare: %s" % ", ".join(missing)))

    return sorted(findings)


# --------------------------------------------------------------------------
# seed validation
# --------------------------------------------------------------------------

def seed_findings(seeds_dir, schema):
    """Project and validate every seed under seeds_dir against the schema."""
    if not os.path.isdir(seeds_dir):
        raise CannotAssess("no seeds directory at %s" % seeds_dir)
    names = sorted(n for n in os.listdir(seeds_dir) if n.endswith(".yaml"))
    if not names:
        raise CannotAssess("no seed files under %s" % seeds_dir)

    findings = []
    for name in names:
        label = name
        try:
            profile = load_yaml(os.path.join(seeds_dir, name))
        except CannotAssess as exc:
            findings.append(finding(F_CANNOT, label, "(file)", str(exc)))
            continue
        if not isinstance(profile, dict):
            findings.append(finding(
                F_TYPE, label, "(record)",
                "seed must be a YAML mapping, got %s" % type_name(profile)))
            continue
        stem = name.split(".", 1)[0]
        if profile.get("id") != stem:
            findings.append(finding(
                F_SHAPE, label, "id",
                "declared id %r does not match the seed filename stem %r"
                % (profile.get("id"), stem)))
        findings.extend(identity_findings(project_identity(profile), schema, label))
    return names, sorted(findings)


# --------------------------------------------------------------------------
# evaluate
# --------------------------------------------------------------------------

def evaluate(schema_path=DEFAULT_SCHEMA,
             profile_schema_path=DEFAULT_PROFILE_SCHEMA,
             catalog_path=DEFAULT_CATALOG,
             seeds_dir=DEFAULT_SEEDS):
    """Run the whole parity check. Returns (status, lines)."""
    if not HAS_JSONSCHEMA:
        return CANNOT_ASSESS, [
            "%s: jsonschema is not importable; schema conformance cannot run "
            "(fail closed)" % F_CANNOT]
    if not HAS_YAML:
        return CANNOT_ASSESS, [
            "%s: PyYAML is not importable; seeds cannot be parsed (fail closed)"
            % F_CANNOT]

    try:
        identity_schema = load_json(schema_path)
        profile_schema = load_json(profile_schema_path)
        catalog = load_yaml(catalog_path)
        if not isinstance(identity_schema, dict):
            raise CannotAssess("%s is not a JSON object" % schema_path)
        if not isinstance(profile_schema, dict):
            raise CannotAssess("%s is not a JSON object" % profile_schema_path)
        if not isinstance(catalog, dict):
            raise CannotAssess("%s is not a YAML mapping" % catalog_path)
        names, findings = seed_findings(seeds_dir, identity_schema)
    except CannotAssess as exc:
        return CANNOT_ASSESS, ["%s: %s" % (F_CANNOT, exc)]

    findings = sorted(set(schema_parity_findings(
        identity_schema, profile_schema, catalog)) | set(findings))

    lines = [
        "shared schema: %s (x-schema-version=%s)"
        % (os.path.relpath(schema_path, PROFILES_DIR),
           identity_schema.get("x-schema-version")),
        "pinned to: agent-profile.schema.json + catalog.yaml "
        "(%d closed vocabularies)" % len(PINNED_DEFINITIONS),
        "seeds projected to the identity view and validated: %d" % len(names),
    ]
    for line in findings:
        lines.append("  DRIFT  " + line)
    if findings:
        lines.append("agent-identity-parity: NOT-OK — %d finding(s)" % len(findings))
        return NOT_OK, lines
    lines.append("agent-identity-parity: OK — vocabularies reconcile and every "
                 "seed satisfies the shared schema")
    return OK, lines
