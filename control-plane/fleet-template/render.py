#!/usr/bin/env python3
"""Per-repo agent fleet template renderer + drift/isolation gate (issue #146).

WHAT THIS IS
------------
One parameterized template (``template.yaml``) renders a COMPLETE per-repo
agent fleet -- roles, SMEs, routing, FinOps ceilings and run-state -- from a
repository's own params file. Nothing about an instance is hand-edited: if a
rendered instance and a fresh render of the same params disagree, that is
drift and the gate says NOT-OK.

Three properties this module is responsible for, all of them measured by
``tests/`` rather than asserted:

1. **Parameterized, not a blob.** Changing a parameter (a domain, a model
   tier, a budget) changes the rendered fleet. Rendering is a pure function of
   (template bytes, params bytes, observations bytes).
2. **Isolated.** Every identity, state path, worktree prefix and lane
   namespace is derived from the ``repo`` parameter, so two repositories'
   fleets share no identity and no state. ``check_isolation`` compares two
   rendered instances and reports any shared or nested namespace, and any
   observation naming a member of a different fleet.
3. **Honest run-state.** Composition is DECLARED; run state is OBSERVED. A
   member is ``required`` only when it was observed running. A declared
   ``required`` member with no observation is ``unverified`` -- never
   reported as present. ``present`` is by construction the set observed
   running, and ``check_invariants`` proves it.

DEPENDENCIES
------------
Python standard library + PyYAML only. No network, no third-party templating,
no third-party validation library: the JSON-Schema subset validator in this
file is what enforces ``schema.yaml`` in the gate. (``tests/`` additionally
cross-checks the same documents with the ``jsonschema`` library when it is
installed, so the schema's claim to be a real JSON Schema is measured too.)

EXIT-CODE CONTRACT (guardrails/honesty tri-state, issue #28)
------------------------------------------------------------
=====  =============  ======================================================
code   status         meaning
=====  =============  ======================================================
0      OK             template, params and every committed instance agree
1      NOT-OK         a real defect: drift, an invariant violation, a
                      hostile observation, or a missing/duplicate pilot
2      CANNOT-ASSESS  the contract could not be assessed: an input is
                      missing/unparseable, or a document violates the schema
=====  =============  ======================================================

CANNOT-ASSESS never reads as a pass. Aggregation is fail-closed: any NOT-OK
fails the gate; otherwise any CANNOT-ASSESS keeps it from reading OK.

Usage:
    python3 control-plane/fleet-template/render.py render --params P [--out F]
    python3 control-plane/fleet-template/render.py report --params P [--json]
    python3 control-plane/fleet-template/render.py validate-instance F
    python3 control-plane/fleet-template/render.py check


---knowledge---
module_id: control-plane.fleet-template.render
system: control-plane
app: fleet-template
solution_class: enterprise
patterns: [pure-function-render, deterministic-render, declared-vs-observed, isolation-by-construction, tri-state-exit]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [main, render_instance, load_bundle, check, check_drift, check_isolation, check_invariants, Assessment, Finding]
invariants: "rendering is a pure function of (template bytes, params bytes, observations bytes); composition is DECLARED and run state is OBSERVED, so a declared required member with no observation is unverified and never reported as present"
gotchas: "every identity, state path, worktree prefix and lane namespace is derived from the repo parameter, so two repositories' fleets share no identity and no state"
related: ["#146"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2

API_VERSION = "ao.fleet-template/v1"
GENERATED_BY = "control-plane/fleet-template/render.py"

TEMPLATE_FILE = "template.yaml"
SCHEMA_FILE = "schema.yaml"
PILOTS_DIR = "pilots"

try:  # PyYAML is the only non-stdlib dependency of this lane.
    import yaml
except ImportError as _exc:  # pragma: no cover - environment failure
    print(f"render: PyYAML is required ({_exc})", file=sys.stderr)
    raise SystemExit(EXIT_CANNOT_ASSESS)

PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)\}")
WHOLE_PLACEHOLDER_RE = re.compile(r"^\$\{([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)\}$")

STATUS_OK = "OK"
STATUS_NOT_OK = "NOT-OK"
STATUS_CANNOT_ASSESS = "CANNOT-ASSESS"

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class FleetTemplateError(Exception):
    """Base class for CANNOT-ASSESS-class failures (the input is not assessable)."""


class InputError(FleetTemplateError):
    """An input could not be read, parsed, or resolved."""


class SchemaViolation(FleetTemplateError):
    """A document does not satisfy its JSON Schema. Hard failure, never a pass."""

    def __init__(self, document_kind: str, violations: Sequence[str]):
        self.document_kind = document_kind
        self.violations = list(violations)
        detail = "; ".join(self.violations) if self.violations else "unknown violation"
        super().__init__(f"{document_kind} violates {SCHEMA_FILE}: {detail}")


@dataclass(frozen=True)
class Finding:
    """A NOT-OK-class defect: well-formed data that breaks a declared rule."""

    code: str
    message: str

    def render_line(self) -> str:
        return f"[{self.code}] {self.message}"


@dataclass
class Assessment:
    """Aggregated tri-state result of a check run."""

    findings: List[Finding] = field(default_factory=list)
    cannot_assess: List[str] = field(default_factory=list)
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        if self.findings:
            return STATUS_NOT_OK
        if self.cannot_assess:
            return STATUS_CANNOT_ASSESS
        return STATUS_OK

    @property
    def exit_code(self) -> int:
        return {
            STATUS_OK: EXIT_OK,
            STATUS_NOT_OK: EXIT_NOT_OK,
            STATUS_CANNOT_ASSESS: EXIT_CANNOT_ASSESS,
        }[self.status]

    def lines(self) -> List[str]:
        out = [f"  {f.render_line()}" for f in self.findings]
        out += [f"  [CANNOT-ASSESS] {m}" for m in self.cannot_assess]
        return out


# ---------------------------------------------------------------------------
# Minimal JSON-Schema (2020-12 subset) validator
# ---------------------------------------------------------------------------

_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def json_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def resolve_ref(root: Any, ref: str) -> Any:
    """Resolve a local ``#/...`` JSON-Pointer reference against ``root``."""
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise FleetTemplateError(f"unsupported $ref {ref!r} (only local #/... refs)")
    node = root
    for raw in ref[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and token in node:
            node = node[token]
            continue
        raise FleetTemplateError(f"unresolvable $ref {ref!r}")
    return node


def validate(document: Any, schema: Any, root: Any, path: str = "$") -> List[str]:
    """Return a list of human-readable violations (empty list == valid)."""
    if schema is True or schema == {}:
        return []
    if schema is False:
        return [f"{path}: forbidden by schema"]
    if not isinstance(schema, dict):
        raise FleetTemplateError(f"malformed schema at {path}: {schema!r}")

    if "$ref" in schema:
        return validate(document, resolve_ref(root, schema["$ref"]), root, path)

    errors: List[str] = []

    for sub in schema.get("allOf", []):
        errors += validate(document, sub, root, path)
    if "anyOf" in schema:
        if not any(not validate(document, sub, root, path) for sub in schema["anyOf"]):
            errors.append(f"{path}: matches none of the anyOf branches")
    if "oneOf" in schema:
        matches = sum(1 for sub in schema["oneOf"] if not validate(document, sub, root, path))
        if matches != 1:
            errors.append(f"{path}: matches {matches} of the oneOf branches (expected exactly 1)")
    if "not" in schema and not validate(document, schema["not"], root, path):
        errors.append(f"{path}: matches a schema declared as forbidden")

    if "const" in schema and document != schema["const"]:
        errors.append(f"{path}: expected {schema['const']!r}, got {document!r}")
    if "enum" in schema and document not in schema["enum"]:
        errors.append(f"{path}: {document!r} is not one of {schema['enum']!r}")

    declared = schema.get("type")
    if declared is not None:
        allowed = declared if isinstance(declared, list) else [declared]
        unknown = [t for t in allowed if t not in _TYPE_CHECKS]
        if unknown:
            raise FleetTemplateError(f"schema at {path}: unknown type(s) {unknown!r}")
        if not any(_TYPE_CHECKS[t](document) for t in allowed):
            errors.append(
                f"{path}: expected {'|'.join(allowed)}, got {json_type_name(document)}"
            )
            return errors

    if isinstance(document, str):
        if "minLength" in schema and len(document) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(document) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], document):
            errors.append(f"{path}: {document!r} does not match pattern {schema['pattern']!r}")
    elif isinstance(document, bool):
        pass
    elif isinstance(document, (int, float)):
        if "minimum" in schema and document < schema["minimum"]:
            errors.append(f"{path}: {document} below minimum {schema['minimum']}")
        if "maximum" in schema and document > schema["maximum"]:
            errors.append(f"{path}: {document} above maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and document <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: {document} not above exclusiveMinimum {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and document >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: {document} not below exclusiveMaximum {schema['exclusiveMaximum']}")
    elif isinstance(document, list):
        if "minItems" in schema and len(document) < schema["minItems"]:
            errors.append(f"{path}: {len(document)} item(s) below minItems {schema['minItems']}")
        if "maxItems" in schema and len(document) > schema["maxItems"]:
            errors.append(f"{path}: {len(document)} item(s) above maxItems {schema['maxItems']}")
        if schema.get("uniqueItems"):
            seen = set()
            for index, item in enumerate(document):
                key = json.dumps(item, sort_keys=True, default=str)
                if key in seen:
                    errors.append(f"{path}[{index}]: duplicate item")
                seen.add(key)
        if "items" in schema:
            for index, item in enumerate(document):
                errors += validate(item, schema["items"], root, f"{path}[{index}]")
    elif isinstance(document, dict):
        if "minProperties" in schema and len(document) < schema["minProperties"]:
            errors.append(f"{path}: {len(document)} key(s) below minProperties {schema['minProperties']}")
        if "maxProperties" in schema and len(document) > schema["maxProperties"]:
            errors.append(f"{path}: {len(document)} key(s) above maxProperties {schema['maxProperties']}")
        for key in schema.get("required", []):
            if key not in document:
                errors.append(f"{path}: missing required key {key!r}")
        properties = schema.get("properties", {})
        for key, sub in properties.items():
            if key in document:
                errors += validate(document[key], sub, root, f"{path}.{key}")
        if "propertyNames" in schema:
            for key in document:
                errors += validate(key, schema["propertyNames"], root, f"{path}.<key>")
        extra = [key for key in document if key not in properties]
        additional = schema.get("additionalProperties", True)
        if additional is False:
            for key in extra:
                errors.append(f"{path}: unexpected key {key!r}")
        elif isinstance(additional, dict):
            for key in extra:
                errors += validate(document[key], additional, root, f"{path}.{key}")

    return errors


def require_valid(document: Any, kind: str, bundle: Dict[str, Any]) -> None:
    """Validate ``document`` against ``bundle[kind]``; raise on any violation."""
    violations = validate(document, bundle[kind], bundle)
    if violations:
        raise SchemaViolation(kind, violations)


# ---------------------------------------------------------------------------
# Loading and hashing
# ---------------------------------------------------------------------------


def load_yaml(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            document = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise InputError(f"{path}: not found") from exc
    except OSError as exc:
        raise InputError(f"{path}: cannot read ({exc})") from exc
    except yaml.YAMLError as exc:
        raise InputError(f"{path}: not valid YAML ({exc})") from exc
    if document is None:
        raise InputError(f"{path}: empty document")
    return document


def read_bytes(path: str) -> bytes:
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except FileNotFoundError as exc:
        raise InputError(f"{path}: not found") from exc
    except OSError as exc:
        raise InputError(f"{path}: cannot read ({exc})") from exc


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_canonical(value: Any) -> str:
    blob = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class _NoAliasDumper(yaml.SafeDumper):
    """Emit no anchors/aliases: identical values are written out in full.

    A shared list rendered once as an anchor and once as an alias makes a diff
    say "something changed" for a change elsewhere, and makes the committed
    instance stop being a literal copy of the data. The instance is data, not a
    graph.
    """

    def ignore_aliases(self, data: Any) -> bool:  # noqa: D102 - PyYAML hook
        return True


def dump_yaml(document: Any) -> str:
    return yaml.dump(
        document,
        Dumper=_NoAliasDumper,
        sort_keys=False,
        default_flow_style=False,
        width=100,
        allow_unicode=True,
    )


# ---------------------------------------------------------------------------
# The templating engine
# ---------------------------------------------------------------------------


def resolve_path(scope: Dict[str, Any], path: str) -> Any:
    """Resolve a dotted parameter path ("budget.per_role") inside ``scope``."""
    node: Any = scope
    for part in path.split("."):
        if isinstance(node, dict):
            if part not in node:
                raise InputError(f"unknown parameter path {path!r} (missing {part!r})")
            node = node[part]
        elif isinstance(node, list):
            try:
                index = int(part)
            except ValueError as exc:
                raise InputError(f"unknown parameter path {path!r} (bad index {part!r})") from exc
            if index < 0 or index >= len(node):
                raise InputError(f"unknown parameter path {path!r} (index {index} out of range)")
            node = node[index]
        else:
            raise InputError(f"unknown parameter path {path!r} (cannot descend into {type(node).__name__})")
    return node


def substitute_scalar(text: str, scope: Dict[str, Any]) -> Any:
    whole = WHOLE_PLACEHOLDER_RE.match(text)
    if whole:
        return resolve_path(scope, whole.group(1))

    def _replace(match: "re.Match[str]") -> str:
        value = resolve_path(scope, match.group(1))
        if isinstance(value, (dict, list)):
            raise InputError(
                f"parameter {match.group(1)!r} is a {json_type_name(value)}; "
                "a container cannot be interpolated into a longer string"
            )
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    return PLACEHOLDER_RE.sub(_replace, text)


def render_node(node: Any, scope: Dict[str, Any]) -> Any:
    """Render a template node: substitute placeholders, expand ``$for_each``."""
    if isinstance(node, dict):
        if "$for_each" in node:
            return render_for_each(node, scope)
        return {key: render_node(value, scope) for key, value in node.items()}
    if isinstance(node, list):
        return [render_node(item, scope) for item in node]
    if isinstance(node, str):
        return substitute_scalar(node, scope)
    return node


def render_for_each(node: Dict[str, Any], scope: Dict[str, Any]) -> Any:
    source = node["$for_each"]
    alias = node.get("$as")
    body = node.get("$template")
    if not isinstance(alias, str) or not alias:
        raise InputError("$for_each requires a string $as alias")
    if "$key" in node:
        if "$value" not in node:
            raise InputError("$for_each with $key also requires $value")
    elif body is None:
        raise InputError("$for_each without $key requires a $template body")
    items = resolve_path(scope, source)
    if isinstance(items, dict):
        items = list(items.values())
    if not isinstance(items, list):
        raise InputError(f"$for_each source {source!r} is not a list or mapping")
    if not items:
        raise InputError(f"$for_each source {source!r} is empty; the fleet would be incomplete")

    built: Any
    if "$key" in node:
        built = {}
        for item in items:
            local = dict(scope)
            local[alias] = item
            key = render_node(node["$key"], local)
            if not isinstance(key, str):
                key = str(key)
            if key in built:
                raise InputError(f"$for_each produced a duplicate mapping key {key!r}")
            built[key] = render_node(node.get("$value", body), local)
    else:
        built = []
        for item in items:
            local = dict(scope)
            local[alias] = item
            built.append(render_node(body, local))
    return built


def effective_params(template: Dict[str, Any], params_doc: Dict[str, Any]) -> Dict[str, Any]:
    """Merge supplied values over declared defaults, in declaration order."""
    scope: Dict[str, Any] = dict(params_doc["values"])
    for decl in template["parameters"]:
        name = decl["name"]
        if name not in scope and "default" in decl:
            scope[name] = render_node(decl["default"], scope)
    missing = [d["name"] for d in template["parameters"] if d.get("required") and d["name"] not in scope]
    if missing:
        raise InputError("missing required parameter(s): " + ", ".join(sorted(missing)))
    return {decl["name"]: scope[decl["name"]] for decl in template["parameters"] if decl["name"] in scope}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def member_ids(instance: Dict[str, Any]) -> List[str]:
    definition = instance["definition"]
    return [member["id"] for member in definition["roles"]] + [
        member["id"] for member in definition["smes"]
    ]


def build_run_state(
    repo: str,
    roles: Sequence[Dict[str, Any]],
    smes: Sequence[Dict[str, Any]],
    observations_doc: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Split declared composition from observed run state.

    ``required`` in the output means *observed running*. A declared required
    member with no observation is ``unverified`` and is never placed in
    ``present``. A parked member stays parked regardless of observation (an
    observation that contradicts the declaration is a finding, not a state).
    """
    members = list(roles) + list(smes)
    declared_required = [m["id"] for m in members if m["state"] == "required"]
    declared_parked = [m["id"] for m in members if m["state"] == "parked"]

    if observations_doc is None:
        source = "none: no run-state observation recorded for this repo"
        observed: List[str] = []
    else:
        if observations_doc["repo"] != repo:
            raise InputError(
                f"run-state observation names repo {observations_doc['repo']!r} "
                f"but is attached to {repo!r}"
            )
        source = observations_doc["source"]
        observed = list(observations_doc["observed_present"])

    observed_set = set(observed)
    tri_required = [member for member in declared_required if member in observed_set]
    unverified = [member for member in declared_required if member not in observed_set]
    tri_parked = list(declared_parked)

    return {
        "observations": {"source": source, "observed_present": observed},
        "declared": {"required": declared_required, "parked": declared_parked},
        "tri_state": {
            "required": tri_required,
            "parked": tri_parked,
            "unverified": unverified,
        },
        "present": list(tri_required),
        "counts": {
            "required": len(tri_required),
            "parked": len(tri_parked),
            "unverified": len(unverified),
            "present": len(tri_required),
        },
    }


def render_instance(
    template: Dict[str, Any],
    params_doc: Dict[str, Any],
    bundle: Dict[str, Any],
    template_bytes: bytes,
    params_bytes: bytes,
    params_source: str,
    observations_doc: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Render the fleet instance. Pure function of its inputs (no clock, no randomness)."""
    require_valid(template, "template", bundle)
    require_valid(params_doc, "params", bundle)
    if observations_doc is not None:
        require_valid(observations_doc, "run_state_document", bundle)

    scope = effective_params(template, params_doc)
    repo = scope["repo"]
    if params_doc["repo"] != repo:
        raise InputError(
            f"params.repo {params_doc['repo']!r} disagrees with values.repo {repo!r}"
        )

    spec = render_node(template["spec"], scope)
    tiers = scope["model_tiers"]

    roles: List[Dict[str, Any]] = []
    for entry in spec["roles"]:
        roles.append(
            {
                "id": f"{repo}:{entry['role']}",
                "role": entry["role"],
                "tier": entry["tier"],
                "model": tiers[entry["tier"]],
                "transport": entry["transport"],
                "effort": entry["effort"],
                "description": entry["description"],
                "memory_file": entry["memory_file"],
                "state": entry["state"],
                "parallelism": int(entry["parallelism"]),
            }
        )

    smes: List[Dict[str, Any]] = []
    for entry in spec["smes"]:
        smes.append(
            {
                "id": f"{repo}:sme:{entry['domain']}",
                "domain": entry["domain"],
                "sme": entry["sme"],
                "role": entry["role"],
                "tier": entry["tier"],
                "model": tiers[entry["tier"]],
                "labels": list(entry["labels"]),
                "prompt": entry["prompt"],
                "state": entry["state"],
            }
        )

    lenses = list(spec["routing"]["lenses"])
    for lens in lenses:
        lens["parallelism"] = int(lens["parallelism"])
    parallelism = {
        "total": sum(int(lens["parallelism"]) for lens in lenses),
        "per_lens": {lens["lens"]: int(lens["parallelism"]) for lens in lenses},
    }

    instance = {
        "apiVersion": API_VERSION,
        "kind": "FleetInstance",
        "metadata": {
            "repo": repo,
            "display_name": scope["display_name"],
            "template": template["metadata"]["name"],
            "template_version": template["metadata"]["version"],
            "template_sha256": sha256_bytes(template_bytes),
            "params_sha256": sha256_bytes(params_bytes),
            "params_source": params_source,
            "generated_by": GENERATED_BY,
        },
        "isolation": spec["isolation"],
        "definition": {
            "repo": repo,
            "roles": roles,
            "smes": smes,
            "routing": spec["routing"],
            "finops": spec["finops"],
            "parallelism": parallelism,
        },
        "run_state": build_run_state(repo, roles, smes, observations_doc),
        "params_digest": {"sha256": sha256_canonical(scope), "values": copy.deepcopy(scope)},
    }
    require_valid(instance, "instance", bundle)
    return instance


# ---------------------------------------------------------------------------
# Semantic invariants (NOT-OK class)
# ---------------------------------------------------------------------------


def check_invariants(instance: Dict[str, Any]) -> List[Finding]:
    """Rules the schema cannot express. Anything here is a real NOT-OK defect."""
    findings: List[Finding] = []
    definition = instance["definition"]
    isolation = instance["isolation"]
    repo = definition["repo"]
    run_state = instance["run_state"]
    finops = definition["finops"]

    if instance["metadata"]["repo"] != repo:
        findings.append(Finding("REPO_MISMATCH", f"metadata.repo {instance['metadata']['repo']!r} != definition.repo {repo!r}"))
    if instance["params_digest"]["values"]["repo"] != repo:
        findings.append(
            Finding(
                "PARAMS_DIGEST_REPO_MISMATCH",
                f"params_digest.values.repo {instance['params_digest']['values']['repo']!r} != definition.repo {repo!r}",
            )
        )

    expected = {
        "fleet_id": f"fleet-{repo}",
        "state_prefix": f".fleet/{repo}",
        "worktree_prefix": f"ao-{repo}-",
        "lane_namespace": repo,
    }
    for key, want in expected.items():
        if isolation[key] != want:
            findings.append(
                Finding("ISOLATION_NOT_NAMESPACED", f"isolation.{key}={isolation[key]!r}, expected {want!r} (derived from repo)")
            )
    if isolation["exclusive"] is not True:
        findings.append(Finding("ISOLATION_NOT_EXCLUSIVE", "isolation.exclusive must be true"))

    for member in member_ids(instance):
        if not member.startswith(f"{repo}:"):
            findings.append(
                Finding("MEMBER_ID_NOT_NAMESPACED", f"member id {member!r} is not namespaced by repo {repo!r}")
            )

    observed = set(run_state["observations"]["observed_present"])
    known = set(member_ids(instance))
    for member in sorted(observed - known):
        findings.append(
            Finding(
                "OBSERVATION_FOREIGN_MEMBER",
                f"run-state observation names {member!r}, which is not a member of fleet {isolation['fleet_id']!r}",
            )
        )
    parked = set(run_state["declared"]["parked"])
    for member in sorted(observed & parked):
        findings.append(
            Finding("PARKED_MEMBER_OBSERVED", f"member {member!r} is declared parked but was observed running")
        )

    declared_required = run_state["declared"]["required"]
    tri = run_state["tri_state"]
    for member in tri["unverified"]:
        if member in run_state["present"]:
            findings.append(
                Finding("UNVERIFIED_REPORTED_PRESENT", f"member {member!r} is unverified and must never be reported present")
            )
    if run_state["present"] != tri["required"]:
        findings.append(Finding("PRESENT_NOT_OBSERVED_SET", "run_state.present must equal the observed subset of required members"))
    if sorted(tri["required"] + tri["parked"] + tri["unverified"]) != sorted(declared_required + run_state["declared"]["parked"]):
        findings.append(Finding("RUN_STATE_PARTITION", "tri_state must partition the declared members (required+parked+unverified)"))

    counts = run_state["counts"]
    expected_counts = {
        "required": len(tri["required"]),
        "parked": len(tri["parked"]),
        "unverified": len(tri["unverified"]),
        "present": len(run_state["present"]),
    }
    if counts != expected_counts:
        findings.append(Finding("RUN_STATE_COUNTS", f"run_state.counts {counts!r} != {expected_counts!r}"))

    domain_set = {sme["domain"] for sme in definition["smes"]}
    role_set = {role["role"] for role in definition["roles"]}
    declared_domains = {entry["name"] for entry in instance["params_digest"]["values"]["domains"]}
    if domain_set != declared_domains:
        findings.append(Finding("SME_DOMAIN_COVERAGE", f"SME domains {sorted(domain_set)} != declared domains {sorted(declared_domains)}"))
    if set(definition["routing"]["domain_to_role"]) != declared_domains:
        findings.append(Finding("ROUTING_DOMAIN_COVERAGE", "routing.domain_to_role keys must equal the declared domain set"))
    lens_domains = {domain for lens in definition["routing"]["lenses"] for domain in lens["domains"]}
    if lens_domains != declared_domains:
        findings.append(Finding("LENS_DOMAIN_COVERAGE", f"lens domains {sorted(lens_domains)} != declared domains {sorted(declared_domains)}"))
    if set(finops["per_domain"]) != declared_domains:
        findings.append(Finding("FINOPS_DOMAIN_COVERAGE", "finops.per_domain keys must equal the declared domain set"))
    if set(finops["per_role"]) != role_set:
        findings.append(Finding("FINOPS_ROLE_COVERAGE", f"finops.per_role keys {sorted(finops['per_role'])} != roles {sorted(role_set)}"))

    allocated = sum(finops["per_role"].values()) + sum(finops["per_domain"].values())
    if allocated > finops["fleet_total"]:
        findings.append(
            Finding(
                "FINOPS_CEILING_EXCEEDED",
                f"per_role + per_domain = {allocated:g}{finops['currency']} exceeds fleet_total {finops['fleet_total']:g}{finops['currency']}",
            )
        )
    if definition["parallelism"]["total"] > finops["max_parallelism"]:
        findings.append(
            Finding(
                "PARALLELISM_CEILING_EXCEEDED",
                f"routing parallelism {definition['parallelism']['total']} exceeds max_parallelism {finops['max_parallelism']}",
            )
        )
    for role in definition["roles"]:
        if role["parallelism"] > finops["max_parallelism"]:
            findings.append(
                Finding(
                    "ROLE_PARALLELISM_CEILING_EXCEEDED",
                    f"role {role['role']!r} parallelism {role['parallelism']} exceeds max_parallelism {finops['max_parallelism']}",
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Drift + isolation
# ---------------------------------------------------------------------------


def _iter_diffs(committed: Any, fresh: Any, path: str = "$") -> Iterable[Tuple[str, Any, Any]]:
    if isinstance(committed, dict) and isinstance(fresh, dict):
        for key in sorted(set(committed) | set(fresh)):
            yield from _iter_diffs(committed.get(key), fresh.get(key), f"{path}.{key}")
        return
    if isinstance(committed, list) and isinstance(fresh, list) and len(committed) == len(fresh):
        for index, (left, right) in enumerate(zip(committed, fresh)):
            yield from _iter_diffs(left, right, f"{path}[{index}]")
        return
    if committed != fresh:
        yield (path, committed, fresh)


def check_drift(committed: Dict[str, Any], fresh: Dict[str, Any]) -> List[Finding]:
    """A committed instance that disagrees with a fresh render of its params is drift."""
    findings: List[Finding] = []
    for path, left, right in _iter_diffs(committed, fresh):
        code = "RENDER_DRIFT"
        if path.startswith("$.params_digest"):
            code = "PARAMS_DIGEST_DRIFT"
        elif path == "$.metadata.params_sha256":
            code = "PARAMS_SHA_DRIFT"
        elif path == "$.metadata.template_sha256":
            code = "TEMPLATE_SHA_DRIFT"
        findings.append(
            Finding(code, f"{path}: committed {left!r} != rendered {right!r}")
        )
    return findings


def check_isolation(instances: Sequence[Dict[str, Any]]) -> List[Finding]:
    """Two repos' fleets must share no identity and no state."""
    findings: List[Finding] = []
    for left, right in combinations(instances, 2):
        left_iso, right_iso = left["isolation"], right["isolation"]
        left_repo = left["definition"]["repo"]
        right_repo = right["definition"]["repo"]
        for key in ("fleet_id", "state_prefix", "worktree_prefix", "lane_namespace"):
            if left_iso[key] == right_iso[key]:
                findings.append(
                    Finding(
                        f"ISOLATION_SHARED_{key.upper()}",
                        f"{left_repo!r} and {right_repo!r} share isolation.{key}={left_iso[key]!r}",
                    )
                )
        prefixes = (left_iso["state_prefix"], right_iso["state_prefix"])
        if prefixes[0] != prefixes[1] and (
            prefixes[1].startswith(prefixes[0]) or prefixes[0].startswith(prefixes[1])
        ):
            findings.append(
                Finding(
                    "ISOLATION_NESTED_STATE",
                    f"state prefixes {prefixes[0]!r} and {prefixes[1]!r} nest; one fleet's state would live inside the other's",
                )
            )
        shared = sorted(set(member_ids(left)) & set(member_ids(right)))
        if shared:
            findings.append(
                Finding("ISOLATION_SHARED_MEMBER", f"{left_repo!r} and {right_repo!r} share member id(s) {shared}")
            )
    return findings


# ---------------------------------------------------------------------------
# The gate driver
# ---------------------------------------------------------------------------


def load_bundle(root: str) -> Tuple[Dict[str, Any], str]:
    path = os.path.join(root, SCHEMA_FILE)
    bundle = load_yaml(path)
    missing = [key for key in ("$defs", "template", "params", "run_state_document", "instance") if key not in bundle]
    if missing:
        raise InputError(f"{path}: schema bundle is missing top-level key(s) {missing} (cannot assess)")
    return bundle, path


def pilot_paths(root: str) -> List[str]:
    directory = os.path.join(root, PILOTS_DIR)
    if not os.path.isdir(directory):
        return []
    return sorted(
        os.path.join(directory, name)
        for name in os.listdir(directory)
        if name.endswith(".params.yaml")
    )


def check(root: str) -> Assessment:
    """Whole-lane check: template, every pilot's params/instance, drift, isolation."""
    result = Assessment()
    root = os.path.abspath(root)

    try:
        bundle, _ = load_bundle(root)
        template_path = os.path.join(root, TEMPLATE_FILE)
        template_bytes = read_bytes(template_path)
        template = load_yaml(template_path)
    except FleetTemplateError as exc:
        result.cannot_assess.append(str(exc))
        return result

    try:
        require_valid(template, "template", bundle)
    except SchemaViolation as exc:
        result.cannot_assess.append(str(exc))
        return result

    params_files = pilot_paths(root)
    if not params_files:
        result.cannot_assess.append(
            f"{os.path.join(root, PILOTS_DIR)}: no *.params.yaml pilot found; nothing to assess"
        )
        return result

    instances: List[Dict[str, Any]] = []
    for params_path in params_files:
        stem = os.path.basename(params_path)[: -len(".params.yaml")]
        try:
            params_bytes = read_bytes(params_path)
            params_doc = load_yaml(params_path)
            observations_doc = None
            if isinstance(params_doc, dict) and params_doc.get("observations"):
                observations_path = os.path.join(os.path.dirname(params_path), params_doc["observations"])
                observations_doc = load_yaml(observations_path)
            fresh = render_instance(
                template,
                params_doc,
                bundle,
                template_bytes,
                params_bytes,
                os.path.relpath(params_path, root),
                observations_doc,
            )
        except FleetTemplateError as exc:
            result.cannot_assess.append(str(exc))
            continue

        if params_doc.get("repo") != stem:
            result.findings.append(
                Finding(
                    "PILOT_NAME_MISMATCH",
                    f"{os.path.relpath(params_path, root)}: repo {params_doc.get('repo')!r} does not match pilot name {stem!r}",
                )
            )
        instances.append(fresh)
        result.findings += check_invariants(fresh)

        committed_path = os.path.join(root, PILOTS_DIR, f"{stem}.fleet.yaml")
        if not os.path.isfile(committed_path):
            result.findings.append(
                Finding("PILOT_INSTANCE_MISSING", f"{os.path.relpath(committed_path, root)}: no committed instance for pilot {stem!r}")
            )
            continue
        try:
            committed = load_yaml(committed_path)
            require_valid(committed, "instance", bundle)
        except SchemaViolation as exc:
            result.cannot_assess.append(str(exc))
            continue
        except FleetTemplateError as exc:
            result.cannot_assess.append(str(exc))
            continue
        # The SHIPPED artifact must also satisfy the invariants, not just the
        # fresh render: drift (below) says "this is not what the params render",
        # while this says "this artifact makes an untrue claim" -- e.g. listing an
        # unverified member as present. Both are findings; identical ones collapse.
        result.findings += check_invariants(committed)
        result.findings += check_drift(committed, fresh)

    result.findings += check_isolation(instances)

    # Findings that are identical in code and message are one finding: rendering
    # the same defect from the fresh instance and from the shipped artifact must
    # not read as two problems.
    deduped: List[Finding] = []
    seen: set = set()
    for finding in result.findings:
        key = (finding.code, finding.message)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)
    result.findings = deduped

    result.detail = {
        "pilots": [instance["isolation"]["fleet_id"] for instance in instances],
        "pilots_dir": os.path.relpath(os.path.join(root, PILOTS_DIR), root),
    }
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _emit(text: str) -> None:
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


def cmd_render(args: argparse.Namespace) -> int:
    root = os.path.abspath(args.root)
    bundle, _ = load_bundle(root)
    template_path = os.path.join(root, TEMPLATE_FILE)
    template_bytes = read_bytes(template_path)
    template = load_yaml(template_path)
    params_bytes = read_bytes(args.params)
    params_doc = load_yaml(args.params)
    observations_doc = None
    if getattr(args, "observations", None):
        observations_doc = load_yaml(args.observations)
    elif params_doc.get("observations"):
        observations_doc = load_yaml(os.path.join(os.path.dirname(args.params), params_doc["observations"]))
    instance = render_instance(
        template,
        params_doc,
        bundle,
        template_bytes,
        params_bytes,
        os.path.relpath(args.params, root),
        observations_doc,
    )
    text = dump_yaml(instance)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text)
        _emit(f"render: OK — wrote {args.out}")
    else:
        sys.stdout.write(text)
    return EXIT_OK


def cmd_report(args: argparse.Namespace) -> int:
    root = os.path.abspath(args.root)
    bundle, _ = load_bundle(root)
    template_path = os.path.join(root, TEMPLATE_FILE)
    template_bytes = read_bytes(template_path)
    template = load_yaml(template_path)
    params_bytes = read_bytes(args.params)
    params_doc = load_yaml(args.params)
    observations_doc = None
    if getattr(args, "observations", None):
        observations_doc = load_yaml(args.observations)
    elif params_doc.get("observations"):
        observations_doc = load_yaml(os.path.join(os.path.dirname(args.params), params_doc["observations"]))
    instance = render_instance(
        template,
        params_doc,
        bundle,
        template_bytes,
        params_bytes,
        os.path.relpath(args.params, root),
        observations_doc,
    )
    findings = check_invariants(instance)
    run_state = instance["run_state"]
    if args.json:
        _emit(json.dumps({"fleet_id": instance["isolation"]["fleet_id"], "run_state": run_state}, indent=2, sort_keys=True))
    else:
        tri = run_state["tri_state"]
        _emit(f"fleet: {instance['isolation']['fleet_id']} (repo {instance['definition']['repo']})")
        _emit(f"  observation source : {run_state['observations']['source']}")
        _emit(f"  present            : {len(tri['required'])}")
        for member in tri["required"]:
            _emit(f"      present      {member}")
        _emit(f"  parked             : {len(tri['parked'])}")
        for member in tri["parked"]:
            _emit(f"      parked       {member}")
        _emit(f"  unverified         : {len(tri['unverified'])} (NOT present)")
        for member in tri["unverified"]:
            _emit(f"      unverified   {member}")
    for finding in findings:
        print("  " + finding.render_line(), file=sys.stderr)
    return EXIT_NOT_OK if findings else EXIT_OK


def cmd_validate_instance(args: argparse.Namespace) -> int:
    root = os.path.abspath(args.root)
    bundle, _ = load_bundle(root)
    document = load_yaml(args.instance)
    require_valid(document, "instance", bundle)
    _emit(f"validate-instance: OK — {args.instance} satisfies {SCHEMA_FILE} (kind={document.get('kind')!r})")
    return EXIT_OK


def cmd_check(args: argparse.Namespace) -> int:
    result = check(os.path.abspath(args.root))
    if args.json:
        _emit(
            json.dumps(
                {
                    "status": result.status,
                    "exit_code": result.exit_code,
                    "findings": [{"code": f.code, "message": f.message} for f in result.findings],
                    "cannot_assess": list(result.cannot_assess),
                    "detail": result.detail,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        _emit(f"fleet-template: {result.detail.get('pilots_dir', PILOTS_DIR)} -> {len(result.detail.get('pilots', []))} pilot(s)")
        for line in result.lines():
            print(line, file=sys.stderr)
        _emit(f"fleet-template: {result.status} (exit {result.exit_code})")
    return result.exit_code


def build_parser() -> argparse.ArgumentParser:
    default_root = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(
        prog="render.py",
        description="Render a per-repo agent fleet from the shared template and check it for drift.",
    )
    parser.add_argument("--root", default=default_root, help="fleet-template directory (default: this file's directory)")
    sub = parser.add_subparsers(dest="command", required=True)

    render = sub.add_parser("render", help="render an instance from a params file")
    render.add_argument("--params", required=True)
    render.add_argument("--observations", default=None)
    render.add_argument("--out", default=None)
    render.set_defaults(func=cmd_render)

    report = sub.add_parser("report", help="report declared vs observed run state for a params file")
    report.add_argument("--params", required=True)
    report.add_argument("--observations", default=None)
    report.add_argument("--json", action="store_true")
    report.set_defaults(func=cmd_report)

    validate_instance = sub.add_parser("validate-instance", help="validate a rendered instance against the schema")
    validate_instance.add_argument("instance")
    validate_instance.set_defaults(func=cmd_validate_instance)

    check_cmd = sub.add_parser("check", help="check the template, every pilot and the isolation between them")
    check_cmd.add_argument("--json", action="store_true")
    check_cmd.set_defaults(func=cmd_check)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except FleetTemplateError as exc:
        print(f"render: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS


if __name__ == "__main__":
    raise SystemExit(main())
