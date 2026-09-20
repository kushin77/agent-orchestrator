"""Test for the live-check sample (issue #1536 scratch branch). Never merged."""

from engine.livecheck_1536_sample import summarize_runs


def test_summarize_runs_totals_per_lane_and_preserves_order():
    assert summarize_runs([("a", 1), ("b", 2), ("a", 3)]) == {"a": 4, "b": 2}


def test_summarize_runs_empty_input_is_empty_not_defaulted():
    assert summarize_runs([]) == {}
