"""The stdlib-only JSON-Schema subset validator both adapters share (#1208).

The keywords below are the ones the fleet's frozen contracts actually use —
``type``, ``required``, ``properties``, ``additionalProperties``, ``enum``,
``items``, ``minLength``, ``minimum``, ``maximum``, ``pattern`` and
``format: date-time`` — and this is the **superset**: the union of the two
copies it replaces, which is to say paperclip's set. Hermes's copy had drifted
below it (it enforced only ``type``/``enum``/``minLength``/``pattern``), so the
drift is closed by moving to the stricter side, never by relaxing the stricter
adapter: a schema that asks for ``format: date-time``, ``minimum`` or
``maximum`` is now enforced by both adapters.

A keyword outside this set is silently ignored, which is how a ``$ref`` or a
``oneOf`` turns a schema file into a decoration. Callers that care
(``integrations/paperclip/reporting/brief_schema.py``) walk their own schema for
keywords outside the set and refuse one rather than trusting the validator to
notice it.

---knowledge---
module_id: integrations._seam.schema
system: integrations
app: seam
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [validate]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

#: The ISO-8601 date-time shape ``"format": "date-time"`` enforces.
_DATE_TIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$"
)


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
        findings.append(f"{path}: expected {expected}, got {type(inst).__name__}")
        return
    if "enum" in schema and inst not in schema["enum"]:
        findings.append(f"{path}: value {inst!r} is outside the closed vocabulary {schema['enum']}")
    if isinstance(inst, str):
        if "minLength" in schema and len(inst) < schema["minLength"]:
            findings.append(f"{path}: length {len(inst)} is below minLength {schema['minLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], inst):
            findings.append(f"{path}: {inst!r} does not match pattern {schema['pattern']}")
        if schema.get("format") == "date-time" and not _DATE_TIME.match(inst):
            findings.append(f"{path}: {inst!r} is not an ISO-8601 date-time")
    if isinstance(inst, (int, float)) and not isinstance(inst, bool):
        if "minimum" in schema and inst < schema["minimum"]:
            findings.append(f"{path}: {inst} is below minimum {schema['minimum']}")
        if "maximum" in schema and inst > schema["maximum"]:
            findings.append(f"{path}: {inst} is above maximum {schema['maximum']}")
    if isinstance(inst, dict):
        for key in schema.get("required", []):
            if key not in inst:
                findings.append(f"{path}: required field '{key}' is missing")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in inst:
                if key not in properties:
                    findings.append(f"{path}: unexpected field '{key}' (additionalProperties: false)")
        for key, sub in properties.items():
            if key in inst:
                _validate(inst[key], sub, f"{path}.{key}", findings)
    if isinstance(inst, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for i, element in enumerate(inst):
                _validate(element, items, f"{path}[{i}]", findings)


def validate(instance: Any, schema: Dict[str, Any], path: str = "$") -> List[str]:
    """Validate ``instance`` against a schema built from the supported subset."""
    findings: List[str] = []
    _validate(instance, schema, path, findings)
    return findings
