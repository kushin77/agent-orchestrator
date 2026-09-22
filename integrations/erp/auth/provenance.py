"""The GR-10 harvest record: what was taken from upstream, and what was built.

Upstream ERPNext is **GPL-3.0**. Two of its shapes informed this lane (the
role-permission table and field-level permissions); neither is code, and none
may become code by accident. The record is a file
(``catalog/provenance.json``) and this module is the thing that refuses to let
it be decorative:

* a record that claims a harvest was ``code-copied`` is refused by name
  (``harvest-code-copied``) — that is the line GR-10 draws, and a loader that
  merely *stored* the claim would let it be crossed by an edit nobody reads;
* a harvest that says what it took but not **what was built instead** is refused
  (``harvest-incomplete``), because "we harvested X" with no replacement named
  is a note, not a provenance record;
* an empty harvest list is refused for the same reason — a record with nothing
  in it cannot be audited.

The distinction the schema cannot express and this module does is *which* modes
are legal for *this* upstream: the schema declares the vocabulary
(``pattern-only`` / ``code-copied``) so an invented mode is a schema violation,
and this loader refuses ``code-copied`` outright.

---knowledge---
module_id: integrations.erp.auth.provenance
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [Harvest, Provenance, load, load_default]
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
from typing import Any, Mapping, Tuple

from .model import Refused

#: The only mode a GPL-3.0 upstream may be harvested under.
LEGAL_MODE = "pattern-only"


@dataclass(frozen=True)
class Harvest:
    """One upstream shape, and what was built in its place."""

    shape: str
    upstream_path: str
    mode: str
    built_instead: str


@dataclass(frozen=True)
class Provenance:
    """A validated GR-10 record."""

    schema: str
    upstream: str
    license: str
    mode: str
    harvests: Tuple[Harvest, ...]

    def to_json(self) -> dict:
        return {
            "schema": self.schema,
            "upstream": self.upstream,
            "license": self.license,
            "mode": self.mode,
            "harvests": [
                {
                    "shape": h.shape,
                    "upstreamPath": h.upstream_path,
                    "mode": h.mode,
                    "builtInstead": h.built_instead,
                }
                for h in self.harvests
            ],
        }


def load(source: Any) -> Provenance:
    """Load and enforce a provenance record from a path or an in-memory mapping."""
    if isinstance(source, Mapping):
        document = source
    elif isinstance(source, (str, Path)):
        path = Path(source)
        if not path.is_file():
            raise Refused("harvest-incomplete", f"no provenance record at {path}")
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise Refused("harvest-incomplete", f"provenance is unreadable ({exc})") from exc
    else:
        raise Refused("harvest-incomplete", "expected a mapping or a path")

    if not isinstance(document, Mapping):
        raise Refused("harvest-incomplete", "the provenance record is not an object")

    for key in ("schema", "upstream", "license", "mode"):
        value = document.get(key)
        if not isinstance(value, str) or not value.strip():
            raise Refused("harvest-incomplete", f"provenance field {key!r} is missing or empty")

    if document["mode"] != LEGAL_MODE:
        raise Refused(
            "harvest-code-copied",
            f"mode {document['mode']!r} — {document['upstream']} is {document['license']} "
            f"and may be harvested as {LEGAL_MODE!r} only",
        )

    raw = document.get("harvests")
    if not isinstance(raw, list) or not raw:
        raise Refused(
            "harvest-incomplete",
            "the record lists no harvests — a provenance record with nothing in it "
            "cannot be audited",
        )

    harvests = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise Refused("harvest-incomplete", f"{entry!r} is not a harvest entry")
        shape = entry.get("shape")
        upstream_path = entry.get("upstreamPath")
        mode = entry.get("mode")
        built = entry.get("builtInstead")
        for name, value in (
            ("shape", shape),
            ("upstreamPath", upstream_path),
            ("builtInstead", built),
        ):
            if not isinstance(value, str) or not value.strip():
                raise Refused(
                    "harvest-incomplete",
                    f"harvest {shape!r}: {name} is missing — a record that names what it "
                    f"took but not what it built instead is a note, not a provenance record",
                )
        if mode != LEGAL_MODE:
            raise Refused(
                "harvest-code-copied",
                f"harvest {shape!r} claims mode {mode!r}; {document['upstream']} is "
                f"{document['license']} and may be harvested as {LEGAL_MODE!r} only",
            )
        harvests.append(
            Harvest(
                shape=shape,
                upstream_path=upstream_path,
                mode=mode,
                built_instead=built,
            )
        )

    return Provenance(
        schema=document["schema"],
        upstream=document["upstream"],
        license=document["license"],
        mode=document["mode"],
        harvests=tuple(harvests),
    )


def load_default() -> Provenance:
    """The shipped record, from ``catalog/provenance.json``."""
    return load(Path(__file__).resolve().parent / "catalog" / "provenance.json")
