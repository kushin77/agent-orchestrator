"""Declared file ownership parsing (issue #740, dispatch half).

Extends the issue-body convention (``snapshot.parse_edges``) with a sibling
parser for the ``Files:`` / ``Files owned (disjoint):`` line, so ``Issue.files``
is populated from real board records without a separate fetch.
"""

from __future__ import annotations

import snapshot as snapshot_mod


def test_parse_files_reads_the_files_convention():
    body = "Parent: #1\n\nFiles: a.py, b/c.py\n"
    assert snapshot_mod.parse_files(body) == ("a.py", "b/c.py")


def test_parse_files_reads_owned_disjoint_spelling():
    body = "Files owned (disjoint): governance/dispatch/order.py\n"
    assert snapshot_mod.parse_files(body) == ("governance/dispatch/order.py",)


def test_parse_files_empty_when_undeclared():
    assert snapshot_mod.parse_files("Parent: #1\n") == ()


def test_build_snapshot_threads_files_onto_the_issue():
    records = [
        {"number": 1, "title": "x", "state": "open", "body": "Files: a.py, b.py"},
    ]
    snapshot = snapshot_mod.build_snapshot(records, source="test")
    assert snapshot.get(1).files == ("a.py", "b.py")


def test_snapshot_round_trips_files_through_json(tmp_path):
    records = [{"number": 1, "title": "x", "state": "open", "body": "Files: a.py"}]
    snapshot = snapshot_mod.build_snapshot(records, source="test")
    path = tmp_path / "snapshot.json"
    snapshot_mod.save(snapshot, path)
    reloaded = snapshot_mod.load(path, apply_queue=False)
    assert reloaded.get(1).files == ("a.py",)
