"""``claim --base <upstream-branch>`` — the DISPATCH half of DG-3 (issue #699).

A claim that would otherwise be refused ONLY because its issue is blocked by an
in-flight upstream lane is accepted as SPECULATIVE when ``speculative_base``
names that upstream's own branch: the call hands off to
``governance.isolation.speculative.claim`` so the isolation gate enforces the
mandatory re-verify before the lane's PR (governance/isolation/README.md
§7.1). Every OTHER out-of-order refusal — wrong milestone frontier, no chain
edge, already-claimed, file-region-claimed — is unaffected by ``--base``: this
suite's negative controls are as important as its happy path (GR-12).
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import claims
import pytest
from model import (
    REASON_ALREADY_CLAIMED,
    REASON_BLOCKED,
    REASON_FILE_REGION_CLAIMED,
    REASON_NO_CHAIN_EDGE,
    REASON_SPECULATIVE_BASE,
    REASON_SPECULATIVE_BASE_NOT_UPSTREAM,
    FileClaim,
    Issue,
    Snapshot,
)

from governance.isolation import speculative
from governance.isolation.identity import mint


def git(cwd: Path | str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run git in a scratch repository, never reading the developer's global config."""
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(cwd), "GIT_CONFIG_NOSYSTEM": "1"},
    )
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real repo: master, plus branch ``issue-603`` (upstream) cut from it, plus
    branch ``issue-602`` (downstream) cut from the upstream branch — exactly the
    branch-stacking shape #699 exists for.
    """
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master", str(root)], check=True, capture_output=True, text=True)
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.com")
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    git(root, "add", "seed.txt")
    git(root, "commit", "-q", "-m", "seed")

    git(root, "branch", "issue-603")
    git(root, "checkout", "-q", "issue-603")
    (root / "upstream.txt").write_text("upstream\n", encoding="utf-8")
    git(root, "add", "upstream.txt")
    git(root, "commit", "-q", "-m", "add upstream work")

    git(root, "branch", "issue-602")
    git(root, "checkout", "-q", "issue-602")
    (root / "downstream.txt").write_text("downstream\n", encoding="utf-8")
    git(root, "add", "downstream.txt")
    git(root, "commit", "-q", "-m", "add downstream work")

    git(root, "branch", "issue-999")  # a real branch that is NOT a blocker of #602

    git(root, "checkout", "-q", "master")
    return root


@pytest.fixture
def board() -> Snapshot:
    """#602 blocked by #603, in the same milestone; #601 the frontier; #650
    already claimed by another agent (for the already-claimed control).
    """
    issues = {
        601: Issue(601, "frontier", milestone="M1"),
        602: Issue(602, "blocked by 603", milestone="M1", blocked_by=(603,)),
        603: Issue(603, "upstream", milestone="M1"),
        # Its own milestone, alone, unblocked: frontier for whoever claims it
        # first, so the ALREADY-CLAIMED control tests arbitration's live-claim
        # refusal specifically, not an incidental no-chain-edge.
        650: Issue(650, "held by another lane", milestone="M3"),
        651: Issue(651, "no chain edge", milestone="M2"),
    }
    return Snapshot(generated_at="2026-09-16T00:00:00Z", source="test", issues=issues)


BASE_TIME = datetime(2026, 9, 16, 0, 5, 0, tzinfo=timezone.utc)


def _claim(board, tmp_path, **kwargs):
    kwargs.setdefault("now", BASE_TIME)
    return claims.claim(
        kwargs.pop("issue", 602),
        kwargs.pop("agent", "agent-x"),
        kwargs.pop("lane", "erp-consumer"),
        board,
        ledger=tmp_path / "claims.jsonl",
        lock_dir=tmp_path / "locks",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Happy path: a genuinely blocked issue, --base names its actual blocker.
# ---------------------------------------------------------------------------


def test_speculative_base_turns_blocked_into_accepted(board, tmp_path, repo):
    event = _claim(board, tmp_path, speculative_base="issue-603", main=repo)

    assert event.event == "claim"
    assert event.reason == REASON_SPECULATIVE_BASE


def test_ledger_event_records_speculative_base(board, tmp_path, repo):
    event = _claim(board, tmp_path, speculative_base="issue-603", main=repo)
    replayed = claims.read_ledger(tmp_path / "claims.jsonl")
    assert replayed[-1].reason == "speculative_base"
    assert event.reason == "speculative_base"


def test_isolation_attestation_is_written_for_the_speculative_lane(board, tmp_path, repo):
    _claim(board, tmp_path, agent="agent-x", lane="erp-consumer", speculative_base="issue-603", main=repo)
    identity = mint(602, "agent-x", "erp-consumer")
    attestation = speculative.read(repo, identity.session_id)
    assert attestation is not None
    assert attestation.speculative_base == "issue-603"


def test_without_base_a_blocked_claim_is_still_refused(board, tmp_path, repo):
    with pytest.raises(claims.ClaimRefused) as excinfo:
        _claim(board, tmp_path, main=repo)
    assert excinfo.value.reason == REASON_BLOCKED


# ---------------------------------------------------------------------------
# Negative controls (mandatory, #699 acceptance criterion 2): --base NEVER
# bypasses an out-of-order refusal it was not built for.
# ---------------------------------------------------------------------------


def test_base_naming_a_non_blocker_branch_is_refused_by_name(board, tmp_path, repo):
    """A real branch exists (issue-999) but is not #602's blocker — refused by
    name, never silently treated as speculative, and #602 stays `blocked`."""
    with pytest.raises(claims.ClaimRefused) as excinfo:
        _claim(board, tmp_path, speculative_base="issue-999", main=repo)
    assert excinfo.value.reason == REASON_SPECULATIVE_BASE_NOT_UPSTREAM


def test_base_does_not_bypass_already_claimed(board, tmp_path, repo):
    # #601 (the frontier, unblocked) is claimed by another agent first; --base
    # cannot invent a speculative exemption where arbitration already refused
    # for a different, earlier reason (a live claim by another agent) — this
    # is refused before `blocked` is even reachable.
    claims.claim(
        601, "holder", "lane-a", board, ledger=tmp_path / "claims.jsonl", lock_dir=tmp_path / "locks", now=BASE_TIME
    )
    with pytest.raises(claims.ClaimRefused) as excinfo:
        _claim(board, tmp_path, issue=601, agent="agent-x", speculative_base="issue-603", main=repo)
    assert excinfo.value.reason == REASON_ALREADY_CLAIMED


def test_base_does_not_bypass_no_chain_edge(board, tmp_path, repo):
    with pytest.raises(claims.ClaimRefused) as excinfo:
        _claim(board, tmp_path, issue=651, speculative_base="issue-603", main=repo)
    assert excinfo.value.reason == REASON_NO_CHAIN_EDGE


def test_base_does_not_bypass_file_region_claimed(board, tmp_path, repo):
    ledger = tmp_path / "claims.jsonl"
    locks = tmp_path / "locks"
    claims.claim(
        601,
        "agent-a",
        "lane-a",
        board,
        ledger=ledger,
        lock_dir=locks,
        now=BASE_TIME,
        files=(FileClaim(path="shared.py", regions=((1, 10),)),),
    )
    # #602 IS genuinely blocked-by #603, so this exercises the file-region
    # refusal happening AFTER the (accepted) speculative exemption would have
    # applied — the file lock must still bite.
    with pytest.raises(claims.ClaimRefused) as excinfo:
        claims.claim(
            602,
            "agent-b",
            "lane-b",
            board,
            ledger=ledger,
            lock_dir=locks,
            now=BASE_TIME,
            speculative_base="issue-603",
            main=repo,
            files=(FileClaim(path="shared.py", regions=((5, 15),)),),
        )
    assert excinfo.value.reason == REASON_FILE_REGION_CLAIMED


def test_mutation_would_be_caught_a_blocked_issue_with_unrelated_base_stays_refused(board, tmp_path, repo):
    """The provocation the gate script runs: a mutant that made `--base` bypass
    the frontier/blocker check regardless of WHICH branch it names would pass
    this test's request but must not — it stays refused by name."""
    for candidate in ("issue-999", "master", "does-not-exist"):
        with pytest.raises(claims.ClaimRefused) as excinfo:
            _claim(board, tmp_path, speculative_base=candidate, main=repo)
        assert excinfo.value.reason == REASON_SPECULATIVE_BASE_NOT_UPSTREAM


# ---------------------------------------------------------------------------
# End-to-end fixture (#699 acceptance criterion 3): claim -> speculative ->
# upstream squash-lands -> reverify -> isolation audit accepts.
# ---------------------------------------------------------------------------


def test_end_to_end_speculative_lands_and_reverifies_clean(board, tmp_path, repo):
    event = _claim(board, tmp_path, speculative_base="issue-603", main=repo)
    assert event.reason == "speculative_base"

    # The upstream squash-lands into master (the landing path this repo uses):
    # a single commit on master carrying the canonical trailer for #603.
    git(repo, "checkout", "-q", "master")
    git(repo, "merge", "--squash", "-q", "issue-603")
    git(repo, "commit", "-q", "-m", "land upstream work", "-m", "Refs kushin77/agent-orchestrator#603")

    # The downstream lane pulls the now-landed master into its own branch —
    # the ordinary way a speculative lane picks up the real landing — which
    # moves its own merge-base with master forward to the squash commit.
    git(repo, "checkout", "-q", "issue-602")
    git(repo, "merge", "-q", "master", "-m", "merge landed master")
    git(repo, "checkout", "-q", "master")

    identity = mint(602, "agent-x", "erp-consumer")

    # Before reverify, the gate still refuses: the attestation's merge_base is
    # stale relative to the merge-base that just moved.
    stale = speculative.verify(repo, identity, base="master")
    assert {problem.code for problem in stale} == {"speculative-base-stale-merge-base"}

    attestation = speculative.reverify(repo, identity, base="master")
    assert attestation.merge_base == git(repo, "rev-parse", "master").stdout.strip()

    # The final merge base names where #602 actually forks from the now-landed
    # master — the re-verify gate accepts.
    accepted = speculative.verify(repo, identity, base="master")
    assert accepted == []
