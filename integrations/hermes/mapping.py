"""The deterministic mapper: repo hermes surface -> one read-only projection (issue #942).

The mapper is a pure function of the tree it is given. It reads hermes's
declared surface — ``registry/personas/cards/hermes.yaml``,
``registry/profiles/seeds/hermes.1.0.0.yaml``, ``gateway/finops/tiers.yaml``
and ``gateway/catalog/modules/hermes/module.json`` — and emits one canonical
document that validates against the inline contract (``CONTRACT``). Same input,
byte-identical output (the gate asserts sha256 equality across two builds).

Two stdlib-only tools live here because the adapter may not take a third-party
dependency (the same constraint ``integrations/paperclip/`` carries):

* a small **YAML subset loader** (``load_yaml``) covering mappings, sequences,
  scalars, quoted strings and block scalars — enough for the fleet's own YAML;
* a small **JSON-Schema subset validator** (``validate``) covering the keywords
  the inline contract uses — ``type``, ``required``, ``properties``,
  ``additionalProperties``, ``enum`` and ``items``.

The binding is stated once, here, so the projection, the CLI and the gate
cannot drift apart: this adapter binds the **hermes-agents routing service**
(Flask on port 9501) and names the gateway's Ollama namesake
(``gateway/providers/hermes.py``) as explicitly excluded (ADR-0012).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import audit as audit_mod
from . import policy as policy_mod

#: The frozen source files the projection reads (hermes's declared surface).
PERSONA_PATH = "registry/personas/cards/hermes.yaml"
PROFILE_PATH = "registry/profiles/seeds/hermes.1.0.0.yaml"
TIERS_PATH = "gateway/finops/tiers.yaml"
MODULE_JSON_PATH = "gateway/catalog/modules/hermes/module.json"
CAPABILITIES_SCHEMA_PATH = "integrations/hermes/capabilities.schema.json"

#: The binding (ADR-0012): the routing service, not the gateway namesake.
BOUND = "hermes-agents routing service"
NAMESAKE_PROVIDER = "gateway/providers/hermes.py"

#: The service contract (ADR-0012 Context 7): Flask on 9501, not wired here.
SERVICE_PORT = 9501
SERVICE_ENDPOINTS = ("/health", "/api/capabilities", "/api/router", "/api/tiering")
SERVICE_RUNTIME = "deployable-not-running"

#: The closed model-tier vocabulary (registry/profiles/catalog.yaml).
MODEL_TIERS = ("LOW", "MED", "HIGH", "MAX")


# ==========================================================================
# YAML subset loader (stdlib only)
# ==========================================================================


def _strip_comment(text: str) -> str:
    out: List[str] = []
    quote = ""
    for ch in text:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = ""
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out)


def _tokenize(text: str) -> List[Tuple[int, str]]:
    """Flatten YAML into ``(indent, content)`` items, dropping comments/blanks.

    A block-scalar header (``key: |`` / ``key: >``) is kept as an empty scalar
    and its body is skipped, so free text inside a description can never be
    mis-read as structure.
    """
    lines = text.split("\n")
    tokens: List[Tuple[int, str]] = []
    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.lstrip(" ")
        if not stripped.strip() or stripped.startswith("#"):
            i += 1
            continue
        indent = len(raw) - len(stripped)
        content = _strip_comment(stripped).rstrip()
        if content.endswith((": |", ": |-", ": >", ": >-")):
            key = content.split(":", 1)[0].strip()
            tokens.append((indent, '%s: ""' % key))
            i += 1
            while i < len(lines):
                nxt = lines[i]
                nstripped = nxt.lstrip(" ")
                if not nstripped.strip():
                    i += 1
                    continue
                if (len(nxt) - len(nstripped)) <= indent:
                    break
                i += 1
            continue
        tokens.append((indent, content))
        i += 1
    return tokens


def _split_kv(content: str) -> Optional[Tuple[str, str]]:
    if content.startswith(("'", '"')):
        return None
    for idx, ch in enumerate(content):
        if ch != ":":
            continue
        if idx + 1 < len(content) and content[idx + 1] != " ":
            return None
        key = content[:idx].strip()
        if not key or " " in key:
            return None
        return key, content[idx + 1:].strip()
    return None


def _split_flow(inner: str) -> List[str]:
    """Split a flow body on top-level commas, quote- and depth-aware."""
    parts: List[str] = []
    depth = 0
    quote = ""
    cur: List[str] = []
    for ch in inner:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = ""
        elif ch in ("'", '"'):
            quote = ch
            cur.append(ch)
        elif ch in "[{":
            depth += 1
            cur.append(ch)
        elif ch in "]}":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur))
    return parts


def _parse_flow(text: str) -> Any:
    """Parse one flow mapping / sequence (``{ k: v }``, ``[ a, b ]``).

    ``gateway/finops/tiers.yaml`` writes its ``taskClasses`` values as inline
    flow mappings (``code-author: { capability: code-author, defaultTier: L0,
    maxTier: L1 }``), which the block parser above does not cover; this reads
    them as real mappings so the tier projection can be deterministic.
    """
    inner = text[1:-1].strip()
    if text.startswith("{"):
        if not inner:
            return {}
        out: Dict[str, Any] = {}
        for part in _split_flow(inner):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                key, value = part.split(":", 1)
                out[key.strip()] = _scalar(value.strip())
            else:
                out[part] = None
        return out
    if not inner:
        return []
    return [_scalar(part.strip()) for part in _split_flow(inner) if part.strip()]


def _scalar(text: str) -> Any:
    text = text.strip()
    if text == "" or text in ("null", "~"):
        return None
    if text in ("true", "True"):
        return True
    if text in ("false", "False"):
        return False
    if len(text) >= 2 and text[0] in "{[" and text[-1] in "}]":
        return _parse_flow(text)
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _parse_map(tokens: List[Tuple[int, str]], idx: int, indent: int) -> Tuple[Dict[str, Any], int]:
    mapping: Dict[str, Any] = {}
    while idx < len(tokens):
        ind, content = tokens[idx]
        if ind < indent:
            break
        if ind > indent:
            raise ValueError("unexpected indent %d (want %d): %r" % (ind, indent, content))
        if content.startswith("- "):
            break
        kv = _split_kv(content)
        if kv is None:
            raise ValueError("not a mapping entry: %r" % content)
        key, value = kv
        idx += 1
        if value == "":
            if idx < len(tokens) and tokens[idx][0] > indent:
                nested, idx = _parse_block(tokens, idx, tokens[idx][0])
                mapping[key] = nested
            else:
                mapping[key] = None
        else:
            mapping[key] = _scalar(value)
    return mapping, idx


def _parse_seq(tokens: List[Tuple[int, str]], idx: int, indent: int) -> Tuple[List[Any], int]:
    seq: List[Any] = []
    while idx < len(tokens):
        ind, content = tokens[idx]
        if ind < indent:
            break
        if ind > indent:
            raise ValueError("unexpected indent %d (want %d): %r" % (ind, indent, content))
        if not content.startswith("- "):
            break
        rest = content[2:].strip()
        idx += 1
        if rest == "":
            if idx < len(tokens) and tokens[idx][0] > indent:
                nested, idx = _parse_block(tokens, idx, tokens[idx][0])
                seq.append(nested)
            else:
                seq.append(None)
            continue
        kv = _split_kv(rest)
        if kv is None:
            seq.append(_scalar(rest))
            continue
        sub_indent = indent + 2
        entry: Dict[str, Any] = {}
        key, value = kv
        if value == "" and idx < len(tokens) and tokens[idx][0] > sub_indent:
            nested, idx = _parse_block(tokens, idx, tokens[idx][0])
            entry[key] = nested
        else:
            entry[key] = _scalar(value) if value != "" else None
        while idx < len(tokens):
            ind2, content2 = tokens[idx]
            if ind2 < sub_indent or content2.startswith("- "):
                break
            if ind2 > sub_indent:
                raise ValueError("unexpected indent %d in mapping: %r" % (ind2, content2))
            kv2 = _split_kv(content2)
            if kv2 is None:
                break
            key2, value2 = kv2
            idx += 1
            if value2 == "" and idx < len(tokens) and tokens[idx][0] > sub_indent:
                nested2, idx = _parse_block(tokens, idx, tokens[idx][0])
                entry[key2] = nested2
            else:
                entry[key2] = _scalar(value2) if value2 != "" else None
        seq.append(entry)
    return seq, idx


def _parse_block(tokens: List[Tuple[int, str]], idx: int, indent: int) -> Tuple[Any, int]:
    if tokens[idx][1].startswith("- "):
        return _parse_seq(tokens, idx, indent)
    return _parse_map(tokens, idx, indent)


def load_yaml(text: str) -> Any:
    """Load the YAML subset the fleet's own files use."""
    tokens = _tokenize(text)
    if not tokens:
        return None
    return _parse_block(tokens, 0, tokens[0][0])[0]


def load_yaml_file(path: Path) -> Any:
    return load_yaml(path.read_text(encoding="utf-8"))


# ==========================================================================
# JSON-Schema subset validator (stdlib only)
# ==========================================================================


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _validate(inst: Any, schema: Dict[str, Any], path: str, findings: List[str]) -> None:
    expected = schema.get("type")
    if expected is not None and not _type_ok(inst, expected):
        findings.append("%s: expected %s, got %s" % (path, expected, type(inst).__name__))
        return
    if "enum" in schema and inst not in schema["enum"]:
        findings.append("%s: value %r is outside the closed vocabulary %s" % (path, inst, schema["enum"]))
    if isinstance(inst, str):
        if "minLength" in schema and len(inst) < schema["minLength"]:
            findings.append("%s: length %d is below minLength %d" % (path, len(inst), schema["minLength"]))
        if "pattern" in schema and not re.search(schema["pattern"], inst):
            findings.append("%s: %r does not match pattern %s" % (path, inst, schema["pattern"]))
    if isinstance(inst, dict):
        for key in schema.get("required", []):
            if key not in inst:
                findings.append("%s: required field '%s' is missing" % (path, key))
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in inst:
                if key not in properties:
                    findings.append("%s: unexpected field '%s' (additionalProperties: false)" % (path, key))
        for key, sub in properties.items():
            if key in inst:
                _validate(inst[key], sub, "%s.%s" % (path, key), findings)
    if isinstance(inst, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for i, element in enumerate(inst):
                _validate(element, items, "%s[%d]" % (path, i), findings)


def validate(instance: Any, schema: Dict[str, Any], path: str = "$") -> List[str]:
    """Validate ``instance`` against a schema built from the supported subset."""
    findings: List[str] = []
    _validate(instance, schema, path, findings)
    return findings


# ==========================================================================
# The inline frozen contract (the closed vocabulary the check validates)
# ==========================================================================

CONTRACT: Dict[str, Any] = {
    "type": "object",
    "required": ["hermes", "persona", "profile", "tiering", "module_binding"],
    "additionalProperties": False,
    "properties": {
        "hermes": {
            "type": "object",
            "required": ["bound", "adr", "mode", "service", "namesake_excluded"],
            "additionalProperties": False,
            "properties": {
                "bound": {"type": "string"},
                "adr": {"type": "string"},
                "mode": {"type": "string"},
                "service": {
                    "type": "object",
                    "required": ["port", "endpoints", "runtime"],
                    "additionalProperties": False,
                    "properties": {
                        "port": {"type": "integer"},
                        "endpoints": {"type": "array", "items": {"type": "string"}},
                        "runtime": {"type": "string"},
                    },
                },
                "namesake_excluded": {
                    "type": "object",
                    "required": ["provider", "wire", "model", "endpoint", "reason"],
                    "additionalProperties": False,
                    "properties": {
                        "provider": {"type": "string"},
                        "wire": {"type": "string"},
                        "model": {"type": "string"},
                        "endpoint": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
            },
        },
        "persona": {
            "type": "object",
            "required": ["id", "name", "posture", "tier", "lanes", "capabilities"],
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "posture": {"type": "string"},
                "tier": {"type": "string", "enum": list(MODEL_TIERS)},
                "lanes": {"type": "array", "items": {"type": "string"}},
                "capabilities": {"type": "array", "items": {"type": "string"}},
            },
        },
        "profile": {
            "type": "object",
            "required": ["id", "version", "owner", "default_model_tier", "capabilities"],
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string"},
                "version": {"type": "string"},
                "owner": {"type": "string"},
                "default_model_tier": {"type": "string", "enum": list(MODEL_TIERS)},
                "capabilities": {"type": "array", "items": {"type": "string"}},
                "tools": {"type": "array", "items": {"type": "string"}},
                "constraints": {"type": "array", "items": {"type": "string"}},
            },
        },
        "tiering": {
            "type": "object",
            "required": ["capabilities", "floor_tier", "escalation_thresholds"],
            "additionalProperties": False,
            "properties": {
                "capabilities": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "required": ["default_tier", "max_tier"],
                        "additionalProperties": False,
                        "properties": {
                            "default_tier": {"type": "string", "enum": ["L0", "L1", "L2"]},
                            "max_tier": {"type": "string", "enum": ["L0", "L1", "L2"]},
                        },
                    },
                },
                "floor_tier": {"type": "string"},
                "escalation_thresholds": {"type": "object"},
            },
        },
        "module_binding": {
            "type": "object",
            "required": ["catalog", "type", "wire_shape", "role"],
            "additionalProperties": False,
            "properties": {
                "catalog": {"type": "string"},
                "type": {"type": "string"},
                "wire_shape": {"type": "string"},
                "role": {"type": "string"},
            },
        },
    },
}


# ==========================================================================
# Source readers
# ==========================================================================


def read_persona(root: Path) -> Dict[str, Any]:
    return load_yaml_file(root / PERSONA_PATH) or {}


def read_profile(root: Path) -> Dict[str, Any]:
    return load_yaml_file(root / PROFILE_PATH) or {}


def read_tiers(root: Path) -> Dict[str, Any]:
    return load_yaml_file(root / TIERS_PATH) or {}


def read_module_binding(root: Path) -> Dict[str, Any]:
    path = root / MODULE_JSON_PATH
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_capabilities_schema(root: Path) -> Dict[str, Any]:
    """Load the surface's capability-registry schema (the `schema` evidence)."""
    path = root / CAPABILITIES_SCHEMA_PATH
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


# ==========================================================================
# The projection
# ==========================================================================


def build_projection(root: Path) -> Dict[str, Any]:
    """Build the canonical read-only projection (offline, deterministic)."""
    persona = read_persona(root)
    profile = read_profile(root)
    tiers = read_tiers(root)
    module = read_module_binding(root)

    persona_caps = tuple(sorted(str(c) for c in (persona.get("capabilitySet") or ())))
    profile_caps = tuple(sorted(str(c) for c in (profile.get("capabilitySet") or ())))

    hermes_policy = policy_mod.build_policy(tiers, TIERS_PATH)

    return {
        "hermes": {
            "bound": BOUND,
            "adr": "ADR-0012",
            "mode": "read-only-projection",
            "service": {
                "port": SERVICE_PORT,
                "endpoints": list(SERVICE_ENDPOINTS),
                "runtime": SERVICE_RUNTIME,
            },
            "namesake_excluded": {
                "provider": NAMESAKE_PROVIDER,
                "wire": "Ollama /api/chat",
                "model": "hermes3",
                "endpoint": "http://localhost:8080/api/chat",
                "reason": (
                    "inference-only Ollama endpoint for the Hermes-3 LLM, not the "
                    "routing service (ADR-0012 Context 5 and 7)"
                ),
            },
        },
        "persona": {
            "id": str(persona.get("id") or "hermes"),
            "name": str(persona.get("name") or "Hermes"),
            "posture": str(persona.get("posture") or "executor"),
            "tier": str(persona.get("defaultModelTier") or ""),
            "lanes": list(persona.get("ownedLanes") or ()),
            "capabilities": list(persona_caps),
        },
        "profile": {
            "id": str(profile.get("id") or "hermes"),
            "version": str(profile.get("version") or ""),
            "owner": str(profile.get("owner") or ""),
            "default_model_tier": str(profile.get("defaultModelTier") or ""),
            "capabilities": list(profile_caps),
            "tools": list(profile.get("toolAllowlist") or ()),
            "constraints": list(profile.get("constraintSet") or ()),
        },
        "tiering": {
            "capabilities": policy_mod.tier_projection(hermes_policy, persona_caps),
            "floor_tier": hermes_policy.floor_tier,
            "escalation_thresholds": dict(hermes_policy.escalation_thresholds),
        },
        "module_binding": {
            "catalog": MODULE_JSON_PATH,
            "type": str(module.get("type") or ""),
            "wire_shape": "Ollama-compatible /api/chat",
            "role": "namesake inference endpoint (excluded from the routing contract)",
        },
    }


def canonical_document(projection: Dict[str, Any]) -> str:
    """The byte-stable serialization the gate hashes."""
    return json.dumps(projection, indent=2, sort_keys=True)


def canonical_sha(projection: Dict[str, Any]) -> str:
    """sha256 of the canonical document (same tree, same digest)."""
    return hashlib.sha256(canonical_document(projection).encode("utf-8")).hexdigest()


# ==========================================================================
# Conformance
# ==========================================================================


def check_projection(root: Path) -> Tuple[List[str], Dict[str, Any]]:
    """Validate the projection; return (findings, projection).

    The findings are closed and each names the drifted field:

    * schema conformance (``CONTRACT``, plus the per-entry ``capabilities.schema.json``);
    * the cross-source consistency audit (``audit.py``): persona/profile
      capability-set parity, persona/profile tier parity, tiering-floor presence,
      and tier coverage (every persona capability must have a FinOps tier
      mapping).
    """
    projection = build_projection(root)
    findings = validate(projection, CONTRACT, "$")
    findings.extend(_check_capabilities_schema(root, projection))

    entries = audit_mod.audit_projection(projection)
    findings.extend(audit_mod.violation_findings(entries))
    return findings, projection


def _check_capabilities_schema(
    root: Path, projection: Dict[str, Any]
) -> List[str]:
    """Validate every projected capability entry against the surface schema."""
    schema = load_capabilities_schema(root)
    capabilities = projection["tiering"]["capabilities"]
    if not schema:
        return ["capabilities.schema.json is missing from integrations/hermes"]
    findings: List[str] = []
    for cap, entry in capabilities.items():
        findings.extend(validate(entry, schema, "tiering.capabilities.%s" % cap))
    return findings
