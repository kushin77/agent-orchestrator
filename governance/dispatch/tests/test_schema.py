"""The frozen dispatch record shapes (issue #885), validated with the stdlib-only
subset validator reused from governance/modules/schema.py."""

from __future__ import annotations

import claims
import pytest
import schema as dispatch_schema
from model import ClaimEvent, Issue


def test_schema_document_is_self_consistent():
    doc = dispatch_schema.load()
    assert doc["$id"] == "ao.dispatch/dispatch-v1"


def test_real_claim_event_validates():
    event = ClaimEvent(event="claim", issue=1, agent="agent-a", at="2026-01-01T00:00:00Z", lane="lane-a")
    assert dispatch_schema.problems(event.to_json(), dispatch_schema.SHAPE_CLAIM_EVENT) == ()


def test_real_issue_row_validates():
    issue = Issue(1, "title", parent=2, blocked_by=(3, 4))
    assert dispatch_schema.problems(issue.to_json(), dispatch_schema.SHAPE_ISSUE_ROW) == ()


def test_real_queue_document_validates():
    import owner_queue
    import policy

    # The packaged queue.yaml (not the autouse-patched, test-isolated
    # `owner_queue.DEFAULT_PATH`, which points at a nonexistent fixture file).
    doc = owner_queue.load(policy._PKG_DIR / "queue.yaml")
    assert doc is not None
    assert dispatch_schema.problems(doc, dispatch_schema.SHAPE_QUEUE_DOCUMENT) == ()


def test_invalid_document_is_refused_by_name():
    with pytest.raises(dispatch_schema.SchemaViolation, match="event"):
        dispatch_schema.validate({"event": "bogus", "issue": 1, "agent": "a", "at": "t"}, dispatch_schema.SHAPE_CLAIM_EVENT)


def test_unknown_shape_is_cannot_assess():
    with pytest.raises(dispatch_schema.SchemaUnavailable):
        dispatch_schema.problems({}, "not-a-declared-shape")
