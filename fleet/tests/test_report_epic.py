"""The active-epic block of the fleet status report (epic #707, lane #722).

Why this module exists
----------------------
The report answered "where does this stand" for four planning sections but never
said *which epic the fleet is focused on* — the single-epic focus the whole
dispatch rule is built around (`.board/focus.json`,
`governance/dispatch/focus.py`, lane F1/#716). This suite pins the block that
closes that gap, three ways:

* **pure over an injected snapshot + focus.** `report.epic_from()` takes a
  `Snapshot` and a `Focus` and returns an `Epic`; it does no I/O, no network and
  no shell-out, so the whole section is asserted from a fixture — no fleet, no
  tmux, no live `.fleet/` (the acceptance criterion for lane F7/#722).
* **what the focus declares, not what this lane invents.** Per-epic progress is
  the board's own child states (`closed/total`), the pooled queue is
  `focus.pooled(...)`, and the effective agent count is copied from the focus —
  `max_agents == 0` reads "the pool" rather than a number the capacity formula
  (lane F3/#718) owns.
* **read-only.** The block writes nothing, so no reporting change can alter a
  dispatch decision (ADR-0012 decision (d)). That is measured, not asserted: the
  tree is digested before and after the report runs over it.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import report  # noqa: E402

# `report` puts `governance/dispatch` on sys.path, which is where these live.
import focus as focus_mod  # noqa: E402
from model import Issue, Snapshot  # noqa: E402

EPIC = 707
CLOCK = "2026-09-14T10:00:00Z"


# ---------------------------------------------------------------------------
# the fixture — a board and a focus, both in memory
# ---------------------------------------------------------------------------


def board_issue(
    number: int,
    *,
    epic: bool = False,
    closed: bool = False,
    parent: int | None = None,
) -> Issue:
    return Issue(
        number=number,
        title=f"synthetic #{number}",
        state="closed" if closed else "open",
        labels=("type:epic",) if epic else ("type:task",),
        parent=parent,
    )


def fixture_snapshot() -> Snapshot:
    """#707 is the pinned epic: 3 closed children, 2 open, 2 pooled issues.

    #600 is a second, lower-numbered *open* epic so the fallback rule is
    observable; #606 is a closed epic so "a closed epic is never active" is
    provable; #700 is closed and must never appear anywhere.
    """
    issues = [
        board_issue(600, epic=True),
        board_issue(606, epic=True, closed=True),
        board_issue(700, closed=True),
        board_issue(EPIC, epic=True),
        board_issue(710, parent=EPIC, closed=True),
        board_issue(711, parent=EPIC, closed=True),
        board_issue(712, parent=EPIC, closed=True),
        board_issue(720, parent=EPIC),
        board_issue(722, parent=EPIC),
        board_issue(730),
        board_issue(731),
    ]
    return Snapshot(generated_at=CLOCK, source="fixture", issues={issue.number: issue for issue in issues})


def fixture_focus(*, epic: int | None = EPIC, max_agents: int = 0, wave_cap: int = 12) -> focus_mod.Focus:
    return focus_mod.Focus(active_epic=epic, activated_at=CLOCK, wave_cap=wave_cap, max_agents=max_agents)


# ---------------------------------------------------------------------------
# the pure builder: the acceptance criterion, asserted from a fixture
# ---------------------------------------------------------------------------


def test_epic_section_reports_the_active_epic_progress_pool_and_agents():
    """Performance criterion: active epic, children closed/total, pool, agents."""
    epic = report.epic_from(fixture_snapshot(), fixture_focus())
    assert epic.number == EPIC, "the pinned epic is the active one"
    assert epic.title == "synthetic #707"
    assert epic.progress == "3/5", "per-epic progress is the children closed/total"
    assert epic.total == 5
    assert epic.closed_children == (710, 711, 712)
    assert epic.open_children == (720, 722)
    assert epic.pooled == (730, 731), "the pool is open, non-epic work outside the active epic"
    assert epic.wave_cap == 12 and epic.activated_at == CLOCK, "the focus's own declarations are carried"


def test_the_pool_and_the_progress_exclude_epics_closed_work_and_children():
    """Negative control: an epic, a closed issue and a child are never pooled."""
    epic = report.epic_from(fixture_snapshot(), fixture_focus())
    assert 600 not in epic.pooled and 606 not in epic.pooled, "an epic is never a unit of work"
    assert 700 not in epic.pooled, "closed work is not queued"
    assert 720 not in epic.pooled and 722 not in epic.pooled, "a child of the active epic is not pooled"
    assert 600 not in (epic.closed_children + epic.open_children), "another epic's children are not this epic's progress"


def test_effective_agents_is_the_focus_declaration_never_an_invented_number():
    """`max_agents == 0` means the pool; a non-zero value is reported verbatim."""
    pooled = report.epic_from(fixture_snapshot(), fixture_focus(max_agents=0))
    assert pooled.max_agents == 0
    assert pooled.effective_agents == "the pool", "0 is labelled, never resolved here (lane F3/#718 owns the formula)"
    assert pooled.to_json()["effective_agents"] == "the pool"

    pinned = report.epic_from(fixture_snapshot(), fixture_focus(max_agents=8))
    assert pinned.max_agents == 8 and pinned.effective_agents == "8"
    assert pinned.to_json()["max_agents"] == 8


def test_a_closed_pin_falls_back_to_a_workable_epic_never_to_the_closed_one():
    """The builder resolves with the dispatch rule — a closed epic is never active."""
    closed_pin = report.epic_from(fixture_snapshot(), fixture_focus(epic=606))
    assert closed_pin.number != 606, "a closed epic must never be reported as active"
    assert closed_pin.number == 600, "it falls back to the lowest workable open epic"
    assert closed_pin.progress == "0/0", "#600 has no children in the fixture"
    assert closed_pin.pooled == (720, 722, 730, 731), (
        "the pool is relative to the ACTIVE epic: #707's open children are pooled once #600 is active "
        "(#707's closed children are not open work and stay out of the queue)"
    )


def test_with_nothing_pinned_the_resolver_still_hosts_a_workable_epic():
    """The frozen rule: no pin resolves to the lowest open, unblocked epic."""
    unpinned = report.epic_from(fixture_snapshot(), None)
    assert unpinned.number == 600 and unpinned.wave_cap == 0 and unpinned.max_agents == 0
    assert unpinned.activated_at == "", "no focus means no activation to cite — never a guessed timestamp"


def test_no_workable_epic_reports_none_rather_than_fabricating_one():
    """Only closed epics: the block says so, with no id and no progress."""
    only_closed = Snapshot(
        generated_at=CLOCK,
        source="fixture",
        issues={606: board_issue(606, epic=True, closed=True)},
    )
    epic = report.epic_from(only_closed, fixture_focus())
    assert epic.number is None and epic.progress == "0/0"
    assert epic.closed_children == () and epic.open_children == ()


def test_an_unreadable_focus_is_refused_never_defaulted_into_an_active_epic():
    """A corrupt `.board/focus.json` must not silently disable the focus (#707)."""
    epic = report.epic_from(fixture_snapshot(), None, error=".board/focus.json: unreadable")
    assert epic.number is None, "a focus that cannot be trusted yields no active epic"
    assert "unreadable" in epic.error and epic.pooled == () and epic.open_children == ()


def test_the_builder_is_pure_and_leaves_its_inputs_untouched():
    snapshot, focus = fixture_snapshot(), fixture_focus()
    first = report.epic_from(snapshot, focus)
    second = report.epic_from(snapshot, focus)
    assert first == second, "the same inputs must yield the same block"
    assert len(snapshot.issues) == 11 and len(focus.pooled) == 0, "the builder mutated its inputs"


def test_validate_report_can_reject_a_malformed_epic_block():
    """Negative control: the validator the JSON round-trip leans on can fail."""
    good = {
        "schema": report.SCHEMA,
        "provenance": {"pattern_source": "x"},
        "basis": {"commit": "abc123"},
        "epic": report.epic_from(fixture_snapshot(), fixture_focus()).to_json(),
        "sections": {name: [] for name in report.SECTIONS},
    }
    assert report.validate_report(good) == []

    missing = {**good, "epic": {field: good["epic"][field] for field in ("number", "title")}}
    assert any("epic is missing" in problem for problem in report.validate_report(missing))

    not_an_object = {**good, "epic": "707"}
    assert any("epic must be an object" in problem for problem in report.validate_report(not_an_object))

    bad_number = {**good, "epic": {**good["epic"], "number": "707"}}
    assert any("epic.number" in problem for problem in report.validate_report(bad_number))


# ---------------------------------------------------------------------------
# the same block, through the whole report over a real (synthetic) tree
# ---------------------------------------------------------------------------


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def build_tree(root: Path, *, focus: focus_mod.Focus | None = fixture_focus(), raw_focus: str | None = None) -> Path:
    """A synthetic checkout: the board, the pinned focus, and one wave plan."""
    write_json(root / ".board" / "snapshot.json", fixture_snapshot().to_json())
    if raw_focus is not None:
        (root / ".board").mkdir(parents=True, exist_ok=True)
        (root / ".board" / "focus.json").write_text(raw_focus, encoding="utf-8")
    elif focus is not None:
        write_json(root / ".board" / "focus.json", focus.to_json())
    write_json(root / ".fleet" / "waves" / f"{EPIC}.json", {
        "parent": EPIC,
        "children": [{"index": 0, "issue": 722, "lane": "fleet", "verify": "python3 -m pytest fleet/tests -q", "depends_on": []}],
        "dispatched": [],
    })
    return root


def state_for(root: Path) -> dict:
    moment = datetime.fromisoformat(CLOCK.replace("Z", "+00:00")).astimezone(timezone.utc)
    return report.read_state(root, now=moment, generated_at=CLOCK)


def built_for(root: Path) -> report.Report:
    built = report.report_from(state_for(root))
    assert built is not None, "the synthetic board is a plan source"
    return built


def tree_digest(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_the_report_carries_the_epic_block_in_json_and_text(tmp_path):
    build_tree(tmp_path)
    built = built_for(tmp_path)
    assert built.epic is not None and built.epic.number == EPIC

    payload = built.to_json()
    assert report.validate_report(payload) == [], "the emitted payload must satisfy its own schema"
    assert payload["epic"]["number"] == EPIC
    assert payload["epic"]["progress"] == {"closed": 3, "total": 5, "label": "3/5"}
    assert payload["epic"]["closed_children"] == [710, 711, 712]
    assert payload["epic"]["open_children"] == [720, 722]
    assert payload["epic"]["pooled"] == [730, 731]
    assert payload["epic"]["effective_agents"] == "the pool"
    assert payload["counts"] == {"now": 0, "next": 1, "blocked": 0, "delivered": 0}, (
        "the epic block is not a fifth section: the four planning counts are unchanged"
    )

    frame = report.render(built)
    assert "EPIC #707 (3/5 children closed)" in frame
    assert "progress: 3/5 children closed" in frame
    assert "pooled:   2 issue(s) — #730, #731" in frame
    assert "the pool (max_agents 0" in frame
    assert "wave cap 12" in frame


def test_a_malformed_focus_file_is_refused_by_the_read_and_the_report(tmp_path):
    build_tree(tmp_path, raw_focus="{not json")
    built = built_for(tmp_path)
    assert built.epic.number is None, "a corrupt focus must not be read as 'nothing pinned'"
    assert "focus.json" in built.epic.error
    assert "no active epic" in report.render(built)
    assert "refused" in report.render(built)


def test_the_report_of_a_fixture_leaves_the_tree_it_reads_untouched(tmp_path, monkeypatch):
    """Read-only (ADR-0012 (d)): reporting writes nothing, so it decides nothing."""
    monkeypatch.setattr(report, "head_commit", lambda *args, **kwargs: "fixture")
    build_tree(tmp_path)
    before = tree_digest(tmp_path)
    assert before, "the fixture must have written something to compare against"

    built = built_for(tmp_path)
    report.render(built)
    built.to_json()

    assert tree_digest(tmp_path) == before, "the epic block mutated the state it read"
