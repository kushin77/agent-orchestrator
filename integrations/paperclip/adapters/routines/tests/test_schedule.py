"""The schedule reader: it parses the code, and it refuses what it cannot express."""

from __future__ import annotations

from pathlib import Path

import pytest

from integrations.paperclip.adapters.routines.model import RoutineRefused
from integrations.paperclip.adapters.routines.schedule import (
    FLEET_DIR_TOKEN,
    ROOT_TOKEN,
    read_schedule,
)
from integrations.paperclip.adapters.routines.tests.conftest import build_schedule_tree, job

MARKERS = ("ao-fleet-watchdog", "ao-fleet-prune", "ao-fleet-reconcile", "ao-fleet-reap")


def test_reads_exactly_one_entry_per_marker(schedule_tree: Path) -> None:
    schedule = read_schedule(schedule_tree)
    assert sorted(entry.marker for entry in schedule.entries) == sorted(MARKERS)
    assert schedule.refusals == []


def test_trigger_is_derived_from_the_code_not_restated(schedule_tree: Path) -> None:
    by_marker = {entry.marker: entry for entry in read_schedule(schedule_tree).entries}
    watchdog = by_marker["ao-fleet-watchdog"].trigger
    prune = by_marker["ao-fleet-prune"].trigger
    assert (watchdog.kind, watchdog.every_minutes) == ("interval", 2)
    assert (prune.kind, prune.at_hour, prune.at_minute) == ("daily", 4, 23)


def test_params_are_read_from_the_code(schedule_tree: Path) -> None:
    by_marker = {entry.marker: entry for entry in read_schedule(schedule_tree).entries}
    params = by_marker["ao-fleet-reconcile"].params
    assert params["argv"][-3:] == ["watch", "--once", "--apply"]
    assert params["log"] == f"{FLEET_DIR_TOKEN}/reconcile.log"
    assert params["cwd"] == ROOT_TOKEN


def test_absolute_paths_are_normalised_so_revision_decides_bytes(tmp_path: Path) -> None:
    first = read_schedule(build_schedule_tree(tmp_path / "one"))
    second = read_schedule(build_schedule_tree(tmp_path / "two"))
    assert [entry.raw for entry in first.entries] == [entry.raw for entry in second.entries]
    assert all(str(tmp_path) not in entry.raw for entry in first.entries)


def test_an_inexpressible_trigger_is_refused_by_name(tmp_path: Path) -> None:
    def monthly(manifest: dict) -> None:
        # A day-of-month restriction cannot be expressed as a fleet Trigger. The
        # mutation goes through the manifest because that is where the schedule
        # lives: `fleet/cron.py` renders it from there (issue #241/#962).
        job(manifest, "prune")["schedule"] = "0 0 1 * *"

    tree = build_schedule_tree(tmp_path, mutate=monthly)
    schedule = read_schedule(tree)
    assert [finding.code for finding in schedule.refusals] == ["inexpressible-trigger"]
    assert schedule.refusals[0].subject == "ao-fleet-prune"
    assert [entry.marker for entry in schedule.entries] == [
        "ao-fleet-watchdog",
        "ao-fleet-reconcile",
        "ao-fleet-reap",
    ]


def test_a_retagged_entry_is_read_as_the_marker_it_carries(tmp_path: Path) -> None:
    def retag(manifest: dict) -> None:
        job(manifest, "prune")["marker"] = "ao-fleet-watchdog"

    tree = build_schedule_tree(tmp_path, mutate=retag)
    # Retagging prune to the watchdog marker makes the watchdog marker appear
    # twice and the prune marker disappear; the reader reports both facts.
    schedule = read_schedule(tree)
    markers = sorted(entry.marker for entry in schedule.entries)
    assert markers == [
        "ao-fleet-reap",
        "ao-fleet-reconcile",
        "ao-fleet-watchdog",
        "ao-fleet-watchdog",
    ]


def test_parse_trigger_refusals_are_named() -> None:
    from integrations.paperclip.adapters.routines.model import parse_trigger

    for expression in ("@reboot", "0 0 1 * *", "*/0 * * * *", "every minute"):
        with pytest.raises(RoutineRefused) as caught:
            parse_trigger(expression, "some-marker")
        assert caught.value.reason == "inexpressible-trigger"
        assert caught.value.subject == "some-marker"
