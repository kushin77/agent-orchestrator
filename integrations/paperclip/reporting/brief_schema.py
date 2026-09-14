"""The frozen brief schema, and the composer's duty to validate what it emits.

``brief.schema.json`` — in this package, outside ``tests/`` — is the frozen shape
of the composition's **machine document**: per module the id, owning repo, the
mandatory status in the three honest states, pin/rev, the consumer assets and the
seed each comes from, health, board ref, drift, and the claim list.

Two properties this module holds, both of them the point rather than a nicety:

* **the validator really validates.** The repository's own JSON-Schema subset
  (``integrations/paperclip/mapping.py``) ignores keywords it does not know, so a
  ``$ref`` or a ``oneOf`` in the schema file would silently validate *nothing*.
  :func:`unsupported_keywords` walks the file and names every keyword outside the
  subset, and the suite fails while one exists — the schema cannot quietly become
  a decoration (GR-12 / AO-GR-4).
* **the composer validates what it emits, every run.** :func:`validate` returns
  one :class:`~governance.modules.model.Refusal` per violation, subject = the
  JSON path, so ``BRIEF-SCHEMA-INVALID`` names where the document stopped being
  the document this package froze.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from governance.modules.model import CannotAssess, Refusal

#: The schema file, beside this module (it travels with the package).
SCHEMA_FILE = "brief.schema.json"

#: Keywords ``integrations.paperclip.mapping.validate`` actually implements. A
#: keyword outside this set is IGNORED by the validator, so the suite asserts the
#: schema uses nothing else.
VALIDATION_KEYWORDS = frozenset(
    {
        "type",
        "enum",
        "minLength",
        "pattern",
        "format",
        "minimum",
        "maximum",
        "required",
        "properties",
        "additionalProperties",
        "items",
    }
)

#: Metadata keywords: annotations the validator never reads, which is fine
#: because they validate nothing by design.
METADATA_KEYWORDS = frozenset(
    {"$schema", "$id", "title", "description", "$comment"}
)


def schema_path(directory: Optional[Path] = None) -> Path:
    """Where the frozen schema lives — beside this module unless told otherwise."""
    base = Path(directory) if directory is not None else Path(__file__).resolve().parent
    return base / SCHEMA_FILE


def load(directory: Optional[Path] = None) -> Dict[str, Any]:
    """Read the schema. Unreadable or malformed is CANNOT-ASSESS, never a pass."""
    path = schema_path(directory)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CannotAssess("the brief schema is unreadable: {} ({})".format(path, exc))
    except ValueError as exc:
        raise CannotAssess("the brief schema is not valid JSON: {} ({})".format(path, exc))
    if not isinstance(data, dict):
        raise CannotAssess("the brief schema is not a JSON object: {}".format(path))
    if data.get("$id") != "ao.module-brief/v1":
        raise CannotAssess(
            "the brief schema is not the frozen one: $id is {!r}, expected {!r}".format(
                data.get("$id"), "ao.module-brief/v1"
            )
        )
    return data


def _walk_keywords(node: Any, path: str, out: List[Tuple[str, str]]) -> None:
    """Every key that is neither a validation nor a metadata keyword.

    ``properties`` is the one place where a key is a *name* rather than a
    keyword, so it is descended into without judging its keys; ``required`` and
    ``enum`` carry name/value lists that are not schemas at all.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                for name, sub in value.items():
                    _walk_keywords(sub, "{}.{}".format(path, name), out)
                continue
            if key in ("required", "enum"):
                continue
            if key not in VALIDATION_KEYWORDS and key not in METADATA_KEYWORDS:
                out.append(("{}.{}".format(path, key), key))
            _walk_keywords(value, "{}.{}".format(path, key), out)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _walk_keywords(value, "{}[{}]".format(path, index), out)


def unsupported_keywords(schema: Dict[str, Any]) -> Tuple[Tuple[str, str], ...]:
    """Every keyword the validator would ignore, with the path that carries it.

    A keyword outside the subset is not an error the validator reports — it is
    silently ignored, which is how a ``$ref`` turns a schema file into a
    formality. This function is what the suite calls to refuse one.
    """
    found: List[Tuple[str, str]] = []
    _walk_keywords(schema, "$", found)
    return tuple(found)


def validate(document: Dict[str, Any], schema: Dict[str, Any]) -> Tuple[Refusal, ...]:
    """Every way ``document`` fails to be the frozen brief, naming the path.

    The repository's own subset validator does the work: this function only turns
    its messages into the ``BRIEF-*`` refusal vocabulary so a failure names its
    subject (the JSON path) instead of a bare string.
    """
    from integrations.paperclip import mapping

    findings: List[Refusal] = []
    for message in mapping.validate(document, schema):
        path, _, detail = message.partition(":")
        findings.append(
            Refusal(
                "BRIEF-SCHEMA-INVALID",
                path.strip() or "$",
                "the emitted brief does not satisfy {}: {}".format(
                    SCHEMA_FILE, detail.strip() or message
                ),
                SCHEMA_FILE,
            )
        )
    return tuple(findings)


__all__ = [
    "METADATA_KEYWORDS",
    "SCHEMA_FILE",
    "VALIDATION_KEYWORDS",
    "load",
    "schema_path",
    "unsupported_keywords",
    "validate",
]
