"""Knowledge-index domain model (issue #139 / M24).

---knowledge---
module_id: governance.knowledge.model
system: governance
app: knowledge
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [Finding, errors, warnings, Provenance, Relationship, KnowledgeItem, Coverage, Index]
invariants: ""
gotchas: ""
related: ["#139"]
do_not_duplicate: null
---knowledge---

The index is the program's authoritative catalogue of institutional knowledge:
its kinds, the provenance record every item must carry, and the findings that
make an invalid index fail loudly rather than quietly.

Two rules shape the design:

* **Every item carries provenance.** Source, owner, version, timestamp and a
  content hash. An item without those is not indexable — a catalogue whose
  entries cannot be traced back to a revision is a rumour store.
* **Findings are actionable.** Each has a stable code, a severity, and a message
  naming the artifact and the rule it broke. Nothing here can be mistaken for an
  advisory note that is safe to ignore.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Mapping, Sequence, Tuple

SCHEMA_ID = "cmr.knowledge/index-v1"

# -- kinds (closed vocabulary) ----------------------------------------------
KIND_GOLDEN_RULES = "golden-rules"
KIND_GOVERNANCE = "governance"
KIND_ARCHITECTURE = "architecture"
KIND_ADR = "adr"
KIND_POLICY = "policy"
KIND_PATTERN_TEMPLATE = "pattern-template"
KIND_ISSUE_METADATA = "issue-metadata"
KIND_LESSONS = "lessons"
KIND_RCA = "rca"

KINDS: Tuple[str, ...] = (
    KIND_GOLDEN_RULES,
    KIND_GOVERNANCE,
    KIND_ARCHITECTURE,
    KIND_ADR,
    KIND_POLICY,
    KIND_PATTERN_TEMPLATE,
    KIND_ISSUE_METADATA,
    KIND_LESSONS,
    KIND_RCA,
)

# Kinds that must yield items, and kinds the program expects but whose canonical
# home may live outside this repository (the CMR hub). The distinction matters: a
# required kind with no items is a failure, whereas an expected kind with no
# reachable source is a *reported gap* — honest either way, never silent.
REQUIRED_KINDS: FrozenSet[str] = frozenset(
    {
        KIND_GOLDEN_RULES,
        KIND_GOVERNANCE,
        KIND_ARCHITECTURE,
        KIND_ADR,
        KIND_POLICY,
        KIND_PATTERN_TEMPLATE,
        KIND_ISSUE_METADATA,
    }
)
EXPECTED_KINDS: FrozenSet[str] = frozenset({KIND_LESSONS, KIND_RCA})

# -- relationships (cross-reference spine) -----------------------------------
# The closed vocabulary of edge types. An edge whose type is not drawn from
# this tuple is invalid; the gate fails it by name. The builder
# (:mod:`governance.knowledge.crossref`) emits a subset of these today; the rest
# are reserved so a future source cannot invent an ungoverned edge type.
RELATIONSHIP_SUPERSEDES = "supersedes"
RELATIONSHIP_PARENT_OF = "parent-of"
RELATIONSHIP_BLOCKED_BY = "blocked-by"
RELATIONSHIP_CAUSED_BY = "caused-by"
RELATIONSHIP_MITIGATES = "mitigates"
RELATIONSHIP_ORIGIN = "origin"
RELATIONSHIP_REFS = "refs"
RELATIONSHIP_PART_OF = "part-of"
RELATIONSHIP_IMPLEMENTS = "implements"

RELATIONSHIP_TYPES: Tuple[str, ...] = (
    RELATIONSHIP_SUPERSEDES,
    RELATIONSHIP_PARENT_OF,
    RELATIONSHIP_BLOCKED_BY,
    RELATIONSHIP_CAUSED_BY,
    RELATIONSHIP_MITIGATES,
    RELATIONSHIP_ORIGIN,
    RELATIONSHIP_REFS,
    RELATIONSHIP_PART_OF,
    RELATIONSHIP_IMPLEMENTS,
)

# -- findings ---------------------------------------------------------------
SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"

# Stable finding codes (tests drive each one deliberately).
CODE_REQUIRED_SOURCE_MISSING = "required-source-missing"
CODE_REQUIRED_KIND_EMPTY = "required-kind-empty"
CODE_EXPECTED_KIND_UNAVAILABLE = "expected-kind-unavailable"
CODE_SECRET_POLICY_VIOLATION = "secret-policy-violation"
CODE_PROVENANCE_INCOMPLETE = "provenance-incomplete"
CODE_INTEGRITY_DRIFT = "integrity-drift"
CODE_UNREADABLE_SOURCE = "unreadable-source"
CODE_MALFORMED_ISSUE_SNAPSHOT = "malformed-issue-snapshot"
CODE_CATALOG_INVALID = "catalog-invalid"


@dataclass(frozen=True)
class Finding:
    """One actionable finding about the index."""

    code: str
    message: str
    severity: str = SEVERITY_ERROR
    path: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "path": self.path,
            "message": self.message,
        }


def errors(findings: Sequence[Finding]) -> Tuple[Finding, ...]:
    return tuple(f for f in findings if f.severity == SEVERITY_ERROR)


def warnings(findings: Sequence[Finding]) -> Tuple[Finding, ...]:
    return tuple(f for f in findings if f.severity == SEVERITY_WARNING)


# -- provenance -------------------------------------------------------------
REQUIRED_PROVENANCE_FIELDS: Tuple[str, ...] = (
    "origin_repo",
    "origin_path",
    "owner",
    "version",
    "sha256",
)


@dataclass(frozen=True)
class Provenance:
    """Where an item came from and which revision it was taken from."""

    origin_repo: str
    origin_path: str
    owner: str
    version: str  # last commit touching the path, else "unversioned"
    sha256: str
    bytes: int = 0
    lines: int = 0
    timestamp: str = ""  # commit timestamp for ``version``, when known
    retrieval: str = "working-tree"  # working-tree | submodule
    upstream: str = ""  # set when the asset is vendored from elsewhere

    def as_dict(self) -> Dict[str, Any]:
        return {
            "origin_repo": self.origin_repo,
            "origin_path": self.origin_path,
            "owner": self.owner,
            "version": self.version,
            "timestamp": self.timestamp,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "lines": self.lines,
            "retrieval": self.retrieval,
            "upstream": self.upstream,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Provenance":
        return cls(
            origin_repo=str(raw.get("origin_repo", "")),
            origin_path=str(raw.get("origin_path", "")),
            owner=str(raw.get("owner", "")),
            version=str(raw.get("version", "")),
            sha256=str(raw.get("sha256", "")),
            bytes=int(raw.get("bytes", 0) or 0),
            lines=int(raw.get("lines", 0) or 0),
            timestamp=str(raw.get("timestamp", "")),
            retrieval=str(raw.get("retrieval", "")),
            upstream=str(raw.get("upstream", "")),
        )

    def missing_fields(self) -> Tuple[str, ...]:
        """Provenance fields that are absent or empty."""
        present = self.as_dict()
        return tuple(
            name for name in REQUIRED_PROVENANCE_FIELDS if not present.get(name)
        )


@dataclass(frozen=True)
class Relationship:
    """One typed edge between two knowledge nodes.

    ``type`` must be drawn from :data:`RELATIONSHIP_TYPES` — the closed
    vocabulary. ``via`` is optional and names the artifact that declared the
    edge (a marker file, a ledger record id, a snapshot field) so the edge is
    traceable back to its source. Edges carry no timestamps: two builds over one
    revision must produce identical bytes.
    """

    from_id: str
    type: str
    to_id: str
    via: str = ""

    def as_dict(self) -> Dict[str, Any]:
        data = {
            "from_id": self.from_id,
            "type": self.type,
            "to_id": self.to_id,
        }
        if self.via:
            data["via"] = self.via
        return data

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Relationship":
        return cls(
            from_id=str(raw.get("from_id", "")),
            type=str(raw.get("type", "")),
            to_id=str(raw.get("to_id", "")),
            via=str(raw.get("via", "")),
        )


@dataclass(frozen=True)
class KnowledgeItem:
    """One indexed object: an id, its kind, its provenance and its terms.

    ``keywords`` are extracted significant terms from the asset. They make the
    catalogue searchable by *content* without storing the content itself — which
    would bloat the artefact and risk retaining material the secret policy
    rejects.
    """

    id: str
    kind: str
    path: str
    title: str
    provenance: Provenance
    tags: Tuple[str, ...] = ()
    keywords: Tuple[str, ...] = ()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "path": self.path,
            "title": self.title,
            "tags": list(self.tags),
            "keywords": list(self.keywords),
            "provenance": self.provenance.as_dict(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "KnowledgeItem":
        return cls(
            id=str(raw.get("id", "")),
            kind=str(raw.get("kind", "")),
            path=str(raw.get("path", "")),
            title=str(raw.get("title", "")),
            tags=tuple(raw.get("tags", ()) or ()),
            keywords=tuple(raw.get("keywords", ()) or ()),
            provenance=Provenance.from_dict(raw.get("provenance", {}) or {}),
        )


@dataclass
class Coverage:
    """Per-kind coverage: how many items, and whether the kind is satisfied."""

    kind: str
    count: int = 0
    required: bool = False
    status: str = "absent"  # present | absent | unavailable
    reason: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "count": self.count,
            "required": self.required,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass
class Index:
    """The built catalogue plus the findings raised while building it."""

    generated_at: str
    repo: str
    items: List[KnowledgeItem] = field(default_factory=list)
    relationships: List[Relationship] = field(default_factory=list)
    coverage: List[Coverage] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    schema: str = SCHEMA_ID

    def counts(self) -> Dict[str, int]:
        by_kind: Dict[str, int] = {}
        for item in self.items:
            by_kind[item.kind] = by_kind.get(item.kind, 0) + 1
        return by_kind

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "generated_at": self.generated_at,
            "repo": self.repo,
            "item_count": len(self.items),
            "counts": self.counts(),
            "coverage": [c.as_dict() for c in self.coverage],
            "findings": [f.as_dict() for f in self.findings],
            "relationships": [r.as_dict() for r in self.relationships],
            "items": [i.as_dict() for i in self.items],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Index":
        return cls(
            generated_at=str(raw.get("generated_at", "")),
            repo=str(raw.get("repo", "")),
            schema=str(raw.get("schema", SCHEMA_ID)),
            items=[KnowledgeItem.from_dict(i) for i in raw.get("items", []) or []],
            relationships=[
                Relationship.from_dict(r)
                for r in raw.get("relationships", []) or []
            ],
            coverage=[
                Coverage(
                    kind=str(c.get("kind", "")),
                    count=int(c.get("count", 0) or 0),
                    required=bool(c.get("required", False)),
                    status=str(c.get("status", "")),
                    reason=str(c.get("reason", "")),
                )
                for c in raw.get("coverage", []) or []
            ],
            findings=[
                Finding(
                    code=str(f.get("code", "")),
                    message=str(f.get("message", "")),
                    severity=str(f.get("severity", SEVERITY_ERROR)),
                    path=str(f.get("path", "")),
                )
                for f in raw.get("findings", []) or []
            ],
        )
