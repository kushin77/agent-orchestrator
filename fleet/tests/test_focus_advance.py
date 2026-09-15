"""The epic-close ADVANCE (epic #707 lane F5 / issue #720).

The brain could decompose the ACTIVE epic into micro-children (#719, lane F4);
what it could not do was leave one. A pinned focus outlives the epic it pins —
``focus.resolve`` falls back to the lowest workable epic only when the pinned one
has closed — so nothing moved the pin, and a fleet that finished #707 would keep
resolving #707 forever.

Two layers are proved here, and they are deliberately separate:

* the **rule** (``brain.advance_focus``) — pure and offline: a snapshot and a
  focus in, the next focus (or ``None``) out. No clock, no network, no filesystem,
  so every branch is exercised directly and the board-complete conjunction can be
  asserted rather than described.
* the **seam** (``brain.advance_epic_focus``) — that it reads the committed board,
  writes the new pin, and *reports* completion instead of silently doing nothing.

The board is a real ``.board/snapshot.json`` + ``.board/focus.json`` pair written
into ``tmp_path`` and wired in through ``brain.BOARD_PATH`` / ``brain.FOCUS_PATH``,
so the resolution runs for real instead of being stubbed into agreement with the
test — the same discipline ``test_decompose_policy.py`` uses.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import brain
import focus as focus_mod

Issue = brain.snapshot_mod.Issue
Snapshot = brain.snapshot_mod.Snapshot


def board(*issues: Issue) -> Snapshot:
    """A committed board built from the given issues."""
    return Snapshot(
        generated_at="2026-09-14T00:00:00Z",
        source="test",
        issues={issue.number: issue for issue in issues},
    )


def epic(number: int, *, closed: bool = False, blocked_by: tuple[int, ...] = ()) -> Issue:
    return Issue(number, f"epic {number}", state="closed" if closed else "open",
                 labels=("type:epic",), blocked_by=blocked_by)


def task(number: int, *, parent: int | None = None, closed: bool = False) -> Issue:
    return Issue(number, f"task {number}", state="closed" if closed else "open", parent=parent)


def pinned(number: int | None) -> focus_mod.Focus:
    return focus_mod.Focus.pinned(number)


# --- rule 1: nothing pinned ---------------------------------------------------


def test_with_no_focus_a_workable_epic_is_pinned():
    """Nothing pinned is not the same as nothing left: resolve from scratch."""
    board_ = board(epic(707), task(1), task(2, parent=707))
    assert brain.advance_focus(board_, None).active_epic == 707


def test_with_no_focus_and_a_pool_but_no_epic_the_focus_holds_no_epic():
    """The pool is work; a completed epic board with out-of-epic work is not done."""
    board_ = board(task(9))
    advanced = brain.advance_focus(board_, None)
    assert advanced is not None and advanced.active_epic is None


def test_with_no_focus_no_epic_and_an_empty_pool_the_board_is_complete():
    """The conjunction: the resolver returns None AND the pool is empty."""
    assert brain.advance_focus(board(task(9, closed=True)), None) is None
    assert brain.advance_focus(board(), None) is None


# --- rule 2: the pinned epic is still open ------------------------------------


def test_an_open_focused_epic_does_not_move() -> None:
    """The focus advances on a CLOSURE, never on a tick."""
    focus = pinned(707)
    board_ = board(epic(707), epic(800), task(1, parent=707))
    assert brain.advance_focus(board_, focus) is focus


def test_an_open_focused_epic_with_no_open_children_still_does_not_move():
    """The measured trap: an empty child set is a legitimate momentary state.

    Lane F4's seam was tested exactly there (`issue_is_closed -> False`, no children
    yet filed); a rule that advanced whenever `children_of(epic)` was empty would
    drop a live epic mid-flight.
    """
    focus = pinned(707)
    board_ = board(epic(707), epic(800))
    assert brain.advance_focus(board_, focus) is focus


# --- rule 3: the pinned epic is gone ------------------------------------------


def test_a_closed_focused_epic_advances_to_the_next_workable_epic():
    focus = pinned(707)
    board_ = board(epic(707, closed=True), epic(800))
    advanced = brain.advance_focus(board_, focus)
    assert advanced is not None and advanced.active_epic == 800


def test_the_closing_epic_is_never_re_pinned():
    """A snapshot read before the refresh still shows the closed epic open.

    The advance must exclude the epic it is advancing off, or it re-pins what just
    closed and the fleet never moves — the loop this rule exists to break.
    """
    focus = pinned(707)
    # 707 is still OPEN in this snapshot (stale read) and is also the lowest epic.
    board_ = board(epic(707), epic(800))
    advanced = brain.advance_focus(board_, focus)
    assert advanced is focus, "a still-open pin is left alone, never re-pinned to itself"

    board_ = board(epic(707, closed=True), epic(800))
    assert brain.advance_focus(board_, focus).active_epic == 800


def test_the_lowest_numbered_workable_epic_wins_and_blocked_epics_are_skipped():
    focus = pinned(707)
    board_ = board(
        epic(707, closed=True),
        epic(750, blocked_by=(751,)),
        task(751),
        epic(760),
        epic(800),
    )
    advanced = brain.advance_focus(board_, focus)
    assert advanced is not None and advanced.active_epic == 760


def test_no_next_epic_but_a_waiting_pool_keeps_the_pool():
    """A pooled issue is never silently dropped (#718/#720)."""
    focus = pinned(707)
    advanced = brain.advance_focus(board(epic(707, closed=True), task(9)), focus)
    assert advanced is not None
    assert advanced.active_epic is None
    assert advanced.pooled == (9,)


def test_no_next_epic_and_an_empty_pool_is_board_complete():
    assert brain.advance_focus(board(epic(707, closed=True)), pinned(707)) is None


def test_the_advance_is_pure():
    """The rule must not read a clock, a file or the network — it is given both halves."""
    focus = pinned(707)
    board_ = board(epic(707, closed=True), epic(800), task(9))
    assert brain.advance_focus(board_, focus) == brain.advance_focus(board_, focus)
    assert brain.advance_focus(board_, None) == brain.advance_focus(board_, None)
    # ...and the focus it was handed is untouched.
    assert focus.active_epic == 707


def test_the_wave_budget_rides_along_when_an_epic_is_replaced():
    """The pin carries the budget the open wave is already sized against."""
    focus = focus_mod.Focus(active_epic=707, activated_at="2026-09-14T00:00:00Z", wave_cap=5, max_agents=3)
    advanced = brain.advance_focus(board(epic(707, closed=True), epic(800)), focus)
    assert advanced is not None and (advanced.wave_cap, advanced.max_agents) == (focus_mod.DEFAULT_WAVE_CAP,
                                                                               focus_mod.DEFAULT_MAX_AGENTS)


# --- the seam: the pin is written, completion is reported ---------------------


@pytest.fixture
def wired(tmp_path: Path, monkeypatch):
    """Point the brain's board and focus at a scratch pair; return both paths."""
    board_path = tmp_path / "snapshot.json"
    focus_path = tmp_path / "focus.json"
    monkeypatch.setattr(brain, "BOARD_PATH", board_path)
    monkeypatch.setattr(brain, "FOCUS_PATH", focus_path)
    return board_path, focus_path


def write_board(board_path: Path, snapshot: Snapshot) -> None:
    brain.snapshot_mod.save(snapshot, board_path)


def write_focus(focus_path: Path, focus: focus_mod.Focus) -> None:
    focus_mod.save(focus, focus_path)


def test_the_seam_writes_the_next_pin(wired):
    board_path, focus_path = wired
    write_board(board_path, board(epic(707, closed=True), epic(800)))
    write_focus(focus_path, pinned(707))
    ok, note = brain.advance_epic_focus()
    assert ok is True and "advanced the focus from #707 to #800" in note
    assert json.loads(focus_path.read_text(encoding="utf-8"))["active_epic"] == 800


def test_the_seam_reports_board_complete_without_rewriting_the_pin(wired):
    board_path, focus_path = wired
    write_board(board_path, board(epic(707, closed=True)))
    write_focus(focus_path, pinned(707))
    before = focus_path.read_text(encoding="utf-8")
    ok, note = brain.advance_epic_focus()
    assert ok is True and brain.BOARD_COMPLETE in note
    assert focus_path.read_text(encoding="utf-8") == before, "a completed board is reported, not rewritten"


def test_the_seam_leaves_an_open_focus_alone(wired):
    board_path, focus_path = wired
    write_board(board_path, board(epic(707), task(1, parent=707)))
    write_focus(focus_path, pinned(707))
    ok, note = brain.advance_epic_focus()
    assert ok is True and "has not moved" in note
    assert json.loads(focus_path.read_text(encoding="utf-8"))["active_epic"] == 707


def test_the_seam_refuses_an_unreadable_board_instead_of_advancing(wired):
    board_path, focus_path = wired
    board_path.write_text("{ this is not json", encoding="utf-8")
    write_focus(focus_path, pinned(707))
    ok, note = brain.advance_epic_focus()
    assert ok is False and "unreadable" in note


def test_the_seam_never_defaults_a_malformed_focus(wired):
    """A corrupt focus is REFUSED, never read as "nothing pinned" (#716)."""
    board_path, focus_path = wired
    write_board(board_path, board(epic(707), epic(800)))
    focus_path.write_text('{"active_epic": 707}\n', encoding="utf-8")
    ok, note = brain.advance_epic_focus()
    assert ok is False and "unreadable" in note


def test_board_complete_needs_both_halves(wired):
    """The conjunction, at the seam: no epic but a live pool is NOT completion."""
    board_path, focus_path = wired
    write_board(board_path, board(epic(707, closed=True), task(9)))
    write_focus(focus_path, pinned(707))
    ok, note = brain.advance_epic_focus()
    assert ok is True and brain.BOARD_COMPLETE not in note
    written = json.loads(focus_path.read_text(encoding="utf-8"))
    assert written["active_epic"] is None and written["pooled"] == [9]
