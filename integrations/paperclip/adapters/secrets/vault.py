"""The per-agent secret vault projection, its refusals and its read guard (#417).

Upstream ``paperclip.ing`` ships a secrets resource family scoped per
company/agent. The fleet has a GR-6 mandate — environment or Secret Manager
only — but no first-class primitive for "this agent, this credential, this
rotation". This module is that primitive, and it is deliberately narrow:

* the primitive **names** a secret — its GSM path, the agent and scope it
  belongs to — and **never carries the value** (GR-6);
* **Secret Manager remains the store of record.** Upstream's secret store is
  *not* adopted as our authority: the adapter is a reference/rotation **view**
  over GSM, and :func:`validate_view` asserts that no second store is introduced
  (two stores means two answers to "what is the credential" — the half-coupling
  ADR-0012 forbids);
* **rotation is expressible**: a view records when a secret was last rotated and
  which agent consumes it, and a secret with **no consumer is reported**, not
  silently kept;
* a read requires an **authenticated, scoped caller** — an unscoped read is
  **refused** (:func:`read_secret`).

The projection is deterministic: the same declaration catalog yields a
byte-identical view. The module is stdlib-only and holds no credential.

``identity/`` (authentication + scope resolution) and ``guardrails/`` (DLP and
egress policy) remain the **enforcement points**; this lane adds the primitive
and the projection and reads neither.

Exit-code contract used by the CLI and the gate: 0 OK / 1 NOT-OK /
2 CANNOT-ASSESS.

---knowledge---
module_id: integrations.paperclip.adapters.secrets.vault
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: [validate, load_schema, load_catalog, iter_declarations, document_store, refs_from, build_view, value_findings, (+10 more)]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .model import (
    GSM_STORE,
    VALUE_KEYS,
    Caller,
    OrphanedSecretError,
    SecretRef,
    UnscopedReadError,
    UnknownSecretError,
    ValidationError,
    ValueNotPermittedError,
    ref_from_declaration,
)

#: The value-free declaration catalog (names, scopes, rotation — no values).
CATALOG_REL = Path("integrations/paperclip/adapters/secrets/catalog/secrets.json")

#: The frozen view schema (the reference/rotation record).
SCHEMA_REL = Path("integrations/paperclip/adapters/secrets/schema/secret.schema.json")


# ==========================================================================
# Schema subset validator (stdlib only)
# ==========================================================================

_DATE_TIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$"
)


def _type_ok(value: Any, expected: Any) -> bool:
    if isinstance(expected, list):
        return any(_type_ok(value, option) for option in expected)
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


def _deref(schema: Dict[str, Any], root: Dict[str, Any]) -> Dict[str, Any]:
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        node: Any = root
        for part in ref[2:].split("/"):
            node = node[part]
        if isinstance(node, dict):
            return node
    return schema


def _validate(inst: Any, schema: Dict[str, Any], root: Dict[str, Any],
              path: str, findings: List[str]) -> None:
    schema = _deref(schema, root)

    expected = schema.get("type")
    if expected is not None and not _type_ok(inst, expected):
        findings.append(f"{path}: expected {expected}, got {type(inst).__name__}")
        return

    if "const" in schema and inst != schema["const"]:
        findings.append(
            f"{path}: expected the constant {schema['const']!r}, got {inst!r}"
        )
    if "enum" in schema and inst not in schema["enum"]:
        findings.append(f"{path}: {inst!r} is outside the closed vocabulary {schema['enum']}")

    if isinstance(inst, str):
        if "minLength" in schema and len(inst) < schema["minLength"]:
            findings.append(f"{path}: length {len(inst)} is below minLength {schema['minLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], inst):
            findings.append(f"{path}: {inst!r} does not match pattern {schema['pattern']}")
        if schema.get("format") == "date-time" and not _DATE_TIME.match(inst):
            findings.append(f"{path}: {inst!r} is not an ISO-8601 date-time")

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
                _validate(inst[key], sub, root, f"{path}.{key}", findings)

    if isinstance(inst, list) and isinstance(schema.get("items"), dict):
        for index, element in enumerate(inst):
            _validate(element, schema["items"], root, f"{path}[{index}]", findings)


def validate(instance: Any, schema: Dict[str, Any], path: str = "$") -> List[str]:
    """Validate ``instance`` against the schema subset :func:`_validate` covers."""
    findings: List[str] = []
    _validate(instance, schema, schema, path, findings)
    return findings


def load_schema(root: Path) -> Dict[str, Any]:
    """Load the frozen reference/rotation view schema."""
    return json.loads((root / SCHEMA_REL).read_text(encoding="utf-8"))


# ==========================================================================
# Loaders and the deterministic projection
# ==========================================================================


def load_catalog(root: Path) -> Dict[str, Any]:
    """Load the value-free declaration catalog.

    A missing catalog is an empty document, not an exception: the caller
    (``validate_view`` / the gate) decides whether "nothing declared" is
    acceptable.
    """
    path = root / CATALOG_REL
    if not path.exists():
        return {"store": GSM_STORE, "secrets": []}
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        return {"store": GSM_STORE, "secrets": []}
    return document


def iter_declarations(document: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The raw declaration rows, in document order."""
    rows = document.get("secrets") or []
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def document_store(document: Dict[str, Any]) -> str:
    """The store the catalog document names, defaulting to the store of record."""
    store = document.get("store")
    return GSM_STORE if store is None else str(store)


def refs_from(document: Dict[str, Any]) -> List[SecretRef]:
    """Project the declarations onto :class:`SecretRef`, sorted by GSM path.

    Sorting by ``gsm_path`` is what makes the view deterministic: two runs over
    one revision produce byte-identical output.
    """
    refs = [ref_from_declaration(row) for row in iter_declarations(document)]
    return sorted(refs, key=lambda ref: ref.gsm_path)


def build_view(root: Path) -> Dict[str, Any]:
    """Build the reference/rotation view from the tree — the projection.

    **Fails closed.** A declaration that carries a value is refused here, by key
    name, before anything is projected: silently dropping a value-shaped field
    would be the same silent tolerance GR-6 exists to forbid, and would let a
    carried value ride along undetected behind a clean-looking view.
    """
    document = load_catalog(root)
    forbidden = value_findings_for_catalog(document)
    if forbidden:
        raise ValueNotPermittedError(forbidden[0])
    refs = refs_from(document)
    return {
        "store": document_store(document),
        "secrets": [ref.to_view() for ref in refs],
    }


# ==========================================================================
# Findings — every message names a rule and a location, never a value
# ==========================================================================


def value_findings(record: Dict[str, Any], location: str) -> List[str]:
    """Refuse a record that carries a value instead of naming a secret.

    The finding names the forbidden **key** and the location; the value is never
    read, echoed, or included.
    """
    findings: List[str] = []
    for key in record:
        if key in VALUE_KEYS:
            findings.append(
                f"{location}: forbidden key '{key}' — a secret is named by its GSM path, "
                "never carried (GR-6); the value is not read"
            )
    return findings


def value_findings_for_catalog(document: Dict[str, Any]) -> List[str]:
    """Refuse any declaration row that carries a value (the catalog path)."""
    findings: List[str] = []
    for index, row in enumerate(iter_declarations(document)):
        findings.extend(value_findings(row, f"catalog.secrets[{index}]"))
    return findings


def store_findings(store: str, location: str) -> List[str]:
    """Refuse any store that is not the store of record — a second store."""
    if store == GSM_STORE:
        return []
    return [
        f"{location}: store {store!r} is not the store of record '{GSM_STORE}' — "
        "a second secret store is refused"
    ]


def catalog_findings(document: Dict[str, Any]) -> List[str]:
    """The declaration-document findings: one store, and no value carried.

    A declaration is refused **at the source** as well as through its projection,
    so a carried value cannot hide behind a projection that drops unknown keys.
    """
    findings = store_findings(document_store(document), "catalog")
    findings.extend(value_findings_for_catalog(document))
    return findings


def orphan_findings(document: Dict[str, Any]) -> List[str]:
    """Report every secret that no consumer claims.

    A consumer-less secret is **reported**, never silently kept.
    """
    findings: List[str] = []
    for index, row in enumerate(iter_declarations(document)):
        ref = ref_from_declaration(row)
        if ref.orphaned:
            findings.append(
                f"catalog.secrets[{index}] ({ref.gsm_path}): no consumer — "
                "an orphaned secret is reported, not silently kept"
            )
    return findings


def rotation_report(document: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The rotation view: when each secret was last rotated and who consumes it.

    ``rotated`` is false when no rotation time is recorded — a never-rotated
    secret is visible as such rather than implied to be fresh.
    """
    report: List[Dict[str, Any]] = []
    for ref in refs_from(document):
        report.append(
            {
                "gsm_path": ref.gsm_path,
                "agent": ref.agent,
                "scope": ref.scope,
                "consumer": ref.consumer,
                "last_rotated_at": ref.last_rotated_at,
                "rotated": ref.rotated,
            }
        )
    return report


def orphans(document: Dict[str, Any]) -> List[str]:
    """The GSM paths of every orphaned secret, sorted."""
    return sorted(ref.gsm_path for ref in refs_from(document) if ref.orphaned)


# ==========================================================================
# Validation of a view document
# ==========================================================================


def validate_view(view: Any, schema: Optional[Dict[str, Any]] = None,
                  root: Optional[Path] = None) -> List[str]:
    """Validate one view document; return findings (empty means conforming).

    Four rules are enforced, each refusable by name:

    1. **no value** — a record carrying a value-bearing key is refused, naming
       the key (GR-6);
    2. **one store** — the document and every record must name the store of
       record; any other store is refused, naming it (no second store);
    3. **conformance** — the schema's required fields, the GSM path shape, the
       closed store constant and the closed record key set all hold;
    4. **no orphan** — a record with no consumer is reported, naming its path.
    """
    findings: List[str] = []

    if not isinstance(view, dict):
        return [f"view: expected an object, got {type(view).__name__}"]

    if schema is None:
        if root is None:
            raise ValidationError(
                "validate_view needs a schema or a root to load one from"
            )
        schema = load_schema(root)

    findings.extend(store_findings(str(view.get("store", "")), "view"))

    secrets = view.get("secrets")
    if isinstance(secrets, list):
        for index, record in enumerate(secrets):
            if not isinstance(record, dict):
                continue
            location = str(record.get("gsm_path") or f"view.secrets[{index}]")
            findings.extend(value_findings(record, f"view.secrets[{index}] ({location})"))
            findings.extend(
                store_findings(str(record.get("store", "")), f"view.secrets[{index}] ({location})")
            )
    else:
        findings.extend(value_findings(view, "view"))

    findings.extend(validate(view, schema))
    findings.extend(orphan_findings(view))
    return findings


# ==========================================================================
# The read guard
# ==========================================================================


def _find_record(view: Dict[str, Any], gsm_path: str) -> Optional[Dict[str, Any]]:
    for record in view.get("secrets") or []:
        if isinstance(record, dict) and record.get("gsm_path") == gsm_path:
            return record
    return None


def read_secret(view: Dict[str, Any], gsm_path: str, caller: Caller) -> Dict[str, Any]:
    """Read one secret **by reference** for an authenticated, scoped caller.

    The return value is the reference/rotation *view record* — the name, the
    scope, the rotation state. It is never the credential: the primitive does not
    hold values, and the value would be fetched from GSM by the caller under its
    own IAM, never by this adapter.

    Refuses, in order:

    * an **unauthenticated** caller (:class:`UnscopedReadError`);
    * a caller not **scoped** for the secret's scope (:class:`UnscopedReadError`)
      — an unscoped read is refused;
    * an **orphaned** secret, which no consumer claims
      (:class:`OrphanedSecretError`);
    * a **unknown** GSM path (:class:`UnknownSecretError`).
    """
    record = _find_record(view, gsm_path)
    if record is None:
        raise UnknownSecretError(
            f"read refused: no secret named '{gsm_path}' is declared in the view"
        )

    if not caller.authenticated:
        raise UnscopedReadError(
            "read refused: the caller is not authenticated — a read requires an "
            "authenticated, scoped caller"
        )

    scope = str(record.get("scope") or "")
    if not caller.may_read(scope):
        raise UnscopedReadError(
            f"read refused: caller '{caller.principal}' lacks scope '{scope}' — "
            "an unscoped read is refused"
        )

    consumer = record.get("consumer")
    if not (consumer or "").strip():
        raise OrphanedSecretError(
            f"read refused: '{gsm_path}' has no consumer — an orphaned secret is "
            "reported, not served"
        )

    return dict(record)


def read_secret_from_root(root: Path, gsm_path: str, caller: Caller) -> Dict[str, Any]:
    """Build the view from ``root`` and read one secret by reference."""
    return read_secret(build_view(root), gsm_path, caller)


def declared_scopes(document: Dict[str, Any]) -> Tuple[str, ...]:
    """Every distinct scope the catalog declares, sorted — the read-grant set."""
    return tuple(sorted({ref.scope for ref in refs_from(document) if ref.scope}))
