"""governance/lifecycle/live.py — the live stage projection (issue #885)."""

from __future__ import annotations

from conftest import clean_item, record  # noqa: E402

from governance.lifecycle import live


def test_project_derives_stage_from_stage_of_not_a_second_source():
    rec = record(
        clean_item(),
        clean_item(issue=1, state="open", pr={}, verify={}, milestone=None, claim={}),
    )
    projected = live.project(rec)
    stages = {entry["issue"]: entry["stage"] for entry in projected["items"]}
    assert stages[269] == "reclaimed"
    assert stages[1] == "filed"
    assert projected["by_stage"]["reclaimed"] == 1
    assert projected["scope"] == rec["scope"]


def test_check_live_accepts_a_faithful_projection():
    rec = record(clean_item())
    projected = live.project(rec)
    assert live.check_live(rec, projected) == []


def test_check_live_refuses_a_feed_naming_an_issue_the_record_does_not_carry():
    rec = record(clean_item())
    drifted = {"items": [{"issue": 9999, "stage": "reclaimed"}]}
    problems = live.check_live(rec, drifted)
    assert any("9999" in problem for problem in problems)


def test_check_live_refuses_a_stage_that_disagrees_with_the_records_own_artifacts():
    """The drift provocation: a feed claiming a stage the item's real artifacts
    do not support must be refused by issue, not trusted because it looks like
    a projection."""
    rec = record(clean_item())
    drifted = {"items": [{"issue": 269, "stage": "filed"}]}  # really "reclaimed"
    problems = live.check_live(rec, drifted)
    assert any("#269" in problem and "drifted" in problem for problem in problems)
