"""The emitted document is deterministic and the committed artifact is real (#413)."""

from __future__ import annotations

from pathlib import Path

from integrations.paperclip.api import openapi


def test_two_builds_are_byte_identical(root: Path) -> None:
    first = openapi.serialize(openapi.build_document(root))
    second = openapi.serialize(openapi.build_document(root))
    assert first == second


def test_committed_artifact_matches_the_fresh_emission(root: Path) -> None:
    committed = (root / openapi.EMITTED_ARTIFACT).read_text(encoding="utf-8")
    assert committed == openapi.serialize(openapi.build_document(root))


def test_serialization_is_sorted_and_newline_terminated(root: Path) -> None:
    text = openapi.serialize(openapi.build_document(root))
    assert text.endswith("}\n")
    assert text.index('"components"') < text.index('"paths"')
