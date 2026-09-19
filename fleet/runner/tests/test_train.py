"""The merge train's controls (issue #1411, parent #1295).

Every control here is a NEGATIVE one: it drives the real code with fakes and
provokes the failure the train exists to refuse. The four the issue names are

  * a CONFLICTING PR is skipped BY NAME and the wave still lands without it
    (`test_a_conflicting_pr_is_skipped_by_name_and_the_wave_lands_without_it`);
  * an EPIC-closing PR body is refused BY NAME
    (`test_an_epic_closing_pr_body_is_refused_by_name`);
  * a red attributes to the RIGHT PR
    (`test_a_red_attributes_to_the_right_pr_and_only_tests_prs_that_touch_it`);
  * a base that moved re-folds and NEVER lands stale evidence
    (`test_a_base_that_moved_re_folds_and_never_lands_stale_evidence`).

The rest are the properties the shape is worthless without: the trailer carries
each folded PR's OWN closing lines (read from the body, never a branch name),
the post is never a fabricated success, the parked rc is never posted, and the
train spells no raw merge command of its own.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from conftest import ROOT  # noqa: F401  (the package import root, as the other suites use it)

from fleet.runner import cli, train as train_mod
from fleet.runner.model import OpenPR
from fleet.runner.verify import Ledger, Result

TIP = "c" * 40
MOVED = "d" * 40
FOLD_HEAD = "f" * 40
HEADS = {11: "1" * 40, 12: "2" * 40, 13: "3" * 40, 14: "4" * 40}
SLUG = "kushin77/agent-orchestrator"


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


def git_fake(*, tip=TIP, merges_conflict=(), moved_to=None, move_after=0, move_once=False, head=FOLD_HEAD, diffs=None, checks=("check-shell-patterns",)):
    """A git transport for the train: worktrees, merges, refs, diffs.

    `merges_conflict` names the head shas whose `git merge` fails. `moved_to`
    models a base that MOVED: the first `move_after` reads of
    `refs/remotes/origin/master` answer `tip`, and every read after that answers
    `moved_to` (`move_once` limits it to exactly one moved read, which is how a
    re-fold converges). The read count, not the fetch, is the trigger, because
    that is what a caller observes.
    """
    state = {"head": head, "unmerged": (), "reads": 0}

    def master() -> str:
        state["reads"] += 1
        if moved_to is None or state["reads"] <= move_after:
            return tip
        if move_once and state["reads"] > move_after + 1:
            return tip
        return moved_to

    def handler(argv, kwargs):
        if argv[:2] == ["worktree", "add"]:
            path = Path(argv[3])
            path.mkdir(parents=True, exist_ok=True)
            (path / "scripts").mkdir(exist_ok=True)
            for name in checks:
                (path / "scripts" / f"{name}.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            return Result(0)
        if argv[:2] == ["worktree", "remove"]:
            shutil.rmtree(Path(argv[3]), ignore_errors=True)
            return Result(0)
        if argv[:1] == ["fetch"]:
            return Result(0)
        if argv[:2] == ["rev-parse", "refs/remotes/origin/master"]:
            return Result(0, master() + "\n")
        if argv[:1] == ["rev-parse"] and argv[1].startswith("refs/remotes/origin/pr/"):
            pr = int(argv[1].rsplit("/", 1)[1])
            return Result(0, HEADS.get(pr, "9" * 40) + "\n")
        if argv[:2] == ["rev-parse", "HEAD"]:
            return Result(0, state["head"] + "\n")
        if argv[:1] == ["merge"]:
            if "--abort" in argv:
                return Result(0)
            sha = argv[-1]
            if sha in merges_conflict:
                state["unmerged"] = ("scripts/check-pr-runner.sh", "fleet/runner/cli.py")
                return Result(1, "", "CONFLICT (content): merge conflict")
            return Result(0)
        if argv[:1] == ["diff"]:
            if "--diff-filter=U" in argv:
                return Result(0, "\n".join(state["unmerged"]) + ("\n" if state["unmerged"] else ""))
            sha = argv[-1].rsplit("...", 1)[-1]
            return Result(0, "\n".join((diffs or {}).get(sha, [])))
        return Result(0)

    return Fake(handler, "git")


# --- control 1: a conflicting PR is skipped BY NAME -----------------------------
def test_a_conflicting_pr_is_skipped_by_name_and_the_wave_lands_without_it(tmp_path: Path):
    """The gather sees CONFLICTING; the FOLD sees a real merge conflict."""
    prs = [
        OpenPR(11, HEADS[11], mergeable="MERGEABLE", title="eleven"),
        OpenPR(12, HEADS[12], mergeable="CONFLICTING", title="twelve"),
        OpenPR(13, HEADS[13], mergeable="MERGEABLE", title="thirteen"),
    ]
    bodies = {11: "Closes #111\n", 12: "Closes #112\n", 13: "Closes #113\n"}
    candidates, skipped = train_mod.fold_candidates(prs, bodies, lambda issue: ("type:task",), {})
    assert [c.number for c in candidates] == [11, 13]
    assert [s.reason for s in skipped] == ["conflicting:#12"], "a conflicting PR is skipped BY NAME"
    assert all("twelve" not in c.title for c in candidates)

    # And a PR that GitHub still calls MERGEABLE but whose merge really conflicts
    # is skipped by name too, with the rest of the wave folding on.
    ledger = Ledger(tmp_path / "ledger.jsonl")
    git = git_fake(merges_conflict=(HEADS[13],), diffs={HEADS[11]: [], HEADS[13]: []})
    fold = train_mod.fold(candidates, repo=tmp_path, git=git, worktree=tmp_path / "wt", base_tip=TIP, ledger=ledger, train_id="train/T")
    assert [c.number for c in fold.folded] == [11], "the wave lands without the PR that conflicted"
    assert [s.reason for s in fold.skipped] == ["conflict:#13:scripts/check-pr-runner.sh,fleet/runner/cli.py"]
    assert fold.head == FOLD_HEAD
    rows = ledger.rows()
    assert any(row.get("event") == "train-conflict" and row.get("pr") == 13 for row in rows)
    assert any(row.get("event") == "train-fold" and row.get("pr") == 11 for row in rows)


def test_a_head_that_moved_between_the_board_read_and_the_fetch_is_skipped_by_name(tmp_path: Path):
    """What is folded must be the sha the board named — not whatever the ref is now."""
    candidate = train_mod.Candidate(11, "e" * 40, "moved", ("Closes #111",), (111,))
    git = git_fake()
    ledger = Ledger(tmp_path / "ledger.jsonl")
    fold = train_mod.fold([candidate], repo=tmp_path, git=git, worktree=tmp_path / "wt", base_tip=TIP, ledger=ledger)
    assert fold.folded == ()
    assert fold.skipped[0].reason == f"head-moved:#11:{'e' * 12}!={HEADS[11][:12]}"
    assert not any(argv[:1] == ["merge"] for argv in git.argvs()), "a moved head is never merged"


# --- control 2: an epic-closing PR body is refused ------------------------------
def test_an_epic_closing_pr_body_is_refused_by_name(tmp_path: Path):
    prs = [OpenPR(11, HEADS[11], title="a task"), OpenPR(12, HEADS[12], title="an epic closer")]
    bodies = {11: "Closes #111\n", 12: "Closes #707\n"}
    labels = {111: ("type:task",), 707: ("type:epic",)}

    candidates, skipped = train_mod.fold_candidates(prs, bodies, lambda issue: labels.get(issue, ()), {})
    assert [c.number for c in candidates] == [11]
    assert [s.reason for s in skipped] == ["refused-epic:#12:closes #707 (type:epic)"]

    # An UNREADABLE label answer must fail CLOSED: an API error is not permission
    # to fold a PR whose body may close an epic.
    _, skipped2 = train_mod.fold_candidates(prs, bodies, lambda issue: None, {})
    assert [s.reason for s in skipped2] == ["labels-unreadable:#11", "labels-unreadable:#12"]


def test_a_pr_whose_body_closes_nothing_is_refused_not_landed(tmp_path: Path):
    """The #1266 shape: 27 merges landed and closed 0 issues. A body with no own
    closing line cannot close anything, so the train refuses it by name."""
    prs = [OpenPR(11, HEADS[11], title="no closes line")]
    candidates, skipped = train_mod.fold_candidates(prs, {11: "## Summary\nnothing here\n"}, lambda issue: (), {})
    assert candidates == []
    assert [s.reason for s in skipped] == ["no-closes:#11"]
    # A body that could not be READ is its own refusal, never "closes nothing".
    _, skipped2 = train_mod.fold_candidates(prs, {11: None}, lambda issue: (), {})
    assert [s.reason for s in skipped2] == ["body-unreadable:#11"]


# --- control 3: a red attributes to the right PR --------------------------------
def test_a_red_attributes_to_the_right_pr_and_only_tests_prs_that_touch_it(tmp_path: Path):
    candidates = [
        train_mod.Candidate(11, HEADS[11], "portal only", ("Closes #111",), (111,)),
        train_mod.Candidate(12, HEADS[12], "green with the check", ("Closes #112",), (112,)),
        train_mod.Candidate(13, HEADS[13], "reds the check", ("Closes #113",), (113,)),
    ]
    diffs = {
        HEADS[11]: ["portal/app.py"],
        HEADS[12]: ["scripts/lib/thing.sh"],
        HEADS[13]: ["scripts/check-shell-patterns.sh"],
    }
    git = git_fake(diffs=diffs)

    def sh_handler(argv, kwargs):
        if argv[:2] == ["bash", "scripts/check-shell-patterns.sh"]:
            tree = Path(kwargs["cwd"])
            if tree.name.startswith("13-"):
                return Result(1, "check-shell-patterns: FAIL — 1 finding(s)\n")
            return Result(0, "check-shell-patterns: PASS\n")
        return Result(0)

    sh = Fake(sh_handler, "sh")
    ledger = Ledger(tmp_path / "ledger.jsonl")
    out = train_mod.attribute_red(
        ["check-shell-patterns"],
        candidates,
        repo=tmp_path,
        git=git,
        sh=sh,
        runner_dir=tmp_path / "runner",
        base_tip=TIP,
        ledger=ledger,
        train_id="train/T",
    )
    assert [(a.check, a.pr, a.verdict) for a in out] == [("check-shell-patterns", 13, "attributed")]
    assert out[0].line() == "red:check-shell-patterns <- #13"
    assert train_mod.held_prs(out) == [(13, "red:check-shell-patterns <- #13 (train verify)")]

    # The candidate whose diff is DISJOINT from the check's inputs was never
    # built: that is the filter that turns a 15-minute bisect into seconds.
    trees = [argv[3] for argv, _ in git.calls if argv[:2] == ["worktree", "add"] and "reasons" in argv[3]]
    assert not any("11-" in tree for tree in trees), trees
    tested = [kwargs["cwd"] for argv, kwargs in sh.calls]
    assert not any(Path(tree).name.startswith("11-") for tree in tested)
    assert out[0].detail == "red on master+#13", "green on master and on master+#12 is what makes #13 the cause"


def test_a_check_red_on_master_alone_is_pre_existing_and_no_candidate_is_blamed(tmp_path: Path):
    candidates = [train_mod.Candidate(12, HEADS[12], "t", ("Closes #112",), (112,))]
    git = git_fake(diffs={HEADS[12]: ["scripts/x.sh"]})

    def sh_handler(argv, kwargs):
        # red on the base ALONE (the tree at TIP has no candidate in its name)
        return Result(1, "check-shell-patterns: FAIL\n")

    sh = Fake(sh_handler, "sh")
    out = train_mod.attribute_red(
        ["check-shell-patterns"],
        candidates,
        repo=tmp_path,
        git=git,
        sh=sh,
        runner_dir=tmp_path / "runner",
        base_tip=TIP,
        ledger=Ledger(tmp_path / "l.jsonl"),
    )
    assert [(a.verdict, a.pr) for a in out] == [("pre-existing", None)]
    assert out[0].line() == "red:check-shell-patterns <- pre-existing(master)"
    assert train_mod.held_prs(out) == [], "a pre-existing red holds nobody"
    assert not any(argv[:2] == ["worktree", "add"] and "reasons/12" in argv[3] for argv, _ in git.calls)


def test_a_failing_check_with_no_script_is_unrunnable_never_green(tmp_path: Path):
    git = git_fake(checks=())
    out = train_mod.attribute_red(
        ["check-does-not-exist"],
        [],
        repo=tmp_path,
        git=git,
        sh=Fake(name="sh"),
        runner_dir=tmp_path / "runner",
        base_tip=TIP,
        ledger=Ledger(tmp_path / "l.jsonl"),
    )
    assert [(a.verdict, a.pr) for a in out] == [("unrunnable", None)]
    assert out[0].line() == "red:check-does-not-exist <- unrunnable"


# --- control 4: a base that moved never lands stale evidence --------------------
def train_transports(*, gh_bodies, labels, git, sh, merge_commit="e" * 40):
    """The full cli-level transports for a train, with every seam faked."""

    def gh_handler(argv, kwargs):
        if argv[:2] == ["auth", "status"]:
            return Result(0)
        if argv[:2] == ["pr", "list"] and "--head" in argv:
            return Result(0, json.dumps([{"number": 900}]))
        if argv[:2] == ["pr", "list"]:
            return Result(
                0,
                json.dumps([{"number": 11, "headRefOid": HEADS[11], "mergeable": "MERGEABLE", "isDraft": False, "baseRefName": "master", "title": "eleven"}]),
            )
        if argv[:2] == ["pr", "view"] and argv[-1] == "body":
            return Result(0, json.dumps({"body": gh_bodies.get(int(argv[2]))}))
        if argv[:2] == ["pr", "view"]:
            return Result(0, json.dumps({"state": "MERGED", "mergeCommit": {"oid": merge_commit}}))
        if argv[:1] == ["api"]:
            issue = int(argv[1].rsplit("/", 1)[1])
            return Result(0, json.dumps(list(labels.get(issue, ()))))
        return Result(0, "[]")

    return cli.Transports(git=git, gh=Fake(gh_handler, "gh"), gcloud=None, sh=sh, env={"AO_RUNNER_HOST_ROLE": "primary"})


def verify_sh(*, verify_rc=0, verify_out="verify: PASS (215 of 215 checks)"):
    def handler(argv, kwargs):
        if argv[:2] == ["bash", "scripts/verify.sh"]:
            return Result(verify_rc, verify_out)
        return Result(0)

    return Fake(handler, "sh")


def test_a_base_that_moved_re_folds_and_never_lands_stale_evidence(tmp_path: Path, monkeypatch):
    """The verified tree's base moved: nothing is posted, nothing is landed."""
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    git = git_fake(moved_to=MOVED, move_after=1)
    sh = verify_sh()
    gh = train_transports(gh_bodies={11: "Closes #111\n"}, labels={111: ("type:task",)}, git=git, sh=sh)
    rc = cli.train_cycle(gh, base=tmp_path / "runner", apply=True, slug=SLUG, max_rounds=1, sleep=lambda _s: None, out=_Sink())
    rows = Ledger(tmp_path / "runner" / "ledger.jsonl").rows()
    assert rc == cli.NOT_OK, "a train whose base moved is not a success"
    assert any(row.get("event") == "train-base-moved" for row in rows), rows
    assert not any(row.get("event") == "train-post" for row in rows), "no green is published for a tree that no longer stands"
    landed = [argv for argv, _ in sh.calls if argv[:2] == ["bash", "scripts/merge-pr.sh"]]
    assert landed == [], "stale evidence is never landed"
    assert any(row.get("event") == "train-abandon" for row in rows), "the round's own PR is closed by name, not left open"


def test_a_base_that_moved_converges_on_the_next_round(tmp_path: Path, monkeypatch):
    """Bounded re-folding: the second round sees a stable base, verifies and lands."""
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    git = git_fake(moved_to=MOVED, move_after=1, move_once=True)
    sh = verify_sh()
    gh = train_transports(gh_bodies={11: "Closes #111\n"}, labels={111: ("type:task",)}, git=git, sh=sh)
    rc = cli.train_cycle(gh, base=tmp_path / "runner", apply=False, slug=SLUG, max_rounds=2, sleep=lambda _s: None, out=_Sink())
    rows = Ledger(tmp_path / "runner" / "ledger.jsonl").rows()
    assert any(row.get("event") == "train-base-moved" for row in rows), rows
    assert any(row.get("event") == "train-land-dry-run" for row in rows), rows
    assert any(row.get("event") == "train-verify" and row.get("state") == "green" and row.get("posted") for row in rows)
    assert rc == cli.OK, rows
    verb = [argv for argv, _ in sh.calls if argv[:2] == ["bash", "scripts/merge-pr.sh"]]
    assert verb == [["bash", "scripts/merge-pr.sh", "--pr", "900"]], verb
    assert ["bash", "scripts/verify.sh", "verify"] in [argv for argv, _ in sh.calls]


def test_a_green_that_could_not_be_posted_is_never_landed(tmp_path: Path):
    """The guard half on its own: `pre_post` refuses and no post is recorded."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    posts: list[tuple[str, int]] = []
    record = train_mod.verify_once(
        git=git_fake(),
        sh=verify_sh(),
        post_status=lambda sha, rc: (posts.append((sha, rc)), Result(0))[1],
        repo=tmp_path,
        runner_dir=tmp_path / "runner",
        worktree=tmp_path / "wt",
        pr=900,
        head=FOLD_HEAD,
        ledger=ledger,
        sleep=lambda _s: None,
        pre_post=lambda: "base-moved:cccccccccccc->dddddddddddd",
    )
    assert (record.rc, record.state, record.posted) == (0, "green", False)
    assert posts == [], "nothing is posted when the tree no longer stands"
    rows = ledger.rows()
    assert any(row.get("event") == "train-post-skipped" and "base-moved" in str(row.get("reason")) for row in rows)


def test_a_parked_verify_is_retried_and_never_posted(tmp_path: Path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    seen: list[int] = []

    def sh_handler(argv, kwargs):
        if argv[:2] == ["bash", "scripts/verify.sh"]:
            seen.append(1)
            return Result(10, "verify: PARKED — no gate permit")
        return Result(0)

    posts: list[tuple[str, int]] = []
    record = train_mod.verify_once(
        git=git_fake(),
        sh=Fake(sh_handler, "sh"),
        post_status=lambda sha, rc: (posts.append((sha, rc)), Result(0))[1],
        repo=tmp_path,
        runner_dir=tmp_path / "runner",
        worktree=tmp_path / "wt",
        pr=900,
        head=FOLD_HEAD,
        ledger=ledger,
        attempts=3,
        sleep=lambda _s: None,
    )
    assert len(seen) == 3, "the parked rc is a permit the box did not have, so it is retried"
    assert record.state == "cannot-assess" and record.rc == 10
    assert posts == [], "a parked run is not a verdict and is never posted"
    assert any(row.get("event") == "train-post-skipped" and "not-a-verdict" in str(row.get("reason")) for row in ledger.rows())


# --- the composed body ----------------------------------------------------------
def test_the_trailer_carries_every_folded_prs_own_closing_lines():
    fold = train_mod.Fold(
        base_tip=TIP,
        head=FOLD_HEAD,
        folded=(
            train_mod.Candidate(11, HEADS[11], "eleven", ("Closes #111", "Fixes #112"), (111, 112), class_rung="enterprise"),
            train_mod.Candidate(12, HEADS[12], "twelve", ("Closes #113",), (113,), class_rung="elite"),
        ),
        skipped=(train_mod.Skip(13, "conflicting:#13"),),
    )
    body = train_mod.compose_body(
        slug=SLUG, train_id="train/T", fold=fold, verify=None, attributions=(), touched=["fleet/runner/train.py"], globs=("scripts/check-*.sh",)
    )
    for line in ("Closes #111", "Fixes #112", "Closes #113"):
        assert line in body.splitlines(), f"the PR's OWN closing line is carried: {line}"
    # The shared predicate requires a `Refs <slug>#<n>` line INSIDE the trailing
    # block, and a bare `Closes #n` alone is not a trailer.
    block = body.rstrip("\n").split("\n\n")[-1].splitlines()
    assert block and all(line.startswith(("Refs ", "Closes ", "Fixes ")) for line in block), block
    assert any(line.startswith("Refs kushin77/agent-orchestrator#") for line in block), block
    # The PR-body contract's own shapes.
    assert any(line.startswith("Closes #") for line in body.splitlines())
    assert any(line.startswith("AI-assistance: ") and line.rstrip().endswith(")") for line in body.splitlines())
    assert "## Pre-existing red" in body
    assert "Gate-changing: no" in body, "the declaration is derived, and this diff touches no gate path"
    assert "class: elite" in body, "the train declares the highest rung its members declared"
    assert "conflicting:#13" in body, "the skipped PR is named in the body too, never silently dropped"


def test_the_gate_changing_declaration_follows_the_folds_own_diff():
    globs = train_mod.gate_paths(Path(ROOT))
    assert train_mod.gate_changing_line(["fleet/runner/train.py"], globs) == "Gate-changing: no"
    line = train_mod.gate_changing_line(["scripts/check-pr-runner.sh", "fleet/runner/train.py"], globs)
    assert line == "Gate-changing: yes — scripts/check-pr-runner.sh", line


# --- the ledger view ------------------------------------------------------------
def test_train_status_answers_from_the_ledger():
    rows = [
        {"event": "train-start", "train_id": "train/20260919T000000Z", "apply": True},
        {"event": "train-skip", "train_id": "train/20260919T000000Z", "pr": 12, "reason": "conflicting:#12"},
        {"event": "train-fold", "train_id": "train/20260919T000000Z", "pr": 11, "sha": HEADS[11], "ok": True},
        {
            "event": "train-verify",
            "train_id": "train/20260919T000000Z",
            "head": FOLD_HEAD,
            "rc": 1,
            "state": "red",
            "attempts": 1,
            "posted": True,
            "failing_checks": ["check-shell-patterns"],
            "verify_summary": "verify: FAIL (1 of 215 checks failed)",
            "evidence_log": "logs/900-ffffffffffff.log",
        },
        {"event": "train-attr", "train_id": "train/20260919T000000Z", "check": "check-shell-patterns", "pr": 13, "verdict": "attributed"},
        {"event": "train-hold", "train_id": "train/20260919T000000Z", "pr": 13, "reason": "red:check-shell-patterns <- #13 (train verify)"},
        {"event": "train-end", "train_id": "train/20260919T000000Z", "rc": 1, "state": "red"},
    ]
    lines = train_mod.train_lines(rows)
    assert len(lines) == 1, lines
    text = lines[0]
    for needle in (
        "train train/20260919T000000Z",
        "apply=True",
        "folded:1",
        "skipped:conflicting:#12",
        "verify=red",
        "failing:check-shell-patterns",
        "red:check-shell-patterns <- #13",
        "held:#13",
        "rc=1",
    ):
        assert needle in text, f"{needle} not in {text}"
    assert train_mod.train_lines([]) == []


def test_the_train_spells_no_raw_merge_command_of_its_own():
    """The ONE guarded verb (lesson 4). The repo's own grep covers cli.py and
    merge.py but not the new module, so the control is asserted here."""
    raw = "gh pr " + "merge"
    for path in (Path(ROOT) / "fleet" / "runner" / "train.py", Path(ROOT) / "fleet" / "runner" / "cli.py"):
        text = path.read_text(encoding="utf-8")
        assert raw not in text, f"{path.name} spells the raw merge command"
    train_text = (Path(ROOT) / "fleet" / "runner" / "train.py").read_text(encoding="utf-8")
    assert "MERGE_VERB" in train_text, "the train lands through the guarded verb, by name"


class _Sink:
    """A stdout sink: the cycle's own report is asserted through the ledger."""

    def write(self, text):
        return len(text)

    def flush(self):
        return None
