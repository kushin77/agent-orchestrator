"""The GR-10 harvest record for this lane, and the control that keeps it honest.

``docs/ERP-MODULE-GAP-ANALYSIS.md`` puts the whole ERP module under one
constraint: **ERPNext is a pattern source only.** It is GPL-3.0, so harvesting
its *shapes* is permitted and copying its *code* is not. GR-10 is the rule that
makes that auditable, and it applies to this lane's own in-repo harvest as well
— the shapes taken from ``integrations/paperclip/budget.py`` and
``telemetry/budgets`` are credited in ``PROVENANCE.md`` with the same record.

A paragraph of prose satisfies nobody: "we did not copy upstream code" stays
true until someone pastes a file. So the record is machine-readable
(``catalog/provenance.json``), validated against
``schema/provenance.schema.json``, and *enforced*:

* a harvest that **claims** ``codeCopied: true`` is refused by name
  (``provenance-code-copied``) — the constraint is measured, not trusted, so a
  lane that vendors upstream code fails here instead of in review;
* a record with **no harvests** is refused (``provenance-empty``): an empty
  record is an unfilled form, and it would let a lane harvest silently by
  recording nothing;
* a record that does not satisfy its schema is refused (``provenance-invalid``)
  by the same keyword-frozen validator the rest of the lane uses, so
  ``provenance`` cannot be a document nobody checks.

---knowledge---
module_id: integrations.erp.finops.provenance
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [Harvest, Provenance, load]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple, Union

from . import schema as schemas
from .model import Refused

__all__ = [
    "DEFAULT_PROVENANCE",
    "POLICY",
    "PROVENANCE_SCHEMA",
    "Harvest",
    "Provenance",
    "load",
]

PACKAGE = Path(__file__).resolve().parent
PROVENANCE_SCHEMA = PACKAGE / "schema" / "provenance.schema.json"
DEFAULT_PROVENANCE = PACKAGE / "catalog" / "provenance.json"

#: The one accepted policy for this module. Stated as data so a record cannot
#: quietly adopt a weaker one.
POLICY = "patterns-only-no-upstream-code"


@dataclass(frozen=True)
class Harvest:
    """One harvested shape: what came from where, under which licence."""

    shape: str
    upstream: str
    upstream_version: str
    license: str
    code_copied: bool
    url: str
    harvested_at: str
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "shape": self.shape,
            "upstream": self.upstream,
            "upstreamVersion": self.upstream_version,
            "license": self.license,
            "codeCopied": self.code_copied,
            "url": self.url,
            "harvestedAt": self.harvested_at,
            "note": self.note,
        }


@dataclass(frozen=True)
class Provenance:
    """A validated harvest record for one module."""

    schema_version: str
    module: str
    policy: str
    harvests: Tuple[Harvest, ...]

    def shapes(self) -> Tuple[str, ...]:
        return tuple(harvest.shape for harvest in self.harvests)

    def upstreams(self) -> Tuple[str, ...]:
        return tuple(sorted({harvest.upstream for harvest in self.harvests}))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "module": self.module,
            "policy": self.policy,
            "harvests": [harvest.to_dict() for harvest in self.harvests],
        }


def _read(source: Union[str, Path, Mapping[str, Any]]) -> Tuple[Mapping[str, Any], str]:
    if isinstance(source, Mapping):
        return source, "<memory>"
    path = Path(source)
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise Refused(
            "provenance-invalid", f"{path}: the harvest record cannot be read: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise Refused(
            "provenance-invalid", f"{path}: a harvest record must be a JSON object"
        )
    return payload, str(path)


def load(source: Union[str, Path, Mapping[str, Any]] = DEFAULT_PROVENANCE) -> Provenance:
    """Load and enforce the harvest record (path or in-memory mapping)."""
    raw, where = _read(source)

    violations = schemas.validate(
        dict(raw), schemas.load_and_refuse(PROVENANCE_SCHEMA), where=where
    )
    if violations:
        raise Refused("provenance-invalid", "; ".join(violations), where=where)

    if str(raw["policy"]) != POLICY:
        raise Refused(
            "provenance-invalid",
            f"policy {raw['policy']!r} is not the accepted {POLICY!r}",
            where=where,
        )

    entries: List[Mapping[str, Any]] = list(raw.get("harvests") or [])
    if not entries:
        raise Refused(
            "provenance-empty",
            "the harvest record declares no harvests, which records nothing",
            where=where,
        )

    harvests: List[Harvest] = []
    for index, entry in enumerate(entries):
        copied = bool(entry.get("codeCopied", False))
        if copied:
            raise Refused(
                "provenance-code-copied",
                f"harvest {entry.get('shape')!r} claims upstream code was copied",
                where=f"{where}:harvests[{index}]",
            )
        harvests.append(
            Harvest(
                shape=str(entry["shape"]),
                upstream=str(entry["upstream"]),
                upstream_version=str(entry["upstreamVersion"]),
                license=str(entry["license"]),
                code_copied=False,
                url=str(entry["url"]),
                harvested_at=str(entry["harvestedAt"]),
                note=str(entry.get("note") or ""),
            )
        )

    return Provenance(
        schema_version=str(raw["schemaVersion"]),
        module=str(raw["module"]),
        policy=str(raw["policy"]),
        harvests=tuple(harvests),
    )
