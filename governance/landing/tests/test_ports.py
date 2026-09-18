"""The effects, at argv level: no force, no empty environment (#764).

These are the two properties a landing must never lose, and neither is visible in
the engine's decision: a push that force-pushes, or a contract run with an empty
environment, would both look like "something went wrong" rather than like a
policy violation. So they are pinned where the argv is built.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from governance.landing import ports

MERGE_COMMIT = "c" * 40


def _stub(argv, *, cwd=None, env=None):
    """A canned `_run`: answers the reads the effects parse."""
    stdout = ""
    if argv[0] == "gh" and "list" in argv:
        stdout = '[{"number":11,"state":"OPEN","headRefOid":"%s"}]' % ("a" * 40)
    elif argv[0] == "gh" and "view" in argv:
        stdout = '{"state":"MERGED","mergeCommit":{"oid":"%s"}}' % MERGE_COMMIT
    return ports.CommandResult(argv=tuple(argv), rc=0, stdout=stdout, stderr="")


@pytest.fixture
def recorded(monkeypatch):
    calls: list = []

    def _run(argv, *, cwd=None, env=None):
        calls.append({"argv": [str(part) for part in argv], "cwd": cwd, "env": env})
        return _stub(list(argv), cwd=cwd, env=env)

    monkeypatch.setattr(ports, "_run", _run)
    return calls


class TestNoForceAnywhere:
    def test_the_push_is_the_plain_upstream_push(self, recorded, tmp_path):
        ports.GitHubOps(tmp_path).push("issue-764")
        assert recorded[0]["argv"] == ["git", "-C", str(tmp_path), "push", "-u", "origin", "issue-764"]

    def test_no_effect_passes_a_force_flag(self, recorded, tmp_path):
        ops = ports.GitHubOps(tmp_path)
        ops.push("issue-764")
        ops.pull_request_for("issue-764")
        ops.open_pr(branch="issue-764", base="master", title="t", body_file=tmp_path / "body.md")
        ops.run_contract(pr_number=11)
        ops.merge_pr(11, subject="t", body_file=tmp_path / "body.md")
        ops.delete_branch("issue-764")
        ops.close_lifecycle(764)
        offenders = [
            call["argv"]
            for call in recorded
            if any(part.startswith("--force") or part in ("-f", "-F") for part in call["argv"])
        ]
        assert offenders == []
        assert len(recorded) >= 7, "the control did not exercise every effect"

    def test_the_merge_is_a_squash_merge_without_an_implicit_branch_delete(self, recorded, tmp_path):
        """The branch delete is its own named step (rule 16), not a side effect."""
        ports.GitHubOps(tmp_path).merge_pr(11, subject="the subject", body_file=tmp_path / "squash.md")
        merge = next(call["argv"] for call in recorded if "merge" in call["argv"])
        assert merge[:2] == ["gh", "pr"]
        assert "--squash" in merge
        assert "--subject" in merge and "the subject" in merge
        assert "--body-file" in merge and str(tmp_path / "squash.md") in merge
        assert "--delete-branch" not in merge

    def test_the_landed_contract_is_the_shared_predicate_over_the_commits_to_squash(self, recorded, tmp_path):
        """The merge precondition validates the artifact that lands (issue #998)."""
        ports.GitHubOps(tmp_path).check_landed_contract(base="master", head="a" * 40)
        argv = recorded[0]["argv"]
        assert argv[0] == "bash"
        assert argv[1].endswith("scripts/check-pr-contract.sh")
        assert "--landed" in argv and "--range" in argv
        assert argv[-1] == "master.." + "a" * 40


class TestTheContractEnvironment:
    def test_ao_pr_number_is_added_to_the_real_environment(self, recorded, tmp_path):
        """An empty environment would run the contract without git on PATH."""
        ports.GitHubOps(tmp_path, base="master").run_contract(pr_number=11)
        env = recorded[0]["env"]
        assert env["AO_PR_NUMBER"] == "11"
        assert env.get("PATH") == os.environ.get("PATH")
        assert env.get("HOME") == os.environ.get("HOME")

    def test_an_explicit_environment_is_honoured_and_augmented(self, recorded, tmp_path):
        ports.GitHubOps(tmp_path, env={"PATH": "/fixture/bin"}).run_contract(pr_number=11)
        env = recorded[0]["env"]
        assert env["PATH"] == "/fixture/bin" and env["AO_PR_NUMBER"] == "11"

    def test_without_a_pull_request_the_contract_runs_without_the_pr_context(self, recorded, tmp_path):
        ports.GitHubOps(tmp_path).run_contract(pr_number=None)
        assert "AO_PR_NUMBER" not in recorded[0]["env"]

    def test_the_contract_is_the_repo_merge_gate(self, recorded, tmp_path):
        ports.GitHubOps(tmp_path).run_contract(pr_number=None)
        assert recorded[0]["argv"] == ["bash", str(tmp_path / "scripts" / "merge-gate.sh"), "run"]


class TestFailuresAreNamed:
    def test_a_refused_push_raises_and_never_retries_with_force(self, monkeypatch, tmp_path):
        calls: list = []

        def _run(argv, *, cwd=None, env=None):
            calls.append([str(part) for part in argv])
            return ports.CommandResult(argv=tuple(argv), rc=1, stdout="", stderr="! [rejected] non-fast-forward")

        monkeypatch.setattr(ports, "_run", _run)
        with pytest.raises(ports.PortError) as raised:
            ports.GitHubOps(tmp_path).push("issue-764")
        assert "normal" in str(raised.value) or "refused" in str(raised.value)
        assert calls == [["git", "-C", str(tmp_path), "push", "-u", "origin", "issue-764"]]
        assert not any(any(part.startswith("--force") for part in call) for call in calls)

    def test_a_failed_merge_raises_rather_than_reporting_success(self, monkeypatch, tmp_path):
        def _run(argv, *, cwd=None, env=None):
            stdout = '{"state":"OPEN","mergeCommit":null}' if "view" in argv else ""
            return ports.CommandResult(argv=tuple(argv), rc=0, stdout=stdout, stderr="")

        monkeypatch.setattr(ports, "_run", _run)
        with pytest.raises(ports.PortError):
            ports.GitHubOps(tmp_path).merge_pr(11, subject="t", body_file=tmp_path / "body.md")


class TestRecordingOps:
    def test_a_dry_run_records_writes_and_performs_none(self, tmp_path):
        reads = ports.GitHubOps(tmp_path)
        recording = ports.RecordingOps(reads=reads)
        recording.push("issue-764")
        recording.merge_pr(11, subject="t", body_file=tmp_path / "body.md")
        recording.delete_branch("issue-764")
        recording.close_lifecycle(764)
        assert [planned.action for planned in recording.planned] == [
            "push",
            "merge",
            "delete-branch",
            "lifecycle-close",
        ]

    def test_reads_stay_real_so_the_plan_is_about_the_lane_as_it_is(self, tmp_path, monkeypatch):
        calls: list = []

        def _run(argv, *, cwd=None, env=None):
            calls.append([str(part) for part in argv])
            return ports.CommandResult(argv=tuple(argv), rc=0, stdout="" if "rev-parse" not in argv else "abc123\n")

        monkeypatch.setattr(ports, "_run", _run)
        recording = ports.RecordingOps(reads=ports.GitHubOps(tmp_path))
        assert recording.head_commit() == "abc123"
        assert calls == [["git", "-C", str(tmp_path), "rev-parse", "HEAD"]]
        assert recording.planned == []
