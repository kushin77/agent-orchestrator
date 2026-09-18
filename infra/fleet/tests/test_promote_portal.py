"""Tests for infra/fleet/promote_portal.py — issue #1329.

Every test drives the PURE seams (`select_newest_master_tag`, `run_cycle`)
with fakes: no subprocess, no network, no filesystem. Real transport
(`_real_*`) is exercised only by `main()`, which these tests never call.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import promote_portal as pp  # noqa: E402


def _tag(tag: str, sha: str) -> pp.TagRef:
    return pp.TagRef(tag=tag, sha=sha)


# --------------------------------------------------------------------------
# select_newest_master_tag
# --------------------------------------------------------------------------


def test_select_picks_highest_commit_index_among_ancestors():
    tags = [_tag("aaa111", "aaa"), _tag("bbb222", "bbb"), _tag("ccc333", "ccc")]
    is_ancestor = lambda sha: True  # noqa: E731
    commit_index = {"aaa": 10, "bbb": 30, "ccc": 20}.get
    decision = pp.select_newest_master_tag(tags, is_ancestor, commit_index)
    assert decision.action == "select"
    assert decision.tag.tag == "bbb222"


def test_select_never_considers_latest():
    tags = [_tag("latest", "zzz"), _tag("aaa111", "aaa")]
    is_ancestor = lambda sha: True  # noqa: E731
    commit_index = {"zzz": 999, "aaa": 5}.get
    decision = pp.select_newest_master_tag(tags, is_ancestor, commit_index)
    assert decision.action == "select"
    assert decision.tag.tag == "aaa111"


def test_select_refuses_tag_not_on_master_by_name():
    tags = [_tag("aaa111", "aaa")]
    decision = pp.select_newest_master_tag(tags, lambda sha: False, lambda sha: 0)
    assert decision.action == "refuse"
    assert decision.code == "tag-not-on-master"
    assert "aaa111" in decision.detail


def test_select_excludes_unmerged_tag_but_picks_a_merged_one():
    tags = [_tag("unmerged", "u"), _tag("merged", "m")]
    is_ancestor = lambda sha: sha == "m"  # noqa: E731
    decision = pp.select_newest_master_tag(tags, is_ancestor, lambda sha: 1)
    assert decision.action == "select"
    assert decision.tag.tag == "merged"


def test_select_cannot_assess_with_no_tags():
    decision = pp.select_newest_master_tag([], lambda sha: True, lambda sha: 0)
    assert decision.action == "cannot-assess"
    assert decision.code == "no-tags"


# --------------------------------------------------------------------------
# run_cycle — fixtures for every injected fact
# --------------------------------------------------------------------------


class Recorder:
    def __init__(self):
        self.records: list[dict] = []
        self.statuses: list[tuple[str, int]] = []
        self.escalations: list[tuple[str, str]] = []
        self.deploys: list[str] = []
        self.park: str | None = None

    def record(self, result: dict) -> None:
        self.records.append(result)

    def post_status(self, sha: str, rc: int) -> None:
        self.statuses.append((sha, rc))

    def escalate(self, code: str, detail: str) -> None:
        self.escalations.append((code, detail))

    def deploy(self, ref: str) -> None:
        self.deploys.append(ref)

    def read_park(self) -> str | None:
        return self.park

    def write_park(self, tag: str | None) -> None:
        self.park = tag


def _base_kwargs(rec: Recorder, *, tags, running, healthy):
    return dict(
        auth_ok=True,
        list_tags=lambda: tags,
        is_ancestor=lambda sha: True,
        commit_index=lambda sha: {"a": 1, "b": 2}.get(sha, 0),
        running_ref=lambda: running,
        deploy=rec.deploy,
        healthz=lambda: healthy,
        escalate=rec.escalate,
        record=rec.record,
        post_status=rec.post_status,
        read_park=rec.read_park,
        write_park=rec.write_park,
        now=lambda: "2026-09-18T00:00:00Z",
    )


def test_run_cycle_ar_auth_missing_escalates_once_and_cannot_assess():
    rec = Recorder()
    kwargs = _base_kwargs(rec, tags=[_tag("t1", "a")], running=None, healthy=True)
    kwargs["auth_ok"] = False
    result = pp.run_cycle(**kwargs)
    assert result["action"] == "cannot-assess"
    assert result["code"] == "ar-auth-missing"
    assert pp.rc_for(result) == pp.CANNOT_ASSESS
    assert len(rec.escalations) == 1
    assert rec.escalations[0][0] == "ar-auth-missing"
    assert rec.deploys == []


def test_run_cycle_noop_when_already_running_the_newest_tag():
    rec = Recorder()
    chosen = _tag("t1", "a")
    kwargs = _base_kwargs(rec, tags=[chosen], running=chosen.ref, healthy=True)
    result = pp.run_cycle(**kwargs)
    assert result["action"] == "noop"
    assert rec.deploys == []


def test_run_cycle_deploys_and_records_on_healthy():
    rec = Recorder()
    chosen = _tag("t1", "a")
    kwargs = _base_kwargs(rec, tags=[chosen], running="old-ref", healthy=True)
    result = pp.run_cycle(**kwargs)
    assert result["action"] == "deployed"
    assert rec.deploys == [chosen.ref]
    assert rec.statuses == [("a", pp.OK)]
    assert rec.park is None
    assert result["previous"] == "old-ref"


def test_run_cycle_rolls_back_on_failed_healthz_and_escalates_once():
    rec = Recorder()
    chosen = _tag("t1", "a")
    kwargs = _base_kwargs(rec, tags=[chosen], running="old-ref", healthy=False)
    result = pp.run_cycle(**kwargs)
    assert result["action"] == "rolled_back"
    assert result["rolled_back"] is True
    # deployed the new ref, then re-deployed the previous ref for rollback
    assert rec.deploys == [chosen.ref, "old-ref"]
    assert len(rec.escalations) == 1
    assert rec.escalations[0][0] == "healthz-failed"
    assert rec.park == chosen.tag
    assert rec.statuses == [("a", pp.NOT_OK)]


def test_run_cycle_parks_and_does_not_re_escalate_on_same_tag():
    rec = Recorder()
    chosen = _tag("t1", "a")
    kwargs = _base_kwargs(rec, tags=[chosen], running="old-ref", healthy=False)
    pp.run_cycle(**kwargs)
    assert len(rec.escalations) == 1
    # second tick, same tag still newest, still failing running_ref comparison:
    # the rung must park, not retry the deploy or escalate again.
    result2 = pp.run_cycle(**kwargs)
    assert result2["action"] == "parked"
    assert len(rec.escalations) == 1
    assert rec.deploys == [chosen.ref, "old-ref"]  # unchanged since the first tick


def test_run_cycle_unparks_once_a_newer_tag_appears():
    rec = Recorder()
    old = _tag("t1", "a")
    kwargs = _base_kwargs(rec, tags=[old], running="old-ref", healthy=False)
    pp.run_cycle(**kwargs)
    assert rec.park == "t1"

    newer = _tag("t2", "b")
    kwargs2 = _base_kwargs(rec, tags=[newer], running="old-ref", healthy=True)
    result = pp.run_cycle(**kwargs2)
    assert result["action"] == "deployed"
    assert rec.park is None


def test_run_cycle_refuses_tag_not_on_master():
    rec = Recorder()
    kwargs = _base_kwargs(rec, tags=[_tag("t1", "a")], running=None, healthy=True)
    kwargs["is_ancestor"] = lambda sha: False
    result = pp.run_cycle(**kwargs)
    assert result["action"] == "refuse"
    assert result["code"] == "tag-not-on-master"
    assert pp.rc_for(result) == pp.NOT_OK
    assert rec.deploys == []


def test_run_cycle_no_tags_is_cannot_assess():
    rec = Recorder()
    kwargs = _base_kwargs(rec, tags=[], running=None, healthy=True)
    result = pp.run_cycle(**kwargs)
    assert result["code"] == "no-tags"
    assert pp.rc_for(result) == pp.CANNOT_ASSESS
