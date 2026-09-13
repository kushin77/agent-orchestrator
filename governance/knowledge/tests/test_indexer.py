"""Indexer behaviour: provenance, coverage and determinism (issue #139)."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import SNAPSHOT, make_tree, sample_path

from indexer import build_index, keywords_for_bytes, load_catalog, write_catalog
from model import (
    KIND_ADR,
    KIND_GOLDEN_RULES,
    KIND_ISSUE_METADATA,
    KIND_LESSONS,
    KIND_PATTERN_TEMPLATE,
    errors,
    warnings,
)
from sources import required_specs


def test_index_covers_every_required_kind(tree: Path):
    index = build_index(tree, owner_repo="acme/widgets")
    assert errors(index.findings) == ()

    covered = {entry.kind: entry.count for entry in index.coverage if entry.count}
    assert covered[KIND_GOLDEN_RULES] >= 2
    assert covered[KIND_ADR] >= 1
    assert covered[KIND_PATTERN_TEMPLATE] >= 1
    # One item per issue in the board snapshot.
    assert covered[KIND_ISSUE_METADATA] == len(SNAPSHOT["issues"])


def test_every_item_carries_complete_provenance(tree: Path):
    index = build_index(tree, owner_repo="acme/widgets")
    assert index.items, "expected a non-empty index"

    for item in index.items:
        assert item.provenance.missing_fields() == (), item.id
        assert item.provenance.origin_repo == "acme/widgets"
        assert len(item.provenance.sha256) == 64
        assert item.provenance.owner


def test_file_provenance_records_size_and_lines(tree: Path):
    index = build_index(tree, owner_repo="acme/widgets")
    agents = next(item for item in index.items if item.id == "AGENTS.md")
    assert agents.provenance.bytes > 0
    assert agents.provenance.lines >= 1
    assert agents.provenance.retrieval == "working-tree"


def test_issue_items_expose_chain_metadata(tree: Path):
    index = build_index(tree, owner_repo="acme/widgets")
    item = next(i for i in index.items if i.id == "issue-139")
    assert item.kind == KIND_ISSUE_METADATA
    assert "#139" in item.title
    assert "OPEN" in item.tags
    assert "parent:138" in item.tags
    assert "blocked_by:137" in item.tags


def test_expected_kind_absent_is_a_warning_not_a_failure(tree: Path):
    """Lessons/RCA live in the CMR hub; unreachable is reported, not fatal."""
    index = build_index(tree, owner_repo="acme/widgets")
    assert errors(index.findings) == ()

    unavailable = [w for w in warnings(index.findings)]
    assert any(KIND_LESSONS in w.message for w in unavailable)
    entry = next(c for c in index.coverage if c.kind == KIND_LESSONS)
    assert entry.status == "unavailable"
    assert entry.required is False


def test_build_is_deterministic(tree: Path):
    """Two runs over one revision must agree, or drift review is meaningless."""
    first = build_index(tree, owner_repo="acme/widgets", generated_at="fixed")
    second = build_index(tree, owner_repo="acme/widgets", generated_at="fixed")

    order_first = [(item.kind, item.id) for item in first.items]
    order_second = [(item.kind, item.id) for item in second.items]
    assert order_first == order_second
    assert order_first == sorted(order_first)  # stable (kind, id) ordering

    shas_first = [(i.id, i.provenance.sha256, i.keywords) for i in first.items]
    shas_second = [(i.id, i.provenance.sha256, i.keywords) for i in second.items]
    assert shas_first == shas_second


def test_keywords_are_bounded_and_deterministic():
    text = "fail-closed guardrails fail-closed verify before done guardrails"
    words = keywords_for_bytes(text.encode("utf-8"))
    assert "guardrails" in words
    assert "fail-closed" in words
    assert len(words) <= 40
    assert words == keywords_for_bytes(text.encode("utf-8"))


def test_keywords_ignore_prose_glue():
    words = keywords_for_bytes(b"the and for with that this from are not you")
    assert words == ()


def test_catalog_round_trips_through_disk(tree: Path):
    index = build_index(tree, owner_repo="acme/widgets", generated_at="fixed")
    path = write_catalog(index, tree / "catalog.json")

    payload = load_catalog(path)
    assert payload["item_count"] == len(index.items)
    assert payload["schema"] == index.schema
    # Provenance survives the round trip intact.
    recorded = {item["id"]: item for item in payload["items"]}
    assert recorded["AGENTS.md"]["provenance"]["sha256"] == next(
        item for item in index.items if item.id == "AGENTS.md"
    ).provenance.sha256


def test_index_records_the_repo_it_belonged_to(tree: Path):
    index = build_index(tree, owner_repo="acme/widgets")
    assert index.repo == "acme/widgets"


def test_sample_path_satisfies_every_required_spec(tree: Path):
    """Guards the fixture itself: each required spec must resolve in the tree."""
    missing = [
        spec.pattern
        for spec in required_specs()
        if not list(tree.glob(spec.pattern))
    ]
    assert missing == []
    for spec in required_specs():
        assert (tree / sample_path(spec.pattern)).is_file()
