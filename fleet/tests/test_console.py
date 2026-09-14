"""The live console: what the operator actually sees in the dashboard window.

The dashboard exists because the three rungs run detached and their state was
spread over five commands. These tests pin the two things that make it worth
having: every section carries the fact it claims to (pid/state/beat, order body,
claim holder, wave glyph, event, watchdog verdict), and the frame is built by
pure functions, so it can be asserted without a TTY — or a fleet.
"""

from __future__ import annotations

import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import console  # noqa: E402


def _snap(**overrides):
    snap = {
        "repo": "kushin77/agent-orchestrator",
        "head": "8d9c219",
        "now": "2026-09-13T21:49:12Z",
        "uptime": "up 3m",
        "rungs": {
            "brain": {"pid": 42, "state": "idle", "commit": "8d9c219", "beat_age": 3},
            "sister": {"pid": 43, "state": "working", "commit": "8d9c219", "beat_age": 1},
            "monitor": {"pid": 44, "state": "alive", "commit": "-", "beat_age": 2},
        },
        "orders": {"pending": 1, "latest": {"id": "o-1", "task": {"issue": 5}, "body": "dispatch one subagent"}},
        "dispatches": [{"type": "ack", "correlation_id": "o-1", "body": "dispatched #5 to the sister at flash/none"}],
        "claims": ["#263 held by copilot-brain (governance-isolation) since 2026-09-13T21:48:53Z"],
        "waves": [{"parent": 219, "children": [{"issue": 232}, {"issue": 233}], "dispatched": [233]}],
        "closed": [232],
        "events": [
            {"ts": "2026-09-13T21:49:13Z", "from": "brain", "to": "sister", "type": "directive", "body": "micro-task"}
        ],
        "watchdog": ["[watchdog] brain: healthy", "[watchdog] monitor: healthy"],
    }
    snap.update(overrides)
    return snap


# --- readers ----------------------------------------------------------------


def test_tail_lines_returns_the_last_lines_in_order(tmp_path):
    path = tmp_path / "slog.jsonl"
    path.write_text("".join(f"line {i}\n" for i in range(500)), encoding="utf-8")
    assert console.tail_lines(path, 3) == ["line 497", "line 498", "line 499"]


def test_tail_lines_drops_the_partial_first_line_of_a_mid_file_read(tmp_path):
    """A bounded read from the end starts mid-record; that fragment is not an event."""
    path = tmp_path / "slog.jsonl"
    path.write_text("x" * 200 + "\nline one\nline two\n", encoding="utf-8")
    assert console.tail_lines(path, 2, max_bytes=20) == ["line one", "line two"]


def test_tail_lines_is_empty_for_a_missing_file(tmp_path):
    assert console.tail_lines(tmp_path / "absent.log", 5) == []


def test_newest_json_orders_by_timestamp_not_by_filename(tmp_path, monkeypatch):
    """Message ids are uuid4, so a filename sort shows a stale order as current."""
    for name, stamp in (("zzz.json", "2026-09-13T00:00:01Z"), ("aaa.json", "2026-09-13T00:00:09Z")):
        (tmp_path / name).write_text(f'{{"id": "{name}", "ts": "{stamp}"}}', encoding="utf-8")
    assert [m["id"] for m in console.newest_json(tmp_path, 1)] == ["aaa.json"]


def test_newest_json_skips_unreadable_messages(tmp_path):
    (tmp_path / "ok.json").write_text('{"id": "ok"}', encoding="utf-8")
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    assert [m["id"] for m in console.newest_json(tmp_path, 5)] == ["ok"]


def test_wave_plans_reads_the_plan_files_lowest_parent_first(tmp_path, monkeypatch):
    monkeypatch.setattr(console, "FLEET_DIR", tmp_path)
    (tmp_path / "waves").mkdir()
    (tmp_path / "waves" / "240.json").write_text('{"parent": 240, "children": []}', encoding="utf-8")
    (tmp_path / "waves" / "219.json").write_text('{"parent": 219, "children": []}', encoding="utf-8")
    (tmp_path / "waves" / "junk.json").write_text("[]", encoding="utf-8")
    assert [plan["parent"] for plan in console.wave_plans()] == [219, 240]


# --- closed-issue state: cached, and never fatal ----------------------------


def _fresh_cache(monkeypatch):
    monkeypatch.setattr(console, "_CLOSED_CACHE", {"at": 0.0, "issues": frozenset()})


def test_closed_issues_is_empty_when_gh_is_unusable(monkeypatch):
    _fresh_cache(monkeypatch)

    def boom():
        raise RuntimeError("gh: not authenticated")

    monkeypatch.setattr(console, "gh_closed_issues", boom)
    assert console.closed_issues() == set(), "an offline gh must not blank the dashboard"


def test_closed_issues_is_fetched_once_per_ttl(monkeypatch):
    _fresh_cache(monkeypatch)
    calls = []
    monkeypatch.setattr(console, "gh_closed_issues", lambda: calls.append(1) or {7, 8})
    assert console.closed_issues(moment=100.0) == {7, 8}
    assert console.closed_issues(moment=101.0) == {7, 8}
    assert len(calls) == 1, "the dashboard redraws every few seconds; gh must not be in that loop"
    assert console.closed_issues(force=True, moment=101.0) == {7, 8}
    assert len(calls) == 2


# --- rendering --------------------------------------------------------------


def test_truncate_collapses_whitespace_and_ellipsises():
    assert console.truncate("a\n  b\tc") == "a b c"
    shortened = console.truncate("y" * 50, 10)
    assert len(shortened) == 10 and shortened.endswith("\u2026")


def test_wave_line_glyphs_mark_closed_dispatched_and_pending():
    plan = {"parent": 219, "children": [{"issue": 232}, {"issue": 233}, {"issue": 234}], "dispatched": [233]}
    line = console.wave_line(plan, {232})
    assert line.startswith("  #219  ")
    assert f"#232 {console.GLYPH_CLOSED}" in line
    assert f"#233 {console.GLYPH_DISPATCHED}" in line
    assert f"#234 {console.GLYPH_PENDING}" in line


def test_rungs_section_shows_pid_state_commit_and_beat_age():
    body = console.rungs_section(_snap()["rungs"])
    for name in ("brain", "sister", "monitor"):
        assert name in body
    assert "pid 42" in body and "idle" in body and "commit 8d9c219" in body and "beat 3s ago" in body


def test_rungs_section_reports_a_rung_with_no_pid_and_no_beat():
    body = console.rungs_section({"brain": {"pid": None, "state": "down", "commit": "-", "beat_age": None}})
    assert "down" in body and "no beat" in body


def test_orders_section_shows_the_pending_count_and_the_latest_order():
    body = console.orders_section(_snap()["orders"])
    assert "pending: 1" in body and "o-1" in body
    assert '"issue": 5' in body and "dispatch one subagent" in body
    assert "(no operator order yet)" in console.orders_section({"pending": 0, "latest": None})


def test_dispatches_and_claims_fall_back_when_empty():
    assert "(no brain acks yet)" in console.dispatches_section([])
    assert "(none)" in console.claims_section([])
    assert "#263 held by copilot-brain" in console.claims_section(_snap()["claims"])


def test_waves_section_renders_every_plan_and_falls_back_when_empty():
    body = console.waves_section(_snap()["waves"], {232})
    assert "#219" in body and f"#232 {console.GLYPH_CLOSED}" in body
    assert "(no wave plan)" in console.waves_section([], set())


def test_events_section_renders_the_last_events_truncated():
    body = console.events_section(
        [{"ts": "2026-09-13T21:49:13Z", "from": "brain", "to": "sister", "type": "directive", "body": "z" * 200}]
    )
    assert "21:49:13" in body and "brain→sister" in body and "directive" in body
    assert "z" * 200 not in body, "a long body must not blow the frame apart"
    assert "(no events yet)" in console.events_section([])


def test_watchdog_section_shows_the_passes_and_the_empty_fallback():
    assert "[watchdog] brain: healthy" in console.watchdog_section(_snap()["watchdog"])
    assert "(no pass yet)" in console.watchdog_section([])


def test_render_carries_every_section_and_the_header_facts():
    frame = console.render(_snap())
    for title in ("RUNGS", "ORDERS", "DISPATCHES", "LIVE CLAIMS", "WAVES", "RECENT EVENTS", "WATCHDOG"):
        assert f"── {title} " in frame, f"the {title} section is missing"
    assert "kushin77/agent-orchestrator" in frame
    assert "HEAD 8d9c219" in frame and "2026-09-13T21:49:12Z" in frame
    assert "tmux attach -t fleet" in frame, "the frame must say how to get here"


# --- the frame the operator's dashboard window runs -------------------------


def test_snapshot_carries_every_key_and_survives_an_empty_fleet(monkeypatch):
    monkeypatch.setattr(console, "loop_pid", lambda pattern: None)
    monkeypatch.setattr(console, "claims_snapshot", lambda: [])
    monkeypatch.setattr(console, "closed_issues", lambda **kwargs: set())
    snap = console.snapshot()
    assert set(snap) == {
        "repo", "head", "now", "uptime", "rungs", "orders", "dispatches",
        "claims", "waves", "closed", "events", "watchdog",
    }
    assert set(snap["rungs"]) == {"brain", "sister", "monitor"}
    assert snap["orders"]["pending"] == 0
    assert snap["waves"] == [] and snap["claims"] == [] and snap["closed"] == []
    assert console.render(snap)


def test_rungs_snapshot_tells_a_dead_rung_from_an_unbeating_one(monkeypatch):
    """"No process" and "a process that never wrote a heartbeat" need different fixes."""
    monkeypatch.setattr(console, "loop_pid", lambda pattern: None)
    dead = console.rungs_snapshot()
    assert dead["brain"]["state"] == "down" and dead["brain"]["pid"] is None
    assert dead["brain"]["beat_age"] is None

    monkeypatch.setattr(console, "loop_pid", lambda pattern: 42)
    unbeating = console.rungs_snapshot()
    assert unbeating["brain"]["state"] == "no-heartbeat" and unbeating["brain"]["pid"] == 42


def test_fit_width_never_lets_the_frame_wrap(tmp_path, monkeypatch):
    monkeypatch.setattr(console, "WIDTH", 96)
    monkeypatch.setattr(console.shutil, "get_terminal_size", lambda fallback=None: type("S", (), {"columns": 78})())
    assert console.fit_width() == 78
    monkeypatch.setattr(console.shutil, "get_terminal_size", lambda fallback=None: type("S", (), {"columns": 10})())
    assert console.fit_width() == 60, "below 60 columns the sections stop being readable"
    monkeypatch.setattr(console, "WIDTH", 96)


def test_main_once_prints_exactly_one_frame(monkeypatch, capsys):
    monkeypatch.setattr(console, "snapshot", lambda: _snap())
    assert console.main(["--once"]) == 0
    out = capsys.readouterr().out
    assert "fleet session" in out and "── RUNGS " in out
    assert "\033[2J" not in out, "--once is the non-interactive frame; it does not clear a screen"


def test_refresh_loop_redraws_without_flicker_and_stops_cleanly(monkeypatch, capsys):
    frames = {"n": 0}

    def counting_snapshot():
        frames["n"] += 1
        if frames["n"] >= 2:
            console._stop = True
        return _snap()

    monkeypatch.setattr(console, "_stop", False)
    monkeypatch.setattr(console, "snapshot", counting_snapshot)
    assert console.refresh_loop(interval=1) == 0
    out = capsys.readouterr().out
    assert out.count("\033[H") == 2, "one home sequence before each frame"
    assert "\033[?1049h" in out and "\033[?1049l" in out, "alternate screen entered and left"
    assert "\033[?25l" in out and "\033[?25h" in out, "cursor hidden during redraw and restored"


def test_the_stop_handler_reacts_to_ctrl_c_and_sigterm(monkeypatch):
    for signum in (signal.SIGINT, signal.SIGTERM):
        monkeypatch.setattr(console, "_stop", False)
        console._request_stop(signum, None)
        assert console._stop is True


# --- the status summary + colour --------------------------------------------


def test_fleet_status_is_the_worst_of_the_rungs():
    assert console.fleet_status({"a": {"state": "healthy"}, "b": {"state": "working"}}) == "healthy"
    assert console.fleet_status({"a": {"state": "healthy"}, "b": {"state": "stale"}}) == "degraded"
    assert console.fleet_status({"a": {"state": "healthy"}, "b": {"state": "down"}}) == "failing"
    assert console.fleet_status({}) == "healthy"


def test_status_line_summarizes_the_fleet():
    line = console.status_line(_snap())
    assert "fleet: healthy" in line
    assert "3 rungs" in line
    assert "1 claims" in line
    assert "wave #219 2/2 done" in line  # 232 closed + 233 dispatched


def test_state_color_maps_the_four_health_bands():
    assert console.state_color("healthy") == "green"
    assert console.state_color("degraded") == "yellow"
    assert console.state_color("failing") == "red"
    assert console.state_color("mystery") == "dim"


def test_paint_is_a_no_op_until_color_is_enabled(monkeypatch):
    monkeypatch.setattr(console, "_COLOR", False)
    assert console.paint("healthy", "green") == "healthy"
    console.enable_color(True)
    assert console.paint("healthy", "green") == "\033[32mhealthy\033[0m"
    console.enable_color(False)
    assert console.paint("healthy", "green") == "healthy"


def test_render_emits_no_ansi_by_default(monkeypatch):
    monkeypatch.setattr(console, "_COLOR", False)
    assert "\033[" not in console.render(_snap())


def test_claims_section_bounds_a_long_claim_list():
    claims = [f"#1 held by a{i}" for i in range(30)]
    body = console.claims_section(claims)
    assert "#1 held by a0" in body
    assert "#1 held by a7" in body
    assert "#1 held by a8" not in body
    assert "+22 more claim(s)" in body
