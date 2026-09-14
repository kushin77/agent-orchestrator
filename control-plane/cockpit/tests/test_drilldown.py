"""The headless driver: drill-down asserted on REAL rendered frames (#566).

The issue names this trap explicitly: the proof must assert on the rendered
output an operator sees — the composed ANSI frame — never on a hand-built dict
or a string of markup. Every assertion here reads the actual frame text after
each keystroke of the real input loop.
"""

from __future__ import annotations

import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]  # control-plane/cockpit
if str(PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR))

from conftest import RecordingTransport, make_cockpit, panel_ids  # noqa: E402


def test_one_keystroke_per_level_down_to_the_raw_record(registry, flags_on):
    transport = RecordingTransport()
    cockpit = make_cockpit(registry, transport, flags_registry=flags_on)
    frames = [cockpit.compose()]
    for _key in "ddddd":
        cockpit.handle_line(_key)
        frames.append(cockpit.compose())

    # every frame is the real composed frame: bordered tiles and the drill section
    for text in frames:
        assert "\u250c" in text and "\u2502" in text, text[:200]
        assert "drill:" in text, text[:200]
        assert "FAILED" not in text

    # the breadcrumb is visible at every step, one keystroke each
    assert "platform \u203a control-plane" in frames[1]
    assert "platform \u203a control-plane \u203a #566" in frames[2]
    assert "ao-rc11" in frames[3]
    assert "c-1 plan the lane" in frames[4]

    # the raw record at the bottom: the tool-call's own detail, not a dict
    assert "raw record:" in frames[5]
    assert "tool: read_file" in frames[5]
    assert "path: control-plane/functions/functions.yaml" in frames[5]

    # every drill level names the declared function it consumes
    for text in frames:
        rendered = panel_ids(text)
        assert rendered <= set(registry.functions)


def test_back_walks_the_same_path_up(registry, flags_on):
    transport = RecordingTransport()
    cockpit = make_cockpit(registry, transport, flags_registry=flags_on)
    cockpit.handle_line("d")
    cockpit.handle_line("d")
    frame_down = cockpit.compose()
    assert "#566 the terminal cockpit" in frame_down
    cockpit.handle_line("b")
    frame_up = cockpit.compose()
    assert "platform \u203a control-plane" in frame_up
    assert "[issue]" in frame_up  # the lane's rows are visible again
    assert "ao-rc11" not in frame_up  # the agent detail is gone


def test_a_digit_selects_the_row_before_descending(registry, flags_on):
    transport = RecordingTransport()
    cockpit = make_cockpit(registry, transport, flags_registry=flags_on)
    cockpit.handle_line("2")  # the second lane: portal
    status = cockpit.handle_line("d")
    assert "platform \u203a portal" in status
    frame_text = cockpit.compose()
    assert "#554 the control API" in frame_text


def test_a_level_with_no_rows_is_named_not_guessed(registry, flags_on):
    transport = RecordingTransport()
    cockpit = make_cockpit(registry, transport, flags_registry=flags_on)
    for _key in "ddd":
        cockpit.handle_line(_key)
    # descend into #566 -> agent ao-rc11 -> call c-1 -> the tool-call raw record
    cockpit.handle_line("d")  # agent
    cockpit.handle_line("d")  # call c-1
    cockpit.handle_line("d")  # tool-call level (the raw record)
    frame_text = cockpit.compose()
    assert "raw record:" in frame_text
    assert "tool: read_file" in frame_text
    # issue #565 has no agents: back up to the lane level, select row 2, descend
    for _key in "bbbb":
        cockpit.handle_line(_key)
    cockpit.handle_line("2")  # issue #565 (the lane's rows are its issues)
    cockpit.handle_line("d")  # into #565 — its agent level is empty
    frame_text = cockpit.compose()
    assert "NO_DATA" in frame_text
    assert "#565 the function registry" in cockpit.drill.breadcrumb()
