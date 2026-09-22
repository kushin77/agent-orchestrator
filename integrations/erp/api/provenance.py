"""The GR-10 harvest record for this lane — and the step that enforces it (ERP-06).

The issue that built this surface names what it cannibalizes
(``integrations/paperclip/api/``, ``control-plane/sdk/``), and GR-10 says a
harvested asset records its source. So the lane carries one record,
``catalog/provenance.json``, and this module refuses a record that has stopped
being true in either direction:

* a harvest whose ``mode`` is anything but ``pattern-only`` or ``consumed`` —
  ``copied``, ``vendored``, or a word nobody has considered yet — is refused with
  ``harvest-code-copied``. The refusal is the *point*: if a later change starts
  copying a file instead of reading its shape, the gate says so by name rather
  than the provenance record quietly becoming a fiction.
* a harvest that names no shape, no source or no replacement is refused with
  ``harvest-incomplete`` — a record that cannot say what was taken and what was
  built instead documents nothing.

Refusals use ``integrations/erp/auth``'s closed vocabulary rather than inventing
a second one: the module already has the words for a bad harvest and this lane has
no business adding more.

**The upstream domain pattern is not restated here.** ``frappe/erpnext``
(GPL-3.0) is the ERP *domain* pattern source, declared once in
``integrations/erp/module.yaml`` and ``integrations/erp/core/provenance.json``.
:func:`check` requires this record to *point at* that declaration rather than
repeat its licence — one declaration, no second copy to drift, which is the same
rule the module manifest's gate applies to the catalogue.

---knowledge---
module_id: integrations.erp.api.provenance
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [load, check]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from integrations.erp.auth.model import Refused

__all__ = ["CATALOG", "POLICY_KEY", "RECORD_PATH", "SCHEMA", "check", "load"]

#: The record's own schema marker.
SCHEMA = "erp.api.provenance/v1"
#: The record, relative to this package.
RECORD_PATH = Path("catalog") / "provenance.json"
#: The directory this module reads the record from.
CATALOG = Path(__file__).resolve().parent / "catalog"
#: The key the domain-level declaration is pointed at.
POLICY_KEY = "upstreamDomainSource"

#: The only harvest modes this lane permits. ``pattern-only`` means a shape was
#: read and re-expressed; ``consumed`` means the module is *imported* and its
#: names used, with no file copied at all. Everything else — and "everything
#: else" is the point — is a copy.
MODES: Tuple[str, ...] = ("pattern-only", "consumed")

#: What every harvest must declare.
_REQUIRED_HARVEST_KEYS: Tuple[str, ...] = ("shape", "mode", "source", "builtInstead")
_REQUIRED_SOURCE_KEYS: Tuple[str, ...] = ("repo", "path", "license")


def _as_mapping(source: Any) -> Mapping[str, Any]:
    if isinstance(source, Mapping):
        return source
    path = Path(source) if source is not None else CATALOG / RECORD_PATH.name
    if not path.is_file():
        raise Refused("declaration-invalid", f"the provenance record is absent at {path}")
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused("declaration-invalid", f"{path} is unreadable ({exc})") from exc
    if not isinstance(loaded, Mapping):
        raise Refused("declaration-invalid", f"{path}: the top level is not an object")
    return loaded


def load(source: Any = None) -> Dict[str, Any]:
    """Load the harvest record from a path or an in-memory mapping.

    The refusal code is the *first violated rule*'s own code, taken from the same
    rule set :func:`check` reports — not inferred by looking for words in the
    messages. Inferring it from prose was wrong the first time it was tried: the
    completeness message "missing 'mode'" contains the word ``mode``, so a missing
    mode was reported as a copied harvest.
    """
    document = _as_mapping(source)
    findings = _findings(document)
    if findings:
        code, message = findings[0]
        raise Refused(code, message)
    return dict(document)


def _findings(record: Mapping[str, Any]) -> List[Tuple[str, str]]:
    """Every violated rule as ``(code, message)``, most specific first.

    One rule set, two views: :func:`check` reports the messages and :func:`load`
    raises the first code. A rule cannot exist in one and not the other.
    """
    findings: List[Tuple[str, str]] = []

    if record.get("schema") != SCHEMA:
        findings.append(
            ("declaration-invalid", f"schema must be {SCHEMA!r}, got {record.get('schema')!r}")
        )

    harvests = record.get("harvests")
    if not isinstance(harvests, list) or not harvests:
        findings.append(
            (
                "harvest-incomplete",
                "no harvests are declared — a record with nothing in it documents nothing",
            )
        )
        harvests = []

    for index, harvest in enumerate(harvests):
        where = f"harvests[{index}]"
        if not isinstance(harvest, Mapping):
            findings.append(("declaration-invalid", f"{where} is not an object"))
            continue
        for key in _REQUIRED_HARVEST_KEYS:
            if not harvest.get(key):
                code = "harvest-code-copied" if key == "mode" and harvest.get("mode") else "harvest-incomplete"
                findings.append((code, f"{where}: missing {key!r}"))
        mode = harvest.get("mode")
        if mode and mode not in MODES:
            findings.append(
                (
                    "harvest-code-copied",
                    f"{where}: mode {mode!r} is not one of {MODES} — a harvest may read a shape "
                    "or consume an interface, but a copy is not a harvest",
                )
            )
        source = harvest.get("source")
        if isinstance(source, Mapping):
            for key in _REQUIRED_SOURCE_KEYS:
                if not source.get(key):
                    findings.append(("harvest-incomplete", f"{where}: source is missing {key!r}"))
        elif source is not None:
            findings.append(("declaration-invalid", f"{where}: source is not an object"))

    if not record.get(POLICY_KEY):
        findings.append(
            (
                "harvest-incomplete",
                f"{POLICY_KEY} is missing: the upstream domain pattern's own declaration must be "
                "pointed at, never restated here",
            )
        )
    return findings


def check(record: Mapping[str, Any]) -> Tuple[str, ...]:
    """Every way the record fails to be a harvest declaration; empty means it holds."""
    return tuple(message for _code, message in _findings(record))
