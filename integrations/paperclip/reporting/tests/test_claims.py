"""Every brief line is a claim that resolves — or a finding naming its line."""

from __future__ import annotations

from pathlib import Path

from integrations.paperclip.reporting import composer
from integrations.paperclip.reporting.model import (
    ARTIFACT,
    ClaimBook,
    claim_findings,
)

HUB = "vendor/CMR"


def test_the_shipped_composition_has_no_unresolved_claim(composition):
    assert composition.claims, "a composition that claims nothing proves nothing"
    assert [f for f in composition.findings if f.code == "BRIEF-CLAIM-UNRESOLVED"] == []


def test_every_claim_carries_at_least_one_citation(composition):
    for claim in composition.claims:
        assert claim.citations, (claim.subject, claim.fact)
        assert all(citation.strip() for citation in claim.citations)


def test_a_claim_with_no_citation_is_refused_by_name_and_line(repo_root: Path):
    book = ClaimBook()
    book.add("# Module brief")
    book.add("")
    book.claim(
        "| pin | 9.9.9 | (nowhere) |",
        subject="code-indexing",
        fact="pin",
        value="9.9.9",
        citations=(),
    )
    findings = claim_findings(
        book.claims,
        repo_root=repo_root,
        hub_root=repo_root / HUB,
        ids=("code-indexing",),
    )
    assert len(findings) == 1
    rendered = findings[0].render()
    assert "BRIEF-CLAIM-UNRESOLVED: code-indexing" in rendered
    assert "line 3" in rendered
    assert "{}:3".format(ARTIFACT) in rendered


def test_a_claim_citing_a_path_that_is_not_there_is_refused_by_name(repo_root: Path):
    book = ClaimBook()
    book.claim(
        "| seed | templates/module/not-here.json |",
        subject="shared-frontend",
        fact="asset_seed:tokens.json",
        value="templates/module/not-here.json",
        citations=("templates/module/not-here.json",),
    )
    findings = claim_findings(
        book.claims,
        repo_root=repo_root,
        hub_root=repo_root / HUB,
        ids=("shared-frontend",),
    )
    assert len(findings) == 1
    assert "templates/module/not-here.json" in findings[0].render()


def test_a_claim_citing_a_registry_row_that_does_not_exist_is_refused(repo_root: Path):
    book = ClaimBook()
    book.claim(
        "| owning repo | kushin77/ghost |",
        subject="ghost",
        fact="owning_repo",
        value="kushin77/ghost",
        citations=("registry:ghost",),
    )
    findings = claim_findings(
        book.claims,
        repo_root=repo_root,
        hub_root=repo_root / HUB,
        ids=("code-indexing",),
    )
    assert len(findings) == 1
    assert "registry:ghost" in findings[0].render()


def test_a_registry_row_citation_resolves_for_a_refused_name_too(repo_root: Path, composition):
    """Membership is a row: a `not-a-module` name still resolves to its row."""
    document_ids = set(composition.ids)
    assert "paperclip" in document_ids  # refused as a hub module, still a cited row
    assert "shared-frontend" in document_ids
    assert not [f for f in composition.findings if f.code == "BRIEF-CLAIM-UNRESOLVED"]


def test_claim_line_numbers_are_the_lines_the_reader_sees(repo_root: Path, registry_document):
    """One claim per line, in document order, and the line is not a separator."""
    composition = composer.compose(registry_document, repo_root, HUB)
    lines = composition.text.splitlines()
    numbers = [claim.line for claim in composition.claims]
    assert numbers == sorted(numbers), "claims are not in document order"
    assert len(set(numbers)) == len(numbers), "two claims claim the same line"
    labels = {
        "owning_repo": "owning repo",
        "mandatory_status": "mandatory status",
        "consumer_assets": "consumer assets",
        "board_ref": "board ref",
    }
    for claim in composition.claims:
        line = lines[claim.line - 1]
        assert 1 <= claim.line <= len(lines)
        assert line.strip(), (claim.subject, claim.fact)
        assert not set(line.strip()) <= {"|", "-", " "}, (claim.subject, claim.fact)
        label = labels.get(claim.fact)
        if label:
            assert label in line, (label, line)
