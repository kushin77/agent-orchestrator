"""guardrails.chat.envelope — the grounding/citations envelope this lane consumes.

The grounding lane (``gateway/mcp``, issue #504) hands a chat turn a **citations
envelope**: the retrieved fragments that will be assembled into the model's
grounding prefix, each labelled with a stable ``source_id``. This module
consumes that shape and never imports that package, so the chat guards are
verifiable standalone. The contract is the small documented document below:

.. code-block:: json

    {
      "fragments": [
        {"source_id": "ticket:OPS-1187", "text": "...", "kind": "ticket"},
        {"source_id": "kb:runbook/restore", "text": "..."}
      ]
    }

Strict, fail-closed reading — an envelope this lane cannot interpret is
*undecidable* (the caller must BLOCK), never "there was no grounding":

* the document is a mapping carrying a ``fragments`` list;
* every fragment is a mapping with a non-empty string ``source_id`` and a
  string ``text``;
* ``source_id`` values are unique inside one envelope — a duplicate makes every
  citation into it ambiguous, so it is refused rather than deduplicated;
* ``kind`` is optional and defaults to ``"document"``; unknown keys are
  preserved verbatim so a producer can enrich the envelope without breaking
  this reader.


---knowledge---
module_id: guardrails.chat.envelope
system: guardrails
app: chat
solution_class: pattern
patterns: [contract-first, fail-closed, consume-never-restate]
derives_from: null
owner_sme: security-sme
tier: L1
interfaces: [GroundingError, GroundingFragment, GroundingEnvelope]
invariants: "an envelope this lane cannot interpret is undecidable, never empty"
gotchas: "the grounding lane (gateway/mcp) is imported nowhere here on purpose, so the chat guards verify standalone"
related: ["#507", "#504"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Sequence

DEFAULT_KIND = "document"


class GroundingError(ValueError):
    """The grounding envelope is absent, malformed or ambiguous."""


@dataclass(frozen=True)
class GroundingFragment:
    """One retrieved fragment that may enter the model's grounding prefix."""

    source_id: str
    text: str
    kind: str = DEFAULT_KIND
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        document = dict(self.extra)
        document.update({"source_id": self.source_id, "text": self.text, "kind": self.kind})
        return document


@dataclass(frozen=True)
class GroundingEnvelope:
    """The validated set of fragments supplied to one turn."""

    fragments: tuple

    def __post_init__(self) -> None:
        seen: set = set()
        for fragment in self.fragments:
            if not isinstance(fragment, GroundingFragment):
                raise GroundingError(
                    f"fragments must be GroundingFragment, got {type(fragment).__name__}"
                )
            if fragment.source_id in seen:
                raise GroundingError(f"duplicate source_id in envelope: {fragment.source_id!r}")
            seen.add(fragment.source_id)

    # -- construction ----------------------------------------------------

    @classmethod
    def from_fragments(cls, fragments: Sequence[Mapping[str, Any]]) -> "GroundingEnvelope":
        """Build an envelope from raw fragment mappings (strict)."""
        if isinstance(fragments, (str, bytes, Mapping)) or not isinstance(fragments, Sequence):
            raise GroundingError(
                f"fragments must be a sequence of mappings, got {type(fragments).__name__}"
            )
        built = []
        for index, raw in enumerate(fragments):
            built.append(_fragment_from_mapping(raw, index))
        return cls(tuple(built))

    @classmethod
    def from_mapping(cls, document: Mapping[str, Any]) -> "GroundingEnvelope":
        """Validate a citations-envelope document."""
        if not isinstance(document, Mapping):
            raise GroundingError(
                f"grounding envelope must be a mapping, got {type(document).__name__}"
            )
        if "fragments" not in document:
            raise GroundingError("grounding envelope carries no 'fragments' key")
        return cls.from_fragments(document["fragments"])

    @classmethod
    def from_json(cls, text: str) -> "GroundingEnvelope":
        """Validate an envelope from its JSON serialization."""
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GroundingError(f"grounding envelope is not valid JSON: {exc}") from exc
        return cls.from_mapping(document)

    # -- reading ---------------------------------------------------------

    def ids(self) -> tuple:
        """Every admitted ``source_id``, in fragment order."""
        return tuple(fragment.source_id for fragment in self.fragments)

    def get(self, source_id: str) -> Optional[GroundingFragment]:
        for fragment in self.fragments:
            if fragment.source_id == source_id:
                return fragment
        return None

    def __contains__(self, source_id: object) -> bool:
        return any(fragment.source_id == source_id for fragment in self.fragments)

    def __len__(self) -> int:
        return len(self.fragments)

    def __iter__(self):
        return iter(self.fragments)

    def to_mapping(self) -> Dict[str, Any]:
        return {"fragments": [fragment.to_dict() for fragment in self.fragments]}


def _fragment_from_mapping(raw: Any, index: int) -> GroundingFragment:
    """Validate one fragment mapping (helper for :meth:`GroundingEnvelope.from_fragments`)."""
    if not isinstance(raw, Mapping):
        raise GroundingError(f"fragment #{index} is not a mapping")
    source_id = raw.get("source_id")
    if not isinstance(source_id, str) or not source_id.strip():
        raise GroundingError(f"fragment #{index} has no non-empty string source_id")
    text = raw.get("text")
    if not isinstance(text, str):
        raise GroundingError(f"fragment {source_id!r} has no string text")
    kind = raw.get("kind", DEFAULT_KIND)
    if not isinstance(kind, str) or not kind:
        raise GroundingError(f"fragment {source_id!r} has a non-string kind")
    extra = {
        key: value
        for key, value in raw.items()
        if key not in ("source_id", "text", "kind")
    }
    return GroundingFragment(source_id=source_id, text=text, kind=kind, extra=extra)
