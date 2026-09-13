"""Query interface: source-backed evidence and compliance context (issue #139)."""

from __future__ import annotations

from pathlib import Path

from conftest import make_tree

from indexer import build_index
from model import KIND_ADR, KIND_GOVERNANCE, KIND_ISSUE_METADATA
from query import coverage_report, query, summarize


def _index(tree: Path):
    return build_index(tree, owner_repo="acme/widgets", generated_at="fixed")


def test_query_by_kind_returns_only_that_kind(tree: Path):
    results = query(_index(tree), kind=KIND_ADR)
    assert results
    assert {result.item.kind for result in results} == {KIND_ADR}


def test_query_by_owner(tree: Path):
    results = query(_index(tree), owner="architecture")
    assert results
    assert {result.item.provenance.owner for result in results} == {"architecture"}


def test_query_by_tag(tree: Path):
    results = query(_index(tree), tag="OPEN")
    assert results
    assert all("OPEN" in result.item.tags for result in results)


def test_query_by_text_matches_keywords(tree: Path):
    make_tree(tree, text="# Note\n\nfail-closed guardrails everywhere\n")
    results = query(_index(tree), text="fail-closed")
    assert results
    assert all("keywords" in result.matched_fields for result in results)


def test_query_by_text_matches_path(tree: Path):
    results = query(_index(tree), text="AGENTS.md")
    assert results
    assert any(result.item.id == "AGENTS.md" for result in results)


def test_query_returns_no_results_for_an_absent_term(tree: Path):
    """NEGATIVE CONTROL: an absent term yields nothing (the CLI exits NOT-OK)."""
    assert query(_index(tree), text="zzz-absent-term-zzz") == []


def test_filters_combine_as_and(tree: Path):
    results = query(_index(tree), text="AGENTS.md", kind=KIND_ISSUE_METADATA)
    assert results == []


def test_limit_is_applied(tree: Path):
    limited = query(_index(tree), limit=2)
    assert len(limited) == 2


def test_evidence_is_source_backed_and_complete(tree: Path):
    result = query(_index(tree), text="AGENTS.md")[0]
    payload = result.evidence()

    evidence = payload["evidence"]
    assert evidence["source"] == "acme/widgets"
    assert evidence["origin_path"] == "AGENTS.md"
    assert len(evidence["sha256"]) == 64
    assert evidence["owner"]
    assert payload["compliance"]["kind_required"] is True
    assert payload["path"] == "AGENTS.md"


def test_evidence_carries_chain_metadata_for_issues(tree: Path):
    result = query(_index(tree), kind=KIND_ISSUE_METADATA, text="issue-139")[0]
    payload = result.evidence()
    assert "parent:138" in payload["compliance"]["tags"]


def test_summarize_is_machine_readable(tree: Path):
    index = _index(tree)
    results = query(index, kind=KIND_GOVERNANCE, limit=1)
    payload = summarize(index, results)

    assert payload["schema"] == index.schema
    assert payload["repo"] == "acme/widgets"
    assert payload["indexed_items"] == len(index.items)
    assert payload["result_count"] == len(results)
    assert payload["results"][0]["evidence"]["owner"]


def test_coverage_report_lists_every_kind(tree: Path):
    coverage = coverage_report(_index(tree))
    kinds = {entry["kind"] for entry in coverage}
    assert {"golden-rules", "adr", "policy", "issue-metadata"} <= kinds
