"""Named negative controls for the PR runner's transports (issue #1343).

Lessons 6-9 (and the transport halves of 3, 4, 5, 10) with FAKE git / gh /
gcloud / sh seams: no test touches the real repo, GitHub or Cloud Build.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest
from conftest import ROOT

from fleet.runner import cli, merge as merge_mod, verify as verify_mod
from fleet.runner.verify import Ledger, Result

SHA = "b" * 40
TIP = "c" * 40


class Fake:
    """A recording transport: `handler(argv, kwargs) -> Result | None`."""

    def __init__(self, handler=None, name="fake"):
        self.calls: list[tuple[list[str], dict]] = []
        self.handler = handler
        self.name = name

    def __call__(self, argv, **kwargs) -> Result:
        self.calls.append((list(argv), kwargs))
        if self.handler:
            result = self.handler(list(argv), kwargs)
            if result is not None:
                return result
        return Result(0, "", "")

    def argvs(self) -> list[list[str]]:
        return [argv for argv, _ in self.calls]


def fake_git_factory(tmp_path: Path, tip: str = TIP):
    def handler(argv, kwargs):
        if argv[:2] == ["worktree", "add"]:
            Path(argv[3]).mkdir(parents=True, exist_ok=True)
            return Result(0)
        if argv[:2] == ["worktree", "remove"]:
            path = Path(argv[3])
            if path.exists():
                for child in path.iterdir():
                    child.unlink()
                path.rmdir()
            return Result(0)
        if argv[:1] == ["rev-parse"]:
            return Result(0, tip + "\n")
        return Result(0)

    return Fake(handler, "git")


def held_fds_under(path: Path) -> list[str]:
    out = []
    for entry in Path("/proc/self/fd").iterdir():
        try:
            target = os.readlink(entry)
        except OSError:
            continue
        if target.startswith(str(path)):
            out.append(target)
    return out


# --- the fan-out width is an INJECTED input, never the machine ------------------
#
# `cli.cycle` reads the live box through `capacity.probe_host()`
# (load1, MemAvailable, nproc) and backs the width off when the box is loaded or
# below the 8.0 GB memory floor; `plan()` then DEFERs the heads that width cannot
# take. A control that lets the machine decide measures green on a dev box and red
# on the Cloud Build runner (7.29 GiB total RAM at a load above nproc) — a red on
# the machine, not on the change. Every cycle driven from this file therefore
# passes `calm_host_probe`; the backoff itself is provoked by name in
# `test_a_backed_off_width_defers_the_extra_head_by_name_and_never_loses_it`.
CALM_BOX = (0.4, 64.0, 8)


def calm_host_probe():
    """(load1, MemAvailable GB, nproc) for a box that needs no backoff."""
    return CALM_BOX


# --- lesson 6 ------------------------------------------------------------------
def test_worktree_is_held_for_the_whole_run_and_removed_by_the_runner_after(tmp_path: Path):
    runner_dir = tmp_path / "runner"
    git = fake_git_factory(tmp_path)
    seen: dict = {}

    def sh(argv, **kwargs):
        wt = Path(kwargs["cwd"])
        seen["exists"] = wt.is_dir()
        seen["held"] = held_fds_under(wt)
        return Result(0, "verify: PASS")

    posts = Fake(name="post")
    outcome = verify_mod.run_verify(
        5, SHA, repo=tmp_path, runner_dir=runner_dir, git=git, sh=sh, post_status=lambda sha, rc: posts(["post", sha, str(rc)]), ledger=Ledger(runner_dir / "ledger.jsonl")
    )
    assert seen["exists"] and seen["held"], "the worktree must be held by an open fd while verify runs"
    assert outcome.worktree_removed
    assert ["worktree", "remove", "--force", str(runner_dir / "worktrees" / f"5-{SHA[:12]}")] in git.argvs()
    assert not (runner_dir / "worktrees" / f"5-{SHA[:12]}").exists()
    assert not held_fds_under(runner_dir), "nothing under the runner dir stays open after the run"
    assert outcome.state == "green" and outcome.posted
    assert posts.argvs() == [["post", SHA, "0"]]


def test_worktree_is_removed_even_when_verify_raises(tmp_path: Path):
    git = fake_git_factory(tmp_path)
    wt = tmp_path / "wt"
    with pytest.raises(RuntimeError, match="boom"):
        with verify_mod.HeldWorktree(git, repo=tmp_path, path=wt, sha=SHA) as held:
            assert held.held
            raise RuntimeError("boom")
    assert held.removed and not wt.exists()
    assert ["worktree", "prune"] in git.argvs()


def test_a_failed_worktree_add_is_cannot_assess_by_name_and_never_posts(tmp_path: Path):
    git = Fake(lambda argv, kw: Result(128, "", "fatal: not a valid object") if argv[:2] == ["worktree", "add"] else None)
    posts = Fake(name="post")
    outcome = verify_mod.run_verify(
        6, SHA, repo=tmp_path, runner_dir=tmp_path / "r", git=git, sh=Fake(), post_status=lambda s, rc: posts([s]), ledger=Ledger(tmp_path / "r" / "l.jsonl")
    )
    assert outcome.state == "cannot-assess" and outcome.detail.startswith("worktree-add-failed:")
    assert posts.calls == []


# --- lesson 7 ------------------------------------------------------------------
def make_transports(*, prs, sh_handler=None, gh_extra=None, gcloud=None, env=None, tip=TIP, tmp_path: Path):
    def gh_handler(argv, kw):
        if argv[:2] == ["auth", "status"]:
            return Result(0)
        if argv[:2] == ["pr", "list"]:
            return Result(0, json.dumps(prs))
        if argv[:1] == ["api"] and argv[1].endswith("/check-runs"):
            return Result(0, json.dumps((gh_extra or {}).get("check-runs", {"check_runs": []})))
        if argv[:1] == ["api"] and argv[1].endswith("/status"):
            return Result(0, json.dumps((gh_extra or {}).get("status", {"statuses": []})))
        return Result(0, "[]")

    git = fake_git_factory(tmp_path, tip)
    return cli.Transports(git=git, gh=Fake(gh_handler, "gh"), gcloud=gcloud, sh=Fake(sh_handler, "sh"), env=env or {"AO_RUNNER_HOST_ROLE": "primary"})


def test_gatelock_prune_runs_before_any_verify_and_a_failed_prune_plans_none(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    prs = [{"number": 7, "headRefOid": SHA, "mergeable": "MERGEABLE", "isDraft": False, "baseRefName": "master"}]

    def sh_ok(argv, kw):
        if argv[:3] == ["python3", "fleet/gatelock.py", "prune"]:
            return Result(0, "gate-lock prune: OK")
        if argv[:2] == ["bash", "scripts/verify.sh"]:
            return Result(0)
        return Result(0)

    t = make_transports(prs=prs, sh_handler=sh_ok, tmp_path=tmp_path)
    base = tmp_path / "runner"
    rc = cli.cycle(t, base=base, apply=False, host_probe=calm_host_probe)
    names = [argv[:3] for argv in t.sh.argvs()]
    assert names[0] == ["python3", "fleet/gatelock.py", "prune"], "prune is the FIRST shell call of a cycle"
    assert ["python3", "fleet/gatelock.py", "prune", "--apply"] in t.sh.argvs()
    assert any(argv[:2] == ["bash", "scripts/verify.sh"] for argv in t.sh.argvs())
    assert rc == 0

    def sh_prune_broken(argv, kw):
        if argv[:3] == ["python3", "fleet/gatelock.py", "prune"]:
            return Result(3, "", "gate-lock: store unusable")
        return Result(0)

    t2 = make_transports(prs=prs, sh_handler=sh_prune_broken, tmp_path=tmp_path)
    rc2 = cli.cycle(t2, base=tmp_path / "runner2", apply=False, host_probe=calm_host_probe)
    assert not any(argv[:2] == ["bash", "scripts/verify.sh"] for argv in t2.sh.argvs()), "no verify after a failed prune"
    rows = Ledger(tmp_path / "runner2" / "ledger.jsonl").rows()
    assert any(r.get("event") == "refuse" and str(r.get("reason", "")).startswith("gatelock-prune-failed:") for r in rows)
    assert rc2 == 1


# --- lesson 8 ------------------------------------------------------------------
def test_fetches_are_serialised_under_one_lock_and_name_explicit_refspecs(tmp_path: Path):
    lock = tmp_path / "fetch.lock"
    active = {"now": 0, "max": 0}
    guard = threading.Lock()

    def handler(argv, kw):
        if argv[:1] == ["fetch"]:
            with guard:
                active["now"] += 1
                active["max"] = max(active["max"], active["now"])
            time.sleep(0.05)
            with guard:
                active["now"] -= 1
        return Result(0)

    git = Fake(handler, "git")
    threads = [threading.Thread(target=verify_mod.fetch_refs, args=(git,), kwargs=dict(repo=tmp_path, pr=n, lock_path=lock)) for n in (1, 2, 3, 4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert active["max"] == 1, "two fetches overlapped: the lock is not serialising them (cannot lock ref)"
    for argv in git.argvs():
        assert argv[0] == "fetch" and "origin" in argv
        assert "+refs/heads/master:refs/remotes/origin/master" in argv, "an explicit refspec, so origin/master exists on a detached checkout"
        assert any(a.startswith("+refs/pull/") and ":refs/remotes/origin/pr/" in a for a in argv)


# --- lesson 9 ------------------------------------------------------------------
def test_status_answers_what_is_verifying_merged_and_blocked_from_the_ledger(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    base = tmp_path / "runner"
    ledger = Ledger(base / "ledger.jsonl")
    ledger.record("cycle-start", apply=False)
    ledger.record("verify", pr=1, sha=SHA, rc=0, state="green", posted=True)
    ledger.record("post", pr=1, sha=SHA, rc=0, ok=True)
    ledger.record("merge", pr=1, sha=SHA, via="scripts/merge-pr.sh", new_tip=TIP)
    ledger.record("await", pr=2, sha=SHA, reason="verify-running:2")
    ledger.record("refuse", pr=3, sha=SHA, reason="merged-tree-unverified:3")
    ledger.record("cycle-end", rc=1)
    cli.write_holds(base, {4: "owner-review"})

    lines = cli.status_lines(ledger.rows(), cli.read_holds(base))
    text = "\n".join(lines)
    assert f"merged->{TIP[:12]}" in text
    assert "awaiting:verify-running:2" in text
    assert "blocked:merged-tree-unverified:3" in text
    assert "HELD:owner-review" in text

    monkeypatch.setenv("AO_FLEET_DIR", str(tmp_path))
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "last rc 1" in out and "#3 | blocked:merged-tree-unverified:3" in out


def test_hold_and_unhold_are_ledgered_and_keep_a_pr_out_of_the_plan(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AO_FLEET_DIR", str(tmp_path))
    assert cli.main(["hold", "9", "--reason", "wait-for-owner"]) == 0
    assert cli.read_holds(tmp_path / "runner") == {9: "wait-for-owner"}
    assert cli.main(["unhold", "9"]) == 0
    assert cli.read_holds(tmp_path / "runner") == {}
    assert cli.main(["unhold", "9"]) == 1
    events = [r["event"] for r in Ledger(tmp_path / "runner" / "ledger.jsonl").rows()]
    assert events == ["hold", "unhold"]


# --- lesson 10 / PARKED -------------------------------------------------------
@pytest.mark.parametrize("rc", [10, 11])
def test_a_parked_verify_is_never_posted(tmp_path: Path, rc):
    git = fake_git_factory(tmp_path)
    posts = Fake(name="post")
    ledger = Ledger(tmp_path / "r" / "l.jsonl")
    outcome = verify_mod.run_verify(
        8, SHA, repo=tmp_path, runner_dir=tmp_path / "r", git=git, sh=lambda argv, **kw: Result(rc, "", "verify: PARKED"), post_status=lambda s, r: posts([s, str(r)]), ledger=ledger
    )
    assert outcome.state == "parked" and not outcome.posted
    assert posts.calls == [], "PARKED is not a gate outcome; the mapper would refuse it"
    assert any(r["event"] == "post-skipped" and r["reason"] == f"parked:{rc}" for r in ledger.rows())
    assert verify_mod.gate_rc_of(rc) is None
    assert verify_mod.gate_rc_of(99) == 2, "an unknown rc is published as CANNOT-ASSESS, never a pass"


def test_the_poster_is_gate_status_sh_with_the_required_context(tmp_path: Path):
    sh = Fake(name="sh")
    verify_mod.real_post_status(sh, tmp_path)(SHA, 1)
    assert sh.argvs() == [["bash", "scripts/gate-status.sh", "post", "--sha", SHA, "--rc", "1"]]


# --- lessons 3 + 4 (transport half) --------------------------------------------
def pr_queue_with_seam(repo: Path) -> None:
    (repo / "scripts").mkdir(parents=True, exist_ok=True)
    (repo / "scripts" / "pr-queue.sh").write_text("#!/usr/bin/env bash\n# --check-merged-tree <pr>\n", encoding="utf-8")


def test_merge_consumes_the_merged_tree_seam_before_the_guarded_verb(tmp_path: Path):
    pr_queue_with_seam(tmp_path)
    sh = Fake(name="sh")
    ledger = Ledger(tmp_path / "l.jsonl")
    out = merge_mod.run_merge(12, SHA, repo=tmp_path, sh=sh, git=fake_git_factory(tmp_path), ledger=ledger, apply=False, runner_dir=tmp_path)
    argvs = sh.argvs()
    assert argvs[0][:3] == ["bash", "scripts/pr-queue.sh", "--check-merged-tree"] and argvs[0][3:] == ["12", "--head", SHA, "--against-base", "origin/master"]
    assert argvs[1] == ["bash", "scripts/merge-pr.sh", "--pr", "12"]
    assert sh.calls[1][1]["env"] == {"AO_MERGE_APPLY": "0"}, "dry-run by default"
    assert out.rc == 0 and not out.merged and out.reason == "dry-run:12"


def test_a_missing_merged_tree_seam_is_cannot_assess_and_nothing_merges(tmp_path: Path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "pr-queue.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    sh = Fake(name="sh")
    out = merge_mod.run_merge(13, SHA, repo=tmp_path, sh=sh, git=Fake(), ledger=Ledger(tmp_path / "l.jsonl"), apply=True, runner_dir=tmp_path)
    assert out.rc == 2 and out.reason == "merged-tree-seam-missing:13"
    assert sh.calls == [], "no seam, no verb"


def test_a_red_merged_tree_refuses_by_name_and_the_verb_is_never_reached(tmp_path: Path):
    pr_queue_with_seam(tmp_path)
    sh = Fake(lambda argv, kw: Result(1, "", "pr-queue: REFUSED — merged-tree-red:pytest-fleet — master+#14 is red") if "--check-merged-tree" in argv else None)
    out = merge_mod.run_merge(14, SHA, repo=tmp_path, sh=sh, git=Fake(), ledger=Ledger(tmp_path / "l.jsonl"), apply=True, runner_dir=tmp_path)
    assert out.rc == 1 and out.reason == "merged-tree-red:pytest-fleet"
    assert not any(argv[1:2] == ["scripts/merge-pr.sh"] for argv in sh.argvs())


def test_the_squash_guard_refusal_is_surfaced_by_name(tmp_path: Path):
    pr_queue_with_seam(tmp_path)
    sh = Fake(lambda argv, kw: Result(1, "", "merge-pr: REFUSED — squash-message-would-drop-trailer") if argv[1:2] == ["scripts/merge-pr.sh"] else None)
    out = merge_mod.run_merge(15, SHA, repo=tmp_path, sh=sh, git=Fake(), ledger=Ledger(tmp_path / "l.jsonl"), apply=True, runner_dir=tmp_path)
    assert out.rc == 1 and out.reason == "squash-message-would-drop-trailer" and not out.merged


def test_a_noncompliant_landed_tip_stops_merging(tmp_path: Path):
    pr_queue_with_seam(tmp_path)
    ledger = Ledger(tmp_path / "l.jsonl")
    out = merge_mod.run_merge(
        16, SHA, repo=tmp_path, sh=Fake(), git=fake_git_factory(tmp_path), ledger=ledger, apply=True, runner_dir=tmp_path, classify_tip=lambda sha: "missing-ticket-trailer"
    )
    assert out.merged and out.stop and out.new_tip == TIP
    assert any(r["event"] == "stop" and r["reason"].startswith(f"landed-tip-noncompliant:{TIP[:12]}:") for r in ledger.rows())


def test_the_transports_never_spell_the_raw_github_merge_command():
    for name in ("merge.py", "cli.py", "verify.py", "plan.py"):
        src = (ROOT / "fleet" / "runner" / name).read_text(encoding="utf-8")
        assert "gh pr merge" not in src, f"{name} must merge through scripts/merge-pr.sh only"


# --- lesson 5 + the host role (cli half) ----------------------------------------
def test_run_refuses_on_a_non_primary_host_by_name(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    t = make_transports(prs=[], env={"AO_RUNNER_HOST_ROLE": "standby"}, tmp_path=tmp_path)
    rc = cli.cycle(t, base=tmp_path / "runner", apply=True, host_probe=calm_host_probe)
    assert rc == 2
    assert t.sh.calls == [] and not any(argv[:2] == ["pr", "list"] for argv in t.gh.argvs())
    rows = Ledger(tmp_path / "runner" / "ledger.jsonl").rows()
    assert any(r.get("reason") == "host-role-not-primary:standby" for r in rows)


def test_an_unauthenticated_gh_is_cannot_assess_by_name_not_green(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    t = make_transports(prs=[], tmp_path=tmp_path)
    t.gh = Fake(lambda argv, kw: Result(1, "", "not logged in") if argv[:2] == ["auth", "status"] else None, "gh")
    rc = cli.cycle(t, base=tmp_path / "runner", apply=True, host_probe=calm_host_probe)
    assert rc == 2
    assert any(r.get("reason") == "gh-unauthenticated" for r in Ledger(tmp_path / "runner" / "ledger.jsonl").rows())


def test_a_full_cycle_verifies_posts_and_dry_run_merges_the_green(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    pr_queue_with_seam(tmp_path)
    prs = [
        {"number": 20, "headRefOid": SHA, "mergeable": "MERGEABLE", "isDraft": False, "baseRefName": "master"},
        {"number": 21, "headRefOid": "d" * 40, "mergeable": "MERGEABLE", "isDraft": False, "baseRefName": "master"},
    ]
    gh_extra = {"check-runs": {"check_runs": []}, "status": {"statuses": []}}

    def sh_handler(argv, kw):
        if argv[:2] == ["bash", "scripts/verify.sh"]:
            return Result(0, "verify: PASS")
        if argv[:2] == ["bash", "scripts/gate-status.sh"]:
            return Result(0, "gate-status: posted")
        return Result(0)

    t = make_transports(prs=prs, sh_handler=sh_handler, gh_extra=gh_extra, tmp_path=tmp_path)
    base = tmp_path / "runner"
    # cycle 1: both heads verified and posted; nothing merged yet (evidence is read at cycle start)
    assert cli.cycle(t, base=base, apply=False, host_probe=calm_host_probe) == 0
    # the width this fixture assumes is asserted, not inherited from the box: a
    # backed-off width DEFERs a head and this control would then read as a red.
    caprow = [r for r in Ledger(base / "ledger.jsonl").rows() if r.get("event") == "capacity"][-1]
    assert caprow["effective"] >= 2, f"the fixture declares a calm 2-wide box, got {caprow}"
    posts = [argv for argv in t.sh.argvs() if argv[:2] == ["bash", "scripts/gate-status.sh"]]
    assert sorted(a[4] for a in posts) == sorted([SHA, "d" * 40])
    # cycle 2: local markers make both green -> merged-tree seam + dry-run verb
    t2 = make_transports(prs=prs, sh_handler=sh_handler, gh_extra=gh_extra, tmp_path=tmp_path)
    assert cli.cycle(t2, base=base, apply=False, host_probe=calm_host_probe) == 0
    verbs = [argv for argv in t2.sh.argvs() if argv[1:2] == ["scripts/merge-pr.sh"]]
    assert sorted(v[3] for v in verbs) == ["20", "21"]
    assert not any(argv[:2] == ["bash", "scripts/verify.sh"] for argv in t2.sh.argvs()), "green heads are not re-verified"


def test_a_backed_off_width_defers_the_extra_head_by_name_and_never_loses_it(tmp_path: Path, monkeypatch):
    """The width is a bound, and what it cannot take is DEFERred BY NAME.

    This is the control that turns the suite's ambient dependence into a
    provoked one. A box below the memory floor at a load above nproc backs the
    fan-out off (here 4 -> 1); the planner may then take one head and must name
    the other `capacity:<pr>:<why>` — in the ledger, in `status`, and in a later
    cycle it must still be verified. A head dropped in silence is a head that
    reads as green.
    """
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    pr_queue_with_seam(tmp_path)
    prs = [
        {"number": 40, "headRefOid": SHA, "mergeable": "MERGEABLE", "isDraft": False, "baseRefName": "master"},
        {"number": 41, "headRefOid": "e" * 40, "mergeable": "MERGEABLE", "isDraft": False, "baseRefName": "master"},
    ]

    def sh_handler(argv, kw):
        if argv[:2] == ["bash", "scripts/verify.sh"]:
            return Result(0, "verify: PASS")
        if argv[:2] == ["bash", "scripts/gate-status.sh"]:
            return Result(0, "gate-status: posted")
        return Result(0)

    base = tmp_path / "runner"
    loaded = make_transports(prs=prs, sh_handler=sh_handler, tmp_path=tmp_path)
    assert cli.cycle(loaded, base=base, apply=False, host_probe=lambda: (20.0, 2.0, 8)) == 0
    rows = Ledger(base / "ledger.jsonl").rows()
    caprow = [r for r in rows if r.get("event") == "capacity"][-1]
    assert caprow["declared"] == 4 and caprow["effective"] == 1, caprow
    assert caprow["reason"].startswith("capacity-backoff:load:") and "memory:" in caprow["reason"], caprow

    # exactly the head the width allowed is verified; the other is named, not dropped
    posts = [argv for argv in loaded.sh.argvs() if argv[:2] == ["bash", "scripts/gate-status.sh"]]
    assert [a[4] for a in posts] == [SHA], "only the head the width allowed may be verified"
    defers = [r for r in rows if r.get("event") == "defer"]
    assert [r["pr"] for r in defers] == [41], defers
    assert defers[0]["reason"] == "capacity:41:no-evidence", "the deferred head is NAMED, never dropped"
    assert bool(defers[0]["sha"])
    # never counted green: no verify for it, and `status` says why, by name
    text = "\n".join(cli.status_lines(rows, {}))
    assert "#41" in text and "deferred:capacity:41:no-evidence" in text, text

    # the bound is a bound, not a loss: the calm cycle still verifies it
    calm = make_transports(prs=prs, sh_handler=sh_handler, tmp_path=tmp_path)
    assert cli.cycle(calm, base=base, apply=False, host_probe=calm_host_probe) == 0
    later = sorted(a[4] for a in calm.sh.argvs() if a[:2] == ["bash", "scripts/gate-status.sh"])
    assert later == ["e" * 40], "the deferred head is picked up on the calm box, not lost"
