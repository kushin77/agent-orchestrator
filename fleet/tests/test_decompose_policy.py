"""The micro-decomposition POLICY (epic #707 lane F4 / issue #719).

Two layers are proved here, and they are deliberately separate:

* the **policy** (`fleet/decompose_policy.py`) — pure and offline, so every guard
  is exercised directly, with no brain, no board and no network: the sizing rule,
  the duplicate guard, the wave cap, and their own anti-formality self-control;
* the **seam** (`brain.handle_decompose`) — that the guards run *before* the first
  `gh issue create` (a refused wave files NOTHING), that children are bound to the
  epic the fleet is actually driving, and that each filed child's body carries
  `Parent: #<epic>`.

The board the seam reads is a real `.board/snapshot.json` + `.board/focus.json`
pair written into `tmp_path` and wired in through `brain.BOARD_PATH` /
`brain.FOCUS_PATH`, so `resolve_active_epic` runs for real instead of being
stubbed into agreement with the test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import brain
import decompose_policy

Issue = brain.snapshot_mod.Issue


# --- fixtures / helpers -------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_live_cap_env(monkeypatch):
    """The wave cap must come from the test, not from the operator's shell."""
    monkeypatch.delenv(decompose_policy.CAP_ENV, raising=False)


def micro(title: str = "a micro child", **overrides) -> dict:
    """A child that satisfies the sizing rule; `overrides` mutates it."""
    child = {
        "title": title,
        "lane": "fleet",
        "verify": "pytest -q fleet/tests",
        "criterion": "the guard fires",
        "files": ["fleet/decompose_policy.py"],
    }
    child.update(overrides)
    return child


def board(
    tmp_path: Path,
    monkeypatch,
    *,
    epic: int = 707,
    open_titles: dict[int, str] | None = None,
) -> Path:
    """Write a committed board + pinned focus into ``tmp_path`` and point the brain at it.

    A real write, a real load: `resolve_active_epic` is exercised, not stubbed.
    """
    issues = {epic: Issue(epic, "test epic", labels=("type:epic",))}
    for number, title in (open_titles or {}).items():
        issues[number] = Issue(number, title)
    snapshot = brain.snapshot_mod.Snapshot(generated_at="2026-09-14T00:00:00Z", source="test", issues=issues)
    board_path = tmp_path / "snapshot.json"
    focus_path = tmp_path / "focus.json"
    brain.snapshot_mod.save(snapshot, board_path)
    brain.focus_mod.save(brain.focus_mod.Focus.pinned(epic), focus_path)
    monkeypatch.setattr(brain, "BOARD_PATH", board_path)
    monkeypatch.setattr(brain, "FOCUS_PATH", focus_path)
    return board_path


def wire(monkeypatch, tmp_path: Path, *, dispatched: list | None = None):
    """Stop `handle_decompose` at the seams: no `gh`, no board refresh, no channel."""
    monkeypatch.setattr(brain, "WAVES", tmp_path / "waves")
    filed: list[tuple[str, str]] = []

    def fake_create(title: str, body: str) -> int:
        filed.append((title, body))
        return 300 + len(filed)

    monkeypatch.setattr(brain, "gh_issue_create", fake_create)
    sent: list[dict] = dispatched if dispatched is not None else []

    def fake_dispatch(order_: dict):
        sent.append(order_)
        return True, "channel send: OK"

    monkeypatch.setattr(brain, "dispatch", fake_dispatch)
    monkeypatch.setattr(brain, "issue_is_closed", lambda _n: False)

    class _Refresh:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(brain.subprocess, "run", lambda *a, **k: _Refresh())
    return filed


def spec(parent: int, children: list) -> dict:
    return {"task": {"decompose": {"parent_issue": parent, "children": children}}}


# --- the sizing rule ----------------------------------------------------------


def test_a_micro_child_is_accepted():
    assert decompose_policy.sizing_problem(micro()) is None


def test_a_child_without_a_title_is_refused():
    problem = decompose_policy.sizing_problem(micro(title="   "))
    assert problem and "names no title" in problem


def test_a_child_without_a_lane_is_refused():
    problem = decompose_policy.sizing_problem(micro(lane=""))
    assert problem and "names no lane" in problem


@pytest.mark.parametrize(
    "child",
    [
        micro(lanes=["fleet", "governance"]),
        micro(lanes="fleet, governance"),
        micro(lane="fleet, governance"),
        micro(lane="fleet;governance"),
    ],
)
def test_a_child_naming_more_than_one_lane_is_refused_as_resplittable(child):
    problem = decompose_policy.sizing_problem(child)
    assert problem and decompose_policy.RESPLITTABLE in problem
    assert "parent SEAM" in problem, "the refusal must name the seam to decompose instead"
    assert "1 lane" in problem, "the rule terminates because a micro-child owns exactly one lane"


def test_a_child_without_a_runnable_verify_is_refused():
    problem = decompose_policy.sizing_problem(micro(verify="   "))
    assert problem and "no runnable Verify:" in problem


def test_a_child_with_more_than_one_verify_line_is_refused_as_resplittable():
    problem = decompose_policy.sizing_problem(micro(verify="Verify: pytest a\npytest b"))
    assert problem and decompose_policy.RESPLITTABLE in problem


def test_a_child_without_a_criterion_is_refused():
    problem = decompose_policy.sizing_problem(micro(criterion=""))
    assert problem and "no acceptance criterion" in problem


def test_a_child_with_more_than_one_criterion_is_refused_as_resplittable():
    problem = decompose_policy.sizing_problem(micro(criterion="", criteria=["one", "two"]))
    assert problem and decompose_policy.RESPLITTABLE in problem


def test_a_child_without_files_is_refused():
    problem = decompose_policy.sizing_problem(micro(files=[]))
    assert problem and "Files:" in problem


def test_a_child_that_is_not_an_object_is_refused_not_crashed():
    assert decompose_policy.sizing_problem("just-a-string") is not None


def test_depth_is_bounded_at_epic_then_micro_child():
    assert decompose_policy.sizing_problem(micro(), depth=decompose_policy.MAX_DEPTH) is None
    problem = decompose_policy.sizing_problem(micro(), depth=decompose_policy.MAX_DEPTH + 1)
    assert problem and "depth" in problem


# --- the duplicate guard ------------------------------------------------------


def test_a_child_matching_an_open_title_is_refused():
    problem = decompose_policy.duplicate_problem(micro("harden the gate"), [(700, "Harden  the gate", "fleet", "x")])
    assert problem and "#700" in problem and "duplicates" in problem


def test_a_child_matching_an_open_lane_and_verify_is_refused():
    open_issue = (701, "an unrelated title", "fleet", "pytest -q fleet/tests")
    problem = decompose_policy.duplicate_problem(micro("a different title"), [open_issue])
    assert problem and "#701" in problem and "lane + Verify:" in problem


def test_a_child_matching_no_open_issue_is_accepted():
    assert decompose_policy.duplicate_problem(micro("fresh work"), [(701, "something else", "fleet", "pytest other")]) is None


def test_the_duplicate_guard_reads_only_the_issues_it_is_given():
    """It must never reach the network: no open issues in, no duplicate out."""
    assert decompose_policy.duplicate_problem(micro("harden the gate"), ()) is None


# --- the wave-level validator (cap + everything above) ------------------------


def test_a_wave_at_the_cap_is_accepted():
    assert decompose_policy.wave_problems([micro(f"child {i}") for i in range(12)], cap=12) == []


def test_a_wave_over_the_cap_is_refused():
    problems = decompose_policy.wave_problems([micro(f"child {i}") for i in range(13)], cap=12)
    assert problems and "over the cap" in problems[0]


def test_a_wave_without_children_is_refused():
    assert decompose_policy.wave_problems([], cap=12)


def test_the_same_child_twice_in_one_wave_is_refused():
    problems = decompose_policy.wave_problems([micro("twice"), micro("twice")], cap=12)
    assert problems and any("twice in this wave" in problem for problem in problems)


def test_one_oversized_child_fails_the_whole_wave():
    problems = decompose_policy.wave_problems([micro("fine"), micro("too big", lanes=["a", "b"])], cap=12)
    assert problems and any(decompose_policy.RESPLITTABLE in problem for problem in problems)


def test_an_unusable_cap_is_refused_rather_than_defaulted():
    assert decompose_policy.wave_problems([micro()], cap=0)


# --- the cap's own precedence -------------------------------------------------


def test_the_env_cap_wins_over_the_focus():
    assert decompose_policy.effective_cap({decompose_policy.CAP_ENV: "3"}, focus_wave_cap=9) == 3


def test_an_unset_env_defers_to_the_focus_wave_cap():
    assert decompose_policy.effective_cap({}, focus_wave_cap=9) == 9


def test_with_no_env_and_no_focus_the_default_applies():
    assert decompose_policy.effective_cap({}) == decompose_policy.DEFAULT_CAP == 12


@pytest.mark.parametrize("raw", ["lots", "0", "-2", "2.5"])
def test_a_malformed_cap_is_a_refusal_not_a_silent_default(raw):
    with pytest.raises(ValueError):
        decompose_policy.effective_cap({decompose_policy.CAP_ENV: raw})


def test_the_restated_default_matches_the_focus_module():
    """`DEFAULT_CAP` restates `focus.DEFAULT_WAVE_CAP`; the two must not drift."""
    import focus

    assert focus.DEFAULT_WAVE_CAP == decompose_policy.DEFAULT_CAP


def test_the_policy_proves_its_own_guards_can_fail():
    assert decompose_policy.self_control() == []


# --- the seam: `handle_decompose` --------------------------------------------


def test_the_seam_reads_the_pinned_focus_of_the_committed_board(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch, epic=707)
    assert brain.resolve_active_epic() == (707, "")


def test_the_seam_refuses_when_the_board_has_no_active_epic(tmp_path, monkeypatch):
    """Nothing pinned and no workable open epic: children are never filed against nothing."""
    snapshot = brain.snapshot_mod.Snapshot(generated_at="2026-09-14T00:00:00Z", source="test", issues={})
    board_path = tmp_path / "snapshot.json"
    focus_path = tmp_path / "focus.json"
    brain.snapshot_mod.save(snapshot, board_path)
    brain.focus_mod.save(brain.focus_mod.Focus.pinned(None), focus_path)
    monkeypatch.setattr(brain, "BOARD_PATH", board_path)
    monkeypatch.setattr(brain, "FOCUS_PATH", focus_path)
    filed = wire(monkeypatch, tmp_path)

    ok, report = brain.handle_decompose(spec(707, [micro()]))

    assert ok is False and "no active epic" in report
    assert filed == [], "a refusal must file nothing"


def test_the_seam_refuses_an_unreadable_board(tmp_path, monkeypatch):
    monkeypatch.setattr(brain, "BOARD_PATH", tmp_path / "absent.json")
    monkeypatch.setattr(brain, "FOCUS_PATH", tmp_path / "absent-focus.json")
    filed = wire(monkeypatch, tmp_path)

    ok, report = brain.handle_decompose(spec(707, [micro()]))

    assert ok is False and "unreadable" in report
    assert filed == []


def test_children_are_refused_when_the_order_names_a_different_epic(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch, epic=707)
    filed = wire(monkeypatch, tmp_path)

    ok, report = brain.handle_decompose(spec(219, [micro()]))

    assert ok is False
    assert "ACTIVE epic is #707" in report and "#219" in report
    assert filed == [], "a child must never be filed against a non-active epic"


def test_children_are_filed_against_the_active_epic_and_stamped_with_it(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch, epic=707)
    filed = wire(monkeypatch, tmp_path)

    ok, report = brain.handle_decompose(spec(707, [micro("harden the seam")]))

    assert ok is True, report
    assert [title for title, _ in filed] == ["harden the seam"]
    assert filed[0][1].startswith("Parent: #707"), "each child body must stamp the active epic"
    assert "Lane: fleet" in filed[0][1]
    assert "Verify: `pytest -q fleet/tests`" in filed[0][1]
    assert "Criterion: the guard fires" in filed[0][1]


def test_a_wave_over_the_cap_files_nothing(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch, epic=707)
    monkeypatch.setenv(decompose_policy.CAP_ENV, "2")
    filed = wire(monkeypatch, tmp_path)

    ok, report = brain.handle_decompose(spec(707, [micro(f"child {i}") for i in range(3)]))

    assert ok is False and "over the cap" in report
    assert filed == [], "the cap must be enforced before the first filing"


def test_a_re_splittable_child_files_nothing(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch, epic=707)
    filed = wire(monkeypatch, tmp_path)

    ok, report = brain.handle_decompose(spec(707, [micro("too big", lanes=["fleet", "governance"])]))

    assert ok is False and decompose_policy.RESPLITTABLE in report and "parent SEAM" in report
    assert filed == []


def test_a_child_that_duplicates_an_open_issue_files_nothing(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch, epic=707, open_titles={555: "harden the seam"})
    filed = wire(monkeypatch, tmp_path)

    ok, report = brain.handle_decompose(spec(707, [micro("harden the seam")]))

    assert ok is False and "#555" in report and "duplicates" in report
    assert filed == [], "the duplicate guard must run before the first filing"


def test_a_malformed_cap_refuses_the_wave_instead_of_defaulting(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch, epic=707)
    monkeypatch.setenv(decompose_policy.CAP_ENV, "lots")
    filed = wire(monkeypatch, tmp_path)

    ok, report = brain.handle_decompose(spec(707, [micro()]))

    assert ok is False and decompose_policy.CAP_ENV in report and "not defaulted silently" in report
    assert filed == []


def test_a_malformed_spec_is_still_refused_before_any_policy_work(tmp_path, monkeypatch):
    board(tmp_path, monkeypatch, epic=707)
    filed = wire(monkeypatch, tmp_path)

    ok, report = brain.handle_decompose({"task": {"decompose": {"parent_issue": 707, "children": []}}})

    assert ok is False and "carries no children" in report
    assert filed == []
