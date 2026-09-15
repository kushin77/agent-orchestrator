"""Board triggers: refresh-or-park on a stale snapshot (issue #727).

The defect this suite exists for
-------------------------------
``governance/dispatch/claims.py`` refuses a claim validated against a stale
snapshot with ``snapshot-stale`` and prints the remedy ("refresh first: ...
snapshot --from-github"). Nothing ever ran the remedy, so the refusal re-fired
for the same directive on every watch cycle: a fail-closed refusal is only half
a control. The epic #708 runaway is exactly this — a refusal with a named remedy
and no trigger for it.

What is asserted here
---------------------
* on ``snapshot-stale`` the loop performs **exactly ONE** refresh, inside a
  bounded window, and the refresh is COUNTED here rather than asserted;
* when that refresh does not clear the staleness the directive is **PARKED** in
  the deferred queue, the envelope STAYS in the inbox (a park is not a
  retirement), and ``channel watch`` does not return it;
* freshness returning releases the parked directive with NO operator action —
  and a healthy directive in the same mailbox is dispatched in the same call, so
  "watch returned nothing" cannot read as a working hold;
* the transition is reported ONCE, naming the snapshot's ``generated_at`` and the
  threshold it tripped;
* a refused network is a first-class OUTCOME (reported and parked), never an
  unhandled crash;
* the "exactly one" property CAN FAIL: a scratch copy of the module with the
  park check disabled refreshes a second time, and the control names that
  failure.

Isolation: every test points ``terminal.RUNS``/``channel.INBOX`` at a tmp
directory (the guard and the park derive their root from those), and the board
snapshot lives in ``tmp_path`` too — so no test touches the live fleet's
``.fleet/`` or the committed ``.board/``.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import channel
import terminal

#: The module under test, as a path: the mutant control loads a scratch copy of
#: it, so the tests must not assume the package import is the file on disk.
DISPATCH_DIR = Path(terminal.__file__).resolve().parent.parent / "governance" / "dispatch"
if str(DISPATCH_DIR) not in sys.path:
    sys.path.insert(0, str(DISPATCH_DIR))

import snapshot as board  # noqa: E402

SNAPSHOT_PATH = Path(board.__file__).resolve()

#: A module must never be served from a stale ``.pyc``: a mutant run that
#: imported a cached module would report the ORIGINAL behaviour and look like a
#: control that proved something (measured elsewhere in this repo).
sys.dont_write_bytecode = True

#: A board snapshot old enough to trip the declared 15-minute threshold.
STALE_STAMP = "2020-01-01T00:00:00Z"


# --- helpers -----------------------------------------------------------------


def wire_scratch(tmp_path: Path, monkeypatch) -> Path:
    """Point the loop's guard/holds and the mailbox at ONE scratch fleet dir.

    ``terminal.RUNS`` and ``channel.INBOX`` are the two paths the park derives
    its root from (``RUNS.parent`` beside the run markers, ``INBOX.parent``
    beside the mailbox), so redirecting them redirects the deferred queue too.
    """
    monkeypatch.setattr(terminal, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(channel, "INBOX", tmp_path / "inbox")
    monkeypatch.setattr(channel, "DONE", tmp_path / "done")
    monkeypatch.setattr(terminal, "stream_run_event", lambda *a, **k: None)
    return tmp_path


def plant(directory: Path, directive_id: str, *, ts: str = "2026-09-14T00:00:00Z") -> Path:
    """Put one pending directive envelope in `directory` (the mailbox shape)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{directive_id}.json"
    path.write_text(
        json.dumps(
            {
                "id": directive_id,
                "ts": ts,
                "type": "directive",
                "from": "brain",
                "to": "sister",
                "correlation_id": f"c-{directive_id}",
                "model": {"tier": "flash", "thinking": "none"},
                "task": {"kind": "work", "issue": 727, "lane": "governance"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def write_snapshot(path: Path, generated_at: str, *, source: str = "test") -> Path:
    path.write_text(
        json.dumps({"generated_at": generated_at, "source": source, "issues": []}) + "\n",
        encoding="utf-8",
    )
    return path


def watch(*, snapshot: Path, stale_minutes: int | None = None) -> tuple[int, str]:
    """Run the real ``channel watch`` once; return (exit code, stdout)."""
    captured = io.StringIO()
    with redirect_stdout(captured):
        rc = channel.cmd_watch(
            argparse.Namespace(
                timeout_seconds=0.05,
                interval=0.01,
                skip=[],
                snapshot=str(snapshot),
                stale_minutes=stale_minutes,
            )
        )
    return rc, captured.getvalue()


def refusing_runner(calls: list[list[str]]):
    """A refresh runner that records its call and REFUSES (no network)."""

    def runner(cmd, **kwargs):
        calls.append(list(cmd))
        raise RuntimeError("network unreachable: no route to github")

    return runner


def record_reporting(calls: list[dict]):
    """A ``report_once`` stand-in that records instead of shelling out."""

    def report_once(directive_id, key, message_type, body, severity="warn"):
        calls.append(
            {"directive": directive_id, "key": key, "type": message_type, "body": body}
        )
        return True

    return report_once


def load_mutant(tmp_path: Path, old: str, new: str) -> object:
    """Import a mutated copy of ``snapshot.py`` — the anchor must actually match.

    A control that reports "the probe caught it" while the mutation never applied
    proves nothing, so the anchor is required to be unique and the rewrite is
    asserted to have changed the file before it is imported.
    """
    source = SNAPSHOT_PATH.read_text(encoding="utf-8")
    assert source.count(old) == 1, f"the mutation anchor is not unique: {old!r}"
    mutated = source.replace(old, new)
    assert mutated != source, "the mutation did not change the source"
    target = tmp_path / "mutant" / "snapshot.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(mutated, encoding="utf-8")
    for cache in target.parent.rglob("__pycache__"):
        for cached in sorted(cache.glob("snapshot*.pyc")):
            cached.unlink()
    name = f"snapshot_mutant_{abs(hash((old, new)))}"
    spec = importlib.util.spec_from_file_location(name, target)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    assert Path(module.__file__).resolve() == target.resolve(), (
        "the mutant did not import the scratch copy it wrote"
    )
    return module


#: The mutation the negative control uses: the park check is skipped, so a second
#: pass refreshes the board again instead of holding the parked directive.
PARK_FORGOTTEN = ("    if parked(directive_id, base):", "    if False:")


# --- 1. exactly ONE refresh, then a park --------------------------------------


def test_a_stale_snapshot_is_refreshed_exactly_once_then_parked(tmp_path, monkeypatch):
    """AC1+AC2: one bounded refresh, counted; still stale ⇒ PARKED, not moved."""
    state = wire_scratch(tmp_path, monkeypatch)
    snapshot = write_snapshot(tmp_path / "snapshot.json", STALE_STAMP)
    calls: list[list[str]] = []
    plant(state / "inbox", "d-stale")

    first = board.refresh_or_park(
        "d-stale",
        snapshot_path=snapshot,
        base=state,
        repo="owner/repo",
        runner=refusing_runner(calls),
    )
    assert len(calls) == 1, f"the first stale refusal must refresh exactly once, got {len(calls)}"
    assert first.action == board.ACTION_PARKED, f"a failed refresh must PARK the directive: {first}"
    assert first.refreshed is False, "a refused network must not report a refresh"
    assert board.parked("d-stale", base=state), "the trigger reported a park it did not write"

    marker = board.park_record("d-stale", base=state)
    assert marker is not None
    assert marker["generated_at"] == STALE_STAMP, (
        f"the park must carry the snapshot's generated_at as evidence: {marker}"
    )
    assert marker["threshold_minutes"] == board.DEFAULT_STALENESS_MINUTES, (
        f"the park must carry the threshold it tripped: {marker}"
    )
    assert (state / "inbox" / "d-stale.json").exists(), (
        "a park is a HOLD, not a retirement: the operator's order must stay in the inbox"
    )

    second = board.refresh_or_park(
        "d-stale",
        snapshot_path=snapshot,
        base=state,
        repo="owner/repo",
        runner=refusing_runner(calls),
    )
    assert second.action == board.ACTION_HELD, f"a second pass must hold, not act: {second}"
    assert len(calls) == 1, (
        f"a parked directive was refreshed AGAIN — 'exactly one' is the whole control: {len(calls)}"
    )


# --- 2. the park holds the directive, freshness releases it -------------------


def test_a_parked_directive_is_not_dispatched_and_is_released_when_fresh(
    tmp_path, monkeypatch
):
    """AC2+AC4: parked ⇒ not re-dispatched; fresh ⇒ released with no operator."""
    state = wire_scratch(tmp_path, monkeypatch)
    snapshot = write_snapshot(tmp_path / "snapshot.json", STALE_STAMP)
    plant(state / "inbox", "d-parked", ts="2026-09-14T00:00:00Z")
    plant(state / "inbox", "d-healthy", ts="2026-09-14T00:00:01Z")

    board.refresh_or_park(
        "d-parked",
        snapshot_path=snapshot,
        base=state,
        repo="owner/repo",
        runner=refusing_runner([]),
    )

    rc, out = watch(snapshot=snapshot)
    assert rc == channel.EXIT_OK, f"watch returned {rc} instead of the healthy directive"
    assert '"id": "d-healthy"' in out, (
        f"the non-vacuity half: a healthy directive must still be dispatched: {out!r}"
    )
    assert "d-parked" not in out, "watch re-dispatched a directive the park was holding"

    # Freshness returns: the SAME parked directive is released, no operator step.
    write_snapshot(snapshot, board.now_iso())
    rc, out = watch(snapshot=snapshot, stale_minutes=board.DEFAULT_STALENESS_MINUTES)
    assert rc == channel.EXIT_OK, f"the released directive was not dispatched (rc={rc})"
    assert '"id": "d-parked"' in out, f"watch did not return the released directive: {out!r}"
    assert not board.parked("d-parked", base=state), (
        "watch dispatched the released directive but left its park behind"
    )


# --- 3. the refresh that clears the staleness does not park -------------------


def test_the_one_refresh_clears_the_staleness_when_the_board_is_reachable(tmp_path, monkeypatch):
    """AC1: when the refresh succeeds the directive is dispatched, not parked."""
    state = wire_scratch(tmp_path, monkeypatch)
    snapshot = write_snapshot(tmp_path / "snapshot.json", STALE_STAMP)
    calls: list[list[str]] = []

    def reachable(cmd, **kwargs):
        calls.append(list(cmd))
        return type("Done", (), {"returncode": 0, "stdout": "[]", "stderr": ""})()

    trigger = board.refresh_or_park(
        "d-stale", snapshot_path=snapshot, base=state, repo="owner/repo", runner=reachable
    )
    assert trigger.action == board.ACTION_REFRESHED, f"a successful refresh must not park: {trigger}"
    assert trigger.refreshed is True
    assert len(calls) == 1, f"a successful refresh must still be exactly one: {len(calls)}"
    assert not board.parked("d-stale", base=state), "a cleared staleness must not leave a park"
    assert not board.is_stale(board.load(snapshot), board.DEFAULT_STALENESS_MINUTES), (
        "the refresh left the snapshot stale — it did not write a usable board state"
    )
    assert board.freshness_restored(snapshot, board.DEFAULT_STALENESS_MINUTES)


# --- 4. a refused network is a first-class outcome ----------------------------


def test_a_refused_network_is_reported_and_repo_agnostic(tmp_path, monkeypatch):
    """The refresh shells out to ``gh``; a refusal must park, never crash."""
    state = wire_scratch(tmp_path, monkeypatch)
    snapshot = write_snapshot(tmp_path / "snapshot.json", STALE_STAMP)
    marker = state / "no-network-marker"

    def runner(cmd, **kwargs):
        marker.write_text("called\n", encoding="utf-8")
        raise RuntimeError("gh issue list failed (1): network is unreachable")

    trigger = board.refresh_or_park(
        "d-offline", snapshot_path=snapshot, base=state, repo="owner/repo", runner=runner
    )
    assert marker.exists(), "the trigger never attempted the refresh"
    assert trigger.action == board.ACTION_PARKED
    assert "network" in trigger.reason, f"the reason must name the refusal: {trigger.reason!r}"


# --- 5. the transition is reported once, with the evidence -------------------


def test_terminal_reports_the_transition_once_with_generated_at_and_threshold(
    tmp_path, monkeypatch
):
    """AC3: the report names the snapshot's generated_at AND the threshold."""
    state = wire_scratch(tmp_path, monkeypatch)
    snapshot = write_snapshot(tmp_path / "snapshot.json", STALE_STAMP)
    reports: list[dict] = []
    monkeypatch.setattr(terminal, "report_once", record_reporting(reports))

    trigger = terminal.board_trigger(
        "d-reported",
        727,
        runner=refusing_runner([]),
        snapshot_path=str(snapshot),
    )
    assert trigger.action == board.ACTION_PARKED
    assert len(reports) == 1, f"the transition must be reported exactly once: {reports}"
    body = reports[0]["body"]
    assert STALE_STAMP in body, f"the report must name the snapshot's generated_at: {body!r}"
    assert f"threshold {board.DEFAULT_STALENESS_MINUTES}m" in body, (
        f"the report must name the threshold it tripped: {body!r}"
    )
    assert reports[0]["directive"] == "d-reported", (
        "the report must be keyed to the directive that hit the stale board"
    )
    assert reports[0]["key"] == f"board-trigger:{board.ACTION_PARKED}"


# --- 6. the CLI verb (the contract's first-class entry point) ----------------


def test_the_trigger_verb_parks_through_the_cli(tmp_path, monkeypatch, capsys):
    """The contract is invocable: `dispatch trigger` refreshes once, else parks."""
    import cli as dispatch_cli

    state = wire_scratch(tmp_path, monkeypatch)
    snapshot = write_snapshot(tmp_path / "snapshot.json", STALE_STAMP)

    def unreachable(*args, **kwargs):
        raise RuntimeError("network is unreachable")

    monkeypatch.setattr(board, "github_records", unreachable)
    rc = dispatch_cli.main(
        [
            "trigger",
            "--directive",
            "d-cli",
            "--snapshot",
            str(snapshot),
            "--fleet-dir",
            str(state),
            "--repo",
            "owner/repo",
        ]
    )
    captured = capsys.readouterr()
    assert rc == dispatch_cli.EXIT_NOT_OK, f"the verb must report the hold, got {rc}"
    assert '"action": "parked"' in captured.out, f"the verb did not park: {captured.out!r}"
    assert board.parked("d-cli", base=state), "the verb reported a park it did not write"


# --- 7. the negative control: "exactly one" CAN fail ------------------------


def test_the_exactly_one_refresh_control_fails_when_the_park_check_is_removed(
    tmp_path, monkeypatch
):
    """A control that cannot fail is a formality — this one is PROVED to fail.

    The real module refreshes once and then holds. A scratch copy whose park
    check is skipped refreshes a second time, which is exactly the per-cycle
    re-dispatch the issue exists to end: the probe below must catch it, or the
    "exactly one refresh" assertion above proves nothing.
    """
    state = wire_scratch(tmp_path, monkeypatch)
    snapshot = write_snapshot(tmp_path / "snapshot.json", STALE_STAMP)

    real_calls: list[list[str]] = []
    board.refresh_or_park(
        "d-real", snapshot_path=snapshot, base=state, repo="owner/repo", runner=refusing_runner(real_calls)
    )
    board.refresh_or_park(
        "d-real", snapshot_path=snapshot, base=state, repo="owner/repo", runner=refusing_runner(real_calls)
    )
    assert len(real_calls) == 1, f"the real module refreshed {len(real_calls)} times"

    mutant = load_mutant(tmp_path, *PARK_FORGOTTEN)
    mutant_calls: list[list[str]] = []
    mutant.refresh_or_park(
        "d-mutant", snapshot_path=snapshot, base=state, repo="owner/repo", runner=refusing_runner(mutant_calls)
    )
    mutant.refresh_or_park(
        "d-mutant", snapshot_path=snapshot, base=state, repo="owner/repo", runner=refusing_runner(mutant_calls)
    )
    assert len(mutant_calls) == 2, (
        "the mutant did not reproduce the runaway — the mutation never applied, so this "
        f"control proves nothing (calls={len(mutant_calls)})"
    )


def test_the_trigger_actions_are_a_closed_vocabulary():
    """The consumer reads an action, not a free-form string (contract surface)."""
    assert set(board.TRIGGER_ACTIONS) == {
        board.ACTION_FRESH,
        board.ACTION_REFRESHED,
        board.ACTION_PARKED,
        board.ACTION_HELD,
        board.ACTION_RESUMED,
    }
    assert set(board.HELD_ACTIONS) == {board.ACTION_PARKED, board.ACTION_HELD}
    assert board.StaleTrigger("d", board.ACTION_FRESH).dispatchable is True
    assert board.StaleTrigger("d", board.ACTION_PARKED).dispatchable is False
    assert board.StaleTrigger("d", board.ACTION_HELD).dispatchable is False
    assert board.StaleTrigger("d", board.ACTION_RESUMED).dispatchable is True
    assert board.TRIGGER_WINDOW_SECONDS > 0, "the refresh must have a bounded window"
    assert board.PARKED_DIRNAME in str(board.park_dir()), "the deferred queue must be declared"
