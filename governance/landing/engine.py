#!/usr/bin/env python3
"""The landing engine (#764): one green lane, landed, with no human step.

    push -> open PR -> pre-merge contract -> merge decision -> squash-merge
         -> delete the source branch -> lifecycle close

Every one of those steps already existed; none of them was joined to the others,
so a delivery still ended with "…and then a human pushes and opens the PR". This
engine joins them and redefines nothing:

* the **pre-merge contract** stays ``scripts/merge-gate.sh run`` (issue #29) —
  run in the lane, with ``AO_PR_NUMBER`` set so its ``pr-contract`` signal
  enforces the PR body at the PR boundary;
* the **merge decision** stays ``governance/merge``'s verdict, consulted through
  :mod:`governance.landing.verdict` (the one seam that loads it);
* the **closure** stays ``governance/lifecycle/cli.py close`` (issue #269).

What this engine adds is the order, the refusal, and the idempotence:

* **Refusal is the default posture.** A lane whose attestation is present and
  red, or green but naming no commit, is refused in *both* modes — nothing is
  pushed, opened, merged or deleted. A dry run never performs a write at all.
* **The attestation must name the commit actually being merged** — re-checked
  against the pull request's own head after the contract re-attests.
* **Idempotence**: a lane whose pull request is already merged is *terminal*. The
  driver reports the terminal state, performs no landing write, and creates no
  second pull request. Closure is still reported truthfully — an unclosed item
  is not a success, and this driver never trades a report for a claim.
* **No force, ever.** The only push is ``git push -u origin <branch>``; a
  rejected push stops the landing.

Exit-code contract (the repo's honesty tri-state, issue #28): 0 OK /
1 NOT-OK / 2 CANNOT-ASSESS. A refusal is named, never a bare "not mergeable".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional, Sequence

from governance.landing import attribution as attribution_mod
from governance.landing import evidence as evidence_mod
from governance.landing import verdict as verdict_mod
from governance.landing.evidence import Attestation, Gap, evidence_gap, read_attestation
from governance.landing.ports import LandingOps, PortError, PullRequest

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2

PERFORMED = "performed"
PLANNED = "planned"
SKIPPED = "skipped"
REFUSED = "refused"
FAILED = "failed"

#: The gaps that are a *refusal* even in apply mode: the evidence is present and
#: invalid, so no amount of re-running is the driver's business. A green
#: attestation naming only a *different* commit is stale rather than invalid —
#: apply mode re-runs the contract, which re-attests the current head.
REFUSING_GAPS = (evidence_mod.GAP_NOT_GREEN, evidence_mod.GAP_UNNAMED_COMMIT, evidence_mod.GAP_UNATTRIBUTED)

#: The evidence path in the PR body / report when the contract has not run yet.
CONTRACT_COMMAND = "bash scripts/merge-gate.sh run"


@dataclass(frozen=True)
class Step:
    """One attempted (or skipped, or refused) landing action."""

    action: str
    outcome: str
    detail: str = ""

    def as_dict(self) -> dict:
        return {"action": self.action, "outcome": self.outcome, "detail": self.detail}


@dataclass(frozen=True)
class LandingRequest:
    """What to land, and how."""

    issue: int
    root: Path
    branch: str = ""
    base: str = "master"
    author: str = ""
    title: str = ""
    subject: str = ""
    ai_assistance: str = ""
    notes_file: Optional[Path] = None
    attestation_file: Optional[Path] = None
    apply: bool = False
    owner_carve_out: bool = True
    #: A *recorded* clean-master sweep to attribute against. When unset the
    #: attribution measures one (a scratch worktree of ``origin/master``), which
    #: is the production path; a recorded baseline is how the controls stay
    #: offline and deterministic. Never a prose claim, whichever way it arrives.
    baseline_file: Optional[Path] = None
    #: The lane's own sweep record; defaults to ``<root>/.verify/test-results.json``.
    lane_record_file: Optional[Path] = None
    #: The rev the baseline is measured at (default: clean ``origin/master``).
    baseline_rev: str = ""

    def resolved_baseline_rev(self) -> str:
        return self.baseline_rev or attribution_mod.DEFAULT_BASELINE_REV

    def resolved_branch(self) -> str:
        return self.branch or f"issue-{self.issue}"

    def evidence_path(self) -> Path:
        """Where the *pre-flight* evidence is read (dry run + the pre-contract decision)."""
        return self.attestation_file or (self.root / evidence_mod.ATTESTATION_REL)

    def contract_evidence_path(self) -> Path:
        """Where the contract's own output lands — the merge-boundary evidence.

        Deliberately NOT overridable: at the merge boundary the evidence must be
        the attestation the contract just wrote for the tree it just gated. An
        override here would let a stale (or hand-written) file stand in for the
        thing the merge decision is supposed to be based on.
        """
        return self.root / evidence_mod.ATTESTATION_REL


@dataclass
class LandingResult:
    """What the driver did (or would do), and the verdict it acted on."""

    issue: int
    branch: str
    base: str
    dry_run: bool = True
    commit: str = ""
    terminal: bool = False
    granted: bool = False
    pr_number: Optional[int] = None
    merge_commit: str = ""
    refusal_code: str = ""
    refusal: str = ""
    attestation: Optional[Attestation] = None
    verdict: Optional[dict] = None
    steps: list = field(default_factory=list)
    lifecycle_rc: Optional[int] = None
    report_path: str = ""
    rc: int = EXIT_OK
    #: The failing suites MEASURED to be failing on clean master too, by name.
    #: Empty unless an attribution was performed and granted — and never dropped
    #: from the record when it is not empty (the "never silently" rule).
    pre_existing: tuple = ()
    #: The full attribution record (the measurement's own account of itself).
    attribution: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.rc == EXIT_OK

    def as_dict(self) -> dict:
        return {
            "issue": self.issue,
            "branch": self.branch,
            "base": self.base,
            "mode": "apply" if not self.dry_run else "dry-run",
            "commit": self.commit,
            "terminal": self.terminal,
            "granted": self.granted,
            "pr_number": self.pr_number,
            "merge_commit": self.merge_commit,
            "refusal_code": self.refusal_code,
            "refusal": self.refusal,
            "attestation": self.attestation.as_dict() if self.attestation else None,
            "verdict": self.verdict,
            "steps": [step.as_dict() for step in self.steps],
            "lifecycle_rc": self.lifecycle_rc,
            "report_path": self.report_path,
            "pre_existing": list(self.pre_existing),
            "attribution": self.attribution,
            "exit_code": self.rc,
        }


def _rc_for_gap(gap: Gap) -> int:
    return EXIT_CANNOT_ASSESS if gap.cannot_assess else EXIT_NOT_OK


def _normalise_rc(rc: int) -> int:
    """A subprocess exit code through the honesty tri-state (never a false pass)."""
    if rc == 0:
        return EXIT_OK
    if rc == 1:
        return EXIT_NOT_OK
    return EXIT_CANNOT_ASSESS


class LandingEngine:
    """The ordered landing, over injected effects."""

    def __init__(self, ops: LandingOps, request: LandingRequest) -> None:
        self.ops = ops
        self.request = request

    # -- body composition ------------------------------------------------------

    def _compose_body(self, result: LandingResult, attestation: Attestation, subjects: Sequence[str]) -> str:
        """The PR body, in the shape ``scripts/check-pr-contract.sh`` enforces.

        The section headings are the machine-readable contract (Closes,
        AI-assistance, Pre-existing red); the evidence block quotes the
        attestation when the lane already has one and names the command the
        driver runs at the merge boundary otherwise — never a remembered number.
        """
        req = self.request
        lines = [
            "## Closes",
            "",
            f"Closes #{req.issue}",
            "",
            "## What changed",
            "",
        ]
        if subjects:
            lines.extend(f"- {subject}" for subject in subjects)
        else:
            lines.append(f"- the lane's commits on `{result.branch}`")
        if req.notes_file and Path(req.notes_file).is_file():
            lines.extend(["", Path(req.notes_file).read_text(encoding="utf-8").rstrip()])
        lines.extend(["", "## Evidence", "", "```"])
        if attestation.readable:
            lines.append(f"$ cat {attestation.path}")
            lines.append(json.dumps(attestation.as_dict(), indent=2, sort_keys=True))
        else:
            lines.append(f"$ {CONTRACT_COMMAND}    # the pre-merge contract, run by the driver")
            lines.append(f"# it writes {evidence_mod.ATTESTATION_REL}, an attestation naming this commit")
        lines.append("```")
        lines.extend(
            [
                "",
                "The driver re-runs the pre-merge contract at the PR boundary "
                f"(`AO_PR_NUMBER` set) and requires the attestation it writes to name the "
                f"pull request head commit — `{result.commit}` — before it merges.",
                "",
                "## AI-assistance",
                "",
                f"AI-assistance: {req.ai_assistance}",
                "",
                "## Pre-existing red",
                "",
            ]
        )
        lines.extend(self._pre_existing_section(result))
        return "\n".join(lines)

    def _pre_existing_section(self, result: LandingResult) -> list:
        """The ``## Pre-existing red`` declaration, in the shape the contract enforces.

        ``scripts/check-pr-contract.sh`` requires this section to be either an
        explicit ``None`` or a reproduction: a ``Reproduce:`` line naming a
        command in backticks, followed by a fenced block. So the *declaration* is
        the repo's existing mechanism, consumed rather than replaced — and what
        stands behind it is the measurement this driver ran, named as such. When
        nothing was attributed the section says ``None``, which is the honest
        answer rather than a claim of cleanliness in general.
        """
        if not result.pre_existing:
            return [
                "None — no failing suite is claimed to be pre-existing. A red ``verify``, ``drift``,\n",
                "``negative-controls``, ``policy-schema`` or ``pr-contract`` signal is never attributed, and\n",
                "neither is a suite that fails in this lane and passes on clean master.\n",
                "",
            ]
        attribution = result.attribution or {}
        lane = (attribution.get("lane") or {}).get("source") or f"<lane>/{attribution_mod.SWEEP_REL}"
        baseline = (attribution.get("baseline") or {}).get("source") or self.request.resolved_baseline_rev()
        lines = [
            "Attributed by measurement, not by claim: the lane's failing set was compared against the same\n",
            "sweep run on clean master, and each suite below fails in BOTH. A suite that fails here and\n",
            "passes on clean master is refused by name instead.\n",
            "",
            f"Reproduce: `{attribution_mod.SWEEP_COMMAND_TEXT}` (in the lane, and on clean master)\n",
            "",
            "```",
            f"lane sweep:     {lane}",
            f"master sweep:   {baseline}",
            "failing in both: " + ", ".join(result.pre_existing),
            "```",
            "",
        ]
        return lines

    # -- the landing -----------------------------------------------------------

    def _compose_squash_body(self, result: LandingResult, subjects: Sequence[str]) -> str:
        """The squash-merge commit body — the artifact that actually lands.

        The repository sets ``squash_merge_commit_message=COMMIT_MESSAGES``, so
        the landed commit body is composed from the branch commit messages, not
        the PR body. Passing an explicit body makes the landed artifact
        deterministic and guarantees the trailing ticket trailer the
        landed-history audit (``check-pr-contract.sh --landed``) requires: the
        message ends in the trailing trailer block
        ``Refs <owner>/<repo>#<n>`` followed by GitHub's own bare auto-close
        keyword, so the squash commit the merge produces re-passes that audit
        (issue #998).
        """
        req = self.request
        lines: list[str] = []
        if subjects:
            lines.extend(f"- {subject}" for subject in subjects)
        else:
            lines.append(f"- the lane's commits on `{result.branch}`")
        lines.extend(
            [
                "",
                f"Refs kushin77/agent-orchestrator#{req.issue}",
                f"Closes #{req.issue}",
            ]
        )
        return "\n".join(lines) + "\n"

    def land(self) -> LandingResult:
        req = self.request
        result = LandingResult(
            issue=req.issue,
            branch=req.resolved_branch(),
            base=req.base,
            dry_run=not req.apply,
        )
        try:
            result.commit = self.ops.head_commit()
        except PortError as exc:
            return self._cannot_assess(result, f"cannot read the lane head ({exc})")
        try:
            existing = self.ops.pull_request_for(result.branch)
        except PortError as exc:
            return self._cannot_assess(result, f"cannot read the pull request for {result.branch} ({exc})")

        if existing is not None and existing.merged:
            return self._terminal(result, existing)
        if existing is not None and not existing.open:
            result.steps.append(
                Step(
                    "inspect",
                    REFUSED,
                    f"PR #{existing.number} is {existing.state} (not merged, not open) — land it by hand "
                    "or re-cut the lane branch; the driver never reopens or rewrites a pull request",
                )
            )
            return self._refuse(
                result, "pull-request-not-open", f"PR #{existing.number} is {existing.state}"
            )

        attestation = read_attestation(req.evidence_path())
        result.attestation = attestation
        gap = evidence_gap(attestation, result.commit)

        # A RED pre-flight attestation is measured before it is refused: if the
        # contract's own per-signal record exists and the only red signal is
        # `tests`, and every failing suite is measured failing on clean master
        # too, the red is pre-existing and does not block. Anything else — a red
        # `verify`, an unattributable suite, a record this driver cannot read —
        # leaves the gap exactly as it was, and the refusal below stands.
        if gap is not None and gap.code == evidence_mod.GAP_NOT_GREEN:
            attributed = self._attribute(result)
            if attributed is not None:
                result.attribution = attributed.as_dict()
                result.steps.append(
                    Step(
                        "attribution",
                        PERFORMED if attributed.grant else REFUSED,
                        attributed.summary,
                    )
                )
                if attributed.grant:
                    result.pre_existing = attributed.pre_existing
                    # Only the *verdict* is explained by the measurement; the
                    # commit-naming rule is re-applied below and still binds.
                    attestation = replace(attestation, rc=0)
                    gap = evidence_gap(attestation, result.commit)

        verdict = self._consult_verdict(result, attestation, existing)
        result.verdict = verdict.as_dict()

        if gap is None and verdict.mergeable:
            if req.apply:
                return self._apply(result, existing)
            return self._grantable(result)

        if gap is not None and gap.code in REFUSING_GAPS:
            return self._refuse(result, gap.code, str(gap))

        if gap is None and not verdict.mergeable:
            reason = ", ".join(verdict.reasons) or "the merge verdict is not mergeable"
            return self._refuse(result, "merge-verdict-blocked", f"governance/merge blocked the merge: {reason}")

        # The attestation is absent, unreadable, carries no verdict, or names an
        # older commit. In apply mode the contract produces the evidence, so the
        # landing continues; a dry run cannot assess (never a pass) but still
        # states exactly what it would do.
        if req.apply:
            return self._apply(result, existing)
        return self._plan_only(result, gap)

    # -- verdict consultation --------------------------------------------------

    def _attribute(self, result: LandingResult, *, contract_record: bool = True):
        """Measure this lane's suite reds against clean master (see attribution.py).

        ``contract_record=False`` measures only the suite half — the part a
        declaration can honestly state before the contract has re-run. Returns
        ``None`` only when the attribution module itself is unreachable — which is
        CANNOT-ASSESS for the caller, never a grant.
        """
        req = self.request
        measure = attribution_mod.attribute_contract if contract_record else attribution_mod.attribute_lane
        try:
            return measure(
                req.root,
                commit=result.commit,
                lane_record=req.lane_record_file,
                baseline_file=req.baseline_file,
                baseline_rev=req.resolved_baseline_rev(),
            )
        except Exception:  # noqa: BLE001 - an unreachable measurement is never a grant
            return None

    def _consult_verdict(
        self, result: LandingResult, attestation: Attestation, pr: Optional[PullRequest]
    ) -> verdict_mod.MergeVerdict:
        """Hand the evidence to ``governance/merge`` and carry its answer back."""
        rc = attestation.rc if attestation.rc is not None else 2
        outcome = verdict_mod.gate_outcome(
            rc, attestation.commit, evidence=f"{attestation.path} (result={attestation.result or 'unknown'})"
        )
        return verdict_mod.decide(
            number=pr.number if pr is not None else 0,
            title=self.request.title or self.request.subject or result.branch,
            author=self.request.author,
            subject=self.request.subject or self.request.title or result.branch,
            branch=result.branch,
            outcome=outcome,
            owner_carve_out=self.request.owner_carve_out,
        )

    # -- outcomes --------------------------------------------------------------

    def _terminal(self, result: LandingResult, pr: PullRequest) -> LandingResult:
        result.terminal = True
        result.pr_number = pr.number
        result.merge_commit = pr.merge_commit or ""
        result.steps.append(
            Step(
                "inspect",
                SKIPPED,
                f"PR #{pr.number} is already MERGED at {pr.merge_commit or 'an unnamed commit'} — "
                "the lane is terminal: no push, no new pull request, no second merge",
            )
        )
        if result.dry_run:
            result.steps.append(
                Step(
                    "lifecycle-close",
                    PLANNED,
                    f"python3 governance/lifecycle/cli.py close --issue {result.issue}",
                )
            )
            return result
        return self._close_out(result)

    def _grantable(self, result: LandingResult) -> LandingResult:
        """Dry run, evidence and verdict both in order: report the plan, change nothing."""
        result.granted = True
        self._plan_steps(result)
        return result

    def _plan_only(self, result: LandingResult, gap: Optional[Gap]) -> LandingResult:
        """Dry run without usable evidence: state the plan, and say CANNOT-ASSESS."""
        self._plan_steps(result)
        result.refusal_code = gap.code if gap is not None else ""
        result.refusal = (
            f"{gap} — the contract has not attested this tree yet; a dry run cannot assess the merge, "
            "and says so rather than guessing" if gap is not None else "no verdict available"
        )
        result.rc = _rc_for_gap(gap) if gap is not None else EXIT_CANNOT_ASSESS
        return result

    def _plan_steps(self, result: LandingResult) -> None:
        """The ordered steps apply mode would perform, recorded for the report."""
        req = self.request
        result.steps.append(Step("push", PLANNED, f"git push -u origin {result.branch}"))
        result.steps.append(
            Step(
                "open-pr",
                PLANNED,
                f"gh pr create --base {req.base} --head {result.branch} (Closes #{req.issue}, "
                f"AI-assistance: {req.ai_assistance})",
            )
        )
        result.steps.append(Step("contract", PLANNED, CONTRACT_COMMAND))
        result.steps.append(
            Step(
                "gate-status",
                PLANNED,
                "bash scripts/gate-status.sh post --sha <pr-head> --rc <contract-rc> "
                "(ADR-0028 — posted before the merge decision, for every contract outcome)",
            )
        )
        result.steps.append(
            Step(
                "landed-contract",
                PLANNED,
                f"bash scripts/check-pr-contract.sh --landed --range {req.base}..<head> "
                "(the commits to be squashed must carry the trailing ticket trailer)",
            )
        )
        result.steps.append(
            Step(
                "merge",
                PLANNED,
                "gh pr merge <n> --squash --subject <title> --body-file <trailer-bearing message> "
                "(only if the fresh attestation is green, names the PR head, and the landed contract is green)",
            )
        )
        result.steps.append(Step("delete-branch", PLANNED, f"git push origin --delete {result.branch}"))
        result.steps.append(
            Step("lifecycle-close", PLANNED, f"python3 governance/lifecycle/cli.py close --issue {req.issue}")
        )

    def _refuse(self, result: LandingResult, code: str, detail: str, *, pre_write: bool = True) -> LandingResult:
        """Refuse the landing, naming what was checked.

        ``pre_write`` distinguishes the two honest shapes of a refusal: before
        any effect (nothing at all happened) and after the push and the pull
        request (they stand, nothing was merged). Claiming the first when the
        second is true would hide a pushed branch from the operator.
        """
        result.refusal_code = code
        result.refusal = detail
        result.rc = EXIT_CANNOT_ASSESS if code in evidence_mod.CANNOT_ASSESS_GAPS else EXIT_NOT_OK
        detail_line = (
            "refused before any write — nothing was pushed, opened, merged or deleted"
            if pre_write
            else "refused after the push and the pull request — nothing was merged, and no branch was deleted"
        )
        result.steps.append(Step("push" if pre_write else "inspect", SKIPPED, detail_line))
        return result

    def _cannot_assess(self, result: LandingResult, detail: str) -> LandingResult:
        result.refusal_code = "cannot-assess"
        result.refusal = detail
        result.rc = EXIT_CANNOT_ASSESS
        result.steps.append(Step("inspect", FAILED, detail))
        return result

    # -- apply -----------------------------------------------------------------

    def _apply(self, result: LandingResult, existing: Optional[PullRequest]) -> LandingResult:
        """Perform the landing, in the contract's own order."""
        req = self.request
        # The PR body declares the pre-existing reds, and it is written before the
        # contract re-runs at the boundary — so measure the *suite* half now, from
        # the lane's own sweep record. This grants nothing (a grant needs the
        # contract's per-signal record); it only stops the declaration from saying
        # "None" about reds the driver can already measure.
        if not result.pre_existing:
            declaration = self._attribute(result, contract_record=False)
            if declaration is not None and declaration.pre_existing:
                result.pre_existing = declaration.pre_existing
                result.attribution = declaration.as_dict()
        try:
            published = self._push(result)
            pr = self._open_pr(result, existing, published)
            contract = self.ops.run_contract(pr_number=pr.number)
        except PortError as exc:
            result.steps.append(Step("landing", FAILED, str(exc)))
            result.refusal_code = "effect-failed"
            result.refusal = str(exc)
            result.rc = EXIT_NOT_OK
            return self._journal(result)
        result.steps.append(
            Step("contract", PERFORMED if contract.rc == 0 else FAILED, f"{CONTRACT_COMMAND} -> rc={contract.rc}")
        )
        for line in contract.tail(12).splitlines():
            result.steps.append(Step("contract", PERFORMED if contract.rc == 0 else FAILED, line))

        # Publish the gate of record (ADR-0028, #1072) BEFORE the merge decision,
        # for every contract outcome — a red or unassessable commit is decorated
        # red/error, never left blank. This runs against the PR's own head, and
        # ahead of the attribution logic below: an attributed pre-existing red
        # still posts `failure` (the context is not yet required, so this is
        # honest rather than defeating the attribution).
        status_sha = pr.head or result.commit
        status_rc = _normalise_rc(contract.rc)
        try:
            status = self.ops.publish_status(sha=status_sha, rc=status_rc)
        except PortError as exc:
            result.steps.append(Step("gate-status", FAILED, str(exc)))
            result.refusal_code = "gate-status-unpublished"
            result.refusal = (
                f"the gate-of-record status could not be published for {status_sha} ({exc}) — a "
                "failed/unreadable poster is CANNOT-ASSESS, and the commit is never left blank or merged"
            )
            result.rc = EXIT_CANNOT_ASSESS
            return self._journal(result)
        result.steps.append(
            Step(
                "gate-status",
                PERFORMED if status.ok else FAILED,
                f"bash scripts/gate-status.sh post --sha {status_sha} --rc {status_rc} -> rc={status.rc}",
            )
        )
        if not status.ok:
            result.refusal_code = "gate-status-unpublished"
            result.refusal = (
                f"the gate-of-record status poster returned rc={status.rc} for {status_sha} — a "
                "failed/unreadable poster is CANNOT-ASSESS, and the commit is never left blank or merged"
            )
            result.rc = EXIT_CANNOT_ASSESS
            return self._journal(result)

        attributed = None
        if contract.rc != 0:
            # The contract's red is measured before it is refused, exactly as at
            # the pre-flight: a red that is entirely suite reds measured failing
            # on clean master is not this lane's failure. Every other red — the
            # gate of record included — still refuses.
            attributed = self._attribute(result)
            if attributed is not None:
                result.attribution = attributed.as_dict()
            if attributed is None or not attributed.grant:
                detail = attributed.summary if attributed is not None else "the attribution could not be measured"
                result.refusal_code = "pre-merge-contract-failed"
                result.refusal = (
                    f"the pre-merge contract returned rc={contract.rc} and its red is NOT attributable to clean "
                    f"master ({detail}) — a red (or unassessable) contract never merges (no-false-green)"
                )
                result.rc = _normalise_rc(contract.rc)
                return self._journal(result)
            result.pre_existing = attributed.pre_existing
            result.steps.append(Step("attribution", PERFORMED, attributed.summary))

        # Defense in depth: read the evidence the contract just wrote, and the
        # commit the pull request actually points at, and re-consult the verdict.
        try:
            head_pr = self.ops.pull_request_for(result.branch)
        except PortError as exc:
            result.steps.append(Step("inspect", FAILED, str(exc)))
            result.refusal_code = "cannot-assess"
            result.refusal = str(exc)
            result.rc = EXIT_CANNOT_ASSESS
            return self._journal(result)
        landing_commit = (head_pr.head if head_pr is not None and head_pr.head else result.commit)
        fresh = read_attestation(req.contract_evidence_path())
        result.attestation = fresh
        if attributed is not None and attributed.grant:
            # The freshly written attestation is red for suites the measurement
            # already established are failing on clean master. Only its verdict
            # is explained; the commit it names must still be this commit.
            fresh = replace(fresh, rc=0)
        gap = evidence_gap(fresh, landing_commit)
        if gap is not None:
            result.steps.append(Step("inspect", REFUSED, str(gap)))
            return self._journal(self._refuse(result, gap.code, str(gap), pre_write=False))
        verdict = self._consult_verdict(result, fresh, head_pr if head_pr is not None else existing)
        result.verdict = verdict.as_dict()
        if not verdict.mergeable:
            reason = ", ".join(verdict.reasons) or "the merge verdict is not mergeable"
            result.steps.append(Step("inspect", REFUSED, f"governance/merge blocked: {reason}"))
            return self._journal(
                self._refuse(
                    result,
                    "merge-verdict-blocked",
                    f"governance/merge blocked the merge: {reason}",
                    pre_write=False,
                )
            )

        # Merge precondition (issue #998): the artifact that lands is the squash
        # commit, composed from the BRANCH COMMIT MESSAGES because the repository
        # sets squash_merge_commit_message=COMMIT_MESSAGES. The PR body is not
        # what lands, so the commits to be squashed must themselves carry the
        # trailing ticket trailer — refused here, by name, before the merge.
        landed = self.ops.check_landed_contract(base=req.base, head=landing_commit)
        result.steps.append(
            Step(
                "landed-contract",
                PERFORMED if landed.rc == 0 else FAILED,
                f"bash scripts/check-pr-contract.sh --landed --range {req.base}..{landing_commit} -> rc={landed.rc}",
            )
        )
        for line in landed.tail(12).splitlines():
            result.steps.append(Step("landed-contract", PERFORMED if landed.rc == 0 else FAILED, line))
        if landed.rc != 0:
            result.refusal_code = "landed-contract-blocked"
            result.refusal = (
                f"the commits to be squashed ({req.base}..{landing_commit}) do not carry the trailing ticket "
                f"trailer (check-pr-contract --landed rc={landed.rc}) — a trailer-less squash never merges"
            )
            result.rc = _normalise_rc(landed.rc)
            return self._journal(result)

        # The squash message is explicit and trailer-bearing, so the landed
        # artifact is deterministic rather than dependent on GitHub's
        # COMMIT_MESSAGES composition.
        try:
            subjects = self.ops.commit_subjects(req.base, result.branch)
        except PortError:
            subjects = ()
        title = req.title or (pr.title if pr is not None and pr.title else "") or result.branch
        squash_body = req.root / ".verify" / f"landing-{req.issue}-squash-message.md"
        squash_body.parent.mkdir(parents=True, exist_ok=True)
        squash_body.write_text(self._compose_squash_body(result, subjects), encoding="utf-8")

        result.granted = True
        try:
            result.merge_commit = self.ops.merge_pr(pr.number, subject=title, body_file=squash_body)
        except PortError as exc:
            result.steps.append(Step("merge", FAILED, str(exc)))
            result.refusal_code = "merge-failed"
            result.refusal = str(exc)
            result.rc = EXIT_NOT_OK
            return self._journal(result)
        result.steps.append(
            Step(
                "merge",
                PERFORMED,
                f"gh pr merge {pr.number} --squash --subject {title!r} --body-file {squash_body.name} "
                f"-> {result.merge_commit or 'merge commit unnamed'}",
            )
        )

        try:
            result.steps.append(Step("delete-branch", PERFORMED, self.ops.delete_branch(result.branch)))
        except PortError as exc:
            result.steps.append(Step("delete-branch", FAILED, str(exc)))
        return self._close_out(self._journal(result))

    def _push(self, result: LandingResult) -> bool:
        """Publish the lane branch (idempotent, never a force)."""
        remote = self.ops.remote_branch_head(result.branch)
        if remote is not None and evidence_mod.same_commit(remote, result.commit):
            result.steps.append(Step("push", SKIPPED, f"origin/{result.branch} is already at {result.commit}"))
            return False
        detail = self.ops.push(result.branch)
        result.steps.append(Step("push", PERFORMED, detail))
        return True

    def _open_pr(self, result: LandingResult, existing: Optional[PullRequest], published: bool) -> PullRequest:
        """Open the pull request, or reuse the open one (never a second PR)."""
        req = self.request
        if existing is not None and existing.open:
            result.pr_number = existing.number
            result.steps.append(Step("open-pr", SKIPPED, f"PR #{existing.number} is already open — reused, not duplicated"))
            return existing
        subjects = self.ops.commit_subjects(req.base, result.branch)
        attestation = result.attestation or read_attestation(req.evidence_path())
        body_file = req.root / ".verify" / f"landing-{req.issue}-pr-body.md"
        body_file.parent.mkdir(parents=True, exist_ok=True)
        body_file.write_text(self._compose_body(result, attestation, subjects), encoding="utf-8")
        title = req.title or (subjects[0] if subjects else self.ops.latest_subject(result.branch))
        pr = self.ops.open_pr(branch=result.branch, base=req.base, title=title, body_file=body_file)
        result.pr_number = pr.number
        pushed = "the lane branch was just published" if published else "the lane branch was already published"
        result.steps.append(
            Step(
                "open-pr",
                PERFORMED,
                f"PR #{pr.number} {pr.url or ''} (body: {body_file.name}; {pushed}, head {result.commit})".strip(),
            )
        )
        return pr

    # -- closure ---------------------------------------------------------------

    def _close_out(self, result: LandingResult) -> LandingResult:
        """Drive hygienic closure, and report its exit code rather than assuming it."""
        evidence = describe(result)
        try:
            closure = self.ops.close_lifecycle(result.issue)
        except PortError as exc:
            result.steps.append(Step("lifecycle-close", FAILED, str(exc)))
            result.lifecycle_rc = None
            result.rc = EXIT_NOT_OK
            return result
        result.lifecycle_rc = closure.rc
        result.steps.append(
            Step(
                "lifecycle-close",
                PERFORMED if closure.rc == 0 else FAILED,
                f"governance/lifecycle/cli.py close --issue {result.issue} -> rc={closure.rc}",
            )
        )
        for line in closure.tail(12).splitlines():
            result.steps.append(Step("lifecycle-close", PERFORMED if closure.rc == 0 else FAILED, line))
        result.rc = _normalise_rc(closure.rc)
        return self._journal(result, evidence=evidence)

    # -- journal ---------------------------------------------------------------

    def _journal(self, result: LandingResult, evidence: str = "") -> LandingResult:
        """Write the landing record into the gate's own ignored dir (apply only).

        A dry run writes nothing at all — "changes nothing" is then a property a
        control can check by looking at the tree, not a promise.
        """
        if result.dry_run:
            return result
        directory = self.request.root / ".verify"
        directory.mkdir(parents=True, exist_ok=True)
        report_path = directory / f"landing-{self.request.issue}-report.md"
        # Trailing whitespace is stripped: this is a plain-text artifact living in
        # the tree, and an unclean report would redden the doc gate — whose red on
        # the gate of record is never attributable, so every later landing would
        # refuse on the driver's own output (issue #764).
        body = evidence or describe(result)
        report_path.write_text(
            "".join(f"{line.rstrip()}\n" for line in body.splitlines()), encoding="utf-8"
        )
        record_path = directory / f"landing-{self.request.issue}.json"
        record_path.write_text(json.dumps(result.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        result.report_path = str(report_path)
        return result


def describe(result: LandingResult) -> str:
    """A legible report; the verdict and every step are quoted, never summarised away."""
    mode = "DRY RUN" if result.dry_run else "APPLY"
    lines = [f"land: issue #{result.issue}  branch={result.branch}  base={result.base}  mode={mode}"]
    lines.append(f"  head: {result.commit or 'unknown'}")
    attestation = result.attestation
    if attestation is not None:
        rc = "-" if attestation.rc is None else str(attestation.rc)
        lines.append(
            f"  evidence: {attestation.path} state={attestation.state} result={attestation.result or '-'} "
            f"exit_code={rc} commit={attestation.commit or '-'}"
        )
    if result.verdict is not None:
        verdict = result.verdict
        lines.append(
            f"  merge verdict (governance/merge): mergeable={verdict['mergeable']} state={verdict['state']} "
            f"reviewer={verdict['reviewer_id'] or '-'} commit={verdict['verify_commit'] or '-'}"
        )
        for reason in verdict["reasons"]:
            lines.append(f"    blocked: {reason}")
        lines.append(f"    rule: {verdict['source']}")
    # Named, never silently dropped: a pre-existing red is reported wherever the
    # landing is reported, so the record carries it whether or not it blocked.
    if result.pre_existing:
        lines.append(
            f"  pre-existing red (measured on clean master, not this lane): {', '.join(result.pre_existing)}"
        )
    if result.attribution and not result.pre_existing and not result.attribution.get("grant"):
        lines.append(f"  attribution: {result.attribution.get('code')} — {result.attribution.get('summary')}")
    for step in result.steps:
        lines.append(f"  {step.action:<17} {step.outcome:<9} {step.detail}")
    if result.terminal:
        lines.append(
            f"landing: TERMINAL — PR #{result.pr_number} is merged at {result.merge_commit or 'an unnamed commit'}; "
            "no second pull request was created"
        )
    elif result.dry_run and result.granted:
        lines.append(
            "landing: DRY RUN — the lane is grantable; nothing was pushed, opened, merged or deleted "
            "(no remote change). Re-run with --apply (or AO_LAND_APPLY=1) to land it."
        )
    elif result.granted:
        lines.append(
            f"landing: MERGED — PR #{result.pr_number} squash-merged at "
            f"{result.merge_commit or 'an unnamed commit'}; the source branch was deleted and the "
            "closure steps above were driven"
        )
    elif result.refusal:
        lines.append(f"landing: REFUSED ({result.refusal_code}) — {result.refusal}")
    if result.lifecycle_rc is not None and result.lifecycle_rc != 0:
        lines.append(
            f"landing: closure is NOT terminal (lifecycle rc={result.lifecycle_rc}) — finish the named steps above; "
            "the driver reports them rather than claiming a close"
        )
    return "\n".join(lines)
