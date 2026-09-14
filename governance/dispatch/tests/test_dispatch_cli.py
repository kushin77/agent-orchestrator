"""The dispatch / claim CLI seam: arbitration refusals with real exit codes (#726).

The unit tests prove the arbitration can refuse. These prove the *seam* does: the
read-only ``dispatch`` command refuses over the real CLI, names the evidence and
the holder in the text an operator reads, exits 1 (refused) or 2 (cannot assess),
and a granted dispatch writes nothing — while ``claim`` applies the same
arbitration before it mutates the ledger.
"""

from __future__ import annotations

import json

import cli
import pytest
import snapshot as snapshot_mod
from model import Issue, Snapshot


def _board() -> Snapshot:
    """#701 dispatchable, #702 closed, #705 under the closed epic #704, #714 held."""
    return Snapshot(
        generated_at=snapshot_mod.now_iso(),
        source="dispatch-cli-test",
        issues={
            701: Issue(701, "dispatchable", milestone="CLI"),
            702: Issue(702, "closed", state="closed", milestone="CLI"),
            704: Issue(704, "closed epic", state="closed", milestone="CLI", labels=("type:epic",)),
            705: Issue(705, "child of a closed epic", milestone="CLI", parent=704),
            714: Issue(714, "held by lane-a", milestone="CLI"),
        },
    )


@pytest.fixture
def board_file(tmp_path):
    path = tmp_path / "snapshot.json"
    snapshot_mod.save(_board(), path)
    return path


@pytest.fixture
def ledger(tmp_path):
    """A ledger holding #714 for agent-a on lane-a — the duplicate-lane control."""
    claims_dir = tmp_path / "claims"
    claims_dir.mkdir()
    record = {
        "event": "claim",
        "issue": 714,
        "agent": "agent-a",
        "at": snapshot_mod.now_iso(),
        "lane": "lane-a",
        "reason": "next-in-milestone",
        "ttl_hours": 24,
    }
    (claims_dir / "00000000000000000001-00714-agent-a-claim.json").write_text(
        json.dumps(record), encoding="utf-8"
    )
    return claims_dir


def _dispatch(board_file, ledger, *args) -> int:
    return cli.main(
        [
            "dispatch",
            "--snapshot", str(board_file),
            "--ledger", str(ledger),
            "--locks", str(board_file.parent / "locks"),
            "--stale-minutes", "15",
            *args,
        ]
    )


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


def test_dispatch_refuses_a_closed_issue_and_names_the_board(board_file, ledger, capsys):
    rc = _dispatch(board_file, ledger, "--issue", "702", "--agent", "agent-b", "--lane", "lane-b")

    err = capsys.readouterr().err
    assert rc == 1
    assert "dispatch REFUSED: issue-closed" in err
    assert "state=closed" in err
    assert str(board_file) in err


def test_dispatch_refuses_an_open_issue_under_a_closed_epic(board_file, ledger, capsys):
    rc = _dispatch(board_file, ledger, "--issue", "705", "--agent", "agent-b", "--lane", "lane-b")

    err = capsys.readouterr().err
    assert rc == 1
    assert "dispatch REFUSED: epic-closed" in err
    assert "#704" in err


def test_dispatch_refuses_a_unit_another_lane_holds_and_names_the_holder(board_file, ledger, capsys):
    rc = _dispatch(board_file, ledger, "--issue", "714", "--agent", "agent-b", "--lane", "lane-b")

    err = capsys.readouterr().err
    assert rc == 1
    assert "dispatch REFUSED: already-claimed" in err
    assert "agent-a" in err
    assert "lane-a" in err
    assert "714.lock" in err


def test_dispatch_refuses_an_unowned_unit(board_file, ledger, capsys):
    rc = _dispatch(board_file, ledger, "--issue", "701", "--agent", "agent-b")

    err = capsys.readouterr().err
    assert rc == 1
    assert "dispatch REFUSED: unowned" in err
    assert "no lane owns #701" in err


def test_dispatch_refuses_a_stale_board_with_exit_2(board_file, ledger, capsys):
    rc = cli.main(
        [
            "dispatch",
            "--snapshot", str(board_file),
            "--ledger", str(ledger),
            "--locks", str(board_file.parent / "locks"),
            "--stale-minutes", "-1",
            "--issue", "701", "--agent", "agent-b", "--lane", "lane-b",
        ]
    )

    err = capsys.readouterr().err
    assert rc == 2
    assert "snapshot-stale" in err


def test_dispatch_without_a_snapshot_is_cannot_assess(tmp_path, capsys):
    rc = _dispatch(tmp_path / "missing.json", tmp_path / "claims",
                   "--issue", "701", "--agent", "agent-b", "--lane", "lane-b")

    assert rc == 2
    assert "CANNOT-ASSESS" in capsys.readouterr().err


def test_dispatch_grants_a_provable_unit_with_its_provenance(board_file, ledger, capsys):
    rc = _dispatch(board_file, ledger, "--issue", "701", "--agent", "agent-b", "--lane", "lane-b")

    out = capsys.readouterr().out
    assert rc == 0
    verdict, _ = json.JSONDecoder().raw_decode(out)
    assert verdict["verdict"] == "granted"
    assert verdict["issue"] == 701
    assert verdict["epic"] is None
    assert verdict["lane"] == "lane-b"
    assert verdict["provenance"]["lane"] == "lane-b"
    assert verdict["provenance"]["evidence"]
    assert "dispatch granted: #701 -> epic <none> -> lane lane-b" in out


def test_dispatch_is_read_only(board_file, ledger):
    before = sorted(path.name for path in ledger.glob("*.json"))
    rc = _dispatch(board_file, ledger, "--issue", "701", "--agent", "agent-b", "--lane", "lane-b")

    assert rc == 0
    assert sorted(path.name for path in ledger.glob("*.json")) == before
    locks = board_file.parent / "locks"
    assert not locks.exists() or not list(locks.glob("*.lock"))


def test_dispatch_refuses_a_directive_aimed_at_a_closed_issue(board_file, ledger, monkeypatch, capsys, tmp_path):
    """Acceptance, verbatim: a *directive* for a closed issue is refused."""
    sent = tmp_path / "sent"
    sent.mkdir()
    (sent / "d-closed.json").write_text(
        json.dumps({"from": "brain", "to": "lane", "type": "directive", "id": "d-closed",
                    "task": {"issue": 702}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli.claims, "SENT_DIR", sent)

    rc = _dispatch(board_file, ledger, "--issue", "702", "--agent", "agent-b", "--lane", "lane-b",
                   "--directive", "d-closed")

    err = capsys.readouterr().err
    assert rc == 1
    assert "issue-closed" in err
    assert "state=closed" in err


def test_claim_refuses_a_closed_epic_with_exit_1(board_file, ledger, capsys):
    """The mutation point applies the same arbitration, not just the pre-flight."""
    rc = _claim(board_file, ledger, "--issue", "705", "--agent", "agent-b", "--lane", "lane-b")

    err = capsys.readouterr().err
    assert rc == 1
    assert "claim REFUSED: epic-closed" in err


def test_claim_records_the_provenance_it_arbitrated(board_file, ledger):
    rc = _claim(board_file, ledger, "--issue", "701", "--agent", "agent-b", "--lane", "lane-b")

    assert rc == 0
    records = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(ledger.glob("*.json"))]
    written = [record for record in records if record["issue"] == 701]
    assert len(written) == 1
    assert written[0]["lane"] == "lane-b"
    assert written[0]["provenance"]["issue"] == 701
    assert written[0]["provenance"]["lane"] == "lane-b"
    assert written[0]["provenance"]["evidence"]


def test_cli_held_names_the_holder_and_its_lane(ledger, capsys):
    """`held` answers "who owns this" with the lane, so a duplicate lane is visible."""
    rc = cli.main(["held", "--issue", "714", "--ledger", str(ledger), "--locks", str(ledger.parent / "locks")])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["agent"] == "agent-a"
    assert payload["lane"] == "lane-a"


def test_negative_control_closed_issue_and_owned_unit_are_both_refused(board_file, ledger, capsys):
    """Negative control (#726): provoke the refusal, keep the real output as evidence.

    A closed issue and a unit another lane already holds must both be refused —
    naming the evidence checked and the holder found — while a provable unit in the
    same run is still granted.
    """
    closed_rc = _dispatch(board_file, ledger, "--issue", "702", "--agent", "agent-b", "--lane", "lane-b")
    closed_err = capsys.readouterr().err
    assert closed_rc == 1
    assert "issue-closed" in closed_err
    assert "state=closed" in closed_err
    assert str(board_file) in closed_err

    held_rc = _dispatch(board_file, ledger, "--issue", "714", "--agent", "agent-b", "--lane", "lane-b")
    held_err = capsys.readouterr().err
    assert held_rc == 1
    assert "already-claimed" in held_err
    assert "agent-a" in held_err
    assert "lane-a" in held_err

    grant_rc = _dispatch(board_file, ledger, "--issue", "701", "--agent", "agent-b", "--lane", "lane-b")
    capsys.readouterr()
    assert grant_rc == 0
