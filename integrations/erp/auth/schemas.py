"""The frozen schemas, and the step that actually *uses* them.

The declarations in ``catalog/`` are also validated structurally, against the
JSON Schemas in ``schema/``, and this module is what makes that a claim rather
than a file that sits beside them. Two things are deliberate about it.

**The validator is ERP-02's, not a second one.** ``integrations/erp/core``
landed the stdlib JSON-Schema subset validator this module's schemas are
written for, and re-implementing it here would create a second answer to "is
this declaration well-formed" — the exact drift the module's single-store rule
exists to prevent. So the schemas are checked with
``integrations.erp.core.schema.validate``.

**Structural and semantic checks are separate, and neither is redundant.** The
schema fixes the *shape* (a rule has a ``kind``, a ``field``, a boolean
``read``); :mod:`policies` refuses the *contradictions* a schema cannot express
(a rule that declares ``block`` while withholding nothing). Both run, and each
names itself when it fails, so a reader can tell a malformed declaration from a
contradictory one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Mapping

from .model import Refused

SCHEMA_DIR = Path(__file__).resolve().parent / "schema"

#: The frozen schemas this lane ships, by file name.
SCHEMAS = (
    "role-map.schema.json",
    "field-policy.schema.json",
    "provenance.schema.json",
)


def load_schema(name: str) -> Mapping[str, Any]:
    """Read one frozen schema, or refuse to assess."""
    path = SCHEMA_DIR / name
    if not path.is_file():
        raise Refused("declaration-invalid", f"no frozen schema at {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused("declaration-invalid", f"schema {name} is unreadable ({exc})") from exc


def violations(instance: Any, name: str, *, where: str = "$") -> List[str]:
    """Every structural violation of ``name`` in ``instance``."""
    from integrations.erp.core import schema as core_schema

    return list(core_schema.validate(instance, load_schema(name), where=where))


def enforce(instance: Any, name: str, what: str) -> None:
    """Refuse ``schema-violation`` when ``instance`` does not match ``name``.

    The message names the declaration *and* the schema, so a failure says which
    of the two is being enforced rather than leaving a reader to guess.
    """
    problems = violations(instance, name)
    if problems:
        raise Refused(
            "schema-violation",
            f"{what} does not match {name}: " + "; ".join(problems[:3]),
        )
