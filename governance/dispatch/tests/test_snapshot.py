"""The board refresh must be COMPLETE and the write must be ATOMIC (issue #2025).

`.board/snapshot.json` is a tracked artefact that `make verify` rewrites while
~15 other checks read it. Two measured defects:

* the fetch was capped (`gh issue list --limit 1000`) while **1019** issues
  existed, so the board silently dropped the 19 oldest ids -- exactly the
  long-lived ids a committed spine still references (the closed `issue-4` that
  `governance/knowledge/catalog.json` points at);
* `save()` wrote the target in place, so a reader could observe a half-written
  board and a crash mid-write left a truncated tracked document behind.

Both are reachable OFFLINE through the injected `runner` seam, which is why these
live in pytest rather than in a shell arm: a shell check cannot pass a runner.

Every arm here is load-bearing: each fails if its fix is reverted (see the
docstring of each test for the mutation that reds it).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from governance.dispatch import snapshot as snapshot_mod


def _issue(number: int, **extra):
    """A record shaped like the REST issues endpoint's payload."""
    row = {
        "number": number,
        "title": "issue %d" % number,
        "state": "closed",
        "closed_at": "2026-01-01T00:00:00Z",
        "labels": [],
        "milestone": None,
        "body": "",
    }
    row.update(extra)
    return row


def _runner(rows, seen=None, returncode=0, stderr=""):
    """A `gh` stand-in: `gh api --paginate --jq '.[]'` streams one object/line.

    It HONOURS a `--limit` if the argv carries one, so a reintroduced cap is
    visible to the arms rather than being silently ignored by the stub — a stub
    that returns everything whatever the argv makes a cap arm vacuous.
    """

    def run(cmd, **kwargs):
        if seen is not None:
            seen["cmd"] = list(cmd)
        out = rows
        for index, part in enumerate(cmd):
            if part == "--limit" and index + 1 < len(cmd):
                out = rows[: int(cmd[index + 1])]
        stdout = "\n".join(json.dumps(row) for row in out) + "\n"
        return subprocess.CompletedProcess(
            args=cmd, returncode=returncode, stdout=stdout, stderr=stderr
        )

    return run


def test_a_board_larger_than_the_old_cap_is_fetched_whole(tmp_path):
    """REGRESSION: 1500 issues must survive the fetch.

    This is a *behavioural* arm, not just a persistence one: the stub honours a
    `--limit` in the argv, so a reintroduced cap returns 1000 rows here.

    Mutations that red it: reintroduce a bounded `--limit` (the stub then
    returns 1000), or truncate client-side anywhere in the fetch.
    """
    path = tmp_path / "snapshot.json"
    ok, detail = snapshot_mod.refresh(
        path, runner=_runner([_issue(n) for n in range(1, 1501)])
    )
    assert ok, detail
    loaded = snapshot_mod.load(path, apply_queue=False)
    assert len(loaded.issues) == 1500
    assert 1 in loaded.issues and 1500 in loaded.issues


def test_a_malformed_row_is_refused_by_name_not_skipped(tmp_path):
    """A board must never be silently incomplete — that is the whole defect.

    `refresh` catches `RuntimeError` and returns `(False, reason)` (its
    documented shape), so the assertion is on the REFUSAL and on the board not
    being written at all — a refused refresh must not leave a partial artefact.

    Mutation that reds it: go back to `continue` on a non-dict row, and the
    refresh reports success with a short board.
    """
    target = tmp_path / "s.json"
    ok, detail = snapshot_mod.refresh(
        target, runner=_runner([_issue(1), "not-an-object"])
    )
    assert ok is False, "a malformed row must refuse the refresh, not shrink the board"
    assert "non-object line" in detail, detail
    assert not target.exists(), "a refused refresh must not write a board at all"


def test_the_fetch_cannot_be_capped(tmp_path):
    """The argv must page to exhaustion, never carry a truncating bound.

    Mutation that reds it: reintroduce `--limit` (the surface that capped this
    board) -- the second assertion fails by name.
    """
    seen: dict = {}
    snapshot_mod.refresh(tmp_path / "s.json", runner=_runner([_issue(1)], seen=seen))
    cmd = seen["cmd"]
    assert "--paginate" in cmd
    assert "--limit" not in cmd, "a capped fetch is what truncated the board (#2025)"
    assert any("per_page=" in str(part) for part in cmd)


def test_pull_requests_are_excluded_and_closed_at_is_normalised(tmp_path):
    """Two shapes the REST endpoint differs on, normalised at the fetch seam.

    Mutation that reds it: drop the `pull_request` filter (a PR lands in the
    board) or drop the `closed_at` -> `closedAt` rename (the closed timestamp
    silently blanks).
    """
    path = tmp_path / "s.json"
    rows = [
        _issue(5, closed_at="2026-03-04T05:06:07Z"),
        _issue(6, pull_request={"url": "https://example.invalid/pr/6"}),
    ]
    ok, detail = snapshot_mod.refresh(path, runner=_runner(rows))
    assert ok, detail

    # Assert on what was WRITTEN, not on what snapshot.load() returns: `load()`
    # does not read `closed_at` back into Issue (a pre-existing gap, disclosed in
    # the PR), so asserting through it would test the loader rather than the fix.
    stored = json.loads(path.read_text(encoding="utf-8"))
    by_number = {entry["number"]: entry for entry in stored["issues"]}
    assert set(by_number) == {5}, "a pull request must not be written into the board"
    assert by_number[5]["closed_at"] == "2026-03-04T05:06:07Z", (
        "the REST endpoint names this `closed_at` while build_snapshot reads "
        "`closedAt`; without the rename the persisted timestamp is blank"
    )
    assert by_number[5]["state"] == "CLOSED", (
        "the REST endpoint returns lowercase state where the committed artefact "
        "and every check fixture use UPPERCASE; shipping lowercase would flip "
        "863 rows in a tracked artefact"
    )


def test_save_never_writes_the_target_in_place(tmp_path, monkeypatch):
    """The tracked board is written by rename, never by a write into the target.

    Mutation that reds it: go back to `target.write_text(...)` -- the target then
    appears in the recorded write paths and the first assertion fails.
    """
    target = tmp_path / "snapshot.json"
    written: list[Path] = []
    real_write_text = Path.write_text

    def spy(self, *args, **kwargs):
        written.append(Path(self))
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", spy)
    snapshot_mod.save(
        snapshot_mod.Snapshot(generated_at="2026-01-01T00:00:00Z", source="test", issues={}),
        target,
    )

    assert written, "save() must write something"
    assert target not in written, (
        "save() wrote the tracked target in place; a reader can observe a "
        "half-written board (#2025) -- it must write a temp file and rename"
    )
    assert json.loads(target.read_text(encoding="utf-8"))["issues"] == []
    # and it leaves no temp file behind
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".")]
