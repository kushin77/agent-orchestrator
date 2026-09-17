"""The append-only audit trail records exactly one line per verdict (issue #885)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from governance.isolation import journal, schema  # noqa: E402
from governance.isolation.violation import Violation  # noqa: E402


def test_append_writes_exactly_one_record_for_a_refusal(tmp_path):
    before = journal.read_all(tmp_path)
    assert before == []

    problems = [Violation("branch-mismatch", "on the wrong branch")]
    entry = journal.append(tmp_path, "abc123", problems)

    lines = journal.journal_path(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert entry.ok is False
    assert entry.codes == ("branch-mismatch",)

    record = json.loads(lines[0])
    schema.validate(schema.JOURNAL_ENTRY, record)


def test_append_is_append_only_across_calls(tmp_path):
    journal.append(tmp_path, "lane-a", [])
    journal.append(tmp_path, "lane-b", [Violation("worktree-missing", "gone")])
    entries = journal.read_all(tmp_path)
    assert [e.session_id for e in entries] == ["lane-a", "lane-b"]
    assert entries[0].ok is True
    assert entries[1].ok is False
    assert entries[1].codes == ("worktree-missing",)


def test_last_for_returns_most_recent_verdict(tmp_path):
    journal.append(tmp_path, "lane-a", [Violation("worktree-missing", "gone")])
    journal.append(tmp_path, "lane-a", [])
    last = journal.last_for(tmp_path, "lane-a")
    assert last is not None
    assert last.ok is True


def test_last_for_unknown_lane_is_none(tmp_path):
    journal.append(tmp_path, "lane-a", [])
    assert journal.last_for(tmp_path, "no-such-lane") is None
