"""Speculative-base re-verify — the ISOLATION half of DG-3 (issue #699).

A downstream lane cut from an in-flight upstream branch (instead of waiting for
its squash-merge) must re-verify its merge base once the upstream lands, and a
PR whose attestation still names the pre-merge branch as its base is refused by
name. The fixture is real: two branches in a real repository, the upstream
squash-merged into master exactly the way a landing lane does it, and the
downstream lane's attestation checked before and after.
"""

from __future__ import annotations

from pathlib import Path

from governance.isolation import speculative
from governance.isolation.identity import mint
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
git = _conftest.git


def codes(problems) -> set[str]:
    return {problem.code for problem in problems}


def _upstream_lane(repo: Path, tmp_path: Path, mounts: Path):
    """A second lane (issue #645) cut from master, with unmerged work."""
    identity = mint(645, "copilot-brain", "erp-integration", worktree_root=tmp_path / "lanes")
    provision(identity, repo, base="HEAD", mounts=mounts)
    (identity.worktree / "erp.txt").write_text("erp\n", encoding="utf-8")
    git(identity.worktree, "add", "erp.txt")
    git(
        identity.worktree,
        "commit",
        "-q",
        "-m",
        "add erp integration",
        "-m",
        "Refs kushin77/agent-orchestrator#645",
    )
    return identity


def _downstream_lane(repo: Path, tmp_path: Path, upstream_branch: str, mounts: Path):
    """Issue #671, cut from the upstream branch instead of master (DG-3)."""
    identity = mint(671, "copilot-brain", "erp-consumer", worktree_root=tmp_path / "lanes")
    provision(identity, repo, base=upstream_branch, mounts=mounts)
    write_record(identity, repo)
    (identity.worktree / "consumer.txt").write_text("consumer\n", encoding="utf-8")
    git(identity.worktree, "add", "consumer.txt")
    git(
        identity.worktree,
        "commit",
        "-q",
        "-m",
        "consume erp integration",
        "-m",
        "Refs kushin77/agent-orchestrator#671",
    )
    return identity


def test_no_claim_is_out_of_scope(lane, repo: Path):
    """A lane that never claimed a speculative base is not constrained by this gate."""
    assert speculative.verify(repo, lane) == []


def test_claim_records_speculative_base_and_merge_base(repo: Path, tmp_path: Path, mounts: Path):
    upstream = _upstream_lane(repo, tmp_path, mounts)
    downstream = _downstream_lane(repo, tmp_path, upstream.branch, mounts)

    attestation = speculative.claim(repo, downstream, upstream.branch, base="master")

    assert attestation.speculative_base == upstream.branch
    assert attestation.speculative_base_sha == git(upstream.worktree, "rev-parse", "HEAD").stdout.strip()
    # merge_base(master, downstream) at cut time is master's tip, since the
    # downstream branch has not landed on master (it isn't even IN master's history).
    assert attestation.merge_base == git(repo, "rev-parse", "master").stdout.strip()


def test_refuses_before_the_upstream_has_landed(repo: Path, tmp_path: Path, mounts: Path):
    upstream = _upstream_lane(repo, tmp_path, mounts)
    downstream = _downstream_lane(repo, tmp_path, upstream.branch, mounts)
    speculative.claim(repo, downstream, upstream.branch, base="master")

    problems = speculative.verify(repo, downstream, base="master")
    assert "speculative-base-not-landed" in codes(problems)


def test_refuses_a_stale_merge_base_after_the_upstream_lands(repo: Path, tmp_path: Path, mounts: Path):
    upstream = _upstream_lane(repo, tmp_path, mounts)
    downstream = _downstream_lane(repo, tmp_path, upstream.branch, mounts)
    speculative.claim(repo, downstream, upstream.branch, base="master")

    # The upstream lane lands: squash-merged into master, exactly as a real
    # landing does it (issue #645 finishes before #671 re-verifies).
    git(repo, "merge", "--squash", upstream.branch)
    git(repo, "commit", "-q", "-m", "erp integration (squash)", "-m", "Refs kushin77/agent-orchestrator#645")

    # The downstream lane pulls the now-landed master into its own branch —
    # the ordinary way a speculative lane picks up the real landing — which
    # moves its OWN merge-base with master forward to the squash commit.
    git(downstream.worktree, "merge", "-q", "master", "-m", "merge landed master")

    # The attestation on disk still names the pre-merge-base merge_base: a PR
    # opened right now would carry a stale base, and must be refused BY NAME.
    before = speculative.record_path(repo, downstream.session_id).read_bytes()
    problems = speculative.verify(repo, downstream, base="master")
    assert "speculative-base-stale-merge-base" in codes(problems)
    # Provoking the refusal must not itself mutate the attestation (verify() is read-only).
    assert speculative.record_path(repo, downstream.session_id).read_bytes() == before


def test_accepts_after_reverify_names_the_final_merge_base(repo: Path, tmp_path: Path, mounts: Path):
    upstream = _upstream_lane(repo, tmp_path, mounts)
    downstream = _downstream_lane(repo, tmp_path, upstream.branch, mounts)
    speculative.claim(repo, downstream, upstream.branch, base="master")

    git(repo, "merge", "--squash", upstream.branch)
    git(repo, "commit", "-q", "-m", "erp integration (squash)", "-m", "Refs kushin77/agent-orchestrator#645")
    git(downstream.worktree, "merge", "-q", "master", "-m", "merge landed master")
    assert speculative.verify(repo, downstream, base="master") != []

    attestation = speculative.reverify(repo, downstream, base="master")
    assert attestation.merge_base == git(repo, "rev-parse", "master").stdout.strip()
    assert attestation.speculative_base == upstream.branch  # unchanged: historical fact

    assert speculative.verify(repo, downstream, base="master") == []


def test_reverify_refuses_without_a_prior_claim(repo: Path, tmp_path: Path, mounts: Path):
    downstream = _downstream_lane(repo, tmp_path, "master", mounts)
    try:
        speculative.reverify(repo, downstream, base="master")
    except speculative.SpeculationRefused:
        pass
    else:
        raise AssertionError("reverify() must refuse a lane with no speculative-base claim")


def test_unresolvable_speculative_base_is_unmeasurable_not_a_pass(repo: Path, tmp_path: Path, mounts: Path):
    # A speculative base whose branch does NOT encode an issue (e.g. a frozen
    # contract artifact rather than a lane branch) has no trailer to search
    # for, so the landed check falls back to raw ancestry — and that fallback
    # must itself refuse to guess when the recorded sha cannot be resolved.
    git(repo, "branch", "contract/shared-schema")
    downstream = _downstream_lane(repo, tmp_path, "master", mounts)
    speculative.claim(repo, downstream, "contract/shared-schema", base="master")

    import json

    path = speculative.record_path(repo, downstream.session_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["speculative_base_sha"] = "0" * 40
    path.write_text(json.dumps(payload), encoding="utf-8")

    problems = speculative.verify(repo, downstream, base="master")
    assert "speculative-base-unmeasurable" in codes(problems)


def test_landed_check_is_not_a_bare_substring_of_the_issue_number(repo: Path, tmp_path: Path, mounts: Path):
    """A squash-landing of a NEIGHBOURING issue must not satisfy this lane's claim.

    `#645` is a substring of `#6450`: a naive `--grep=#645` (or any bare
    `#<issue>` search) is satisfied by a commit that actually landed issue
    6450, which is exactly the false-green the positional trailer rule (#287)
    exists to rule out elsewhere in this module. The landed check here must
    match the full canonical trailer string with a digit boundary, not a
    fragment of it.
    """
    upstream = _upstream_lane(repo, tmp_path, mounts)
    downstream = _downstream_lane(repo, tmp_path, upstream.branch, mounts)
    speculative.claim(repo, downstream, upstream.branch, base="master")

    # Land an UNRELATED issue (6450) whose number collides as a substring of 645.
    git(repo, "checkout", "-q", "master")
    (repo / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
    git(repo, "add", "unrelated.txt")
    git(repo, "commit", "-q", "-m", "unrelated work", "-m", "Refs kushin77/agent-orchestrator#6450")

    problems = speculative.verify(repo, downstream, base="master")
    assert "speculative-base-not-landed" in codes(problems)


def test_landed_check_is_not_satisfied_by_a_bare_prose_mention(repo: Path, tmp_path: Path, mounts: Path):
    """A commit that merely MENTIONS the issue number (no `Refs <slug>` prefix)
    must not be read as evidence that the speculative base landed."""
    upstream = _upstream_lane(repo, tmp_path, mounts)
    downstream = _downstream_lane(repo, tmp_path, upstream.branch, mounts)
    speculative.claim(repo, downstream, upstream.branch, base="master")

    git(repo, "checkout", "-q", "master")
    (repo / "note.txt").write_text("note\n", encoding="utf-8")
    git(repo, "add", "note.txt")
    git(repo, "commit", "-q", "-m", "still blocked on #645", "-m", "no trailer here, just a mention")

    problems = speculative.verify(repo, downstream, base="master")
    assert "speculative-base-not-landed" in codes(problems)


def test_audit_lane_surfaces_the_speculative_finding(repo: Path, tmp_path: Path, mounts: Path):
    """The lane audit (governance/isolation/audit.py) reports this by name too,
    so `cli.py audit` — the surface the gate script drives — refuses it."""
    from governance.isolation.audit import audit_lane

    upstream = _upstream_lane(repo, tmp_path, mounts)
    downstream = _downstream_lane(repo, tmp_path, upstream.branch, mounts)
    speculative.claim(repo, downstream, upstream.branch, base="master")

    assert "speculative-base-not-landed" in codes(audit_lane(downstream, repo))
