"""The CLI: tri-state exit, the registry seam, and the determinism verb."""

from __future__ import annotations

import json
from pathlib import Path

from integrations.paperclip.adapters.routines import cli


def test_project_is_ok_and_prints_canonical_json(schedule_tree: Path, capsys) -> None:
    rc = cli.main(["--root", str(schedule_tree), "project"])
    captured = capsys.readouterr()
    assert rc == 0
    document = json.loads(captured.out)
    assert document["view"] == "routines"
    assert document["source"] == "fleet/cron.py"
    assert document["count"] == 4
    assert sorted(routine["marker"] for routine in document["routines"]) == [
        "ao-fleet-prune",
        "ao-fleet-reap",
        "ao-fleet-reconcile",
        "ao-fleet-watchdog",
    ]


def test_verify_is_ok(schedule_tree: Path, capsys) -> None:
    rc = cli.main(["--root", str(schedule_tree), "verify"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "each projected once" in captured.out


def test_project_refuses_an_owner_less_registry(
    schedule_tree: Path, tmp_path: Path, capsys
) -> None:
    dump = tmp_path / "registry.json"
    assert cli.main(["--root", str(schedule_tree), "registry"]) == 0
    registry = json.loads(capsys.readouterr().out)
    for routine in registry["routines"]:
        if routine["id"] == "fleet-reconcile":
            routine["owner"] = ""
    dump.write_text(json.dumps(registry), encoding="utf-8")

    rc = cli.main(["--root", str(schedule_tree), "--registry", str(dump), "project"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "routine-unowned" in captured.err
    assert "fleet-reconcile" in captured.err


def test_project_is_cannot_assess_when_the_schedule_source_is_missing(
    tmp_path: Path, capsys
) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    rc = cli.main(["--root", str(bare), "project"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "CANNOT-ASSESS" in captured.err


def test_registry_declares_no_schedule(capsys) -> None:
    rc = cli.main(["registry"])
    registry = json.loads(capsys.readouterr().out)
    assert rc == 0
    for routine in registry["routines"]:
        assert set(routine) == {"id", "marker", "owner", "lane", "anchor"}


def test_a_malformed_registry_is_refused_by_name(schedule_tree: Path, tmp_path: Path, capsys) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"routines": [{"id": "x", "marker": "y"}]}), encoding="utf-8")
    rc = cli.main(["--root", str(schedule_tree), "--registry", str(bad), "project"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "bad-registry" in captured.err
