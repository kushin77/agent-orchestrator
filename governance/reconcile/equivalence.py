"""Content-equivalence stranding predicate (#740).

``git merge-base --is-ancestor <lane HEAD> <base>`` answers "is this lane's
commit reachable from the base branch". That is the wrong question once a
landing path squash-merges or replays commits: a fully-landed lane's original
commit is never reachable from the base afterwards, even though every byte of
its work is there. Measured counter-example (#740): ``origin/issue-650-erp-crm``
(``b850830``) reads NOT-merged by ancestry, while the tree hash of
``integrations/erp/crm`` at that commit is identical to the tree hash of the
same path at the landed commit ``cf60963`` on ``master``.

This module answers the question ancestry cannot: is the lane's *content*
genuinely absent from the base, regardless of how it got there (or didn't)?
A lane is stranded only when at least one file it touched, relative to its own
merge-base with the target, differs in content from that same path on the
target ref (or is altogether missing there). A lane that only rebases forward
through unrelated file churn (``Makefile``, ``scripts/verify.sh``, ...) with
zero files added, and whose touched paths are byte-identical on the target,
is landed-equivalent — not stranded.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class EquivalenceUnavailable(Exception):
    """The repository state needed to judge equivalence could not be read."""


@dataclass(frozen=True)
class EquivalenceReport:
    """The verdict for one lane against one target ref."""

    lane_ref: str
    target_ref: str
    merge_base: str
    stranded: bool
    touched_paths: tuple[str, ...] = field(default_factory=tuple)
    differing_paths: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""

    def to_json(self) -> dict:
        return {
            "lane_ref": self.lane_ref,
            "target_ref": self.target_ref,
            "merge_base": self.merge_base,
            "stranded": self.stranded,
            "touched_paths": list(self.touched_paths),
            "differing_paths": list(self.differing_paths),
            "reason": self.reason,
        }


def _run(repo_root: Path | str, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise EquivalenceUnavailable(
            f"git {' '.join(args)} failed (rc={result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def _is_ancestor(repo_root: Path | str, lane_ref: str, target_ref: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "merge-base", "--is-ancestor", lane_ref, target_ref],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _blob_at(repo_root: Path | str, ref: str, path: str) -> str | None:
    """The blob object id for ``path`` at ``ref``, or ``None`` if it does not
    exist there (deleted / never existed)."""
    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "-q", "--verify", f"{ref}:{path}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def content_equivalent(
    repo_root: Path | str,
    lane_ref: str,
    target_ref: str = "origin/master",
) -> EquivalenceReport:
    """Is ``lane_ref``'s work genuinely present on ``target_ref``?

    Not stranded (``stranded=False``) when either:

    * ancestry already holds (the trivial, cheapest case), or
    * every path the lane touched relative to its own merge-base with the
      target carries byte-identical content on the target ref (squash-landed
      / replayed — the tree-hash-equivalence case from #740).

    Stranded when at least one touched path differs (including "added on the
    lane, absent on the target" and "the lane deleted it, the target still has
    a different version") — the lane holds a real, unlanded change by name.
    """
    if _is_ancestor(repo_root, lane_ref, target_ref):
        return EquivalenceReport(
            lane_ref=lane_ref,
            target_ref=target_ref,
            merge_base=_run(repo_root, "merge-base", lane_ref, target_ref).strip(),
            stranded=False,
            reason="ancestry holds: the lane's commit is reachable from the target",
        )

    merge_base = _run(repo_root, "merge-base", lane_ref, target_ref).strip()
    diff_output = _run(repo_root, "diff", "--name-only", f"{merge_base}..{lane_ref}")
    touched = tuple(sorted(p for p in diff_output.splitlines() if p.strip()))

    if not touched:
        # No content diverges from the merge-base at all: nothing to strand.
        return EquivalenceReport(
            lane_ref=lane_ref,
            target_ref=target_ref,
            merge_base=merge_base,
            stranded=False,
            touched_paths=touched,
            reason="the lane touches no path relative to its merge-base",
        )

    differing: list[str] = []
    for path in touched:
        lane_blob = _blob_at(repo_root, lane_ref, path)
        target_blob = _blob_at(repo_root, target_ref, path)
        if lane_blob != target_blob:
            differing.append(path)

    if differing:
        return EquivalenceReport(
            lane_ref=lane_ref,
            target_ref=target_ref,
            merge_base=merge_base,
            stranded=True,
            touched_paths=touched,
            differing_paths=tuple(differing),
            reason=(
                f"{len(differing)} of {len(touched)} touched path(s) differ from "
                f"{target_ref}: genuinely unlanded content"
            ),
        )

    return EquivalenceReport(
        lane_ref=lane_ref,
        target_ref=target_ref,
        merge_base=merge_base,
        stranded=False,
        touched_paths=touched,
        reason=(
            f"all {len(touched)} touched path(s) are byte-identical on {target_ref}: "
            "landed-equivalent (squash-merged or replayed), not stranded"
        ),
    )
