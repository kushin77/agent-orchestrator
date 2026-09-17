"""Speculative-base attestation — the ISOLATION half of DG-3 (issue #699).

A lane blocked only by file ownership (not by an unresolved question) no longer
has to wait for the upstream lane's squash-merge before it can start: it may cut
its worktree from the **upstream lane's branch** instead of ``master`` and work
now. That is speculative execution, and it creates exactly one new risk this
module exists to close: a PR that still names the branch it was cut from as its
merge base, after the real merge base has moved on. A stale base is not merely
untidy — it means the PR's diff was never actually checked against what master
looks like *after* the upstream lane landed.

Two records, reusing the shape ``guardrails/honesty/attestation.py`` already
uses for a verify attestation (``git_sha``, evidence-first, never invented from
whole cloth):

* the **speculative base** — the upstream branch (and the commit it resolved to)
  the lane was cut from. Recorded once, at ``claim`` time, and never rewritten:
  it is the historical fact of where the lane started.
* the **final merge base** — ``git merge-base <base> <branch>``, recorded fresh
  every time the lane re-verifies. This is the number the re-verify gate
  actually checks: it must equal what git computes *right now*, not what it
  computed when the lane was cut.

The gate this module backs (see ``scripts/check-session-isolation.sh``) refuses
a lane by name when either is stale:

* ``speculative-base-not-landed`` — the upstream branch the lane claims as its
  speculative base has not (yet) reached ``master``. The lane jumped ahead of
  its own claim.
* ``speculative-base-stale-merge-base`` — the attestation's ``merge_base`` no
  longer equals ``git merge-base <base> <branch>``: master moved (or the
  upstream landed) since the lane last re-verified, and the lane never re-ran
  its verification against the new base.
* ``speculative-base-unmeasurable`` — the speculative base commit cannot be
  resolved at all (e.g. history was rewritten). Unproven, never a pass (GR-12).

A lane with no speculative-base record is not making a speculative claim at
all, so :func:`verify` reports nothing for it — this module only ever
constrains a lane that opted in.

Dispatch-side API (for the follow-up `claim --base <upstream-branch>` lane,
issue #699 DG-3 dispatch half, in ``governance/dispatch/**`` — NOT touched
here):

* ``claim(main, identity, upstream_branch, base="master")`` — call once, when
  the lane is cut from the upstream branch instead of ``master``. This is what
  distinguishes "speculative" (an attested, re-verified claim) from
  "out-of-order" (no claim at all, still refused by every other ownership gate).
* ``reverify(main, identity, base="master")`` — call once, right before the
  lane opens its PR, after the upstream has (or claims to have) landed. Refuses
  to shrink the checked claim: it rewrites ``merge_base``/``git_sha`` fresh from
  git, never from caller-supplied values.
* ``verify(main, identity, base="master")`` — what the gate calls; never
  called by a lane against itself to "pre-clear" a stale claim, since it only
  reads what ``claim``/``reverify`` already wrote.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .identity import SessionIdentity, branch_issue, commit_trailer
from .violation import Violation
from .worktree import git

#: Where speculative-base attestations live, alongside the lane records
#: themselves (``.fleet/lanes``) but in their own namespace so a plain
#: ``list_records`` glob over lane JSON is untouched by this module.
RECORD_DIR = ".fleet/lanes/speculative"

DEFAULT_BASE = "master"


class SpeculationRefused(ValueError):
    """The claim or re-verify inputs cannot produce a safe attestation."""


@dataclass(frozen=True)
class SpeculativeAttestation:
    """One lane's speculative-base claim, and its most recently proven merge base."""

    session_id: str
    branch: str
    speculative_base: str
    speculative_base_sha: str
    merge_base: str
    git_sha: str

    def to_json(self) -> dict:
        return {
            "session_id": self.session_id,
            "branch": self.branch,
            "speculative_base": self.speculative_base,
            "speculative_base_sha": self.speculative_base_sha,
            "merge_base": self.merge_base,
            "git_sha": self.git_sha,
        }

    @classmethod
    def from_json(cls, payload: dict) -> "SpeculativeAttestation":
        return cls(
            session_id=str(payload["session_id"]),
            branch=str(payload["branch"]),
            speculative_base=str(payload["speculative_base"]),
            speculative_base_sha=str(payload["speculative_base_sha"]),
            merge_base=str(payload["merge_base"]),
            git_sha=str(payload["git_sha"]),
        )


def record_dir(main: Path | str) -> Path:
    return Path(main) / RECORD_DIR


def record_path(main: Path | str, session_id: str) -> Path:
    return record_dir(main) / f"{session_id}.json"


def _write(main: Path | str, attestation: SpeculativeAttestation) -> Path:
    directory = record_dir(main)
    directory.mkdir(parents=True, exist_ok=True)
    path = record_path(main, attestation.session_id)
    path.write_text(json.dumps(attestation.to_json(), indent=2) + "\n", encoding="utf-8")
    return path


def read(main: Path | str, session_id: str) -> SpeculativeAttestation | None:
    path = record_path(main, session_id)
    if not path.exists():
        return None
    return SpeculativeAttestation.from_json(json.loads(path.read_text(encoding="utf-8")))


def forget(main: Path | str, session_id: str) -> None:
    record_path(main, session_id).unlink(missing_ok=True)


def _rev_parse(main: Path | str, rev: str) -> str:
    result = git(main, "rev-parse", rev)
    if result.returncode != 0:
        raise SpeculationRefused(f"cannot resolve {rev!r}: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def _merge_base(main: Path | str, base: str, branch: str) -> str:
    result = git(main, "merge-base", base, branch)
    if result.returncode != 0:
        raise SpeculationRefused(
            f"cannot compute merge-base({base!r}, {branch!r}): {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def claim(
    main: Path | str,
    identity: SessionIdentity,
    upstream_branch: str,
    base: str = DEFAULT_BASE,
) -> SpeculativeAttestation:
    """Record that ``identity``'s lane was (or is) cut from ``upstream_branch``.

    Called once, at claim time — this is the dispatch-side ``claim --base
    <upstream-branch>`` distinguishing a *speculative* lane from an
    *out-of-order* one. ``speculative_base_sha`` is resolved now, so a later
    re-verify can prove whether that exact commit has since landed on ``base``
    rather than merely whether *some* commit named ``upstream_branch`` has.
    """
    speculative_base_sha = _rev_parse(main, upstream_branch)
    merge_base = _merge_base(main, base, identity.branch)
    git_sha = _rev_parse(main, identity.branch)
    attestation = SpeculativeAttestation(
        session_id=identity.session_id,
        branch=identity.branch,
        speculative_base=upstream_branch,
        speculative_base_sha=speculative_base_sha,
        merge_base=merge_base,
        git_sha=git_sha,
    )
    _write(main, attestation)
    return attestation


def reverify(
    main: Path | str,
    identity: SessionIdentity,
    base: str = DEFAULT_BASE,
) -> SpeculativeAttestation:
    """Recompute ``merge_base``/``git_sha`` from git, right now, and re-record them.

    This is the mandatory step before a speculative lane opens its PR (#699
    acceptance criterion 1): it never accepts a caller-supplied merge base —
    only git's own answer, for exactly the reason the gate exists to check.
    Refuses when the lane never claimed a speculative base in the first place,
    since there is nothing to re-verify.
    """
    existing = read(main, identity.session_id)
    if existing is None:
        raise SpeculationRefused(
            f"lane {identity.session_id} has no speculative-base claim to re-verify; call claim() first"
        )
    merge_base = _merge_base(main, base, identity.branch)
    git_sha = _rev_parse(main, identity.branch)
    attestation = SpeculativeAttestation(
        session_id=existing.session_id,
        branch=identity.branch,
        speculative_base=existing.speculative_base,
        speculative_base_sha=existing.speculative_base_sha,
        merge_base=merge_base,
        git_sha=git_sha,
    )
    _write(main, attestation)
    return attestation


def _landed_by_trailer(
    main: Path | str,
    speculative_base: str,
    base: str,
    repo_slug: str,
) -> bool | None:
    """Squash-safe landed check: did ``base`` acquire the upstream issue's commit?

    ``git merge --squash`` (the landing path this repository uses,
    ``governance/isolation/landed.py``) drops the original commits' parent
    link, so ``git merge-base --is-ancestor <sha> <base>`` is **always** false
    after a squash-landing even though the work plainly landed — checking raw
    ancestry would refuse every speculative lane the instant its upstream
    landed the normal way.

    This is deliberately NOT the positional trailer predicate
    (``governance/isolation/trailer.py`` -> ``scripts/check-pr-contract.sh``,
    issue #288): that rule classifies one commit's trailer block, and asking
    it "does ANY commit in this whole range mention the ticket" is not the
    question it answers. What is checked here is narrower and cheaper: does
    ``base``'s log contain the exact string ``commit_trailer(issue, slug)``
    (``Refs <slug>#<issue>``) with a non-digit (or nothing) immediately after
    it — so a landing for issue 6450 cannot satisfy a claim on issue 645, and
    a bare mention like "blocked on #645" (no ``Refs <slug>`` prefix) cannot
    either. It is still a substring search, not a trailer-block parse, and is
    named as such rather than claimed to be the shared predicate.

    Returns ``None`` when the branch does not encode an issue at all (nothing
    to search the log for), so the caller falls back to raw ancestry.
    """
    issue = branch_issue(speculative_base)
    if issue is None:
        return None
    needle = commit_trailer(issue, repo_slug)
    pattern = re.compile(re.escape(needle) + r"(?!\d)")
    log = git(main, "log", base, "--format=%x1e%B")
    if log.returncode != 0:
        return None
    for message in log.stdout.split("\x1e"):
        if pattern.search(message):
            return True
    return False


def verify(
    main: Path | str,
    identity: SessionIdentity,
    base: str = DEFAULT_BASE,
) -> list[Violation]:
    """The re-verify gate: refuse a lane whose speculative-base claim is stale.

    A lane with no claim on record made none, so it is out of scope here —
    every *other* ownership/dispatch gate still applies to it unchanged. A lane
    WITH a claim must show that (a) the branch it claims to be built on has
    actually landed on ``base``, and (b) the merge base it attested is the one
    git computes right now — not a snapshot from before ``base`` moved.
    """
    attestation = read(main, identity.session_id)
    if attestation is None:
        return []

    by_trailer = _landed_by_trailer(main, attestation.speculative_base, base, identity.repo_slug)
    if by_trailer is not None:
        landed_ok = by_trailer
    else:
        ancestor = git(main, "merge-base", "--is-ancestor", attestation.speculative_base_sha, base)
        if ancestor.returncode not in (0, 1):
            return [
                Violation(
                    "speculative-base-unmeasurable",
                    f"cannot determine whether speculative base {attestation.speculative_base!r} "
                    f"({attestation.speculative_base_sha[:8]}) has landed on {base!r}: "
                    f"{ancestor.stderr.strip() or ancestor.stdout.strip()}",
                )
            ]
        landed_ok = ancestor.returncode == 0

    if not landed_ok:
        return [
            Violation(
                "speculative-base-not-landed",
                f"speculative base {attestation.speculative_base!r} "
                f"({attestation.speculative_base_sha[:8]}) has not landed on {base!r}; "
                f"the attestation was cut ahead of its own claim",
            )
        ]

    try:
        actual_merge_base = _merge_base(main, base, identity.branch)
    except SpeculationRefused as exc:
        return [Violation("speculative-base-unmeasurable", str(exc))]

    if actual_merge_base != attestation.merge_base:
        return [
            Violation(
                "speculative-base-stale-merge-base",
                f"attestation names merge_base={attestation.merge_base[:8]}, but "
                f"git merge-base {base} {identity.branch} is now {actual_merge_base[:8]}; "
                "re-verify (governance/isolation/speculative.py:reverify) before opening the PR",
            )
        ]
    return []
