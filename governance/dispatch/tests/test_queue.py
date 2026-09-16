"""The owner's committed dispatch queue (issue #928).

Covers: an out-of-order claim is refused with a `queue:` detail even when
GitHub carries no `blocked_by` edge for it; the in-order successor is
accepted; a closed queue-issue drops out of the chain (unblocking its
successor); and `queue.validate()` catches a cycle in the `blocked_by`
overrides.
"""

from __future__ import annotations

import claims
import order
import owner_queue as queue_mod
from model import REASON_BLOCKED, Issue, Snapshot


def _snapshot(*issues: Issue) -> Snapshot:
    return Snapshot(
        generated_at="2026-09-16T00:00:00Z",
        source="test",
        issues={issue.number: issue for issue in issues},
    )


def _two_wave_queue() -> dict:
    return {
        "waves": [
            {"name": "wave-0", "issues": [101, 102, 103]},
            {"name": "wave-1", "issues": [201, 202]},
        ],
        "blocked_by": {},
    }


def test_out_of_order_claim_is_refused_with_queue_detail(tmp_path):
    """#202 (wave 1) has no GitHub blocked_by edge, but the queue orders it after
    #101-#103 (wave 0) and #201 (earlier in wave 1) — all still open."""
    snapshot = _snapshot(
        Issue(101, "w0 first", state="open"),
        Issue(102, "w0 second", state="open"),
        Issue(103, "w0 third", state="open"),
        Issue(201, "w1 first", state="open"),
        Issue(202, "w1 second", state="open"),
    )
    overlaid = queue_mod.overlay(snapshot, _two_wave_queue())
    issue = overlaid.get(202)
    assert set(issue.blocked_by) == {101, 102, 103, 201}

    verdict = order.eligible(overlaid, 202, queue_data=_two_wave_queue())
    assert verdict.eligible is False
    assert verdict.reason == REASON_BLOCKED
    assert "queue:" in verdict.detail
    assert "#202" in verdict.detail

    try:
        claims.arbitrate(
            202,
            "agent",
            "lane",
            overlaid,
            ledger=tmp_path / "claims",
            lock_dir=tmp_path / "locks",
            queue_data=_two_wave_queue(),
        )
        raised = False
    except claims.ClaimRefused as exc:
        raised = True
        assert exc.reason == REASON_BLOCKED
        assert "queue:" in exc.detail
    assert raised


def test_in_order_claim_is_accepted():
    """#101 is the first entry of wave 0: no queue blocker, so it is not refused
    by the queue (it is still subject to the ordinary chain-edge rules)."""
    snapshot = _snapshot(
        Issue(101, "w0 first", milestone="M1", state="open"),
        Issue(102, "w0 second", state="open"),
    )
    overlaid = queue_mod.overlay(snapshot, _two_wave_queue())
    issue = overlaid.get(101)
    assert issue.blocked_by == ()

    verdict = order.eligible(overlaid, 101)
    assert verdict.eligible is True


def test_closed_issue_drops_out_of_the_chain():
    """When #101/#102/#103 (wave 0) all close, wave 1's #201 is unblocked by the
    queue even though it declared no GitHub edge of its own."""
    snapshot = _snapshot(
        Issue(101, "w0 first", state="closed"),
        Issue(102, "w0 second", state="closed"),
        Issue(103, "w0 third", state="closed"),
        Issue(201, "w1 first", milestone="M1", state="open"),
        Issue(202, "w1 second", state="open"),
    )
    overlaid = queue_mod.overlay(snapshot, _two_wave_queue())
    issue = overlaid.get(201)
    assert issue.blocked_by == ()

    verdict = order.eligible(overlaid, 201)
    assert verdict.eligible is True

    # #202 is still blocked, but only by #201 now (the closed wave-0 issues
    # dropped out of the union).
    still_blocked = overlaid.get(202)
    assert set(still_blocked.blocked_by) == {201}


def test_validate_catches_a_cycle():
    data = {
        "waves": [{"name": "w", "issues": [301, 302]}],
        "blocked_by": {301: [302], 302: [301]},
    }
    snapshot = _snapshot(Issue(301, "a", state="open"), Issue(302, "b", state="open"))
    problems = queue_mod.validate(data, snapshot)
    assert any("cycle" in problem for problem in problems)


def test_validate_catches_duplicates_and_unknown_numbers():
    data = {
        "waves": [
            {"name": "w0", "issues": [401, 402]},
            {"name": "w1", "issues": [402, 403]},
        ],
    }
    snapshot = _snapshot(Issue(401, "a", state="open"), Issue(402, "b", state="open"))
    problems = queue_mod.validate(data, snapshot)
    assert any("listed twice" in problem for problem in problems)
    assert any("#403" in problem and "not present" in problem for problem in problems)


def test_validate_flags_a_closed_queued_issue():
    data = {"waves": [{"name": "w0", "issues": [501]}]}
    snapshot = _snapshot(Issue(501, "closed already", state="closed"))
    problems = queue_mod.validate(data, snapshot)
    assert any("#501" in problem and "closed" in problem for problem in problems)


def test_overlay_is_a_noop_with_no_queue_file():
    snapshot = _snapshot(Issue(1, "solo", state="open"))
    assert queue_mod.overlay(snapshot, None) is snapshot


def test_next_claimable_returns_the_wave_0_front_only():
    snapshot = _snapshot(
        Issue(101, "w0 first", state="open"),
        Issue(102, "w0 second", state="open"),
        Issue(103, "w0 third", state="open"),
        Issue(201, "w1 first", state="open"),
        Issue(202, "w1 second", state="open"),
    )
    ready = queue_mod.next_claimable(snapshot, _two_wave_queue())
    assert ready == [101]


def test_next_claimable_advances_as_issues_close():
    snapshot = _snapshot(
        Issue(101, "w0 first", state="closed"),
        Issue(102, "w0 second", state="open"),
        Issue(103, "w0 third", state="open"),
        Issue(201, "w1 first", state="open"),
    )
    ready = queue_mod.next_claimable(snapshot, _two_wave_queue())
    assert ready == [102]
