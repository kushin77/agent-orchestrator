"""The dispatch entry point works end to end (issue #1179).

Three defects are pinned here, each with the provocation that proves the control
can fail rather than the happy path alone:

1. a stale board was refused and never refreshed, so every entry-point verb was
   ``CANNOT-ASSESS`` for anyone not already inside the fleet loop;
2. an issue dangling on a CLOSED epic was refused (correctly) and named by
   nothing, so it was invisible AND permanently unclaimable;
3. ``frontier()`` advertised work ``eligible`` refuses (issue #1168).
"""

from __future__ import annotations

import json

import claims
import cli
import liveness
import order
import snapshot as snapshot_mod
from model import Issue, Snapshot

STALE = "2020-01-01T00:00:00Z"


def _board(stamp: str) -> Snapshot:
    """A one-issue milestone with no epic, so nothing is pooled by a focus."""
    issues = {601: Issue(601, "the frontier", milestone="M25", labels=("type:task",))}
    return Snapshot(generated_at=stamp, source="test", issues=issues)


def _dangling_board() -> Snapshot:
    """M1 whose lowest open issue (#11) declares a CLOSED parent, with #12 behind it."""
    issues = {
        10: Issue(10, "the closed epic", state="closed", milestone="M1", labels=("type:epic",)),
        11: Issue(11, "child of a closed epic", milestone="M1", parent=10),
        12: Issue(12, "the real frontier", milestone="M1", labels=("type:task",)),
    }
    return Snapshot(generated_at=STALE, source="test", issues=issues)


def _write(tmp_path, snapshot: Snapshot, name: str = "snapshot.json"):
    path = tmp_path / name
    snapshot_mod.save(snapshot, path)
    return path


def _paths(tmp_path, snap_path):
    return [
        "--snapshot", str(snap_path),
        "--ledger", str(tmp_path / "claims"),
        "--locks", str(tmp_path / "locks"),
    ]


# --- defect 1: the entry point refreshes in band before it refuses -------------


def test_a_stale_board_is_refreshed_in_band_when_asked(tmp_path, capsys, monkeypatch):
    """The refusal names a remedy, so the remedy is a real lever — exactly once."""
    snap_path = _write(tmp_path, _board(STALE))
    calls = []

    def fake_refresh(path, **kwargs):
        calls.append((path, kwargs))
        snapshot_mod.save(_board(snapshot_mod.now_iso()), snap_path)
        return True, "refreshed 1 issue(s) from fixture/repo"

    monkeypatch.setattr(cli.snapshot_mod, "refresh", fake_refresh)
    rc = cli.main(["eligible", "--issue", "601", "--agent", "me", "--refresh",
                   "--stale-minutes", "15", *_paths(tmp_path, snap_path)])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert len(calls) == 1, "the in-band refresh must be exactly ONE attempt"
    assert json.loads(captured.out)["eligible"] is True
    assert "snapshot was stale" in captured.err


def test_a_refresh_that_does_not_clear_staleness_names_the_attempt(tmp_path, capsys, monkeypatch):
    """A refresh that fails is reported BY NAME, never as a bare age."""
    snap_path = _write(tmp_path, _board(STALE))
    monkeypatch.setattr(
        cli.snapshot_mod, "refresh", lambda path, **kwargs: (False, "network unreachable")
    )
    rc = cli.main(["status", "--refresh", "--stale-minutes", "15", *_paths(tmp_path, snap_path)])
    captured = capsys.readouterr()
    assert rc == 2
    assert "snapshot-stale" in captured.err
    assert "one bounded refresh failed: network unreachable" in captured.err
    # The producer's state is named too, so a dead control reads as a dead control.
    assert "board refresher" in captured.err or "no-board-refresher" in captured.err


def test_the_default_refresh_is_opt_in_and_says_so(tmp_path, capsys, monkeypatch):
    """A read verb must not reach the network unasked (measured: it rewrote the board).

    Making the refresh the default let a pytest suite drive a real board refresh
    during `make verify`, changing the tracked `.board/snapshot.json`. So the
    default is hermetic, and the refusal names `--refresh` as the lever.
    """
    snap_path = _write(tmp_path, _board(STALE))

    def explode(path, **kwargs):
        raise AssertionError("the default must not reach the refresh seam")

    monkeypatch.setattr(cli.snapshot_mod, "refresh", explode)
    rc = cli.main(["claim", "--issue", "601", "--agent", "me", "--lane", "gov",
                   "--stale-minutes", "15", *_paths(tmp_path, snap_path)])
    captured = capsys.readouterr()
    assert rc == 2
    assert "snapshot-stale" in captured.err
    assert "no refresh attempted — run this verb with --refresh" in captured.err
    # The remedy the refusal names must still be there.
    assert "refresh first" in captured.err


def test_a_fresh_board_is_not_refreshed_even_when_asked(tmp_path, capsys, monkeypatch):
    """No refresh is attempted when the board is already fresh."""
    snap_path = _write(tmp_path, _board(snapshot_mod.now_iso()))

    def explode(path, **kwargs):
        raise AssertionError("a fresh board must not trigger a refresh")

    monkeypatch.setattr(cli.snapshot_mod, "refresh", explode)
    rc = cli.main(["status", "--refresh", "--stale-minutes", "15", *_paths(tmp_path, snap_path)])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert "frontier: #601" in captured.out


# --- defect 2: a dangling-on-a-closed-epic issue is REPORTED -------------------


def test_status_reports_a_dangling_epic_with_its_remedy(tmp_path, capsys, monkeypatch):
    snap_path = _write(tmp_path, _dangling_board())
    monkeypatch.setattr(cli.snapshot_mod, "refresh", lambda path, **kwargs: (False, "offline"))
    rc = cli.main(["status", "--stale-minutes", "100000000",
                   *_paths(tmp_path, snap_path)])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert "dangling-epic: 1 open issue(s) declare a Parent that is closed" in captured.err
    assert "dangling-epic: #11 declares Parent #10, which is closed" in captured.err
    assert "re-point the issue's `Parent:`" in captured.err


def test_status_says_none_when_nothing_dangles(tmp_path, capsys):
    snap_path = _write(tmp_path, _board(snapshot_mod.now_iso()))
    rc = cli.main(["status", "--stale-minutes", "100000000", *_paths(tmp_path, snap_path)])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert "dangling-epic: none" in captured.out
    assert "dangling-epic:" not in captured.err


def test_dangling_verb_is_not_ok_when_an_issue_dangles(tmp_path, capsys):
    snap_path = _write(tmp_path, _dangling_board())
    rc = cli.main(["dangling", "--stale-minutes", "100000000", *_paths(tmp_path, snap_path)])
    captured = capsys.readouterr()
    assert rc == 1
    assert "dangling-epic: #11 declares Parent #10, which is closed" in captured.err
    assert "re-point the issue's `Parent:`" in captured.err


def test_dangling_verb_is_ok_on_a_clean_board(tmp_path, capsys):
    """The negative control: the detector can say no."""
    snap_path = _write(tmp_path, _board(snapshot_mod.now_iso()))
    rc = cli.main(["dangling", "--stale-minutes", "100000000", *_paths(tmp_path, snap_path)])
    assert rc == 0
    assert "dangling-epic: none" in capsys.readouterr().out


def test_dangling_verb_cannot_assess_a_missing_board(tmp_path, capsys):
    """An unreadable board is CANNOT-ASSESS (2), never a pass."""
    rc = cli.main(["dangling", "--snapshot", str(tmp_path / "absent.json")])
    assert rc == 2
    assert "CANNOT-ASSESS" in capsys.readouterr().err


def test_the_epic_closed_refusal_is_not_relaxed(tmp_path, capsys):
    """The fix is visibility, NOT re-allowing the work: `claim` still refuses."""
    snap_path = _write(tmp_path, _dangling_board())
    rc = cli.main(["eligible", "--issue", "11", "--agent", "me",
                   "--stale-minutes", "100000000",
                   *_paths(tmp_path, snap_path)])
    captured = capsys.readouterr()
    assert rc == 1
    verdict = json.loads(captured.out)
    assert verdict["eligible"] is False
    assert verdict["reason"] == "epic-closed"


# --- defect 3: the frontier never advertises work `claim` refuses (issue #1168)


def test_frontier_skips_an_epic_closed_issue():
    board = _dangling_board()
    frontier = order.frontier(board, "M1")
    assert frontier is not None
    assert frontier.number == 12, "the epic-closed #11 must never be the frontier"
    assert order.eligible(board, 11).reason == "epic-closed"


def test_unclaimable_frontier_is_empty_when_the_predicate_is_shared():
    assert order.unclaimable_frontier(_dangling_board(), "M1") == ""


def _weak_frontier(snapshot, milestone, claimed_by_others=frozenset(), focus_path=None,
                   queue_data=None):
    """The PRE-#1168 frontier predicate: the shared refusals are not applied.

    Written out here rather than imported so the provocation stays a genuine,
    independent restatement of the old rule — a mutant that called the new code
    would prove nothing.
    """
    candidates = [
        issue
        for issue in snapshot.open_issues()
        if issue.milestone == milestone
        and not issue.is_epic
        and issue.number not in claimed_by_others
        and not snapshot.blockers_open(issue)
    ]
    candidates.sort(key=lambda issue: issue.number)
    return candidates[0] if candidates else None


def test_unclaimable_frontier_provokes_the_pre_1168_disagreement(monkeypatch):
    """PROVOKE the disagreement: weaken `frontier` and the report must name it.

    Without this, `test_unclaimable_frontier_is_empty_when_the_predicate_is_shared`
    would be a happy-path assertion — a control whose pass and fail paths could
    both be empty (GR-12).
    """
    board = _dangling_board()
    monkeypatch.setattr(order, "frontier", _weak_frontier)
    disagreement = order.unclaimable_frontier(board, "M1")
    assert disagreement.startswith("unclaimable-frontier: #11 is the frontier of 'M1'")
    assert "epic-closed" in disagreement


def test_status_never_advertises_the_epic_closed_frontier(tmp_path, capsys):
    snap_path = _write(tmp_path, _dangling_board())
    rc = cli.main(["status", "--stale-minutes", "100000000",
                   *_paths(tmp_path, snap_path)])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert "frontier: #12" in captured.out
    assert "frontier: #11" not in captured.out


def test_status_names_the_claimable_active_epic_frontier_when_the_milestone_is_pooled(
    tmp_path, capsys
):
    """A bare `frontier: <none>` must not read as "nothing to do".

    With an epic focus active, the whole milestone can legitimately be pooled.
    The reader still needs the frontier the fleet IS driving — and it must be
    claimable, so `eligible` is asked, with the SAME focus.
    """
    board = Snapshot(
        generated_at=snapshot_mod.now_iso(),
        source="test",
        issues={
            900: Issue(900, "the active epic", milestone="M1", labels=("type:epic",)),
            902: Issue(902, "pooled, outside the epic", milestone="M1"),
            903: Issue(903, "the epic's next step", milestone="M2", parent=900),
        },
    )
    snap_path = _write(tmp_path, board)
    focus_path = tmp_path / "focus.json"
    focus_path.write_text(
        json.dumps(
            {
                "active_epic": 900,
                "activated_at": "2026-09-13T12:00:00Z",
                "wave_cap": 12,
                "max_agents": 0,
                "pooled": [],
            }
        ),
        encoding="utf-8",
    )
    rc = cli.main(["status", "--stale-minutes", "100000000",
                   "--focus", str(focus_path), *_paths(tmp_path, snap_path)])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert "frontier: <none>" in captured.out
    assert "active epic: #900 the active epic" in captured.out
    assert "active-epic frontier: #903" in captured.out
    # The pooled issue is named as a disagreement, never advertised as the frontier.
    assert "unclaimable-frontier: #902" in captured.err
    assert "frontier: #902" not in captured.out


# --- the liveness verdict: is the declared producer actually installed? --------


def _manifest(path, *, enabled: bool):
    job = {
        "name": "snapshot-refresh",
        "marker": "ao-fleet-snapshot-refresh",
        "refreshes": liveness.BOARD_REFRESH_ROLE,
        "schedule": "*/10 * * * *",
        "command": "/usr/bin/python3 governance/dispatch/cli.py snapshot --from-github",
        "user": "",
        "log": "snapshot-refresh.log",
        "singleton": True,
        "enabled": enabled,
    }
    path.write_text(json.dumps({"schema": "fleet-jobs-v1", "jobs": [job]}), encoding="utf-8")
    return path


def _crontab(path, marker: str | None):
    lines = ["0 1 * * * /bin/true # someone-elses-job"]
    if marker is not None:
        lines.append(f"*/10 * * * * /usr/bin/python3 refresh.py # {marker}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_liveness_reports_an_installed_enabled_producer(tmp_path):
    verdict = liveness.assess(
        manifest_path=_manifest(tmp_path / "on.json", enabled=True),
        crontab_file=_crontab(tmp_path / "cron.txt", "ao-fleet-snapshot-refresh"),
    )
    assert verdict.verdict == liveness.VERDICT_INSTALLED
    assert verdict.ok is True
    assert verdict.finding == ""


def test_liveness_reports_declared_but_not_installed(tmp_path):
    """The gap the module exists for: never a pass."""
    verdict = liveness.assess(
        manifest_path=_manifest(tmp_path / "on.json", enabled=True),
        crontab_file=_crontab(tmp_path / "cron.txt", None),
    )
    assert verdict.verdict == liveness.VERDICT_DECLARED_NOT_INSTALLED
    assert verdict.ok is False
    assert verdict.finding.startswith("declared-but-not-installed: the board-refresh rung")


def test_liveness_reports_a_rung_installed_while_declared_off(tmp_path):
    """The mirror image: the crontab carries a line the declaration says should not be there."""
    verdict = liveness.assess(
        manifest_path=_manifest(tmp_path / "off.json", enabled=False),
        crontab_file=_crontab(tmp_path / "cron.txt", "ao-fleet-snapshot-refresh"),
    )
    assert verdict.verdict == liveness.VERDICT_INSTALLED_BUT_OFF
    assert verdict.finding.startswith("installed-but-declared-off:")


def test_liveness_reports_no_producer_when_none_can_be_named(tmp_path):
    verdict = liveness.assess(
        manifest_path=_manifest(tmp_path / "off.json", enabled=False),
        crontab_file=_crontab(tmp_path / "cron.txt", None),
        self_refresh=False,
    )
    assert verdict.verdict == liveness.VERDICT_NO_PRODUCER
    assert verdict.finding.startswith("no-board-refresher:")


def test_liveness_accepts_an_in_band_refresh_as_the_producer(tmp_path):
    """The negative control for `no-producer`: the same board WITH self-refresh is OK."""
    verdict = liveness.assess(
        manifest_path=_manifest(tmp_path / "off.json", enabled=False),
        crontab_file=_crontab(tmp_path / "cron.txt", None),
    )
    assert verdict.verdict == liveness.VERDICT_SELF_REFRESH
    assert verdict.ok is True


def test_liveness_cannot_assess_an_unreadable_crontab(tmp_path):
    verdict = liveness.assess(
        manifest_path=_manifest(tmp_path / "on.json", enabled=True),
        crontab_file=tmp_path / "absent-crontab.txt",
    )
    assert verdict.verdict == liveness.VERDICT_CANNOT_ASSESS
    assert verdict.assessable is False
    assert verdict.ok is False


def test_liveness_cannot_assess_a_missing_manifest(tmp_path):
    verdict = liveness.assess(
        manifest_path=tmp_path / "absent-manifest.json",
        crontab_file=_crontab(tmp_path / "cron.txt", "ao-fleet-snapshot-refresh"),
    )
    assert verdict.verdict == liveness.VERDICT_CANNOT_ASSESS
    assert "missing" in verdict.detail


def test_liveness_cannot_assess_when_crontab_cannot_be_run(tmp_path):
    """No `crontab` binary (or an unanswerable one) is CANNOT-ASSESS, never 'empty'."""

    def refusing(cmd, **kwargs):
        raise FileNotFoundError("crontab")

    verdict = liveness.assess(
        manifest_path=_manifest(tmp_path / "on.json", enabled=True),
        runner=refusing,
    )
    assert verdict.verdict == liveness.VERDICT_CANNOT_ASSESS
    assert "was not found" in verdict.detail


def test_liveness_treats_no_crontab_for_this_user_as_an_empty_crontab(tmp_path):
    """`no crontab for <user>` is a READABLE, empty answer — not CANNOT-ASSESS."""

    class Proc:
        returncode = 1
        stdout = ""
        stderr = "no crontab for someone"

    verdict = liveness.assess(
        manifest_path=_manifest(tmp_path / "on.json", enabled=True),
        runner=lambda cmd, **kwargs: Proc(),
    )
    assert verdict.verdict == liveness.VERDICT_DECLARED_NOT_INSTALLED


def test_a_marker_mentioned_inside_another_command_is_not_that_jobs_line(tmp_path):
    """The marker must be the line's own trailing tag, not a bare substring."""
    crontab = tmp_path / "cron.txt"
    crontab.write_text(
        "0 1 * * * /bin/echo 'ao-fleet-snapshot-refresh' # someone-else\n", encoding="utf-8"
    )
    verdict = liveness.assess(
        manifest_path=_manifest(tmp_path / "on.json", enabled=True),
        crontab_file=crontab,
    )
    assert verdict.verdict == liveness.VERDICT_DECLARED_NOT_INSTALLED


# --- the claims the fix must NOT have broken ----------------------------------


def test_the_control_fixtures_frontier_is_unchanged_by_the_shared_predicate():
    """#601 is still the CONTROL frontier: the shared refusals do not touch it."""
    snapshot = claims.control_snapshot()
    frontier = order.frontier(snapshot, "CONTROL")
    assert frontier is not None
    assert frontier.number == 601
