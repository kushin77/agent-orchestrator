"""The citations envelope — every grounded claim names its source (ADR-0023).

The envelope is the *shape* half of grounding: a grounded answer is a document
whose ``citations`` array carries one entry per grounded claim, naming the
retrieved fragment it came from (``fragment_id``) and that fragment's
``source_id`` — a bridge family plus revision, a tool call id, or a ticket id,
the identifiers the grounding lane (issue #504) emits per fragment.

This module codes against that **small, documented dict shape** rather than
importing the grounding lane's package: the chat lane's fixtures, harness and
tests must run standalone, and the contract that matters is the shape, not an
object graph across lanes.

The one behaviour that needs more than a shape is fabrication. A citation whose
``source_id`` was never supplied to the turn is a fabricated citation — the
exact failure ADR-0023 exists to prevent — and :func:`fabricated` names it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, List, Mapping, Optional, Sequence

#: The identifier families a ``source_id`` may come from (issue #504).
SOURCE_KINDS = ("bridge", "tool_call", "ticket")

#: ``<kind>:<stable id>`` — the shape every ``source_id`` must have.
SOURCE_ID_PATTERN = re.compile(r"^(bridge|tool_call|ticket):[A-Za-z0-9._:/-]+$")

#: The keys one citation entry may carry (the envelope is closed).
CITATION_KEYS = ("fragment_id", "source_id", "revision", "claim")


class EnvelopeError(ValueError):
    """Raised when a citations envelope is missing, malformed or fabricated."""


@dataclass(frozen=True)
class Citation:
    """One grounded claim's provenance: the fragment and its source."""

    fragment_id: str
    source_id: str
    revision: Optional[str] = None
    claim: Optional[str] = None

    @property
    def kind(self) -> str:
        """The identifier family of ``source_id`` (``bridge`` / ``tool_call`` /
        ``ticket``)."""
        return self.source_id.split(":", 1)[0]

    def to_dict(self) -> dict:
        entry = {"fragment_id": self.fragment_id, "source_id": self.source_id}
        if self.revision is not None:
            entry["revision"] = self.revision
        if self.claim is not None:
            entry["claim"] = self.claim
        return entry

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Citation":
        """Validate one citation entry, refusing an unknown key or a bad id."""
        if not isinstance(raw, Mapping):
            raise EnvelopeError(f"citation must be a mapping, got {type(raw).__name__}")
        unknown = sorted(set(raw) - set(CITATION_KEYS))
        if unknown:
            raise EnvelopeError(f"citation carries unknown key(s): {unknown}")
        for required in ("fragment_id", "source_id"):
            value = raw.get(required)
            if not isinstance(value, str) or not value.strip():
                raise EnvelopeError(f"citation is missing a non-empty {required!r}")
        source_id = str(raw["source_id"])
        if not SOURCE_ID_PATTERN.match(source_id):
            raise EnvelopeError(
                f"source_id {source_id!r} does not match <kind>:<id> for a kind in "
                f"{', '.join(SOURCE_KINDS)}"
            )
        revision = raw.get("revision")
        claim = raw.get("claim")
        return cls(
            fragment_id=str(raw["fragment_id"]),
            source_id=source_id,
            revision=None if revision is None else str(revision),
            claim=None if claim is None else str(claim),
        )


def validate_citations(value: Any) -> List[Citation]:
    """Validate a payload's ``citations`` value into :class:`Citation` objects."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise EnvelopeError("citations must be a list")
    return [Citation.from_dict(entry) for entry in value]


def envelope(fragments: Iterable[Mapping[str, Any]]) -> List[Citation]:
    """Build the envelope for admitted fragments, in the order they were given.

    Deterministic by construction: the order is the caller's order and nothing
    is sorted, hashed or timestamped.
    """
    built: List[Citation] = []
    for fragment in fragments:
        built.append(
            Citation(
                fragment_id=str(fragment["fragment_id"]),
                source_id=str(fragment["source_id"]),
                revision=(
                    None
                    if fragment.get("revision") is None
                    else str(fragment["revision"])
                ),
            )
        )
    return built


def envelope_dicts(fragments: Iterable[Mapping[str, Any]]) -> List[dict]:
    """:func:`envelope` as plain dicts, ready to embed in a JSON payload."""
    return [entry.to_dict() for entry in envelope(fragments)]


def fabricated(
    citations: Iterable[Citation], supplied_source_ids: Iterable[str]
) -> List[str]:
    """``source_id``\\ s cited that were never supplied to the turn, sorted."""
    supplied = {str(source_id) for source_id in supplied_source_ids}
    invented = {entry.source_id for entry in citations if entry.source_id not in supplied}
    return sorted(invented)


def schema_requires_envelope(schema: Mapping[str, Any]) -> bool:
    """True when a JSON Schema *requires* the ``citations`` key.

    This is the mechanical form of "the envelope is checkable, not
    aspirational": a schema that merely permits ``citations`` lets an uncited
    answer validate, so the registry's module contract refuses it.
    """
    if not isinstance(schema, Mapping):
        return False
    required = schema.get("required")
    if not isinstance(required, Sequence) or isinstance(required, (str, bytes)):
        return False
    if "citations" not in required:
        return False
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return False
    citations = properties.get("citations")
    return isinstance(citations, Mapping) and citations.get("type") == "array"


def citation_floor(schema: Mapping[str, Any]) -> int:
    """The minimum citation count a schema demands (0 when it demands none)."""
    if not isinstance(schema, Mapping):
        return 0
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return 0
    citations = properties.get("citations")
    if not isinstance(citations, Mapping):
        return 0
    minimum = citations.get("minItems")
    return minimum if isinstance(minimum, int) and minimum > 0 else 0
