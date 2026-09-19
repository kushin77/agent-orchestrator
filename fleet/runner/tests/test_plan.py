"""Named negative controls for the PR runner's pure core (issue #1343).

One test per lesson the 2026-09-18 prototype measured (lessons 1-5 and 10 live
here; 6-9 are transport lessons and live in test_transports.py). Each test is
a control: it drives `plan()` with the exact input shape that broke the
prototype and asserts the planner REFUSES BY NAME.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from conftest import ROOT

from fleet.runner import evidence as ev
from fleet.runner.model import (
    AWAIT,
    CANCEL_STALE,
    CANNOT_ASSESS,
    DEFER,
    GATE_CONTEXT,
    GREEN,
    HOLD,
    MERGE,
    MERGE_VERB,
    RED,
    REFUSE,
    SOURCE_CLOUD_BUILD,
    SOURCE_GATE_STATUS,
    SOURCE_LOCAL,
    VERIFY,
    Evidence,
    LiveBuild,
    OpenPR,
    PruneResult,
)
from fleet.runner.plan import plan

OLD = "a" * 40
NEW = "b" * 40
TIP = "c" * 40
OLD_TIP = "d" * 40


def kinds(actions, kind):
    return [a for a in actions if a.kind == kind]


def one(actions, kind, pr=None):
    found = [a for a in kinds(actions, kind) if pr is None or a.pr == pr]
    assert len(found) == 1, f"expected one {kind} for #{pr}, got {[a.as_dict() for a in actions]}"
    return found[0]


# --- lesson 1 ------------------------------------------------------------------
def test_a_pushed_head_invalidates_prior_evidence_and_cancels_the_stale_build():
    prs = [OpenPR(number=7, head_sha=NEW)]
    table = ev.EvidenceTable([Evidence(7, OLD, SOURCE_CLOUD_BUILD, GREEN)])  # green for the OLD head
    live = [LiveBuild(id="cb-old", pr=7, sha=OLD, status="pending")]
    actions = plan(prs, table, live, master_tip=TIP)

    assert not kinds(actions, MERGE), "green evidence for an old head must not merge the new one"
    cancel = one(actions, CANCEL_STALE, 7)
    assert cancel.build_id == "cb-old" and cancel.reason.startswith("stale-head:7:")
    verify = one(actions, VERIFY, 7)
    assert verify.sha == NEW
    assert actions[0].kind == CANCEL_STALE, "freeing capacity comes first"


def test_evidence_is_keyed_by_pr_and_sha_never_by_pr_alone():
    table = ev.EvidenceTable([Evidence(7, OLD, SOURCE_LOCAL, GREEN)])
    assert table.for_head(7, NEW) == ()
    assert table.verdict(7, NEW).state == "none"


# --- lesson 2 ------------------------------------------------------------------
@pytest.mark.parametrize("status", ["expired", "cancelled", "parked"])
def test_expired_is_requeued_not_awaited(status):
    prs = [OpenPR(number=8, head_sha=NEW)]
    live = [LiveBuild(id=f"cb-{status}", pr=8, sha=NEW, status=status)]
    actions = plan(prs, ev.EvidenceTable(), live, master_tip=TIP)

    assert not kinds(actions, AWAIT), f"a {status} build is terminal: never wait on it"
    verify = one(actions, VERIFY, 8)
    assert verify.reason == f"requeue:{status}:cb-{status}:8"


def test_a_pending_build_for_the_current_head_is_awaited_not_duplicated():
    prs = [OpenPR(number=8, head_sha=NEW)]
    live = [LiveBuild(id="cb-live", pr=8, sha=NEW, status="pending")]
    actions = plan(prs, ev.EvidenceTable(), live, master_tip=TIP)
    assert one(actions, AWAIT, 8).build_id == "cb-live"
    assert not kinds(actions, VERIFY)


def test_a_red_verdict_is_not_requeued_it_is_named():
    prs = [OpenPR(number=8, head_sha=NEW)]
    table = ev.EvidenceTable([Evidence(8, NEW, SOURCE_LOCAL, RED)])
    actions = plan(prs, table, master_tip=TIP)
    assert not kinds(actions, VERIFY) and not kinds(actions, MERGE)
    assert one(actions, REFUSE, 8).reason == f"verify-red:8:{SOURCE_LOCAL}"


# --- lesson 3 ------------------------------------------------------------------
def test_merge_requires_evidence_for_the_current_master_tip():
    prs = [OpenPR(number=9, head_sha=NEW)]
    stale = ev.EvidenceTable([Evidence(9, NEW, SOURCE_LOCAL, GREEN, base_tip=OLD_TIP)])
    actions = plan(prs, stale, master_tip=TIP)

    assert not kinds(actions, MERGE), "green against an older master tip is not green now"
    refusal = one(actions, REFUSE, 9)
    assert refusal.reason.startswith("merged-tree-stale:9:")
    assert one(actions, VERIFY, 9).reason == "requeue:merged-tree-stale:9"
    assert not kinds(actions, AWAIT)

    fresh = ev.EvidenceTable([Evidence(9, NEW, SOURCE_LOCAL, GREEN, base_tip=TIP)])
    merge = one(plan(prs, fresh, master_tip=TIP), MERGE, 9)
    assert merge.extra["master_tip"] == TIP
    assert merge.extra["merged_tree_seam"] == "scripts/pr-queue.sh --check-merged-tree"


def test_no_master_tip_means_no_merge_at_all():
    prs = [OpenPR(number=9, head_sha=NEW)]
    table = ev.EvidenceTable([Evidence(9, NEW, SOURCE_CLOUD_BUILD, GREEN)])
    actions = plan(prs, table, master_tip=None)
    assert not kinds(actions, MERGE)
    assert one(actions, REFUSE, 9).reason == "master-tip-unknown:9"


# --- lesson 4 ------------------------------------------------------------------
def test_merge_action_names_the_guarded_entrypoint_never_gh_pr_merge():
    prs = [OpenPR(number=10, head_sha=NEW)]
    table = ev.EvidenceTable([Evidence(10, NEW, SOURCE_CLOUD_BUILD, GREEN)])
    merge = one(plan(prs, table, master_tip=TIP), MERGE, 10)
    assert merge.via == MERGE_VERB == "scripts/merge-pr.sh"
    assert (ROOT / MERGE_VERB).is_file()
    # The verb itself runs the squash guard first; the planner never spells the raw command.
    plan_src = (ROOT / "fleet" / "runner" / "plan.py").read_text(encoding="utf-8")
    assert "gh pr merge" not in plan_src


def test_a_conflicting_pr_is_refused_by_name_not_merged():
    prs = [OpenPR(number=10, head_sha=NEW, mergeable="CONFLICTING")]
    table = ev.EvidenceTable([Evidence(10, NEW, SOURCE_CLOUD_BUILD, GREEN)])
    actions = plan(prs, table, master_tip=TIP)
    assert not kinds(actions, MERGE)
    assert one(actions, REFUSE, 10).reason == "not-mergeable:10:CONFLICTING"


# --- lesson 5 ------------------------------------------------------------------
def test_cannot_assess_is_never_counted_green():
    prs = [OpenPR(number=11, head_sha=NEW)]
    table = ev.EvidenceTable(
        [
            Evidence(11, NEW, SOURCE_LOCAL, CANNOT_ASSESS, detail="gh-unauthenticated"),
            Evidence(11, NEW, SOURCE_GATE_STATUS, CANNOT_ASSESS, detail="error"),
        ]
    )
    actions = plan(prs, table, master_tip=TIP)
    assert not kinds(actions, MERGE)
    verify = one(actions, VERIFY, 11)
    assert verify.reason.startswith("requeue:cannot-assess:")
    # And the raw vocabulary never maps an unknown word to green.
    assert ev.state_of_check_run("banana") == CANNOT_ASSESS
    assert ev.state_of_commit_status("error") == CANNOT_ASSESS
    assert ev.state_of_cloud_build("INTERNAL_ERROR") == CANNOT_ASSESS
    assert ev.state_of_verify_rc(2) == CANNOT_ASSESS
    assert ev.state_of_verify_rc(11) == "parked"


def test_cloud_build_green_outranks_a_host_env_red_and_both_are_recorded():
    prs = [OpenPR(number=12, head_sha=NEW)]
    table = ev.EvidenceTable(
        [
            Evidence(12, NEW, SOURCE_LOCAL, RED, detail="docker-compose-missing"),
            Evidence(12, NEW, SOURCE_CLOUD_BUILD, GREEN),
        ]
    )
    verdict = table.verdict(12, NEW)
    assert verdict.green and verdict.basis.source == SOURCE_CLOUD_BUILD
    assert "local-marker=red" in verdict.explain()
    merge = one(plan(prs, table, master_tip=TIP), MERGE, 12)
    assert merge.reason == f"green:{SOURCE_CLOUD_BUILD}:12"


def test_a_local_green_does_not_outrank_a_cloud_build_red():
    table = ev.EvidenceTable([Evidence(13, NEW, SOURCE_LOCAL, GREEN), Evidence(13, NEW, SOURCE_CLOUD_BUILD, RED)])
    # Any green is green (the prototype accepted either source), but the basis is
    # the local one and the cloud red is on record next to it — the status verb
    # shows both, the merged-tree seam decides at merge time.
    verdict = table.verdict(13, NEW)
    assert verdict.green and verdict.basis.source == SOURCE_LOCAL
    assert "cloud-build=red" in verdict.explain()


# --- lesson 8 (#1378: a foreign red with no local run is re-queued, not stuck) --
def test_a_foreign_red_with_no_local_record_is_requeued():
    prs = [OpenPR(number=16, head_sha=NEW)]
    table = ev.EvidenceTable([Evidence(16, NEW, SOURCE_CLOUD_BUILD, RED)])
    actions = plan(prs, table, master_tip=TIP)
    assert not kinds(actions, REFUSE)
    assert one(actions, VERIFY, 16).reason == f"requeue:foreign-red:16:{SOURCE_CLOUD_BUILD}"


def test_a_foreign_red_with_a_local_red_record_stays_refused():
    prs = [OpenPR(number=17, head_sha=NEW)]
    table = ev.EvidenceTable(
        [Evidence(17, NEW, SOURCE_CLOUD_BUILD, RED), Evidence(17, NEW, SOURCE_LOCAL, RED)]
    )
    actions = plan(prs, table, master_tip=TIP)
    assert not kinds(actions, VERIFY)
    assert one(actions, REFUSE, 17).reason == "verify-red:17:local-marker"


def test_a_foreign_red_with_a_local_green_merges_instead():
    prs = [OpenPR(number=18, head_sha=NEW)]
    table = ev.EvidenceTable(
        [Evidence(18, NEW, SOURCE_CLOUD_BUILD, RED), Evidence(18, NEW, SOURCE_LOCAL, GREEN)]
    )
    actions = plan(prs, table, master_tip=TIP)
    assert not kinds(actions, VERIFY) and not kinds(actions, REFUSE)
    assert one(actions, MERGE, 18).reason == f"green:{SOURCE_LOCAL}:18"


# --- lesson 7 (planner half: a failed prune plans no new verify) ----------------
def test_a_failed_gatelock_prune_plans_no_verify():
    prs = [OpenPR(number=14, head_sha=NEW)]
    actions = plan(prs, ev.EvidenceTable(), prune=PruneResult(ok=False, detail="store-unusable"), master_tip=TIP)
    assert not kinds(actions, VERIFY)
    assert one(actions, REFUSE, 14).reason == "gatelock-prune-failed:store-unusable"


# --- holds and capacity -----------------------------------------------------------
def test_a_held_pr_is_neither_verified_nor_merged():
    prs = [OpenPR(number=15, head_sha=NEW)]
    table = ev.EvidenceTable([Evidence(15, NEW, SOURCE_CLOUD_BUILD, GREEN)])
    actions = plan(prs, table, holds={15: "owner-review"}, master_tip=TIP)
    assert [a.kind for a in actions] == [HOLD]
    assert actions[0].reason == "held:15:owner-review"


def test_capacity_defers_the_fourth_verify_and_counts_local_in_flight():
    prs = [OpenPR(number=n, head_sha=NEW) for n in (20, 21, 22, 23)]
    live = [LiveBuild(id="local-20", pr=20, sha=NEW, status="pending", kind="local")]
    actions = plan(prs, ev.EvidenceTable(), live, master_tip=TIP, capacity=3)
    assert one(actions, AWAIT, 20)
    assert sorted(a.pr for a in kinds(actions, VERIFY)) == [21, 22]
    assert one(actions, DEFER, 23).reason.startswith("capacity:23")


def test_drafts_are_skipped_by_name():
    actions = plan([OpenPR(number=30, head_sha=NEW, draft=True)], ev.EvidenceTable(), master_tip=TIP)
    assert [a.kind for a in actions] == ["skip"] and actions[0].reason == "draft:30"


# --- lesson 10 -----------------------------------------------------------------
def test_poster_protection_mapper_and_runner_name_the_same_context():
    poster = (ROOT / "scripts" / "gate-status.sh").read_text(encoding="utf-8")
    match = re.search(r'CONTEXT="\$\{AO_GATE_CONTEXT:-([^}]+)\}"', poster)
    assert match and match.group(1) == GATE_CONTEXT

    mapper = (ROOT / "scripts" / "gate-status-map.py").read_text(encoding="utf-8")
    assert f'CONTEXT = "{GATE_CONTEXT}"' in mapper

    yaml = pytest.importorskip("yaml")
    policy = yaml.safe_load((ROOT / "governance" / "platform" / "branch-protection.yaml").read_text(encoding="utf-8"))
    contexts = policy.get("required_status_contexts") or []
    assert GATE_CONTEXT in contexts, "a required context nothing posts blocks everyone forever"


# --- local markers round-trip --------------------------------------------------------
def test_local_markers_round_trip_and_an_unreadable_marker_is_cannot_assess(tmp_path: Path):
    ev.write_local_marker(tmp_path, 40, NEW, 0, base_tip=TIP, detail="verify rc 0", recorded_at="t1")
    ev.write_local_marker(tmp_path, 41, NEW, 11, base_tip=None, detail="PARKED", recorded_at="t2")
    (tmp_path / f"42-{NEW}").write_text("not json", encoding="utf-8")
    records = {r.pr: r for r in ev.from_local_markers(tmp_path)}
    assert records[40].state == GREEN and records[40].base_tip == TIP
    assert records[41].state == "parked"
    assert records[42].state == CANNOT_ASSESS and records[42].detail.startswith("marker-unreadable:")
