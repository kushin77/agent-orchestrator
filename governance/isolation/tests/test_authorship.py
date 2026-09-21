"""Authorship ownership — a lane's own range belongs to the lane session (#934).

The ambient identity env that rule 15 prescribes is what broke the gate of record:
`GIT_AUTHOR_*`/`GIT_COMMITTER_*` outrank `git config --worktree`, so a commit made
in a shell carrying another session's pair is signed by that other session, and
`audit.py` — which selects a lane's commits *by author address* — could not see it
at all. The gate was not merely red; it was blinded.

These tests hold both halves of the fix:

* the rule **bites** — a commit a foreign session authored inside an otherwise
  correctly signed lane is named, with no help from the trailer rule;
* the rule is **precise** — the commits the base branch carried (which in this
  repository are all authored by *some* agent) are not attributed to the lane, so
  a lane that merges `master` to stay current is not failed for it. A rule that
  flagged those would red every lane and get itself disabled.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from governance.isolation import cli
from governance.isolation.audit import Violation
from governance.isolation.identity import GIT_IDENTITY_VARS, mint
from governance.isolation.worktree import provision, write_record

import importlib.util as _importlib_util  # noqa: E402

# A bare ``from conftest import ...`` is not safe here: when this suite is
# collected alongside other governance suites, every one of their
# ``tests/conftest.py`` files lands under the same bare module identity
# ``conftest`` in ``sys.modules``, so whichever conftest is imported LAST
# silently wins the name for the rest of collection (issues #699, #702).
# Loading this file's own conftest by absolute path guarantees this module
# always gets ITS directory's conftest regardless of collection order.
_conftest_spec = _importlib_util.spec_from_file_location(
    "governance_isolation_tests_conftest", Path(__file__).with_name("conftest.py")
)
_conftest = _importlib_util.module_from_spec(_conftest_spec)
_conftest_spec.loader.exec_module(_conftest)
commit = _conftest.commit
git = _conftest.git

FOREIGN_NAME = "agent-someone-else"
FOREIGN_EMAIL = "agent+someone-else@agents.invalid"

REPO_ROOT = Path(__file__).resolve().parents[3]


def codes(problems) -> set[str]:
    return {problem.code for problem in problems}


def commit_as(
    path: Path, filename: str, message: str, trailer: str, name: str, email: str
) -> str:
    """Commit under an EXPLICIT identity pair — the shape an ambient export makes.

    `conftest.git` deliberately carries no `GIT_*` identity, which is what makes it
    a faithful stand-in for a shell that exported none. This helper is the other
    case: the pair is set for the one command, exactly as
    `GIT_AUTHOR_EMAIL=… git commit` does, and exactly as a shared shell that
    inherited rule 15's env would.
    """
    (path / filename).write_text(f"{filename}\n", encoding="utf-8")
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(path),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
    }
    subprocess.run(["git", "-C", str(path), "add", filename], check=True, capture_output=True, text=True, env=env)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-q", "-m", message, "-m", trailer],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()


def test_a_commit_a_foreign_session_authored_is_refused(lane, repo: Path):
    """The contamination the ambient env produced, now named instead of hidden.

    The message carries a well-formed ticket trailer for the lane's own issue, so
    the trailer rule is satisfied and authorship is the ONLY defect — which is why
    the shared `audit_lane` (author-address selection) cannot fail this lane and
    this rule must.
    """
    commit_as(lane.worktree, "work.txt", "implement something", lane.trailer, FOREIGN_NAME, FOREIGN_EMAIL)
    problems = cli.foreign_authored_commits(lane, repo)
    assert codes(problems) == {"commit-authored-by-another-session"}
    # The refusal names both the commit and whose identity signed it.
    assert FOREIGN_EMAIL in str(problems)
    # ...and it is not the trailer rule doing the work: that one is satisfied.
    assert "commit-missing-ticket-trailer" not in codes(cli.audit_lane(lane, repo))


def test_the_same_commit_authored_by_the_session_is_accepted(lane, repo: Path):
    """Vacuity: without the foreign pair there is nothing to refuse."""
    subprocess.run(
        ["git", "-C", str(lane.worktree), "commit", "--allow-empty", "-q", "-m", "x", "-m", lane.trailer],
        check=True,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(lane.worktree), "GIT_CONFIG_NOSYSTEM": "1"},
    )
    assert cli.foreign_authored_commits(lane, repo) == []


def test_a_commit_the_base_branch_carried_is_not_attributed_to_the_lane(lane, repo: Path):
    """The anti-false-positive half — every commit here is an agent's.

    In this repository *every* commit is authored by an `agents.invalid` identity,
    and a lane merges `master` to stay current, so "any foreign agent author
    reachable from HEAD" would fail every lane that does. The lane's own range is
    what makes the rule usable.
    """
    subprocess.run(
        ["git", "-C", str(lane.worktree), "commit", "--allow-empty", "-q", "-m", "mine", "-m", lane.trailer],
        check=True,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(lane.worktree), "GIT_CONFIG_NOSYSTEM": "1"},
    )
    commit_as(repo, "theirs.txt", "landed by another session", "Refs kushin77/agent-orchestrator#999",
              FOREIGN_NAME, FOREIGN_EMAIL)
    git(lane.worktree, "merge", "--no-edit", "-q", "master")
    # The foreign commit IS reachable from the lane's HEAD...
    reachable = git(lane.worktree, "log", "--no-merges", "--format=%ae").stdout
    assert FOREIGN_EMAIL in reachable
    # ...and is still not the lane's own history.
    assert cli.foreign_authored_commits(lane, repo) == []


def test_a_human_author_in_the_lane_range_is_not_flagged(lane, repo: Path):
    """Scope, stated as a control: the rule is about *session* authorship.

    What is enforced is that a session's own range belongs to that session — not
    that nobody else may ever touch a lane branch. A reviewer's commit is not a
    session identity and is deliberately not this rule's business.
    """
    commit_as(lane.worktree, "review.txt", "a maintainer's commit", lane.trailer,
              "Human Reviewer", "reviewer@example.invalid")
    assert cli.foreign_authored_commits(lane, repo) == []


def test_a_range_that_cannot_be_derived_is_unmeasurable(repo: Path, tmp_path: Path, mounts: Path):
    """No base ref resolves: unproven, never satisfied (GR-12).

    A rule that quietly reported "nothing to check" here would be the fail-open
    direction — indistinguishable from a repository where lanes are genuinely
    clean.
    """
    lonely = tmp_path / "lonely"
    lonely.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "lonely", str(lonely)], check=True, capture_output=True, text=True)
    git(lonely, "config", "user.name", "Human Dev")
    git(lonely, "config", "user.email", "human@example.com")
    (lonely / "seed.txt").write_text("seed\n", encoding="utf-8")
    git(lonely, "add", "seed.txt")
    git(lonely, "commit", "-q", "-m", "seed")
    identity = mint(263, "copilot-brain", "governance-isolation", worktree_root=tmp_path / "lanes2")
    provision(identity, lonely, base="HEAD", mounts=mounts)
    problems = cli.foreign_authored_commits(identity, lonely)
    assert codes(problems) == {"commit-authorship-unmeasurable"}


def test_a_missing_worktree_produces_no_authorship_finding(repo: Path, tmp_path: Path):
    """The rule defers to `worktree-missing` rather than double-reporting."""
    identity = mint(263, "copilot-brain", "governance-isolation", worktree_root=tmp_path / "absent")
    assert cli.foreign_authored_commits(identity, repo) == []
    assert "worktree-missing" in codes(cli.audit_lane(identity, repo))


def test_the_audit_verdict_does_not_depend_on_the_ambient_identity(lane, repo: Path):
    """The property the gate's env-independence control asserts, asserted here too.

    The same lane, with a foreign pair exported into the child, must audit OK: the
    signature is re-derived from `git config --worktree`, so the caller's
    environment is not an input to the verdict.
    """
    write_record(lane, repo)
    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "governance" / "isolation" / "cli.py"),
         "audit", "--main", str(repo), "--session", lane.session_id],
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(repo),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": FOREIGN_NAME,
            "GIT_AUTHOR_EMAIL": FOREIGN_EMAIL,
            "GIT_COMMITTER_NAME": FOREIGN_NAME,
            "GIT_COMMITTER_EMAIL": FOREIGN_EMAIL,
        },
    )
    assert completed.returncode == 0, completed.stderr


def test_the_shared_shell_env_withholds_the_git_pair(lane):
    """The safe form is the documented one: the leak cannot be `eval`ed by accident."""
    shared = lane.shared_shell_env()
    assert set(shared) & set(GIT_IDENTITY_VARS) == set()
    assert shared["AO_SESSION_ID"] == lane.session_id
    # The spawned-process form keeps both halves — a separate process is not shared.
    assert set(lane.env()) >= set(GIT_IDENTITY_VARS)
    # ...and the signature is offered as DATA, not as exportable variables.
    assert set(lane.git_signature()) == {"name", "email"}
    assert not set(lane.git_signature()) & set(GIT_IDENTITY_VARS)


def test_commit_form_scopes_the_signature_to_one_command(lane):
    form = lane.commit_form()
    for name in GIT_IDENTITY_VARS:
        assert f"{name}=" in form
    assert form.endswith("git commit -F <message-file>")


def test_a_per_commit_c_override_does_not_beat_the_exported_pair(lane):
    """The measurement that decides the documented form (git 2.53.0, issue #934).

    `git -c user.name=… -c user.email=…` looks like a safe per-commit override and
    is not: the exported pair wins, for the author *and* the committer. That is why
    the safe form prefixes the variables onto the one command instead — an
    assignment scoped to a process replaces the ambient pair rather than losing to
    it. If git ever changes this, the documented form is wrong and this test says so.
    """
    (lane.worktree / "cflag.txt").write_text("cflag\n", encoding="utf-8")
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(lane.worktree),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": FOREIGN_NAME,
        "GIT_AUTHOR_EMAIL": FOREIGN_EMAIL,
        "GIT_COMMITTER_NAME": FOREIGN_NAME,
        "GIT_COMMITTER_EMAIL": FOREIGN_EMAIL,
    }
    subprocess.run(["git", "-C", str(lane.worktree), "add", "cflag.txt"], check=True, capture_output=True, text=True, env=env)
    subprocess.run(
        ["git", "-C", str(lane.worktree), "-c", "user.name=cflag", "-c", "user.email=cflag@example.invalid",
         "commit", "-q", "-m", "cflag"],
        check=True, capture_output=True, text=True, env=env,
    )
    recorded = subprocess.run(
        ["git", "-C", str(lane.worktree), "log", "-1", "--format=%an%x1f%ae%x1f%cn%x1f%ce"],
        check=True, capture_output=True, text=True, env=env,
    ).stdout.strip()
    assert recorded == "\x1f".join([FOREIGN_NAME, FOREIGN_EMAIL, FOREIGN_NAME, FOREIGN_EMAIL])

    # The prefixed form, by contrast, is the session's — which is why it is the
    # one the CLI prints.
    (lane.worktree / "prefixed.txt").write_text("prefixed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(lane.worktree), "add", "prefixed.txt"], check=True, capture_output=True, text=True, env=env)
    signature = ["GIT_AUTHOR_NAME=" + lane.author_name, "GIT_AUTHOR_EMAIL=" + lane.author_email,
                 "GIT_COMMITTER_NAME=" + lane.author_name, "GIT_COMMITTER_EMAIL=" + lane.author_email]
    subprocess.run(
        ["env", *signature, "git", "-C", str(lane.worktree), "commit", "-q", "-m", "prefixed"],
        check=True, capture_output=True, text=True, env=env,
    )
    assert lane.author_email in subprocess.run(
        ["git", "-C", str(lane.worktree), "log", "-1", "--format=%ae"],
        check=True, capture_output=True, text=True, env=env,
    ).stdout


def test_the_violation_is_a_named_finding(lane, repo: Path):
    """GR-12: the finding is quotable evidence, not prose."""
    commit_as(lane.worktree, "work.txt", "implement something", lane.trailer, FOREIGN_NAME, FOREIGN_EMAIL)
    (problem,) = cli.foreign_authored_commits(lane, repo)
    assert isinstance(problem, Violation)
    assert str(problem).startswith("commit-authored-by-another-session:")
    assert "issue #934" in str(problem)


def test_audit_lane_full_keeps_both_rules(lane, repo: Path):
    """The composed audit is the union — each rule still catches its own shape.

    One commit per rule, deliberately: the session's own untrailed commit is the
    trailer rule's (the authorship rule cannot see it as foreign), and a foreign
    session's trailed commit is the authorship rule's (the trailer rule cannot see
    it at all, because it is not selected). Asserting only one shape would leave
    the other rule's survival unproven.
    """
    commit(lane.worktree, "mine.txt", "no trailer on this one")
    commit_as(lane.worktree, "theirs.txt", "authored elsewhere", lane.trailer, FOREIGN_NAME, FOREIGN_EMAIL)
    problems = codes(cli.audit_lane_full(lane, repo))
    assert "commit-missing-ticket-trailer" in problems
    assert "commit-authored-by-another-session" in problems


@pytest.mark.parametrize("var", GIT_IDENTITY_VARS)
def test_the_gate_clears_every_identity_variable(lane, var):
    """The gate's `unset` list and the identity module's list are the same four.

    Two lists of one rule drift silently, so the gate's list is required to be
    exactly this one — if a fifth variable is ever added here, the gate must learn
    it or the ambient leak returns by a new name.
    """
    lib_rel = "scripts/lib/unset-git-env.sh"
    unset_lib = (REPO_ROOT / lib_rel).read_text(encoding="utf-8")
    unset_lines = [line for line in unset_lib.splitlines() if line.startswith("unset GIT_")]
    assert unset_lines, "the shared lib does not clear the ambient identity env"
    assert var in " ".join(unset_lines)
    for name in ("check-session-isolation.sh", "check-isolation-landed.sh"):
        gate = (REPO_ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert lib_rel in gate, f"{name} no longer sources {lib_rel}"
