"""CLI + rebuildability tests (issue #401)."""

from __future__ import annotations

import json

import importlib.util as _importlib_util  # noqa: E402
from pathlib import Path as _ConftestPath  # noqa: E402

# A bare ``from conftest import ...`` is not safe here: when this suite is
# collected alongside other governance suites, every one of their
# ``tests/conftest.py`` files lands under the same bare module identity
# ``conftest`` in ``sys.modules``, so whichever conftest is imported LAST
# silently wins the name for the rest of collection (issues #699, #702, #1042).
# Loading this file's own conftest by absolute path guarantees this module
# always gets ITS directory's conftest regardless of collection order.
_conftest_spec = _importlib_util.spec_from_file_location(
    "governance_ticket_tests_conftest", _ConftestPath(__file__).with_name("conftest.py")
)
_conftest = _importlib_util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
issue = _conftest.issue
write_board = _conftest.write_board
from cli import main
from model import STORE_RELPATH

TICKET_10 = "kushin77/agent-orchestrator#10"


def _prepared(root):
    write_board(root, [issue(10, parent=4)])
    return root


def test_project_then_verify_round_trips(root, capsys):
    _prepared(root)
    assert main(["project", "--root", str(root)]) == 0
    store = root / STORE_RELPATH
    assert store.is_file()
    assert main(["verify", "--root", str(root)]) == 0
    output = capsys.readouterr().out
    assert "ticket-projection: OK" in output


def test_verify_detects_a_tampered_store_and_names_the_field(root, capsys):
    _prepared(root)
    assert main(["project", "--root", str(root)]) == 0
    store = root / STORE_RELPATH
    payload = json.loads(store.read_text(encoding="utf-8"))
    payload["generated_at"] = "tampered"
    store.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["verify", "--root", str(root)]) == 1
    err = capsys.readouterr().err
    assert "store-mismatch" in err
    assert "generated_at" in err


def test_verify_rebuilds_a_deleted_store(root, capsys):
    _prepared(root)
    assert main(["project", "--root", str(root)]) == 0
    store = root / STORE_RELPATH
    before = store.read_text(encoding="utf-8")
    store.unlink()

    assert main(["verify", "--root", str(root)]) == 0
    capsys.readouterr()
    assert store.read_text(encoding="utf-8") == before


def test_project_is_idempotent_across_runs(root):
    _prepared(root)
    assert main(["project", "--root", str(root)]) == 0
    first = (root / STORE_RELPATH).read_text(encoding="utf-8")
    assert main(["project", "--root", str(root)]) == 0
    second = (root / STORE_RELPATH).read_text(encoding="utf-8")
    assert first == second


def test_verify_refuses_a_stamped_store(root, capsys):
    _prepared(root)
    assert main(["project", "--root", str(root), "--stamp", "1"]) == 0
    assert main(["verify", "--root", str(root)]) == 1
    err = capsys.readouterr().err
    assert "store-mismatch" in err
    assert "generated_at" in err


def test_a_mismatch_is_repeatable_and_leaves_the_store_in_place(root, capsys):
    _prepared(root)
    assert main(["project", "--root", str(root)]) == 0
    store = root / STORE_RELPATH
    payload = json.loads(store.read_text(encoding="utf-8"))
    payload["generated_at"] = "tampered"
    store.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # A second run must still refuse: deleting the store on a mismatch would make
    # the next run compare against nothing and pass (a false green).
    assert main(["verify", "--root", str(root)]) == 1
    capsys.readouterr()
    assert store.is_file()
    assert main(["verify", "--root", str(root)]) == 1
    err = capsys.readouterr().err
    assert "store-mismatch" in err


def test_a_missing_board_is_cannot_assess(root, capsys):
    (root / ".board" / "snapshot.json").unlink(missing_ok=True)
    assert main(["project", "--root", str(root)]) == 2
    assert "CANNOT-ASSESS" in capsys.readouterr().err


def test_negative_control_file_provokes_and_names_the_ticket(root, tmp_path, capsys):
    _prepared(root)
    control = tmp_path / "control.json"
    control.write_text(
        json.dumps(
            [
                {
                    "ticket": TICKET_10,
                    "field": "goal",
                    "producer": "rogue/lane",
                    "value": "#1",
                    "where": "negative-control",
                }
            ]
        ),
        encoding="utf-8",
    )
    assert main(["project", "--root", str(root), "--negative-control", str(control)]) == 1
    err = capsys.readouterr().err
    assert "authority-two-writers" in err
    assert TICKET_10 in err
    assert not (root / STORE_RELPATH).exists()
