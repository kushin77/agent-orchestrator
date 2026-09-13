"""Query the knowledge index with source-backed evidence (issue #139).

A result is never just a title: every hit carries the provenance of the asset it
came from and the compliance context for its kind. "Query results return
source-backed evidence and compliance context" is the acceptance criterion, and it
is why `QueryResult.evidence()` exists rather than returning bare items.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from model import (
    EXPECTED_KINDS,
    REQUIRED_KINDS,
    Index,
    KnowledgeItem,
)


@dataclass(frozen=True)
class QueryResult:
    """One hit, with the fields that matched and its evidence bundle."""

    item: KnowledgeItem
    matched_fields: Tuple[str, ...]

    def evidence(self) -> Dict[str, Any]:
        """Source-backed evidence plus the compliance context for the kind."""
        provenance = self.item.provenance
        return {
            "id": self.item.id,
            "kind": self.item.kind,
            "title": self.item.title,
            "path": self.item.path,
            "matched_fields": list(self.matched_fields),
            "evidence": {
                "source": provenance.origin_repo,
                "origin_path": provenance.origin_path,
                "sha256": provenance.sha256,
                "version": provenance.version,
                "timestamp": provenance.timestamp,
                "owner": provenance.owner,
                "retrieval": provenance.retrieval,
                "upstream": provenance.upstream,
            },
            "compliance": {
                "kind_required": self.item.kind in REQUIRED_KINDS,
                "kind_expected": self.item.kind in EXPECTED_KINDS,
                "tags": list(self.item.tags),
            },
        }


def _haystacks(item: KnowledgeItem) -> Dict[str, str]:
    return {
        "id": item.id,
        "path": item.path,
        "title": item.title,
        "tags": " ".join(item.tags),
        "keywords": " ".join(item.keywords),
        "owner": item.provenance.owner,
    }


def query(
    index: Index,
    *,
    text: Optional[str] = None,
    kind: Optional[str] = None,
    owner: Optional[str] = None,
    tag: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[QueryResult]:
    """Search the index. All supplied filters must match (logical AND)."""
    needle = (text or "").strip().lower()
    results: List[QueryResult] = []

    for item in index.items:
        if kind is not None and item.kind != kind:
            continue
        if owner is not None and item.provenance.owner != owner:
            continue
        if tag is not None and tag not in item.tags:
            continue

        fields = _haystacks(item)
        if needle:
            matched = tuple(
                name for name, value in fields.items() if needle in value.lower()
            )
            if not matched:
                continue
        else:
            matched = tuple(fields)

        results.append(QueryResult(item=item, matched_fields=matched))

    results.sort(key=lambda result: (result.item.kind, result.item.id))
    if limit is not None:
        results = results[:limit]
    return results


def coverage_report(index: Index) -> List[Dict[str, Any]]:
    """Per-kind coverage for board reporting."""
    return [entry.as_dict() for entry in index.coverage]


def summarize(index: Index, results: Sequence[QueryResult]) -> Dict[str, Any]:
    """A machine-readable summary suitable for board reporting and gating."""
    return {
        "schema": index.schema,
        "generated_at": index.generated_at,
        "repo": index.repo,
        "indexed_items": len(index.items),
        "result_count": len(results),
        "results": [result.evidence() for result in results],
    }
