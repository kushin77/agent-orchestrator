"""Cross-repo chain edges (issue #181, gap 1).

The ``Blocked-by:``/``Parent:`` parser only understood same-repo ``#N`` numbers.
This suite proves the extension: a cross-repo reference written ``owner/repo#N``
is captured into ``Issue.cross_refs`` without disturbing same-repo parsing, a
mixed line captures both, and a malformed cross-repo ref is ignored (it simply
does not match the strict pattern — the same fail-closed posture as ``_numbers``
ignoring non-digits).
"""

from __future__ import annotations

import json

import snapshot as snapshot_mod
from model import Issue, Snapshot


def _issue(body: str) -> Issue:
    snapshot = snapshot_mod.build_snapshot(
        [{"number": 900, "title": "t", "state": "open", "body": body}],
        source="test",
        generated_at="2026-09-13T00:00:00Z",
    )
    return snapshot.get(900)


# --- same-repo behaviour is unchanged ---------------------------------------


def test_same_repo_parent_and_blocker_still_parse():
    body = "Parent: #152\nBlocked-by: #9, #10\n"
    parent, blocked, cross = snapshot_mod.parse_edges(body)
    assert parent == 152
    assert blocked == (9, 10)
    assert cross == ()


def test_same_repo_part_of_spelling_still_parses():
    parent, blocked, cross = snapshot_mod.parse_edges("Part-of: #152\n")
    assert parent == 152
    assert blocked == ()
    assert cross == ()


# --- cross-repo refs --------------------------------------------------------


def test_cross_repo_blocker_parses_into_cross_refs():
    body = "Blocked-by: kushin77/code-indexing#128\n"
    parent, blocked, cross = snapshot_mod.parse_edges(body)
    assert parent is None
    assert blocked == ()
    assert cross == ("kushin77/code-indexing#128",)


def test_cross_repo_parent_parses_into_cross_refs():
    parent, blocked, cross = snapshot_mod.parse_edges("Parent: kushin77/deepseek#91\n")
    assert parent is None
    assert blocked == ()
    assert cross == ("kushin77/deepseek#91",)


def test_mixed_line_parses_both_same_repo_and_cross_repo():
    body = "Blocked-by: #9, kushin77/code-indexing#128\n"
    parent, blocked, cross = snapshot_mod.parse_edges(body)
    assert parent is None
    assert blocked == (9,)
    assert cross == ("kushin77/code-indexing#128",)


def test_cross_repo_ref_is_deduplicated_and_sorted():
    body = (
        "Blocked-by: kushin77/code-indexing#128, kushin77/deepseek#91\n"
        "Blocked-by: kushin77/code-indexing#128\n"
    )
    parent, blocked, cross = snapshot_mod.parse_edges(body)
    assert parent is None
    assert blocked == ()
    assert cross == ("kushin77/code-indexing#128", "kushin77/deepseek#91")


def test_malformed_cross_repo_ref_is_ignored():
    body = "Blocked-by: kushin77/code-indexing#\n"
    parent, blocked, cross = snapshot_mod.parse_edges(body)
    assert parent is None
    assert blocked == ()
    assert cross == ()


def test_cross_repo_mention_outside_an_edge_line_is_not_an_edge():
    """A body that mentions another repo is not a chain edge (issue #181's own body does this)."""
    body = "Ground truth: see kushin77/code-indexing#128 for the contract.\n"
    parent, blocked, cross = snapshot_mod.parse_edges(body)
    assert parent is None
    assert blocked == ()
    assert cross == ()


# --- threading through the snapshot builder and model -----------------------


def test_build_snapshot_threads_cross_refs_into_the_issue():
    issue = _issue("Blocked-by: #7, kushin77/code-indexing#128\n")
    assert issue.blocked_by == (7,)
    assert issue.cross_refs == ("kushin77/code-indexing#128",)


def test_load_defaults_cross_refs_when_the_field_is_absent(tmp_path):
    """The committed snapshot predates ``cross_refs``; loading it must not break."""
    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps(
            {
                "generated_at": "2026-09-13T00:00:00Z",
                "source": "test",
                "issues": [
                    {
                        "number": 900,
                        "title": "t",
                        "state": "open",
                        "milestone": "",
                        "labels": [],
                        "parent": None,
                        "blocked_by": [7],
                        "closed_at": "",
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    loaded = snapshot_mod.load(path)
    issue = loaded.get(900)
    assert issue.blocked_by == (7,)
    assert issue.cross_refs == ()


def test_save_load_round_trip_preserves_cross_refs(tmp_path):
    snapshot = snapshot_mod.Snapshot(
        generated_at="2026-09-13T00:00:00Z",
        source="test",
        issues={900: Issue(900, "t", blocked_by=(7,), cross_refs=("kushin77/code-indexing#128",))},
    )
    path = tmp_path / "snapshot.json"
    snapshot_mod.save(snapshot, path)
    loaded = snapshot_mod.load(path)
    assert loaded.get(900).blocked_by == (7,)
    assert loaded.get(900).cross_refs == ("kushin77/code-indexing#128",)
