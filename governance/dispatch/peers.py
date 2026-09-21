"""The A2A peer-check standard — enumerate siblings, refuse on overlap, enhance
never clobber (issue #1549, EPIC #1510).

THE STANDARD, IN FOUR STEPS. A lane runs this **before it claims work** — at
session start, before every `governance/dispatch/cli.py claim`, and on the fleet
loop's cadence. It is a ritual with a mechanical core, not advice:

1. **Enumerate.** Read the live claim ledger (``.board/claims/``; the same store
   ``governance/dispatch/claims.active_claims`` replays) and list every sibling
   holding a live claim: its agent, its issue, its lane, when it last acted, and
   the channel it is reachable on. The caller's own claim is never a sibling.
2. **Detect.** For each sibling, compute the files it owns — the per-file leases
   on its claim record (``FileClaim``), the ``Files:`` line of its issue as the
   board snapshot parsed it, and the paths its branch touches against the merge
   base — and intersect them with the caller's own claimed files. The intersect
   is ``model.file_claims_conflict`` — the ONE predicate this repository already
   uses for file leases (#702) and for wave planning (#740). It is imported, never
   re-implemented: a second copy could drift, and a drifted predicate is a gate
   that cannot fail.
3. **Refuse, by name.** Any sibling that intersects the caller's files is an
   ``OVERLAP``. The check exits 1 and names the sibling (agent, issue, lane), the
   channel, and the specific overlapping paths. The caller does **not** start work
   on those files: it files a child issue ``Parent: #<epic>`` documenting the
   conflict, or it waits. Refusing is a verdict, not a warning.
4. **Enhance, never clobber.** For a sibling it can genuinely help, the check
   emits ONE concrete enhancement — a specific fact the sibling does not have (a
   shared file, a colliding ``Files:`` declaration, a sister issue). A bare
   status ping is noise and is not an enhancement: :func:`enhancement` returns
   ``None`` rather than prose with no new information.

WHAT IT DELIBERATELY DOES NOT DO. It does not build a coordination transport.
The ledger, the snapshot, the issue bodies and ``git`` all already exist; this
module only reads them and produces a verdict. It does not write the ledger: the
check is a read, and the caller is the one who acts on it.

HONEST ATTRIBUTION. The channel column is evidence-labelled and may be
``unknown``. Cross-vendor attribution from git/gh metadata alone is measured to
be unreliable here: the live ledger (1327 records, 2026-09-20) carries ids such
as ``ao-sub-1521`` and ``subagent-brain-di`` that name no vendor, while
``copilot-*`` and the ``kushin77`` owner login are decidable and are classified.
A row that cannot be attributed says so and names what was checked; it is never
guessed, and the overlap verdict — the load-bearing half — does not depend on it.

Exit contract (``guardrails/honesty`` tri-state, consumed never redefined):
``0`` disjoint (proceed) / ``1`` OVERLAP (refused, by name) /
``2`` CANNOT-ASSESS (no ledger, no caller identity, or an unreadable store).

Usage::

    python3 governance/dispatch/peers.py --help
    python3 governance/dispatch/peers.py --standard
    bash scripts/peer-check.sh --agent ao-sub-1549 --issue 1549

---knowledge---
module_id: governance.dispatch.peers
system: governance
app: dispatch
solution_class: enterprise
patterns: [fail-closed, declared-authority]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [branch_candidates, classify_channel, Sibling, PeerReport, overlap_paths, Collision, sibling_collisions, peer_check]
invariants: "any sibling that intersects the caller's claimed files is refused by name"
gotchas: "imports model.file_claims_conflict rather than re-implementing the intersect"
related: ["#1549"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

_PKG_DIR = Path(__file__).resolve().parent
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

import claims as claims_mod  # noqa: E402
import snapshot as snapshot_mod  # noqa: E402
from model import ClaimEvent, FileClaim, file_claims_conflict  # noqa: E402

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2

#: The domain every minted fleet identity signs with. An author email outside it
#: is evidence of a human, which is what makes the ``human`` classification
#: decidable rather than a guess.
MINTED_DOMAIN = ".invalid"


def branch_candidates(issue: int) -> tuple[str, ...]:
    """Candidate ref names for a sibling's branch, in resolution order.

    Rule 15 of ``AGENTS.md`` makes ``issue-<n>`` canonical; the suffixed form is
    what this box actually carries for some lanes (measured 2026-09-20).
    """
    return (f"issue-{issue}", f"refs/heads/issue-{issue}", f"issue-{issue}-*")


def _fmt_regions(regions: Sequence[tuple[int, int]] | None) -> str:
    if regions is None:
        return "whole file"
    return " ".join("%d-%d" % (start, end) for start, end in regions)


def classify_channel(
    agent: str,
    *,
    author: str = "",
    bus_members: Iterable[str] = (),
    owner: str = "kushin77",
) -> tuple[str, str]:
    """Classify one agent id into a channel, with the evidence that decided it.

    Returns ``(channel, evidence)``. The order of the checks IS the precedence:
    the bus is authoritative because it is a live answer, the owner login is
    next, a runtime prefix follows, and a non-minted author email is the only
    remaining positive signal. Everything else is ``unknown`` and says which
    three signals were checked and found nothing — the honest answer, and the
    one the escalation clause of issue #1549 is about.
    """
    handle = (agent or "").strip()
    lowered = handle.lower()
    members = {str(m).strip().lower() for m in bus_members if str(m).strip()}
    if lowered and lowered in members:
        return "claude-bus", f"'{handle}' is live on the Claude bus (ListAgents)"
    if lowered and lowered == (owner or "").strip().lower():
        return "human", f"'{handle}' is the repository owner login"
    if lowered.startswith("copilot") or lowered.startswith("agent-copilot"):
        return "copilot", f"'{handle}' carries the Copilot runtime prefix"
    if lowered.startswith("deepseek") or lowered.startswith("agent-deepseek"):
        return "deepseek", f"'{handle}' carries the DeepSeek runtime prefix"
    if lowered.startswith("hermes") or lowered.startswith("agent-hermes"):
        return "hermes", f"'{handle}' carries the Hermes runtime prefix"
    email = (author or "").strip().lower()
    if "@" in email and not email.endswith(MINTED_DOMAIN):
        return "human", f"author '{author}' signs outside the minted fleet domain"
    return "unknown", (
        f"'{handle or '<empty>'}' carries no runtime prefix, is not the owner login, "
        "and its author email is either absent or a minted fleet identity"
    )


@dataclass(frozen=True)
class Sibling:
    """One live sibling lane, and what this caller must know about it."""

    agent: str
    issue: int | None
    lane: str
    channel: str
    channel_evidence: str
    at: str
    files: tuple[FileClaim, ...]
    file_evidence: tuple[str, ...]
    overlap: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def verdict(self) -> str:
        if self.overlap:
            return "OVERLAP"
        if not self.files:
            # Never silently `disjoint`: a sibling with no file evidence at all
            # is unverifiable, exactly as a wave candidate without a `Files:`
            # declaration is in `scripts/check-lane-collision.sh` (#740).
            return "unverifiable"
        return "disjoint"

    @property
    def identity(self) -> str:
        parts = [self.agent or "<unknown-agent>"]
        if self.issue is not None:
            parts.append(f"#{self.issue}")
        if self.lane:
            parts.append(f"lane {self.lane}")
        return ", ".join(parts)

    def to_json(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "issue": self.issue,
            "lane": self.lane,
            "channel": self.channel,
            "channel_evidence": self.channel_evidence,
            "at": self.at,
            "verdict": self.verdict,
            "overlap": list(self.overlap),
            "files": [_file_to_json(f) for f in self.files],
            "file_evidence": list(self.file_evidence),
            "notes": list(self.notes),
        }


def _file_to_json(claim: FileClaim) -> dict[str, Any]:
    regions = None if claim.regions is None else [list(r) for r in claim.regions]
    return {"path": claim.path, "regions": regions}


@dataclass(frozen=True)
class PeerReport:
    """The whole peer-check: the caller's files, the siblings, and the verdict."""

    caller_agent: str
    caller_issue: int | None
    caller_files: tuple[FileClaim, ...]
    siblings: tuple[Sibling, ...]
    notes: tuple[str, ...] = ()

    @property
    def overlapping(self) -> tuple[Sibling, ...]:
        return tuple(s for s in self.siblings if s.overlap)

    @property
    def unverifiable(self) -> tuple[Sibling, ...]:
        return tuple(s for s in self.siblings if not s.files)

    @property
    def collisions(self) -> tuple[Collision, ...]:
        """Sibling-vs-sibling file collisions among the live lanes."""
        return sibling_collisions(self.siblings)

    @property
    def verdict(self) -> str:
        return "OVERLAP" if self.overlapping else "disjoint"

    def refusal(self) -> str | None:
        """The refuse-on-overlap verdict, naming the sibling and the paths.

        ``None`` when disjoint. When overlapping, the FIRST conflicting sibling
        leads (so the string is stable under an unordered ledger replay) and any
        others are named after it. Every path is the specific overlapping one,
        never the whole file set — a refusal that does not name the path cannot
        be acted on.
        """
        if not self.overlapping:
            return None
        lead = self.overlapping[0]
        caller = self.caller_agent or "<unknown-agent>"
        if self.caller_issue is not None:
            caller = f"{caller} on #{self.caller_issue}"
        more = ""
        if len(self.overlapping) > 1:
            rest = ", ".join(s.identity for s in self.overlapping[1:])
            more = f"; {len(self.overlapping) - 1} further sibling(s) also overlap: {rest}"
        return (
            f"peer-check REFUSED: OVERLAP — sibling {lead.identity} "
            f"(channel {lead.channel}, {lead.channel_evidence}) already holds "
            f"{', '.join(lead.overlap)}, which {caller} also claims{more}. "
            "Do not start work on those files: file a child issue naming the "
            "conflict or wait for the sibling to land."
        )

    def enhancement(self) -> str | None:
        """ONE specific fact a sibling does not have, or ``None``.

        Never a bare status ping: the sentence must carry a fact the sibling
        cannot read off its own issue. Three shapes qualify, in this order — two
        live sibling lanes already holding the same file (neither of them can see
        the other), a file this caller and the sibling both name, and a sibling
        whose file set cannot be verified at all. When none of them holds, this
        returns ``None`` and the check posts nothing, which is the point.
        """
        collisions = self.collisions
        if collisions:
            lead = collisions[0]
            others = ""
            if len(collisions) > 1:
                others = (
                    f" ({len(collisions) - 1} further live pair(s) collide the same way)"
                )
            return (
                f"enhance {lead.first.identity}: {lead.second.identity} is live right now "
                f"and also holds {', '.join(lead.paths)} — whichever of you merges second "
                f"rebases that file, and neither of you can see the other from your own "
                f"issue{others}."
            )
        if self.overlapping:
            lead = self.overlapping[0]
            return (
                f"enhance {lead.identity}: your live claim and "
                f"{self.caller_agent} on #{self.caller_issue} both name "
                f"{', '.join(lead.overlap)} — one of the two lanes should re-scope "
                "that file before either merges."
            )
        for sibling in self.siblings:
            if sibling.files or sibling.issue is None:
                continue
            return (
                f"enhance {sibling.identity}: its live claim declares no files and no "
                "branch of it resolves, so every other lane has to treat it as possibly "
                "holding everything — declaring `Files:` on the issue would let siblings "
                "prove disjointness mechanically."
            )
        return None

    def table(self) -> str:
        """The human-readable output: one row per sibling plus the verdict."""
        lines: list[str] = []
        caller_bits = [self.caller_agent or "<unknown-agent>"]
        if self.caller_issue is not None:
            caller_bits.append(f"#{self.caller_issue}")
        caller_bits.append(
            "files: "
            + (", ".join(f.path for f in self.caller_files) if self.caller_files else "<none declared>")
        )
        lines.append("peer-check — caller " + " ".join(caller_bits))
        header = f"{'VERDICT':<13} {'SIBLING':<34} {'CHANNEL':<10} {'LAST ACTIVE':<21} FILES"
        lines.append(header)
        lines.append("-" * len(header))
        if not self.siblings:
            lines.append("(no sibling holds a live claim)")
        for sibling in self.siblings:
            if sibling.overlap:
                files_cell = "; ".join(sibling.overlap)
            elif sibling.files:
                files_cell = "; ".join(f.path for f in sibling.files)
            else:
                files_cell = "<no file evidence>"
            lines.append(
                f"{sibling.verdict:<13} {sibling.identity:<34} {sibling.channel:<10} "
                f"{sibling.at or '<unknown>':<21} {files_cell}"
            )
        for note in self.notes:
            lines.append(f"note: {note}")
        collisions = self.collisions
        if collisions:
            lines.append("")
            lines.append(
                f"live sibling-to-sibling collisions: {len(collisions)} pair(s) "
                "hold the same file right now"
            )
            for collision in collisions:
                lines.append(
                    f"  {collision.first.identity} <-> {collision.second.identity}: "
                    f"{'; '.join(collision.paths)}"
                )
        refusal = self.refusal()
        if refusal:
            lines.append("")
            lines.append(refusal)
        else:
            lines.append("")
            unverifiable = self.unverifiable
            suffix = (
                f" — {len(unverifiable)} sibling(s) carry no file evidence and are "
                "unverifiable, never assumed disjoint"
                if unverifiable
                else ""
            )
            lines.append(f"peer-check: disjoint — proceed{suffix}")
        enhancement = self.enhancement()
        if enhancement:
            lines.append(f"peer-check: {enhancement}")
        return "\n".join(lines)

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": "ao.peer-check/1",
            "caller_agent": self.caller_agent,
            "caller_issue": self.caller_issue,
            "caller_files": [_file_to_json(f) for f in self.caller_files],
            "verdict": self.verdict,
            "refusal": self.refusal(),
            "enhancement": self.enhancement(),
            "siblings": [s.to_json() for s in self.siblings],
            "collisions": [c.to_json() for c in self.collisions],
            "notes": list(self.notes),
        }


def overlap_paths(mine: Sequence[FileClaim], theirs: Sequence[FileClaim]) -> tuple[str, ...]:
    """The specific paths on which two file sets conflict, in discovery order.

    Delegates the decision to ``model.file_claims_conflict`` so a whole-file
    lease and a disjoint-region pair are judged by the one predicate the claim
    path (#702) and the wave planner (#740) already use.
    """
    found: list[str] = []
    for one in mine:
        for other in theirs:
            if not file_claims_conflict(one, other):
                continue
            label = one.path
            if one.regions is not None or other.regions is not None:
                label = (
                    f"{one.path} (caller {_fmt_regions(one.regions)} vs "
                    f"sibling {_fmt_regions(other.regions)})"
                )
            if label not in found:
                found.append(label)
    return tuple(found)


@dataclass(frozen=True)
class Collision:
    """Two sibling lanes holding the same file — a fact neither can see alone.

    This is the enhancement the standard is for. Measured live 2026-09-20: two
    lanes held ``docs/README.md`` at once and the first time either learned of it
    would have been a rebase conflict at merge time.
    """

    first: Sibling
    second: Sibling
    paths: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "first": self.first.identity,
            "second": self.second.identity,
            "paths": list(self.paths),
        }


def sibling_collisions(siblings: Sequence[Sibling]) -> tuple[Collision, ...]:
    """Every pair of siblings whose file sets conflict, in stable order.

    The caller is not part of this: it is already judged against every sibling by
    the overlap verdict. A pair where either side carries no file evidence is
    skipped — absence of evidence is not a collision, and reporting it as one
    would be a finding the tool cannot support.
    """
    found: list[Collision] = []
    for i, first in enumerate(siblings):
        for second in siblings[i + 1 :]:
            if not first.files or not second.files:
                continue
            paths = overlap_paths(first.files, second.files)
            if paths:
                found.append(Collision(first=first, second=second, paths=paths))
    return tuple(found)


def _declared_paths(paths: Iterable[str]) -> tuple[FileClaim, ...]:
    """Turn bare path strings into whole-file ``FileClaim`` records."""
    seen: list[FileClaim] = []
    for raw in paths:
        text = str(raw).strip()
        if text and not any(existing.path == text for existing in seen):
            seen.append(FileClaim(path=text))
    return tuple(seen)


def peer_check(
    caller_files: Sequence[FileClaim],
    live: Mapping[int, ClaimEvent],
    *,
    caller_agent: str,
    caller_issue: int | None = None,
    issue_files: Mapping[int, Sequence[str]] | None = None,
    branch_files: Mapping[int, Sequence[str]] | None = None,
    branch_refs: Mapping[int, str] | None = None,
    bus_members: Iterable[str] = (),
    owner: str = "kushin77",
) -> PeerReport:
    """Enumerate every live sibling and judge it against the caller's files.

    ``live`` is the replayed live claim set (``claims.active_claims``): an issue
    whose lease has expired is not a sibling, so a dead lane never blocks a new
    one — the same rule ``claims.find_file_conflict`` already applies.

    ``issue_files`` (issue -> declared ``Files:`` paths), ``branch_files``
    (issue -> paths its branch touches) and ``branch_refs`` (issue -> the ref
    resolved) are supplied by the caller so the judgement is a pure function of
    its inputs and is testable without a repository.
    """
    issue_files = issue_files or {}
    branch_files = branch_files or {}
    branch_refs = branch_refs or {}
    siblings: list[Sibling] = []
    notes: list[str] = []

    for number in sorted(live):
        holder = live[number]
        if number == caller_issue and holder.agent == caller_agent:
            continue  # the caller's own claim is not a sibling
        collected: list[FileClaim] = list(holder.files)
        evidence: list[str] = []
        if holder.files:
            evidence.append(f"{len(holder.files)} declared on the live claim record")
        declared = _declared_paths(issue_files.get(number, ()))
        if declared:
            collected.extend(declared)
            evidence.append(f"{len(declared)} declared by the issue's `Files:` line")
        touched = _declared_paths(branch_files.get(number, ()))
        if touched:
            collected.extend(touched)
            evidence.append(f"{len(touched)} touched by {branch_refs.get(number, '<branch>')}")
        merged: list[FileClaim] = []
        for claim in collected:
            if not any(existing.path == claim.path for existing in merged):
                merged.append(claim)
        own_notes: list[str] = []
        if not merged:
            own_notes.append("no file evidence: claim, `Files:` line and branch all empty")
        channel, channel_evidence = classify_channel(
            holder.agent, bus_members=bus_members, owner=owner
        )
        siblings.append(
            Sibling(
                agent=holder.agent,
                issue=number,
                lane=holder.lane,
                channel=channel,
                channel_evidence=channel_evidence,
                at=holder.at,
                files=tuple(merged),
                file_evidence=tuple(evidence),
                overlap=overlap_paths(caller_files, merged),
                notes=tuple(own_notes),
            )
        )

    if not caller_files:
        notes.append(
            "the caller declares no files, so every sibling is judged against an "
            "empty set: state your lane's files (--files, or a claim carrying them)"
        )
    unattributed = [s for s in siblings if s.channel == "unknown"]
    if unattributed:
        notes.append(
            f"{len(unattributed)} of {len(siblings)} sibling(s) could not be attributed to a "
            "channel from the metadata at hand (reported as `unknown`, never guessed)"
        )
    return PeerReport(
        caller_agent=caller_agent,
        caller_issue=caller_issue,
        caller_files=tuple(caller_files),
        siblings=tuple(siblings),
        notes=tuple(notes),
    )


def resolve_ledger(root: Path | str, main_worktree: Path | str | None = None) -> tuple[Path | None, str]:
    """Find the live claim ledger, or say which locations were checked.

    A lane works in its own worktree, and ``.board/claims/`` is **untracked** —
    so a lane worktree usually does NOT carry the live ledger, while the shared
    (main) worktree does. Measured 2026-09-20: the lane worktree holds the four
    tracked ``.board`` files and no ``claims/`` directory, the main checkout
    holds 1327 claim records. Reading the lane's own ``.board`` would therefore
    report "no siblings" while a hundred lanes are live, which is the worst
    possible answer. The main worktree is therefore an explicit fallback, and
    every path checked is named.
    """
    checked: list[str] = []
    candidates: list[Path] = [Path(root) / ".board" / "claims"]
    if main_worktree:
        candidates.append(Path(main_worktree) / ".board" / "claims")
    for candidate in candidates:
        checked.append(str(candidate))
        if not candidate.is_dir():
            continue
        if not any(candidate.glob("*.json")):
            checked.append(f"{candidate} (a directory, but it holds no *.json claim record)")
            continue
        return candidate, f"live claim ledger: {candidate}"
    return None, "no live claim ledger found; checked: " + "; ".join(checked)


def git_branch_files(
    root: Path | str,
    issue: int,
    *,
    base: str = "origin/master",
    timeout: float = 20.0,
) -> tuple[str | None, tuple[str, ...], str]:
    """The paths a sibling's branch touches, resolved against the merge base.

    Returns ``(ref, paths, reason)``. ``ref`` is ``None`` when no candidate ref
    exists — an honest "unresolved", never an empty set silently read as
    disjoint. Only LOCAL refs are read: no fetch, no network.
    """
    for candidate in branch_candidates(issue):
        try:
            listed = subprocess.run(
                ["git", "-C", str(root), "for-each-ref", "--format=%(refname:short)", candidate],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return None, (), f"git could not be run: {exc}"
        refs = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
        if not refs:
            continue
        ref = sorted(refs)[0]
        try:
            diff = subprocess.run(
                ["git", "-C", str(root), "diff", "--name-only", f"{base}...{ref}"],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return None, (), f"git could not be run: {exc}"
        if diff.returncode != 0:
            return None, (), f"{ref}: {diff.stderr.strip() or 'diff failed'}"
        paths = tuple(line.strip() for line in diff.stdout.splitlines() if line.strip())
        return ref, paths, f"{ref} touches {len(paths)} path(s) against {base}"
    return None, (), f"no local branch matching {' / '.join(branch_candidates(issue))} exists here"


def _parse_files_arg(text: str) -> tuple[FileClaim, ...]:
    """Parse ``--files a.py,b.py`` (comma-separated; ``path:start-end`` regions)."""
    out: list[FileClaim] = []
    for chunk in text.split(","):
        item = chunk.strip()
        if not item:
            continue
        path, _, region = item.partition(":")
        path = path.strip()
        regions: tuple[tuple[int, int], ...] | None = None
        if region.strip():
            start_text, _, end_text = region.strip().partition("-")
            try:
                regions = ((int(start_text), int(end_text)),)
            except ValueError as exc:
                raise ValueError(f"--files: region '{region}' is not start-end: {exc}") from exc
        out.append(FileClaim(path=path, regions=regions))
    return tuple(out)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="peer-check",
        description="A2A peer-check: enumerate live sibling lanes, refuse on file overlap.",
    )
    parser.add_argument("--ledger", default="", help="claim ledger directory or .jsonl file")
    parser.add_argument("--root", default="", help="repository root for git reads (default: this repo)")
    parser.add_argument("--snapshot", default="", help="board snapshot (default: <root>/.board/snapshot.json)")
    parser.add_argument("--caller-agent", default="", help="the querying agent id (required)")
    parser.add_argument("--caller-issue", type=int, default=0, help="the querying issue number")
    parser.add_argument("--files", default="", help="comma-separated files this lane claims")
    parser.add_argument("--owner", default="kushin77", help="the owner login, classified as human")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    parser.add_argument("--no-branch-files", action="store_true", help="skip the git branch-diff file source")
    parser.add_argument("--standard", action="store_true", help="print the standard and exit")
    return parser


def _caller_files_from_live(
    live: Mapping[int, ClaimEvent], agent: str, issue: int | None
) -> tuple[FileClaim, ...]:
    """The caller's own claimed files, read from its live claim record."""
    if issue is not None and issue in live and live[issue].agent == agent:
        return tuple(live[issue].files)
    for number in sorted(live):
        if live[number].agent == agent:
            return tuple(live[number].files)
    return ()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.standard:
        print(__doc__)
        return EXIT_OK

    root = Path(args.root).resolve() if args.root else _PKG_DIR.parents[1]
    caller_issue = args.caller_issue or None

    if not args.caller_agent:
        print(
            "peer-check: CANNOT-ASSESS — no caller identity (--caller-agent); a check "
            "that cannot name its caller cannot name whose files are at risk",
            file=sys.stderr,
        )
        return EXIT_CANNOT_ASSESS

    main_worktree = None
    try:
        listing = subprocess.run(
            ["git", "-C", str(root), "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=20.0,
            check=False,
        )
        for line in listing.stdout.splitlines():
            if line.startswith("worktree "):
                main_worktree = line[len("worktree ") :].strip()
                break
    except (OSError, subprocess.SubprocessError):
        main_worktree = None

    ledger = Path(args.ledger) if args.ledger else None
    if ledger is None:
        resolved, ledger_note = resolve_ledger(root, main_worktree)
        if resolved is None:
            print(f"peer-check: CANNOT-ASSESS — {ledger_note}", file=sys.stderr)
            return EXIT_CANNOT_ASSESS
        ledger = resolved
    else:
        # A ledger that was NAMED but does not exist is CANNOT-ASSESS, never an
        # empty sibling set: `read_ledger` answers `[]` for an absent path, and
        # `[]` renders as a clean "disjoint". That collapse is the false green
        # this standard exists to prevent, so it is refused here.
        if not ledger.exists():
            print(
                f"peer-check: CANNOT-ASSESS — --ledger {ledger} does not exist; "
                "an absent ledger is not an empty one",
                file=sys.stderr,
            )
            return EXIT_CANNOT_ASSESS
        if ledger.is_dir() and not any(ledger.glob("*.json")) and not (ledger.parent / "claims.jsonl").is_file():
            print(
                f"peer-check: CANNOT-ASSESS — --ledger {ledger} holds no *.json claim "
                "record and no sibling legacy claims.jsonl; its emptiness is unreadable",
                file=sys.stderr,
            )
            return EXIT_CANNOT_ASSESS
        ledger_note = f"live claim ledger: {ledger} (named by --ledger)"

    try:
        events = claims_mod.read_ledger(ledger)
    except ValueError as exc:
        print(f"peer-check: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    live = claims_mod.active_claims(events)

    try:
        caller_files = _parse_files_arg(args.files) if args.files else ()
    except ValueError as exc:
        print(f"peer-check: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    if not caller_files:
        caller_files = _caller_files_from_live(live, args.caller_agent, caller_issue)

    snapshot_path = Path(args.snapshot) if args.snapshot else root / ".board" / "snapshot.json"
    issue_files: dict[int, Sequence[str]] = {}
    snapshot_note = ""
    if snapshot_path.is_file():
        try:
            snapshot = snapshot_mod.load(snapshot_path, apply_queue=False)
        except (ValueError, OSError) as exc:
            snapshot_note = f"board snapshot unreadable ({exc}); `Files:` declarations not read"
        else:
            for number in live:
                record = snapshot.get(number)
                if record is not None and record.files:
                    issue_files[number] = record.files
            snapshot_note = (
                f"board snapshot {snapshot_path} generated {snapshot.generated_at} "
                f"({snapshot_mod.age_minutes(snapshot):.0f} min old); "
                f"`Files:` declarations read for {len(issue_files)} sibling(s)"
            )
    else:
        snapshot_note = f"board snapshot {snapshot_path} is absent; `Files:` declarations not read"

    branch_files: dict[int, Sequence[str]] = {}
    branch_refs: dict[int, str] = {}
    branch_notes: list[str] = []
    if not args.no_branch_files:
        for number in sorted(live):
            if number == caller_issue and live[number].agent == args.caller_agent:
                continue
            ref, paths, reason = git_branch_files(root, number)
            if ref is None:
                branch_notes.append(f"#{number}: {reason}")
            else:
                branch_refs[number] = ref
                branch_files[number] = paths
    else:
        branch_notes.append("the git branch file source was disabled (--no-branch-files)")

    report = peer_check(
        caller_files,
        live,
        caller_agent=args.caller_agent,
        caller_issue=caller_issue,
        issue_files=issue_files,
        branch_files=branch_files,
        branch_refs=branch_refs,
        owner=args.owner,
    )
    notes = (ledger_note, snapshot_note, *branch_notes, *report.notes)
    report = PeerReport(
        caller_agent=report.caller_agent,
        caller_issue=report.caller_issue,
        caller_files=report.caller_files,
        siblings=report.siblings,
        notes=notes,
    )

    if args.json:
        print(json.dumps(report.to_json(), indent=2, sort_keys=True))
    else:
        print(report.table())

    if report.verdict == "OVERLAP":
        print(report.refusal() or "", file=sys.stderr)
        return EXIT_NOT_OK
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
