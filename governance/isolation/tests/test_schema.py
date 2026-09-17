"""isolation.schema.json freezes the record shapes this package persists (issue #885)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from governance.isolation import schema  # noqa: E402


def test_schema_file_is_itself_well_formed():
    schema._document()  # raises SchemaUnavailable (via the subset validator) on a bad schema


def test_valid_lane_identity_record_passes():
    record = {
        "session_id": "deadbeef1234",
        "issue": 885,
        "agent_id": "sync-sme",
        "lane": "isolation",
        "branch": "issue-885-isolation-elite",
        "worktree": "/tmp/ao-worktrees/ao-885-deadbeef1234",
        "repo_slug": "kushin77/agent-orchestrator",
        "author_name": "agent-sync-sme",
        "author_email": "agent+sync-sme@agents.invalid",
    }
    schema.validate(schema.LANE_IDENTITY, record)


def test_lane_identity_record_missing_a_field_is_refused():
    record = {
        "session_id": "deadbeef1234",
        "issue": 885,
        "agent_id": "sync-sme",
        "lane": "isolation",
        "branch": "issue-885-isolation-elite",
        "worktree": "/tmp/whatever",
        "repo_slug": "kushin77/agent-orchestrator",
        "author_name": "agent-sync-sme",
        # author_email omitted
    }
    with pytest.raises(schema.RecordSchemaViolation, match="author_email"):
        schema.validate(schema.LANE_IDENTITY, record)


def test_lane_identity_record_with_extra_field_is_refused():
    record = {
        "session_id": "deadbeef1234",
        "issue": 885,
        "agent_id": "sync-sme",
        "lane": "isolation",
        "branch": "issue-885-isolation-elite",
        "worktree": "/tmp/whatever",
        "repo_slug": "kushin77/agent-orchestrator",
        "author_name": "agent-sync-sme",
        "author_email": "agent+sync-sme@agents.invalid",
        "unexpected_field": "surprise",
    }
    with pytest.raises(schema.RecordSchemaViolation):
        schema.validate(schema.LANE_IDENTITY, record)


def test_valid_speculative_attestation_passes():
    record = {
        "session_id": "deadbeef1234",
        "branch": "issue-885-isolation-elite",
        "speculative_base": "issue-878-lane-6",
        "speculative_base_sha": "abcd1234",
        "merge_base": "ef012345",
        "git_sha": "9988776655",
    }
    schema.validate(schema.SPECULATIVE_ATTESTATION, record)


def test_invalid_speculative_attestation_is_refused():
    with pytest.raises(schema.RecordSchemaViolation):
        schema.validate(schema.SPECULATIVE_ATTESTATION, {"branch": "issue-1"})


def test_valid_journal_entry_passes():
    record = {
        "schema": "ao.isolation/journal-entry-v1",
        "ts": 1758000000.0,
        "session_id": "deadbeef1234",
        "ok": True,
        "codes": [],
    }
    schema.validate(schema.JOURNAL_ENTRY, record)


def test_journal_entry_wrong_schema_tag_is_refused():
    record = {
        "schema": "wrong-tag",
        "ts": 1.0,
        "session_id": "x",
        "ok": True,
        "codes": [],
    }
    with pytest.raises(schema.RecordSchemaViolation):
        schema.validate(schema.JOURNAL_ENTRY, record)


def test_valid_landed_baseline_entry_passes():
    record = {
        "sha": "afebf84844b76e692f237cafdead2da9d3b688de",
        "code": "commit-missing-ticket-trailer",
        "why": "no Refs trailer anywhere in the message",
    }
    schema.validate(schema.LANDED_BASELINE_ENTRY, record)


def test_landed_baseline_entry_bad_code_is_refused():
    record = {"sha": "abc", "code": "not-a-declared-code", "why": "..."}
    with pytest.raises(schema.RecordSchemaViolation):
        schema.validate(schema.LANDED_BASELINE_ENTRY, record)


def test_real_landed_baseline_entries_all_satisfy_the_frozen_shape():
    """The frozen shape must actually match production data, or it is decorative."""
    import json

    baseline_path = Path(__file__).resolve().parents[1] / "landed-baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    for entry in baseline["entries"]:
        schema.validate(schema.LANDED_BASELINE_ENTRY, entry)
