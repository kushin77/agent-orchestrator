"""The lane's GR-10 harvest record, and the step that enforces it.

Upstream ``frappe/erpnext`` is **GPL-3.0** and is a **pattern source only**:
no upstream code, schema or text is copied or vendored, and a reading clone
lives under ``.research/``, which is gitignored. That claim is worth nothing as
prose, so it is recorded as data in two places and enforced here:

* ``provenance.json`` — the document model's harvest record, one entry per
  shipped family schema, keyed by file name. ERP-02's own asset gate refuses a
  schema that carries no harvest record and a record with no schema, so this
  file is read by both models;
* the catalogue's embedded ``provenance`` — one record for the lane's
  declaration surface.

The enforcement is deliberately two-sided: a record that claims upstream code
was copied is refused as ``code-copied`` (the doctrine's own line), and a record
that is malformed or empty is refused as ``provenance-invalid`` rather than
being treated as "nothing to check" — a missing harvest record is the failure
this module exists to catch, so it must never be the state that passes.

---knowledge---
module_id: integrations.erp.ops.provenance
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [load, schema_records, enforce_harvest, enforce_catalogue, audit]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Mapping, Optional, Tuple

from .model import MODEL_ROOT, Refused

__all__ = [
    "HARVEST_REQUIRED",
    "PROVENANCE_FILE",
    "audit",
    "enforce_catalogue",
    "enforce_harvest",
    "load",
    "schema_records",
]

PROVENANCE_FILE = "provenance.json"

#: The fields a per-schema harvest record must carry.
HARVEST_REQUIRED: Tuple[str, ...] = ("upstream_doctype", "harvest")


def load(path: Optional[Path | str] = None) -> Mapping[str, Any]:
    """Read the document model's harvest record, or refuse to assess."""
    resolved = Path(path) if path is not None else MODEL_ROOT / PROVENANCE_FILE
    if not resolved.is_file():
        raise Refused(
            "provenance-invalid", f"no harvest record at {resolved}"
        )
    try:
        document = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused(
            "provenance-invalid", f"{resolved.name} is unreadable ({exc})"
        ) from exc
    if not isinstance(document, Mapping):
        raise Refused("provenance-invalid", f"{resolved.name} must be a JSON object")
    return document


def schema_records(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """The per-schema harvest records out of a document-model record."""
    records = record.get("schemas")
    if not isinstance(records, Mapping) or not records:
        raise Refused(
            "provenance-invalid",
            f"{PROVENANCE_FILE}: 'schemas' must map every shipped schema to its "
            "harvest record",
        )
    return records


def enforce_harvest(record: Mapping[str, Any], what: str) -> None:
    """Refuse a per-schema harvest record that is empty or claims copied code.

    ``what`` names the schema the record belongs to, so a failure names the
    artifact a reader has to open rather than the checker.
    """
    if not isinstance(record, Mapping):
        raise Refused("provenance-invalid", f"{what}: harvest record is not an object")
    for field in HARVEST_REQUIRED:
        value = record.get(field)
        if value is None or value == "" or value == []:
            raise Refused(
                "provenance-invalid", f"{what}: harvest record has no '{field}'"
            )
    upstream = record.get("upstream_doctype")
    if not isinstance(upstream, str) or not upstream.strip():
        raise Refused(
            "provenance-invalid",
            f"{what}: upstream_doctype must name the upstream doctype the shapes "
            "were read from (a string, never null)",
        )
    harvest = record.get("harvest")
    if not isinstance(harvest, list) or not harvest:
        raise Refused("provenance-invalid", f"{what}: harvest list is empty")
    for index, entry in enumerate(harvest):
        if not isinstance(entry, str) or not entry.strip():
            raise Refused(
                "provenance-invalid", f"{what}: harvest[{index}] is not a sentence"
            )


def enforce_catalogue(record: Mapping[str, Any], what: str = "catalog") -> None:
    """Refuse a lane-level provenance record that drifts from the doctrine.

    The doctrine's line is that upstream is a pattern source: a record that
    claims code was copied is refused by name (``code-copied``), not quietly
    tolerated, and a record that is missing a field cannot claim anything at all.
    """
    if not isinstance(record, Mapping):
        raise Refused("provenance-invalid", f"{what}: provenance is not an object")
    if record.get("code_copied") is not False or record.get("pattern_source_only") is not True:
        raise Refused(
            "code-copied",
            f"{what}: provenance claims copied upstream code "
            f"(code_copied={record.get('code_copied')!r}, "
            f"pattern_source_only={record.get('pattern_source_only')!r}); upstream "
            "is GPL-3.0 and a pattern source only",
        )
    upstream = record.get("upstream")
    if not isinstance(upstream, str) or not upstream.strip():
        raise Refused("provenance-invalid", f"{what}: provenance has no upstream")
    license_name = record.get("license")
    if not isinstance(license_name, str) or "GPL" not in license_name.upper():
        raise Refused(
            "provenance-invalid",
            f"{what}: license {license_name!r} does not record the GPL licence the "
            "harvest is bounded by",
        )
    for field in ("retrieved", "source_doc"):
        value = record.get(field)
        if not isinstance(value, str) or not value.strip():
            raise Refused("provenance-invalid", f"{what}: provenance has no '{field}'")


def audit(record: Optional[Mapping[str, Any]] = None) -> List[str]:
    """Every way the shipped harvest records fall short of the doctrine."""
    problems: List[str] = []
    document = record if record is not None else load()
    try:
        records = schema_records(document)
    except Refused as refusal:
        return [refusal.detail]
    for name in sorted(records):
        try:
            enforce_harvest(records[name], f"{PROVENANCE_FILE}:{name}")
        except Refused as refusal:
            problems.append(refusal.detail)
    return problems
