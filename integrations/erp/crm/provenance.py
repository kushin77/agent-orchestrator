"""The GR-10 harvest record for this lane, and the control that keeps it honest.

``docs/ERP-MODULE-GAP-ANALYSIS.md`` states the constraint this module exists
under: **ERPNext is a pattern source only.** It is GPL-3.0, so harvesting its
shapes is permitted and copying its code is not — and the repository rule that
makes that auditable is GR-10, which requires every harvested asset to record
its source, path and licence.

A note in a document satisfies nobody: "we did not copy upstream code" is
exactly the kind of claim that stays true until someone pastes a file. So the
record is machine-readable (:``catalog/provenance.json``) and *enforced*:

* every harvest carries its upstream project, version, licence, URL and
  harvest date, and the document as a whole names the policy
  ``patterns-only-no-upstream-code``;
* a record that *claims* ``codeCopied: true`` is refused by name
  (``code-copied``) — the constraint is checked, not trusted, so a lane that
  does vendor upstream code fails here rather than after review;
* a record with no harvests at all is refused (``provenance-invalid``): an empty
  provenance file is an unfilled form, and it would let a lane harvest silently
  by simply not recording anything.

---knowledge---
module_id: integrations.erp.crm.provenance
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from . import schema as schemas
from .model import Refused

PROVENANCE_SCHEMA = Path(__file__).resolve().parent / "schema" / "provenance.schema.json"
DEFAULT_PROVENANCE = Path(__file__).resolve().parent / "catalog" / "provenance.json"

#: The one accepted policy for this lane. Stated as data so a record cannot
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

    schema_version: int
    module: str
    policy: str
    harvests: Tuple[Harvest, ...] = field(default_factory=tuple)

    def shapes(self) -> Tuple[str, ...]:
        return tuple(harvest.shape for harvest in self.harvests)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "module": self.module,
            "policy": self.policy,
            "harvests": [harvest.to_dict() for harvest in self.harvests],
        }


def _cross_check(document: Mapping[str, Any]) -> List[str]:
    problems: List[str] = []
    harvests = document.get("harvests")
    if not isinstance(harvests, list) or not harvests:
        problems.append("harvests: a provenance record must name at least one harvest")
        return problems
    seen: Dict[str, int] = {}
    for position, entry in enumerate(harvests):
        if not isinstance(entry, dict):
            problems.append(f"harvests[{position}]: expected an object")
            continue
        shape = entry.get("shape")
        if isinstance(shape, str):
            seen[shape] = seen.get(shape, 0) + 1
    duplicates = sorted(name for name, count in seen.items() if count > 1)
    if duplicates:
        problems.append(f"harvests: records {', '.join(duplicates)} more than once")
    return problems


def load(
    source: Path | str | Mapping[str, Any] = DEFAULT_PROVENANCE,
    *,
    schema_path: Path | str = PROVENANCE_SCHEMA,
) -> Provenance:
    """Load and enforce a harvest record from a path or an in-memory mapping."""
    if isinstance(source, Mapping):
        document: Any = dict(source)
        origin = "in-memory provenance"
    else:
        path = Path(source)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise Refused("provenance-invalid", f"{path}: not found") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise Refused("provenance-invalid", f"{path}: {exc}") from exc
        origin = str(path)

    if not isinstance(document, dict):
        raise Refused("provenance-invalid", f"{origin}: must be a JSON object")

    try:
        schema = schemas.load(schema_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise Refused("provenance-invalid", f"unreadable provenance schema: {exc}") from exc

    problems = schemas.validate_named(
        document, schema, origin, schema_label=str(schema_path)
    )
    problems.extend(_cross_check(document))
    if problems:
        raise Refused(
            "provenance-invalid", f"{origin}: " + "; ".join(schemas.sorted_problems(problems))
        )

    harvests: List[Harvest] = []
    for entry in document["harvests"]:
        if entry["codeCopied"]:
            raise Refused(
                "code-copied",
                f"{origin}: harvest {entry['shape']!r} claims code copied from "
                f"{entry['upstream']} ({entry['license']}); this module is a pattern "
                "source consumer and may not vendor upstream code",
            )
        harvests.append(
            Harvest(
                shape=entry["shape"],
                upstream=entry["upstream"],
                upstream_version=entry["upstreamVersion"],
                license=entry["license"],
                code_copied=bool(entry["codeCopied"]),
                url=entry["url"],
                harvested_at=entry["harvestedAt"],
                note=entry.get("note", ""),
            )
        )

    if document["policy"] != POLICY:
        raise Refused(
            "provenance-invalid",
            f"{origin}: policy is {document['policy']!r}, not {POLICY!r}",
        )
    return Provenance(
        schema_version=document["schemaVersion"],
        module=document["module"],
        policy=document["policy"],
        harvests=tuple(harvests),
    )
