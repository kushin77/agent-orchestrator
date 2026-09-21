"""The tiered try-loop dispatcher (issue #1524): L0 -> L1 -> L2 escalation.

These tests prove the escalation path end-to-end with a deterministic fixture:
an acceptance command that fails at L0 and passes at L1, producing exactly two
ledger entries (one ``tier:L0`` failed attempt, one ``tier:L1`` passed attempt)
and an ``escalate:L1`` label + a comment quoting the L0 failure. The negative
control proves an issue with no ``tier:*`` label defaults to ``tier:L0`` and logs
a warning. The dry-run proves the tier -> model mapping resolves without side
effects.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import audit
import cli
import schema as dispatch_schema
import tiered

FIXTURE_BODY = """Parent: #1510

## Acceptance

- The deterministic escalation oracle: `[ "$AO_TIER" != "L0" ]`

## Escalation

- Max attempts: N = 1
"""

# A body whose acceptance command passes at L0 — used by the negative control,
# which must show an untiered issue completes at L0 rather than crashing.
NEGATIVE_BODY = """## Acceptance

- The negative-control oracle passes at L0: `[ "$AO_TIER" = "L0" ]`

## Escalation

- Max attempts: N = 1
"""


def _now() -> str:
    return datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_read_tier_extracts_the_tier_label():
    assert tiered.read_tier(["tier:L1", "governance-tier"]) == ("L1", None)


def test_read_tier_defaults_to_l0_with_a_warning():
    assert tiered.read_tier(["governance-tier"]) == ("L0", "no tier:* label present — defaulting to tier:L0")


def test_read_tier_refuses_an_unknown_tier():
    import pytest

    with pytest.raises(tiered.TieredRefusal) as exc:
        tiered.read_tier(["tier:L9"])
    assert exc.value.reason == "tier-label-unknown"


def test_resolve_model_maps_each_tier_to_its_deepseek_model():
    assert tiered.resolve_model("L0") == ("deepseek", "deepseek-v4-flash")
    assert tiered.resolve_model("L1") == ("deepseek", "deepseek-v4-pro")
    assert tiered.resolve_model("L2") == ("deepseek", "deepseek-v4-pro")


def test_resolve_model_honours_the_claude_provider():
    assert tiered.resolve_model("L0", provider="claude") == ("claude", "haiku")
    assert tiered.resolve_model("L1", provider="claude") == ("claude", "sonnet")
    assert tiered.resolve_model("L2", provider="claude") == ("claude", "opus")


def test_parse_acceptance_commands_reads_backticks_and_fenced_blocks():
    body = """## Acceptance

- run this: `echo hello`
- ```bash
  echo one
  echo two
  ```
"""
    commands = tiered.parse_acceptance_commands(body)
    assert commands == ["echo hello", "echo one", "echo two"]


def test_escalation_path_produces_exactly_two_ledger_entries(tmp_path):
    """L0 fails once (N=1) -> escalate:L1 -> L1 passes: exactly 2 audit entries."""
    trail = tmp_path / "dispatch-audit.jsonl"
    applied_labels: list[tuple[int, str]] = []
    comments: list[tuple[int, str]] = []

    outcome = tiered.run(
        999,
        FIXTURE_BODY,
        ["tier:L0"],
        audit_path=trail,
        agent="ao-sub-1524",
        at=_now(),
        invoke=lambda tier, provider, model: 0,
        apply_label=lambda issue, label: applied_labels.append((issue, label)),
        post_comment=lambda issue, text: comments.append((issue, text)),
    )

    records = audit.read(trail)
    assert len(records) == 2
    assert records[0]["tier"] == "L0"
    assert records[0]["status"] == "fail"
    assert records[0]["kind"] == "tiered_dispatch"
    assert records[1]["tier"] == "L1"
    assert records[1]["status"] == "pass"
    # Every record validates against the frozen audit shape.
    for record in records:
        assert dispatch_schema.problems(record, dispatch_schema.SHAPE_AUDIT_RECORD) == ()

    # The escalation label was applied, and the comment quotes the L0 output.
    assert applied_labels == [(999, "escalate:L1")]
    assert len(comments) == 1
    assert "tier:L0" in comments[0][1]
    assert outcome["final_status"] == "pass"
    assert outcome["final_tier"] == "L1"


def test_negative_control_an_escalate_label_does_not_break_the_in_lane_climb(tmp_path):
    """#1851 negative control: the frontier's `escalate:*` refusal is frontier-only.

    `tiered.py` climbs tiers INSIDE one lane run and never reads the frontier, so
    an issue that already carries `escalate:L1` must still be re-dispatched one
    tier up (L0 -> L1) — the refusal must not read `escalate:*` as "never dispatch
    again". This is the same two-attempt climb as the escalation test above, with
    the escalate label already present on the issue at dispatch time.
    """
    trail = tmp_path / "dispatch-audit.jsonl"
    outcome = tiered.run(
        999,
        FIXTURE_BODY,
        ["tier:L0", "escalate:L1"],
        audit_path=trail,
        agent="ao-sub-1524",
        at=_now(),
        invoke=lambda tier, provider, model: 0,
        apply_label=lambda issue, label: None,
        post_comment=lambda issue, text: None,
    )
    records = audit.read(trail)
    assert [record["tier"] for record in records] == ["L0", "L1"]
    assert [record["status"] for record in records] == ["fail", "pass"]
    assert outcome["final_status"] == "pass"
    assert outcome["final_tier"] == "L1"


def test_negative_control_no_tier_label_defaults_to_l0_and_logs_warning(tmp_path):
    trail = tmp_path / "dispatch-audit.jsonl"
    outcome = tiered.run(
        999,
        NEGATIVE_BODY,
        ["governance-tier"],
        audit_path=trail,
        agent="ao-sub-1524",
        at=_now(),
        invoke=lambda tier, provider, model: 0,
        apply_label=lambda issue, label: None,
        post_comment=lambda issue, text: None,
    )
    assert outcome["tier"] == "L0"
    assert outcome["warning"] is not None
    records = audit.read(trail)
    # Negative control: the loop did NOT crash on the missing label; it ran at L0
    # and completed there (the body's acceptance passes at L0), and the warning
    # was recorded in the ledger itself.
    assert len(records) == 1
    assert records[0]["tier"] == "L0"
    assert records[0]["status"] == "pass"
    assert "defaulting to tier:L0" in (records[0]["detail"] or "")
    assert outcome["final_status"] == "pass"


def test_dry_run_resolves_mapping_without_dispatching(tmp_path):
    trail = tmp_path / "dispatch-audit.jsonl"
    outcome = tiered.run(
        999,
        FIXTURE_BODY,
        ["tier:L1"],
        audit_path=trail,
        agent="ao-sub-1524",
        at=_now(),
        dry_run=True,
    )
    assert outcome["dry_run"] is True
    assert outcome["provider"] == "deepseek"
    assert outcome["model"] == "deepseek-v4-pro"
    # No ledger write, no label, no comment on a dry-run.
    assert audit.read(trail) == []


def test_cli_try_loop_dry_run_prints_the_mapping(tmp_path, capsys):
    body_file = tmp_path / "body.md"
    body_file.write_text(FIXTURE_BODY, encoding="utf-8")
    trail = tmp_path / "dispatch-audit.jsonl"
    rc = cli.main([
        "try-loop",
        "--issue", "999",
        "--labels", json.dumps(["tier:L0"]),
        "--body-file", str(body_file),
        "--audit", str(trail),
        "--dry-run",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["dry_run"] is True
    assert payload["tier"] == "L0"
    assert payload["model"] == "deepseek-v4-flash"
    assert audit.read(trail) == []


def test_cli_try_loop_negative_control_defaults_to_l0(tmp_path, capsys):
    body_file = tmp_path / "body.md"
    body_file.write_text(NEGATIVE_BODY, encoding="utf-8")
    trail = tmp_path / "dispatch-audit.jsonl"
    rc = cli.main([
        "try-loop",
        "--issue", "999",
        "--labels", json.dumps(["governance-tier"]),
        "--body-file", str(body_file),
        "--audit", str(trail),
        "--no-gh",
        "--no-dispatch",
    ])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["tier"] == "L0"
    assert payload["warning"] is not None
    # The L0 acceptance command passes at L0, so the run completes OK.
    assert rc == 0
    records = audit.read(trail)
    assert len(records) == 1
    assert records[0]["tier"] == "L0"
    assert records[0]["status"] == "pass"
