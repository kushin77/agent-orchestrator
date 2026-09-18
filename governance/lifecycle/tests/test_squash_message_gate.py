"""Merge is refused when the composed squash message would drop the ticket
trailer (issue #1102, parent #878).

``GhOps.merge_pull_request`` calls ``scripts/check-squash-message.sh --pr <n>``
immediately before ``gh pr merge --squash``. This suite drives that call the same
way ``test_verify_port.py`` drives the real verification port: a stub script
stands in for ``scripts/check-squash-message.sh`` (the only fiction), and a stub
``gh`` on ``PATH`` records whether it was ever invoked. When the stub reports
NOT-OK, the merge must be refused BY NAME (``squash-message-would-drop-trailer``)
and ``gh pr merge`` must never run; when it reports OK, the merge proceeds and
``gh`` is called exactly once.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from governance.lifecycle.cli import GhOps

PR_NUMBER = 1102


def _stub_check_squash_message(root: Path, *, ok: bool) -> None:
    """Stand in for scripts/check-squash-message.sh at the path GhOps calls."""
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    stub = scripts_dir / "check-squash-message.sh"
    if ok:
        body = (
            "#!/usr/bin/env bash\n"
            "printf 'check-squash-message: OK — trailer is the final paragraph\\n'\n"
            "exit 0\n"
        )
    else:
        body = (
            "#!/usr/bin/env bash\n"
            "printf 'check-squash-message: NOT-OK — "
            "commit-ref-outside-the-trailer-block\\n' >&2\n"
            "exit 1\n"
        )
    stub.write_text(body, encoding="utf-8")
    stub.chmod(0o755)


def _stub_gh(root: Path, monkeypatch) -> Path:
    """A ``gh`` on PATH that records every call and reports the PR merged."""
    binary_dir = root / "bin"
    binary_dir.mkdir(exist_ok=True)
    calls = root / "gh-calls"
    stub = binary_dir / "gh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$STUB_GH_CALLS"\n'
        'if [ "$1" = "pr" ] && [ "$2" = "view" ]; then\n'
        '  printf \'{"state": "MERGED", "mergeCommit": {"oid": "deadbeef"}}\\n\'\n'
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binary_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("STUB_GH_CALLS", str(calls))
    return calls


def test_a_body_whose_trailer_is_followed_by_prose_refuses_the_merge(tmp_path, monkeypatch):
    """The trailer paragraph is not the final paragraph -> refused BY NAME, gh untouched."""
    _stub_check_squash_message(tmp_path, ok=False)
    calls = _stub_gh(tmp_path, monkeypatch)

    with pytest.raises(RuntimeError) as raised:
        GhOps(root=tmp_path).merge_pull_request(PR_NUMBER)

    assert "squash-message-would-drop-trailer" in str(raised.value)
    assert not calls.exists() or calls.read_text(encoding="utf-8") == ""


def test_a_body_whose_trailer_is_the_final_paragraph_proceeds(tmp_path, monkeypatch):
    """The trailer paragraph IS the final paragraph -> the merge proceeds normally."""
    _stub_check_squash_message(tmp_path, ok=True)
    calls = _stub_gh(tmp_path, monkeypatch)

    oid = GhOps(root=tmp_path).merge_pull_request(PR_NUMBER)

    assert oid == "deadbeef"
    call_lines = calls.read_text(encoding="utf-8").splitlines()
    assert any(line.startswith(f"pr merge {PR_NUMBER} --squash") for line in call_lines)
