"""The capacity gate — max-agents bounded by three real limits (#718).

These are the unit-level controls. The PROVOKED controls (each bound made to
bind, and the relaxed input proved admitted) live in
``scripts/check-capacity-gate.sh``, which is registered in ``scripts/verify.sh``
and drives the real module in this tree.
"""

from __future__ import annotations

import json

import pytest

import capacity


RICH = capacity.Resources(
    ram_available_bytes=32 * 1024**3, tmp_available_bytes=32 * 1024**3
)
A = capacity.Lane(id="lane-a", issue=1, files=("fleet/a.py",))
B = capacity.Lane(id="lane-b", issue=2, files=("fleet/a.py", "fleet/b.py"))
C = capacity.Lane(id="lane-c", issue=3, files=("fleet/c.py",))
OPAQUE = capacity.Lane(id="lane-d", issue=4, files=None)


def resolve(**kwargs) -> capacity.Capacity:
    kwargs.setdefault("resources", RICH)
    kwargs.setdefault("env", {})
    kwargs.setdefault("pool_size", 10)
    return capacity.resolve_capacity(**kwargs)


# --- the default (FLEET_MAX_AGENTS) -----------------------------------------


def test_fleet_max_agents_sets_the_default():
    assert capacity.default_max_agents({"FLEET_MAX_AGENTS": "4"}, pool_size=10) == 4


def test_zero_means_the_pool_like_the_focus_schema():
    assert capacity.default_max_agents({"FLEET_MAX_AGENTS": "0"}, focus_max_agents=4, pool_size=7) == 7
    assert capacity.default_max_agents({}, focus_max_agents=0, pool_size=7) == 7


def test_the_focus_names_the_epic_default_only_when_positive():
    assert capacity.default_max_agents({}, focus_max_agents=3, pool_size=7) == 3


def test_the_env_beats_the_focus():
    assert capacity.default_max_agents({"FLEET_MAX_AGENTS": "2"}, focus_max_agents=4, pool_size=7) == 2


@pytest.mark.parametrize("bad", ["zero", "-1", "1.5", "many"])
def test_a_misconfigured_default_is_refused_not_defaulted(bad):
    with pytest.raises(capacity.CapacityConfigError):
        capacity.default_max_agents({"FLEET_MAX_AGENTS": bad}, pool_size=7)


@pytest.mark.parametrize("bad", ["0", "-8", "lots"])
def test_a_misconfigured_budget_is_refused(bad):
    with pytest.raises(capacity.CapacityConfigError):
        resolve(env={"AO_LANE_RAM_MIB": bad})


# --- bound 1: the pool ------------------------------------------------------


def test_pool_bound_holds_then_admits_when_relaxed():
    tight = resolve(ready=[A, B, C], env={"FLEET_MAX_AGENTS": "1"})
    assert tight.effective == 1 and tight.binding == "pool"
    decision = capacity.admit(C, capacity=tight, active=[A])
    assert not decision.admitted and decision.bound == "pool"

    relaxed = resolve(ready=[A, B, C], env={"FLEET_MAX_AGENTS": "3"})
    assert capacity.admit(C, capacity=relaxed, active=[A]).admitted


# --- bound 2: file-disjointness (AO-GR-24) ----------------------------------


def test_the_disjoint_bound_holds_the_lane_sharing_a_file():
    selected = capacity.disjoint_bound([A, B, C])
    assert selected.limit == 2
    assert selected.admitted == ("lane-a", "lane-c")
    assert len(selected.collisions) == 1
    assert selected.collisions[0].holder == "lane-a"
    assert selected.collisions[0].files == ("fleet/a.py",)
    assert "fleet/a.py" in selected.collisions[0].message()


def test_a_collision_holds_even_when_a_worker_is_free():
    cap = resolve(ready=[A, B], env={"FLEET_MAX_AGENTS": "9"})
    decision = capacity.admit(B, capacity=cap, active=[A])
    assert not decision.admitted and decision.bound == "disjoint"


def test_two_lanes_with_the_same_file_set_are_never_both_dispatched():
    twin = capacity.Lane(id="lane-twin", issue=9, files=("fleet/a.py",))
    cap = resolve(ready=[A, twin], env={"FLEET_MAX_AGENTS": "9"})
    assert capacity.admit(A, capacity=cap, active=[]).admitted
    assert not capacity.admit(twin, capacity=cap, active=[A]).admitted


def test_an_undeclared_file_set_is_named_never_silently_disjoint():
    selected = capacity.disjoint_bound([A, OPAQUE])
    assert selected.unattributable == ("lane-d",)
    assert "lane-d" in selected.detail()
    assert "lane-d" in selected.admitted  # admitted, but reported


def test_strict_mode_holds_undeclared_lanes():
    selected = capacity.disjoint_bound([A, OPAQUE], files_required=True)
    assert selected.limit == 1 and "lane-d" not in selected.admitted

    cap = resolve(ready=[A, OPAQUE], env={"FLEET_MAX_AGENTS": "9", "AO_LANE_FILES_REQUIRED": "1"})
    other = capacity.Lane(id="lane-e", issue=5, files=None)
    assert not capacity.admit(other, capacity=cap, active=[OPAQUE]).admitted


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("fleet/a.py", ("fleet/a.py",)),
        ("fleet/a.py, fleet/b.py", ("fleet/a.py", "fleet/b.py")),
        (["fleet/a.py\nfleet/b.py"], ("fleet/a.py", "fleet/b.py")),
        ("", None),
        ([], None),
        (None, None),
        ("   ", None),
        ({"files": ["x"]}, None),
    ],
)
def test_declared_files_normalises(raw, expected):
    assert capacity.declared_files(raw) == expected


def test_declared_files_deduplicates_in_order():
    assert capacity.declared_files(["a", "a", "b"]) == ("a", "b")


# --- bound 3: the resource ceiling -----------------------------------------


def test_the_ceiling_is_computed_from_headroom():
    bound = capacity.resource_bound(RICH, ram_budget_mib=1024, tmp_budget_mib=1024)
    assert bound.limit == 32
    assert "32 lane(s)" in bound.detail()


def test_ram_and_tmp_each_bind():
    ram = capacity.resource_bound(
        capacity.Resources(ram_available_bytes=2048 * 1024**2, tmp_available_bytes=32 * 1024**3),
        ram_budget_mib=1024,
        tmp_budget_mib=512,
    )
    assert ram.limit == 2 and "bound by RAM" in ram.detail()
    tmp = capacity.resource_bound(
        capacity.Resources(ram_available_bytes=32 * 1024**3, tmp_available_bytes=600 * 1024**2),
        ram_budget_mib=1024,
        tmp_budget_mib=512,
    )
    assert tmp.limit == 1 and "headroom" in tmp.detail()


def test_a_full_box_resolves_to_zero_and_holds():
    starved = capacity.Resources(ram_available_bytes=100 * 1024**2, tmp_available_bytes=100 * 1024**2)
    cap = resolve(ready=[C], env={"FLEET_MAX_AGENTS": "9"}, resources=starved)
    assert cap.effective == 0 and cap.binding == "resource"
    decision = capacity.admit(C, capacity=cap, active=[])
    assert not decision.admitted and decision.bound == "resource"


def test_an_unmeasurable_box_is_cannot_assess_never_unbounded():
    unmeasured = capacity.Resources(
        ram_available_bytes=None,
        tmp_available_bytes=None,
        problems=("/proc/meminfo: unreadable", "/tmp: unreadable"),
    )
    cap = resolve(ready=[C], env={"FLEET_MAX_AGENTS": "9"}, resources=unmeasured)
    assert not cap.assessed and cap.effective == 0
    assert "CANNOT-ASSESS" in cap.line()
    decision = capacity.admit(C, capacity=cap, active=[])
    assert not decision.admitted and decision.bound == "resource"


def test_measure_resources_records_an_unreadable_probe(tmp_path):
    measured = capacity.measure_resources(meminfo=tmp_path / "absent", tmp_path=str(tmp_path / "nope"))
    assert not measured.measured
    assert measured.problems


def test_measure_resources_reads_a_real_probe(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 100 kB\nMemAvailable: 2048 kB\n", encoding="utf-8")
    measured = capacity.measure_resources(meminfo=meminfo, tmp_path=str(tmp_path))
    assert measured.ram_available_bytes == 2048 * 1024
    assert measured.tmp_available_bytes > 0


# --- the resolution ---------------------------------------------------------


def test_effective_is_the_minimum_of_the_three_and_names_the_binding_one():
    cap = resolve(ready=[A, B, C], env={"FLEET_MAX_AGENTS": "9"})
    assert cap.effective == 2 and cap.binding == "disjoint"
    assert [b.name for b in cap.bounds] == ["pool", "disjoint", "resource"]
    assert "bound_by=disjoint" in cap.line()


def test_every_bound_is_reported_even_when_it_does_not_bind():
    cap = resolve(ready=[A, C], env={"FLEET_MAX_AGENTS": "9"})
    assert cap.effective == 2
    limits = {b.name: b.limit for b in cap.bounds}
    assert limits["pool"] == 9 and limits["disjoint"] == 2
    assert limits["resource"] == (32 * 1024) // capacity.DEFAULT_LANE_RAM_MIB


def test_admit_reports_the_numbers():
    # Enough disjoint lanes that the POOL, not the disjoint bound, is the one
    # that binds — so the hold has to name the pool.
    extra = [
        capacity.Lane(id=f"lane-{n}", issue=100 + n, files=(f"fleet/{n}.py",)) for n in range(4)
    ]
    cap = resolve(ready=[A, C, *extra], env={"FLEET_MAX_AGENTS": "4"})
    assert cap.effective == 4 and cap.binding == "pool"
    line = capacity.admit(A, capacity=cap, active=[]).line(A)
    assert "#1" in line and "admitted" in line
    held = capacity.admit(C, capacity=cap, active=[A, A, A, A]).line(C)
    assert "HELD" in held and "bound: pool" in held


def test_lane_from_directive_reads_the_issue_and_the_declared_files():
    lane = capacity.Lane.from_directive(
        {"id": "d-1", "task": {"issue": 718, "files": ["fleet/capacity.py"]}}
    )
    assert lane.id == "d-1" and lane.issue == 718
    assert lane.files == ("fleet/capacity.py",)


def test_lane_from_directive_tolerates_a_missing_task():
    lane = capacity.Lane.from_directive({"id": "d-2"})
    assert lane.issue is None and lane.files is None and lane.id == "d-2"


def test_a_directive_with_a_bad_issue_is_not_counted_as_work():
    assert capacity.Lane.from_directive({"id": "d", "task": {"issue": True}}).issue is None
    assert capacity.Lane.from_directive({"id": "d", "task": {"issue": 0}}).issue is None


# --- the focus reader -------------------------------------------------------


def _focus_file(tmp_path, payload) -> str:
    target = tmp_path / "focus.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    return str(target)


def test_the_focus_names_the_default(tmp_path):
    assert capacity.read_focus_max_agents(_focus_file(tmp_path, {"max_agents": 6})) == 6
    assert capacity.read_focus_max_agents(_focus_file(tmp_path, {"max_agents": 0})) == 0


def test_an_absent_focus_is_none(tmp_path):
    assert capacity.read_focus_max_agents(tmp_path / "absent.json") is None


@pytest.mark.parametrize(
    "payload",
    [{"active_epic": 707}, {"max_agents": -1}, {"max_agents": True}, {"max_agents": "6"}, []],
)
def test_a_corrupt_focus_is_refused(tmp_path, payload):
    with pytest.raises(capacity.CapacityConfigError):
        capacity.read_focus_max_agents(_focus_file(tmp_path, payload))


# --- the module's own anti-formality control --------------------------------


def test_self_control_is_clean():
    assert capacity.self_control() == []


def test_headline_names_the_default_and_the_ceiling():
    line = capacity.headline(focus_max_agents=0, pool_size=10, resources=RICH)
    assert "max_agents_default=10" in line
    assert f"resource_ceiling={(32 * 1024) // capacity.DEFAULT_LANE_RAM_MIB}" in line
    assert "per dispatch cycle" in line


# --- the loop's own integration (not a re-implementation of it) --------------


def _in_flight(lane: capacity.Lane) -> None:
    """Park ``lane`` in the loop's real IN_FLIGHT registry, as a running worker does."""
    import terminal

    terminal.register_run(lane.id, lane.issue or 0, f"agent-{lane.id}")
    terminal.IN_FLIGHT[lane.id]["capacity_lane"] = lane


def _clear(*ids: str) -> None:
    import terminal

    for directive_id in ids:
        terminal.unregister_run(directive_id)


def _directive(directive_id: str, issue: int, *files: str) -> dict:
    task: dict = {"issue": issue}
    if files:
        task["files"] = list(files)
    return {"id": directive_id, "task": task}


def test_the_loop_resolves_and_holds_on_the_pool_bound(monkeypatch, tmp_path):
    """`terminal.resolve_capacity` — the loop's own resolver, not a copy of it."""
    import terminal

    monkeypatch.setattr(terminal, "FOCUS_PATH", tmp_path / "absent.json")
    monkeypatch.setenv("FLEET_MAX_AGENTS", "1")
    monkeypatch.setenv("FLEET_SISTER_POOL", "10")
    running = capacity.Lane(id="run-a", issue=1, files=("fleet/a.py",))
    _in_flight(running)
    try:
        resolved, lane = terminal.resolve_capacity(_directive("d-b", 2, "fleet/b.py"))
        assert resolved.effective == 1 and resolved.binding == "pool"
        decision = capacity.admit(lane, capacity=resolved, active=terminal.capacity_lanes())
        assert not decision.admitted and decision.bound == "pool"
        assert "HELD" in decision.line(lane)
    finally:
        _clear("run-a")


def test_the_loop_holds_on_the_resource_ceiling(monkeypatch, tmp_path):
    import terminal

    monkeypatch.setattr(terminal, "FOCUS_PATH", tmp_path / "absent.json")
    monkeypatch.setenv("FLEET_MAX_AGENTS", "9")
    # A budget larger than the box: the computed ceiling is then zero lanes.
    monkeypatch.setenv("AO_LANE_RAM_MIB", str(1024 * 1024))
    resolved, lane = terminal.resolve_capacity(_directive("d-c", 3, "fleet/c.py"))
    assert resolved.binding == "resource" and resolved.effective == 0
    decision = capacity.admit(lane, capacity=resolved, active=[])
    assert not decision.admitted and decision.bound == "resource"


def test_the_loop_holds_a_directive_whose_files_collide(monkeypatch, tmp_path):
    import terminal

    monkeypatch.setattr(terminal, "FOCUS_PATH", tmp_path / "absent.json")
    monkeypatch.setenv("FLEET_MAX_AGENTS", "9")
    monkeypatch.delenv("AO_LANE_RAM_MIB", raising=False)
    running = capacity.Lane(id="run-z", issue=10, files=("scripts/verify.sh",))
    _in_flight(running)
    try:
        resolved, lane = terminal.resolve_capacity(
            _directive("d-z", 11, "scripts/verify.sh", "fleet/z.py")
        )
        decision = capacity.admit(lane, capacity=resolved, active=terminal.capacity_lanes())
        assert not decision.admitted and decision.bound == "disjoint"
        assert decision.collision is not None
        assert "scripts/verify.sh" in decision.collision.message()
    finally:
        _clear("run-z")


def test_the_loop_reads_the_pinned_focus_for_the_default(monkeypatch, tmp_path):
    import json

    import terminal

    focus = tmp_path / "focus.json"
    focus.write_text(json.dumps({"max_agents": 6}), encoding="utf-8")
    monkeypatch.setattr(terminal, "FOCUS_PATH", focus)
    monkeypatch.delenv("FLEET_MAX_AGENTS", raising=False)
    resolved, _lane = terminal.resolve_capacity(_directive("d-f", 12, "fleet/f.py"))
    assert resolved.bounds[0].limit == 6

