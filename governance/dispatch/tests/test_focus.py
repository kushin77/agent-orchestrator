"""Active-epic resolver + focus schema (epic #707, lane F1 / issue #716)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

import focus
from model import Issue, Snapshot


def board(*issues: Issue) -> Snapshot:
    return Snapshot(generated_at="2026-09-14T00:00:00Z", source="test", issues={i.number: i for i in issues})


VALID = {
    "active_epic": 900,
    "activated_at": "2026-09-14T00:00:00Z",
    "wave_cap": 12,
    "max_agents": 0,
    "pooled": [],
}


def test_resolves_the_lowest_open_unblocked_epic():
    snap = board(
        Issue(900, "epic a", labels=("type:epic",)),
        Issue(901, "epic b", labels=("type:epic",)),
        Issue(910, "work"),
    )
    assert focus.resolve(snap).number == 900


def test_pinned_open_epic_wins_over_a_lower_one():
    snap = board(
        Issue(900, "epic a", labels=("type:epic",)),
        Issue(901, "epic b", labels=("type:epic",)),
    )
    assert focus.resolve(snap, pinned=901).number == 901


def test_pinned_closed_epic_falls_back_to_the_next_workable_epic():
    snap = board(
        Issue(900, "epic a", labels=("type:epic",)),
        Issue(902, "epic closed", state="closed", labels=("type:epic",)),
    )
    assert focus.resolve(snap, pinned=902).number == 900


def test_epic_with_an_open_blocker_is_never_selected():
    snap = board(
        Issue(901, "epic blocked", labels=("type:epic",), blocked_by=(910,)),
        Issue(910, "the blocker"),
        Issue(903, "epic workable", labels=("type:epic",)),
    )
    assert focus.resolve(snap).number == 903


def test_no_open_epic_returns_none():
    snap = board(Issue(910, "work"), Issue(902, "closed epic", state="closed", labels=("type:epic",)))
    assert focus.resolve(snap) is None


def test_a_pinned_non_epic_id_is_never_returned():
    snap = board(Issue(900, "epic a", labels=("type:epic",)), Issue(910, "work"))
    assert focus.resolve(snap, pinned=910).number == 900


def test_open_children_excludes_closed_children():
    snap = board(
        Issue(900, "epic a", labels=("type:epic",)),
        Issue(9001, "child open", parent=900),
        Issue(9002, "child closed", state="closed", parent=900),
    )
    assert [i.number for i in focus.open_children(snap, 900)] == [9001]


def test_pooled_excludes_the_epic_its_children_and_other_epics():
    snap = board(
        Issue(900, "epic a", labels=("type:epic",)),
        Issue(901, "epic b", labels=("type:epic",)),
        Issue(9001, "child", parent=900),
        Issue(9101, "out of epic work"),
    )
    assert [i.number for i in focus.pooled(snap, 900)] == [9101]


def test_pooled_with_no_epic_is_every_non_epic_open_issue():
    snap = board(Issue(900, "epic a", labels=("type:epic",)), Issue(910, "work"))
    assert [i.number for i in focus.pooled(snap, None)] == [910]


def test_load_returns_none_when_the_file_is_absent(tmp_path):
    assert focus.load(tmp_path / "focus.json") is None


def test_load_required_raises_when_the_file_is_absent(tmp_path):
    with pytest.raises(focus.FocusInvalid):
        focus.load(tmp_path / "focus.json", required=True)


def test_load_rejects_a_malformed_file(tmp_path):
    target = tmp_path / "focus.json"
    target.write_text(json.dumps({**VALID, "wave_cap": 0}), encoding="utf-8")
    with pytest.raises(focus.FocusInvalid):
        focus.load(target)


def test_save_then_load_round_trips(tmp_path):
    target = tmp_path / "focus.json"
    focus.save(focus.Focus.pinned(707), target)
    loaded = focus.load(target)
    assert loaded is not None and loaded.active_epic == 707


def test_schema_rejects_a_bool_wave_cap():
    problems = focus.validate_schema({**VALID, "wave_cap": True})
    assert problems and "wave_cap" in problems[0]


def test_self_control_is_clean():
    assert focus.self_control() == []


# --- stale committed focus self-heal (issue #1717) -------------------------


def _write(path, active_epic, activated_at):
    focus.save(focus.Focus(active_epic=active_epic, activated_at=activated_at), path)


def test_stale_and_refreshable_uses_the_refreshed_epic(tmp_path, monkeypatch):
    target = tmp_path / "focus.json"
    _write(target, 706, "2026-09-01T00:00:00Z")  # >72h old
    snap = board(
        Issue(706, "epic old", state="closed", labels=("type:epic",)),
        Issue(1510, "epic live", labels=("type:epic",)),
    )

    def fake_refresh(path=None):
        _write(target, 1510, "2026-09-21T00:00:00Z")
        return True, "refreshed"

    monkeypatch.setattr(focus, "_refresh", fake_refresh)
    ok, detail = focus.self_heal(snap, target)
    assert ok and detail
    assert focus.active(snap, target).number == 1510


def test_stale_and_unrefreshable_fails_closed_by_name(tmp_path, monkeypatch):
    target = tmp_path / "focus.json"
    _write(target, 706, "2026-09-01T00:00:00Z")
    snap = board(Issue(706, "epic old", state="closed", labels=("type:epic",)))

    def fake_refresh(path=None):
        return False, "offline: no gh auth"

    monkeypatch.setattr(focus, "_refresh", fake_refresh)
    ok, detail = focus.self_heal(snap, target)
    assert not ok
    assert "CANNOT-ASSESS" not in detail  # self_heal names the failure; the CLI adds the verdict word
    assert "stale" in detail and "706" in detail


def test_fresh_focus_is_left_untouched(tmp_path, monkeypatch):
    target = tmp_path / "focus.json"
    _write(target, 900, "2026-09-14T00:00:00Z")
    snap = board(Issue(900, "epic a", labels=("type:epic",)))

    def boom(path=None):
        raise AssertionError("refresh must not be called for a fresh focus")

    monkeypatch.setattr(focus, "_refresh", boom)
    ok, detail = focus.self_heal(snap, target, now=datetime(2026, 9, 14, 1, 0, 0, tzinfo=timezone.utc))
    assert ok and detail == ""
    assert focus.active(snap, target).number == 900
