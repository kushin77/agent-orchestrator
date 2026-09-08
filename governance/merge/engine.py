#!/usr/bin/env python3
"""Offline merge-governance engine (EPIC-00 issue #43, phase 8).

Drives a PR through the merge-governance state machine with *injected*
runtime signals so the whole pipeline runs offline (no network, no live
GitHub API — where ``gh`` behavior is referenced, it is injected):

    opened -> verify-gate -> sme-review-assigned -> approved/verified
                                                     -> mergeable | blocked

``MergeGovernanceEngine.run()`` wires the pieces together:

1. **Verify gate** — an injected ``GateCheck`` (default: always-green demo
   gate; tests inject red / cannot-assess gates) produces a
   :class:`~gate.VerifyOutcome` naming the commit under test. NOT-OK and
   CANNOT-ASSESS results block; an OK result without a commit-named
   attestation blocks too (an attestation names a COMMIT — issue #29 parity).
2. **Independent SME review** — an injected reviewer assigner (default: the
   issue #11 persona library) assigns a reviewer persona distinct from the
   author; the reviewer approve verdict is recorded as evidence.
3. **Merge decision** — ``model.merge_verdict`` is the single rule: green
   verification + independent reviewer + no-self-merge (or the owner
   autonomous-merge carve-out). The engine never authors the change — it only
   decides (AO-GR-12: the control plane never executes the work it governs).
4. **Audit trail** — every decision is appended to the PR's audit log.

CLI::

    python3 governance/merge/engine.py demo

runs one mergeable scenario (owner autonomous-merge carve-out with a green
gate) and one blocked scenario (red gate), printing each PR's final state,
verdict reasons and audit trail. Exits 0 only when the demo assertions hold.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Callable, List, Optional

from gate import GateCheck, VerifyOutcome, green_gate, red_gate
from model import (
    AuditEntry,
    BlockReason,
    MergeGovernanceMachine,
    MergePr,
    PrState,
)
from reviewer import (
    NoReviewerAvailableError,
    PersonaAssigner,
    ReviewerPersona,
    SeparationViolationError,
)


@dataclass(frozen=True)
class MergeGovernanceResult:
    """The outcome of one engine run over a PR."""

    number: int
    state: PrState
    mergeable: bool
    block_reason: Optional[str]
    reviewer_id: Optional[str]
    verify_commit: Optional[str]
    audit: List[AuditEntry]

    def audit_records(self) -> List[dict]:
        return [entry.as_dict() for entry in self.audit]


class MergeGovernanceEngine:
    """Offline engine composing gate + reviewer + state machine + audit."""

    def __init__(
        self,
        gate_check: Optional[GateCheck] = None,
        reviewer_assigner: Optional[PersonaAssigner] = None,
        owner_carve_out: bool = True,
    ) -> None:
        # The owner autonomous-merge mandate (2026-09-07) is active in this
        # fleet: an agent merges its own PR after green verification evidence.
        # It is modeled as an explicit flag so tenants/consumers can disable it
        # (the carve-out never replaces the evidence requirement).
        self.gate_check: GateCheck = gate_check or green_gate()
        self.reviewer_assigner = reviewer_assigner or PersonaAssigner()
        self.owner_carve_out = owner_carve_out

    # -- pieces ----------------------------------------------------------------

    def _make_pr(
        self,
        number: int,
        title: str,
        author: str,
        author_tenant: str,
        subject: str,
        branch: str,
    ) -> MergePr:
        return MergePr(
            number=number,
            title=title,
            author=author,
            author_tenant=author_tenant,
            subject=subject,
            branch=branch,
        )

    def _run_verify(
        self, machine: MergeGovernanceMachine, commit: str
    ) -> None:
        outcome: VerifyOutcome = self.gate_check(machine.pr, commit)
        machine.record_verify(
            rc=outcome.rc, commit=outcome.commit, evidence=outcome.evidence
        )

    def _assign_reviewer(
        self, machine: MergeGovernanceMachine, subject: str
    ) -> None:
        try:
            persona: ReviewerPersona = self.reviewer_assigner.assign_reviewer(
                machine.pr.author_tenant, machine.pr.author, subject
            )
        except NoReviewerAvailableError:
            machine.block(
                BlockReason.REVIEWER_NOT_ASSIGNED,
                "no independent reviewer persona is available (separation of duties)",
            )
            return
        try:
            self.reviewer_assigner.assert_reviewer_distinct(
                machine.pr.author_tenant, machine.pr.author, persona
            )
            machine.assign_reviewer(
                persona_id=persona.id,
                posture=persona.posture,
                reviewer_tenant=persona.tenant,
            )
        except SeparationViolationError as exc:
            machine.block(BlockReason.SELF_REVIEW, str(exc))

    # -- one-shot ----------------------------------------------------------------

    def run(
        self,
        *,
        number: int,
        title: str,
        author: str,
        author_tenant: str = "platform",
        subject: str,
        branch: str,
        merger: Optional[str] = None,
        commit: str = "HEAD",
    ) -> MergeGovernanceResult:
        """Drive one PR through the full governance lifecycle.

        ``merger`` defaults to the author (self-merge posture); the owner
        autonomous-merge carve-out flag decides whether self-merge is
        permitted. ``commit`` is the commit the verify gate attests.
        """
        pr = self._make_pr(number, title, author, author_tenant, subject, branch)
        machine = MergeGovernanceMachine(pr)
        machine.begin_verify()
        self._run_verify(machine, commit)
        if pr.is_blocked:
            return self._result(pr)
        self._assign_reviewer(machine, subject)
        if pr.is_blocked:
            return self._result(pr)
        if pr.reviewer_id is not None:
            machine.approve_review()
        else:
            machine.block(
                BlockReason.REVIEWER_NOT_ASSIGNED,
                "cannot approve review without an assigned independent reviewer",
            )
            return self._result(pr)
        machine.conclude_merge(
            merger=merger or pr.author, owner_carve_out=self.owner_carve_out
        )
        return self._result(pr)

    @staticmethod
    def _result(pr: MergePr) -> MergeGovernanceResult:
        return MergeGovernanceResult(
            number=pr.number,
            state=pr.state,
            mergeable=pr.is_mergeable,
            block_reason=pr.block_reason,
            reviewer_id=pr.reviewer_id,
            verify_commit=pr.verify_commit,
            audit=list(pr.audit),
        )


# --------------------------------------------------------------------------- #
# CLI demo
# --------------------------------------------------------------------------- #


def _demo() -> int:
    print("== merge-governance demo (issue #43) ==")

    engine = MergeGovernanceEngine(owner_carve_out=True)
    ok = engine.run(
        number=1,
        title="feat(governance/merge): merge-governance contract",
        author="coder",
        subject="merge governance of the verify-gate change (independent SME review)",
        branch="issue-43-merge-governance",
        merger="coder",  # self-merge under the owner autonomous-merge carve-out
        commit="abc123",
    )
    print(f"\nPR #{ok.number}: state={ok.state.value} mergeable={ok.mergeable}")
    print(f"  reviewer={ok.reviewer_id} verify_commit={ok.verify_commit}")
    for entry in ok.audit:
        print(f"  [{entry.seq}] {entry.action}: {entry.detail}")

    blocked = MergeGovernanceEngine(
        gate_check=red_gate("tests failed"), owner_carve_out=True
    ).run(
        number=2,
        title="feat(governance/merge): a red gate never merges",
        author="coder",
        subject="a change that never turns the verify gate green",
        branch="issue-x",
        merger="coder",
        commit="def456",
    )
    print(f"\nPR #{blocked.number}: state={blocked.state.value} mergeable={blocked.mergeable}")
    print(f"  block_reason={blocked.block_reason}")
    for entry in blocked.audit:
        print(f"  [{entry.seq}] {entry.action}: {entry.detail}")

    assert ok.mergeable, "green gate + review + carve-out must be mergeable"
    assert not blocked.mergeable, "red gate must never be mergeable"
    assert blocked.block_reason == BlockReason.VERIFY_NOT_GREEN.value
    print("\ndemo assertions OK: green+carve-out mergeable; red gate blocked")
    return 0


def _main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="governance/merge/engine.py",
        description="Offline merge-governance engine (issue #43)",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="demo",
        choices=("demo",),
        help="subcommand to run (default: demo)",
    )
    args = parser.parse_args(argv)
    if args.command == "demo":
        return _demo()
    parser.error(f"unknown command {args.command!r}")
    return 2  # pragma: no cover


if __name__ == "__main__":
    sys.exit(_main())
