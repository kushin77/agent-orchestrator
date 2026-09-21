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
import claims
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


# --- the A2A peer gate (issue #1625, cadence point 3 of #1524) ---------------
#
# The peer-check standard of issue #1549, wired into the tiered loop: a dispatch
# whose target files overlap a live sibling is ABORTED before any model runs,
# escalating per the standard's own protocol. These arms pin that the gate both
# (a) actually STOPS the dispatch — not merely logs — when a sibling overlaps,
# (b) lets a disjoint dispatch proceed unchanged, and (c) is FAIL-CLOSED: an
# unresolvable ledger refuses rather than reporting a false "no siblings".


def _fresh_at() -> str:
    # A claim timestamp inside the 24h TTL, so active_claims keeps the record.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_claim(ledger, name, *, issue, agent, path, lane="portal"):
    ledger.mkdir(parents=True, exist_ok=True)
    (ledger / name).write_text(
        json.dumps(
            {
                "event": "claim",
                "issue": issue,
                "agent": agent,
                "at": _fresh_at(),
                "lane": lane,
                "reason": "fixture",
                "files": [{"path": path, "regions": None}],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def test_a_peer_overlap_aborts_the_dispatch_before_any_model_runs(tmp_path):
    """An injected refusal must STOPS the loop: no invoke, no pass audit row."""
    import pytest

    trail = tmp_path / "dispatch-audit.jsonl"
    invoked: list[tuple[str, str, str]] = []
    comments: list[tuple[int, str]] = []

    with pytest.raises(tiered.TieredRefusal) as exc:
        tiered.run(
            999,
            FIXTURE_BODY,
            ["tier:L0"],
            audit_path=trail,
            agent="ao-sub-1625",
            at=_now(),
            invoke=lambda tier, provider, model: invoked.append((tier, provider, model)) or 0,
            apply_label=lambda issue, label: None,
            post_comment=lambda issue, text: comments.append((issue, text)),
            peer_gate=lambda issue, body, agent: (
                "peer-check REFUSED: OVERLAP — sibling ao-sub-12 on #12 "
                "(channel claude-bus) already holds scripts/peer-check.sh, which "
                "ao-sub-1625 on #999 also claims. Do not start work on those files."
            ),
        )
    assert exc.value.reason == "peer-overlap"
    assert "ao-sub-12" in exc.value.detail
    # The load-bearing assertion: the gate STOPPED the dispatch, it did not
    # merely log a warning. The model was never invoked...
    assert invoked == []
    # ...and no audit row — pass OR fail — was written, so there is no
    # `status:"pass"` for anything downstream to mistake for a completed run.
    records = audit.read(trail)
    assert records == []
    assert not any(record.get("status") == "pass" for record in records)
    # The abort was announced (escalation protocol) before the refusal was raised.
    assert len(comments) == 1
    assert "tiered dispatch aborted before dispatch" in comments[0][1]
    assert "ao-sub-12" in comments[0][1]


def test_a_disjoint_peer_gate_lets_the_dispatch_proceed(tmp_path):
    """The negative control: a gate returning None changes nothing."""
    trail = tmp_path / "dispatch-audit.jsonl"
    invoked: list[tuple[str, str, str]] = []

    outcome = tiered.run(
        999,
        NEGATIVE_BODY,
        ["tier:L0"],
        audit_path=trail,
        agent="ao-sub-1625",
        at=_now(),
        invoke=lambda tier, provider, model: invoked.append((tier, provider, model)) or 0,
        apply_label=lambda issue, label: None,
        post_comment=lambda issue, text: None,
        peer_gate=lambda issue, body, agent: None,
    )
    assert outcome["final_status"] == "pass"
    assert invoked == [("L0", "deepseek", "deepseek-v4-flash")]
    assert len(audit.read(trail)) == 1


def test_the_default_peer_gate_refuses_a_real_overlap_by_name(tmp_path):
    """The real gate (not an injected one) refuses against a fixture ledger."""
    body = "## Ownership\n\nFiles: governance/dispatch/tiered.py\n"
    ledger = tmp_path / "claims"
    _write_claim(
        ledger,
        "0001-00012-ao-sub-12-claim.json",
        issue=12,
        agent="ao-sub-12",
        path="governance/dispatch/tiered.py",
    )
    # Prove the provocation took effect through the engine's OWN reader, so the
    # refusal below is measured against the ledger the gate actually replays.
    live = claims.active_claims(claims.read_ledger(ledger))
    assert [f.path for f in live[12].files] == ["governance/dispatch/tiered.py"]

    refusal = tiered._default_peer_gate(999, body, "ao-sub-1625", ledger=ledger)
    assert refusal is not None
    assert refusal.startswith("peer-check REFUSED: OVERLAP")
    assert "ao-sub-12" in refusal
    assert "#12" in refusal
    assert "governance/dispatch/tiered.py" in refusal


def test_the_default_peer_gate_is_disjoint_for_different_files(tmp_path):
    """Same fixture ledger, non-overlapping path: the gate proceeds (returns None)."""
    body = "## Ownership\n\nFiles: governance/dispatch/tiered.py\n"
    ledger = tmp_path / "claims"
    _write_claim(
        ledger,
        "0001-00013-ao-sub-13-claim.json",
        issue=13,
        agent="ao-sub-13",
        path="registry/personas/README.md",
    )
    assert tiered._default_peer_gate(999, body, "ao-sub-1625", ledger=ledger) is None


def test_the_default_peer_gate_fails_closed_when_no_ledger_resolves(tmp_path):
    """An unresolvable ledger is a refusal, NEVER a silent 'no siblings'."""
    import pytest

    empty = tmp_path / "no-ledger-here"
    empty.mkdir()
    with pytest.raises(tiered.TieredRefusal) as exc:
        tiered._default_peer_gate(
            999, "Files: a.py\n", "ao-sub-1625", root=empty, main_worktree=empty
        )
    assert exc.value.reason == "peer-ledger-unreadable"
    # The negative control that makes this arm load-bearing: the gate did NOT
    # answer None (which would report "disjoint" while ~100 lanes are live).
    assert "no live claim ledger found" in exc.value.detail


def test_the_default_peer_gate_refuses_a_named_but_absent_ledger(tmp_path):
    """A ledger NAMED but absent is CANNOT-ASSESS, not an empty sibling set."""
    import pytest

    absent = tmp_path / "claims-that-was-never-written"
    with pytest.raises(tiered.TieredRefusal) as exc:
        tiered._default_peer_gate(
            999, "Files: a.py\n", "ao-sub-1625", ledger=absent
        )
    assert exc.value.reason == "peer-ledger-unreadable"
    assert "does not exist" in exc.value.detail
