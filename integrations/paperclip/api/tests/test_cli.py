"""The CLI verbs are the operator surface the gate drives (issue #413)."""

from __future__ import annotations

from pathlib import Path

from integrations.paperclip.api import cli, openapi


def test_check_verb_is_green_on_the_committed_tree(root: Path) -> None:
    assert cli.main(["--root", str(root), "check"]) == cli.RC_OK


def test_controls_verb_is_green_on_the_committed_tree(root: Path) -> None:
    assert cli.main(["--root", str(root), "controls"]) == cli.RC_OK


def test_emit_writes_the_committed_artifact(root: Path, tmp_path: Path) -> None:
    out = tmp_path / "openapi.json"
    assert cli.main(["--root", str(root), "emit", "--out", str(out)]) == cli.RC_OK
    assert out.read_text(encoding="utf-8") == (root / openapi.EMITTED_ARTIFACT).read_text(
        encoding="utf-8"
    )


def test_company_verb_prints_the_declared_mapping(root: Path) -> None:
    assert cli.main(["--root", str(root), "company"]) == cli.RC_OK
