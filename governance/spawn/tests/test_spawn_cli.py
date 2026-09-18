"""The local spawn path: the same document, the same refusal (issue #793).

Criterion 3 of #793 is that a locally spawned subagent is governed by the same
envelope as a fleet one. The proof is that `governance/spawn/cli.py open` refuses
exactly what `fleet/terminal.py` refuses, with the same exit code, from the same
validator — and that a spawn it ADMITS carries every fact, read from the root's
own board rather than invented here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT / "governance" / "dispatch") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "governance" / "dispatch"))

from governance.spawn import model  # noqa: E402


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def scratch_board(root: Path, *, issue: int = 793, lane: str = "spawn-envelope", agent: str = "local-agent") -> Path:
    """A root with its OWN board: focus, snapshot and a real claim event.

    Written with the claim ledger's own writer, not by hand: the envelope must
    read what `governance/dispatch/cli.py claim` writes, and a test that
    hand-rolled the file would prove only that two hand-rolled files agree.
    """
    import claims as claim_ledger

    board = root / ".board"
    board.mkdir(parents=True, exist_ok=True)
    (board / "focus.json").write_text(
        json.dumps(
            {"active_epic": 708, "activated_at": now_stamp(), "wave_cap": 12, "max_agents": 0, "pooled": []}
        ),
        encoding="utf-8",
    )
    (board / "snapshot.json").write_text(
        json.dumps(
            {
                "generated_at": now_stamp(),
                "source": "fixture",
                "issues": [
                    {
                        "number": issue,
                        "title": "fixture issue",
                        "state": "OPEN",
                        "milestone": "",
                        "labels": [],
                        "parent": 708,
                        "blocked_by": [],
                        "cross_refs": [],
                        "closed_at": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    claim_ledger.append_event(
        claim_ledger.ClaimEvent(
            event="claim", issue=issue, agent=agent, at=now_stamp(), lane=lane
        ),
        board / "claims",
    )
    return board


def session_env(issue: int = 793, lane: str = "spawn-envelope", agent: str = "local-agent") -> dict[str, str]:
    """A real minted identity — `governance/isolation/cli.py env`, not a literal."""
    result = subprocess.run(
        [
            sys.executable, str(REPO_ROOT / "governance" / "isolation" / "cli.py"), "env",
            "--issue", str(issue), "--agent", agent, "--lane", lane, "--json",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return dict(json.loads(result.stdout))


def run_cli(args: list[str], root: Path, env: dict[str, str], *, scrub: bool = False, **extra_env: str):
    base = {
        key: value
        for key, value in os.environ.items()
        if not (scrub and key.startswith(("AO_", "GIT_AUTHOR", "GIT_COMMITTER")))
    }
    merged = {**base, **env, **extra_env}
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "governance" / "spawn" / "cli.py"), *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        env=merged,
    )


def test_the_local_path_is_admitted_with_the_same_document(tmp_path: Path) -> None:
    root = tmp_path / "root"
    worktree = tmp_path / "wt"
    worktree.mkdir(parents=True)
    scratch_board(root)
    env = session_env()
    env["AO_WORKTREE"] = str(worktree)

    result = run_cli(
        ["open", "--issue", "793", "--lane", "spawn-envelope", "--agent", "local-agent",
         "--root", str(root), "--worktree", str(worktree), "--no-mint", "--no-claim", "--json"],
        root,
        env,
    )

    assert result.returncode == model.EXIT_OK, result.stderr
    document = json.loads(result.stdout)
    assert model.validate(document) == []
    assert document["claim"]["owner"] == "local-agent"
    assert document["focus"]["epic"] == 708
    assert document["spawn"]["path"] == "local"
    assert document["trailer"].endswith("#793")
    session = document["session"]
    assert session["author_name"] and session["author_email"]
    assert session["committer_name"] == session["author_name"], (
        "the minted committer half must equal the author half"
    )
    assert session["committer_email"] == session["author_email"]
    assert session["author_name"] == f"agent-{session['agent']}"
    assert (root / ".fleet" / "spawn" / f"{document['session']['id']}.json").exists()


def test_the_local_path_prints_the_rendered_block(tmp_path: Path) -> None:
    from governance.spawn import render

    root = tmp_path / "root"
    worktree = tmp_path / "wt"
    worktree.mkdir(parents=True)
    scratch_board(root)
    env = session_env()
    env["AO_WORKTREE"] = str(worktree)

    result = run_cli(
        ["open", "--issue", "793", "--lane", "spawn-envelope", "--agent", "local-agent",
         "--root", str(root), "--worktree", str(worktree), "--no-mint", "--no-claim"],
        root,
        env,
    )

    assert result.returncode == model.EXIT_OK, result.stderr
    assert render.MARKER in result.stdout
    assert "STANDING MANDATE" in result.stdout
    assert "AO-GR-22" in result.stdout


def test_the_local_path_refuses_by_name_when_the_claim_is_absent(tmp_path: Path) -> None:
    root = tmp_path / "root"
    worktree = tmp_path / "wt"
    worktree.mkdir(parents=True)
    (root / ".board").mkdir(parents=True)  # a board with no claim in its ledger
    env = session_env()
    env["AO_WORKTREE"] = str(worktree)

    result = run_cli(
        ["open", "--issue", "793", "--lane", "spawn-envelope", "--agent", "local-agent",
         "--root", str(root), "--worktree", str(worktree), "--no-mint", "--no-claim"],
        root,
        env,
    )

    assert result.returncode == model.EXIT_REFUSED, result.stdout
    assert "claim.owner" in result.stderr
    assert "REFUSED" in result.stderr


def test_the_claim_step_runs_against_the_roots_own_board(tmp_path: Path) -> None:
    """Without `--no-claim` the real dispatcher is driven, scoped to `--root`.

    The issue is absent from the scratch board, so the claim is refused — and the
    envelope is then refused BY NAME for the field that is genuinely empty. No
    event reaches the real repository's ledger.
    """
    root = tmp_path / "root"
    worktree = tmp_path / "wt"
    worktree.mkdir(parents=True)
    (root / ".board").mkdir(parents=True)
    (root / ".board" / "snapshot.json").write_text(
        json.dumps({"generated_at": now_stamp(), "source": "fixture", "issues": []}), encoding="utf-8"
    )
    env = session_env()
    env["AO_WORKTREE"] = str(worktree)

    result = run_cli(
        ["open", "--issue", "793", "--lane", "spawn-envelope", "--agent", "local-agent",
         "--root", str(root), "--worktree", str(worktree), "--no-mint"],
        root,
        env,
    )

    assert result.returncode == model.EXIT_REFUSED
    assert "claim not taken" in result.stderr
    assert "claim.owner" in result.stderr


def test_a_build_needs_no_board_and_refuses_the_fields_it_cannot_read(tmp_path: Path) -> None:
    """Offline and fail-closed: no session, no claim, no epic — all refused by name."""
    empty = tmp_path / "empty"
    empty.mkdir()
    result = run_cli(
        ["build", "--issue", "793", "--lane", "spawn-envelope", "--agent", "local-agent",
         "--root", str(empty), "--worktree", str(empty)],
        empty,
        {},
        scrub=True,
    )

    assert result.returncode == model.EXIT_REFUSED
    for field in ("claim.owner", "session.id"):
        assert field in result.stderr, result.stderr


@pytest.mark.parametrize("field", ["issue", "lane", "worktree", "session", "trailer", "claim",
                                   "focus", "capacity", "budget", "gate", "verify"])
def test_check_refuses_every_required_field_by_name(tmp_path: Path, field: str, envelope_fields: dict) -> None:
    fields = dict(envelope_fields)
    spawn_meta = fields.pop("spawn")
    document = model.assemble(fields, spawn=spawn_meta)
    document.pop(field)
    target = tmp_path / "doc.json"
    target.write_text(model.dumps(document), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "governance" / "spawn" / "cli.py"), "check", "--file", str(target)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == model.EXIT_REFUSED
    assert field in result.stderr
