"""A2A peer-check standard wired into its four cadence points (#1549's own
Acceptance, wired by #1625): claim, pre-dispatch, the fleet loop's cadence, and
the SME card rule. Each arm proves an overlapping sibling claim is refused BY
NAME and a disjoint one passes — the same ``peers.peer_check`` function at
every point, never re-implemented.

Does not touch ``governance/dispatch/tests/test_peers.py`` (another lane owns
its one pre-existing red arm).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "fleet"))

import cli
import claims as claims_mod
import snapshot as snapshot_mod
import tiered
import yaml


def _ledger_with_sibling(tmp_path: Path, *, path: str = "a.sh") -> Path:
    """A live claim ledger holding ONE sibling (#12, agent-x) on ``path``."""
    claims_dir = tmp_path / "claims"
    claims_dir.mkdir()
    record = {
        "event": "claim",
        "issue": 12,
        "agent": "agent-x",
        "at": snapshot_mod.now_iso(),
        "lane": "lane-x",
        "reason": "next-in-milestone",
        "ttl_hours": 24,
        "files": [{"path": path, "regions": None}],
    }
    (claims_dir / "0001-00012-agent-x-claim.json").write_text(json.dumps(record), encoding="utf-8")
    return claims_dir


# --- cadence point 1: cli.py claim ------------------------------------------


def _board(tmp_path: Path) -> Path:
    from model import Issue, Snapshot

    path = tmp_path / "snapshot.json"
    snapshot_mod.save(
        Snapshot(
            generated_at=snapshot_mod.now_iso(),
            source="peer-check-cadence-test",
            issues={701: Issue(701, "dispatchable", milestone="CLI")},
        ),
        path,
    )
    return path


def _claim(board_file, ledger, *args) -> int:
    return cli.main(
        [
            "claim",
            "--snapshot", str(board_file),
            "--ledger", str(ledger),
            "--locks", str(board_file.parent / "locks"),
            "--stale-minutes", "15",
            *args,
        ]
    )


def test_claim_refuses_an_overlapping_sibling_by_name(tmp_path, capsys):
    board_file = _board(tmp_path)
    ledger = _ledger_with_sibling(tmp_path, path="a.sh")

    rc = _claim(
        board_file, ledger,
        "--issue", "701", "--agent", "agent-b", "--lane", "lane-b",
        "--files", json.dumps([{"path": "a.sh"}]),
    )

    err = capsys.readouterr().err
    assert rc == 1
    assert "peer-check REFUSED: OVERLAP" in err
    assert "agent-x" in err and "#12" in err and "lane-x" in err and "a.sh" in err
    assert not list(ledger.glob("*701*"))  # never recorded


def test_claim_allows_a_disjoint_claim(tmp_path):
    board_file = _board(tmp_path)
    ledger = _ledger_with_sibling(tmp_path, path="a.sh")

    rc = _claim(
        board_file, ledger,
        "--issue", "701", "--agent", "agent-b", "--lane", "lane-b",
        "--files", json.dumps([{"path": "b.py"}]),
    )

    assert rc == 0
    assert list(ledger.glob("*701*"))


# --- cadence point 3: tiered.py pre-dispatch --------------------------------


def test_tiered_run_aborts_a_dispatch_whose_files_overlap_a_sibling(tmp_path):
    ledger = _ledger_with_sibling(tmp_path, path="a.sh")

    with pytest.raises(tiered.TieredRefusal) as excinfo:
        tiered.run(
            701,
            "## Acceptance\n\n```bash\ntrue\n```\n",
            ["tier:L0"],
            agent="agent-b",
            ledger=ledger,
            caller_files=["a.sh"],
        )
    assert excinfo.value.reason == "peer-check-overlap"
    assert "agent-x" in excinfo.value.detail
    assert "a.sh" in excinfo.value.detail


def test_tiered_run_proceeds_past_disjoint_files(tmp_path):
    ledger = _ledger_with_sibling(tmp_path, path="a.sh")

    outcome = tiered.run(
        701,
        "## Acceptance\n\n```bash\ntrue\n```\n",
        ["tier:L0"],
        agent="agent-b",
        ledger=ledger,
        caller_files=["b.py"],
        dry_run=True,
    )
    assert outcome["dry_run"] is True


def test_tiered_run_falls_back_to_the_agents_own_live_claim(tmp_path):
    """No call site names ``caller_files`` (cli.py's try-loop call passes none):
    the pre-dispatch check must still be live, so it falls back to the caller's
    own recorded claim — the same fallback ``peers.main`` already uses when
    ``--files`` is absent.
    """
    ledger = _ledger_with_sibling(tmp_path, path="a.sh")
    own = {
        "event": "claim", "issue": 701, "agent": "agent-b", "at": snapshot_mod.now_iso(),
        "lane": "lane-b", "reason": "next-in-milestone", "ttl_hours": 24,
        "files": [{"path": "a.sh", "regions": None}],
    }
    (ledger / "0002-00701-agent-b-claim.json").write_text(json.dumps(own), encoding="utf-8")

    with pytest.raises(tiered.TieredRefusal) as excinfo:
        tiered.run(
            701,
            "## Acceptance\n\n```bash\ntrue\n```\n",
            ["tier:L0"],
            agent="agent-b",
            ledger=ledger,
        )
    assert excinfo.value.reason == "peer-check-overlap"
    assert "agent-x" in excinfo.value.detail and "a.sh" in excinfo.value.detail


# --- cadence point 2: the fleet loop's cadence ------------------------------


def _ledger_with_sibling_and_own_claim(tmp_path: Path, *, path: str = "a.sh") -> Path:
    """A live ledger holding sibling #12 (agent-x) AND the loop's own claim
    (#20, deepseek-sister) — both on ``path``, so the cadence hook's own
    ``_caller_files_from_live`` lookup has something to judge against.
    """
    claims_dir = _ledger_with_sibling(tmp_path, path=path).parent / "claims"
    record = {
        "event": "claim",
        "issue": 20,
        "agent": "deepseek-sister",
        "at": snapshot_mod.now_iso(),
        "lane": "sister-loop",
        "reason": "next-in-milestone",
        "ttl_hours": 24,
        "files": [{"path": path, "regions": None}],
    }
    (claims_dir / "0002-00020-deepseek-sister-claim.json").write_text(json.dumps(record), encoding="utf-8")
    return claims_dir


def test_fleet_loop_cadence_refuses_an_overlapping_sibling_by_name(tmp_path):
    import terminal

    ledger = _ledger_with_sibling_and_own_claim(tmp_path, path="a.sh")

    refusal = terminal.peer_check_cadence("deepseek-sister", ledger=ledger)

    assert refusal is not None
    assert "agent-x" in refusal and "#12" in refusal and "lane-x" in refusal and "a.sh" in refusal


def test_fleet_loop_cadence_is_a_noop_with_no_live_sibling(tmp_path):
    import terminal

    empty_ledger = tmp_path / "empty-claims"
    empty_ledger.mkdir()
    assert terminal.peer_check_cadence("deepseek-sister", ledger=empty_ledger) is None


# --- cadence point 4: the SME card rule + the gate that asserts it ---------


def test_a_platform_sme_card_carries_the_peer_check_rule():
    card_path = Path(__file__).resolve().parents[3] / "registry/personas/cards/platform-sme.yaml"
    card = yaml.safe_load(card_path.read_text(encoding="utf-8"))
    rule = "before starting work, run the peer-check standard; refuse to touch files in OVERLAP"
    assert rule in card.get("guardrails", [])
