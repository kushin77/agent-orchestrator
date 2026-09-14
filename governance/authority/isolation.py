#!/usr/bin/env python3
"""Repo isolation — the two-fleet demonstration (issue #150).

Acceptance criterion: *two repos' fleets are demonstrated to be fully isolated —
no shared state, no cross-repo writes — with the enterprise controller as the
only cross-repo actor.*

This module makes the claim observable instead of asserted:

* :class:`RepoStateStore` keeps **one independent store per repo** (distinct
  objects, no shared container). Every read and write goes through
  ``model.can_act(..., action="state")``, so a fleet that is not scoped to a repo
  cannot reach its state at all — the denial happens before the store is
  touched, and the untouched snapshot proves it.
* :func:`verify_isolation` runs the scenario and *derives* the verdict from what
  the engine actually returned: cross-repo writes denied, the target repo's
  state byte-identical before and after the attempt, a fleet's key listing
  containing only its own repo's keys, and the enterprise controller as the only
  principal whose allowed actions span more than one repo. Findings — not
  assertions in the happy path — decide ``IsolationReport.isolated``, so a
  weakened scoping rule shows up as a finding rather than a passing run.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PKG_DIR = Path(__file__).resolve().parent
if str(PKG_DIR) not in sys.path:  # standalone module, no package __init__.py
    sys.path.insert(0, str(PKG_DIR))

import model  # noqa: E402  (import after the sys.path bootstrap on purpose)

Matrix = model.Matrix
Decision = model.Decision


@dataclass
class RepoState:
    """One repo's own state — never shared with another repo's store."""

    repo: str
    values: Dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> Dict[str, Any]:
        return dict(self.values)

    def keys(self) -> List[str]:
        return sorted(self.values)


class RepoStateStore:
    """Per-repo state whose every access is gated by the authority matrix.

    A store is created with one independent :class:`RepoState` per declared repo.
    Reads/writes require ``action="state"`` authority on exactly that repo, so an
    un-scoped principal is denied before any mutation can happen.
    """

    def __init__(self, matrix: Matrix) -> None:
        self._matrix = matrix
        self._states: Dict[str, RepoState] = {one.id: RepoState(one.id) for one in matrix.repos}

    @property
    def repos(self) -> Tuple[str, ...]:
        return tuple(self._states)

    def state_sharing_ok(self) -> bool:
        """True when every repo holds its own store object (no shared container)."""
        identities = {id(state.values) for state in self._states.values()}
        return len(identities) == len(self._states)

    def snapshot(self, repo: str) -> Dict[str, Any]:
        """Inspector view for the gate/report only — never reachable by an actor."""
        state = self._states.get(repo)
        return {} if state is None else state.snapshot()

    def write(self, principal: str, repo: str, key: str, value: Any) -> Decision:
        gate = model.can_act(self._matrix, principal, repo, "state")
        if not gate.ok:
            return gate
        self._states[repo].values[key] = value
        return model.allow(f"{principal} wrote {key!r} in {repo}", principal=principal, repo=repo, key=key)

    def read(self, principal: str, repo: str, key: str) -> Tuple[Decision, Optional[Any]]:
        gate = model.can_act(self._matrix, principal, repo, "state")
        if not gate.ok:
            return gate, None
        return model.allow(f"{principal} read {key!r} in {repo}", principal=principal, repo=repo, key=key), (
            self._states[repo].values.get(key)
        )

    def keys(self, principal: str, repo: str) -> Tuple[Decision, List[str]]:
        gate = model.can_act(self._matrix, principal, repo, "state")
        if not gate.ok:
            return gate, []
        return model.allow(f"{principal} listed {repo}", principal=principal, repo=repo), self._states[repo].keys()


@dataclass(frozen=True)
class IsolationStep:
    """One probed interaction and the verdict the engine returned for it."""

    label: str
    principal: str
    repo: str
    operation: str
    verdict: str
    reason: str
    detail: str = ""

    def render(self) -> str:
        line = f"  {self.verdict:<14} {self.label}: {self.principal} {self.operation} {self.repo}"
        return f"{line} ({self.reason})"


@dataclass(frozen=True)
class IsolationReport:
    """The demonstrated isolation state of a matrix."""

    repos: Tuple[str, ...]
    cross_repo_principals: Tuple[str, ...]
    steps: Tuple[IsolationStep, ...]
    findings: Tuple[str, ...]
    allowed_cross_repo_actions: Tuple[str, ...]

    @property
    def isolated(self) -> bool:
        return not self.findings

    def to_dict(self) -> Dict[str, Any]:
        return {
            "isolated": self.isolated,
            "repos": list(self.repos),
            "cross_repo_principals": list(self.cross_repo_principals),
            "allowed_cross_repo_actions": list(self.allowed_cross_repo_actions),
            "findings": list(self.findings),
            "steps": [
                {
                    "label": one.label,
                    "principal": one.principal,
                    "repo": one.repo,
                    "operation": one.operation,
                    "verdict": one.verdict,
                    "reason": one.reason,
                    "detail": one.detail,
                }
                for one in self.steps
            ],
        }


def _first_actor(matrix: Matrix, principal_id: str, posture: str) -> Optional[str]:
    principal = matrix.principal(principal_id)
    if principal is None:
        return None
    for actor in principal.actors:
        if actor.posture == posture:
            return actor.id
    return principal.id


def _step(label: str, principal: str, repo: str, operation: str, decision: Decision) -> IsolationStep:
    return IsolationStep(
        label=label,
        principal=principal,
        repo=repo,
        operation=operation,
        verdict=decision.verdict.value,
        reason=decision.reason,
        detail=decision.detail,
    )


def verify_isolation(matrix: Matrix) -> IsolationReport:
    """Run the two-repo scenario and derive whether the repos are isolated."""
    findings: List[str] = []
    steps: List[IsolationStep] = []

    fleets = [one for one in matrix.principals if one.kind == "repo-fleet"]
    cross_repo = tuple(sorted(one.id for one in matrix.principals if one.cross_repo))
    store = RepoStateStore(matrix)

    if len(matrix.repos) < 2:
        findings.append(
            f"fewer than two repos declared ({len(matrix.repos)}) — isolation between two repos cannot be "
            "demonstrated"
        )
    if not cross_repo:
        findings.append("no cross_repo principal is declared — roll-up has no authority holder")

    if not store.state_sharing_ok():
        findings.append("two repos share one state container object — state is not per repo")

    if len(fleets) >= 2:
        left, right = fleets[0], fleets[1]
        left_repo, right_repo = left.scope[0], right.scope[0]
        left_actor = _first_actor(matrix, left.id, "executor") or left.id
        right_actor = _first_actor(matrix, right.id, "executor") or right.id

        own_write = store.write(left_actor, left_repo, "claim:issue-1", {"by": left_actor})
        steps.append(_step("own-repo state write", left_actor, left_repo, "write", own_write))
        if not own_write.ok:
            findings.append(
                f"fleet {left.id!r} cannot write its OWN repo state {left_repo!r} ({own_write.reason}) — the "
                "scoping rule is denying inside the scope"
            )

        before = store.snapshot(left_repo)
        foreign_write = store.write(right_actor, left_repo, "claim:issue-2", {"by": right_actor})
        steps.append(_step("cross-repo state write", right_actor, left_repo, "write", foreign_write))
        if foreign_write.ok:
            findings.append(
                f"cross-repo write allowed: {right_actor!r} wrote {left_repo!r} arbitrary state — repos are "
                "NOT isolated"
            )
        after = store.snapshot(left_repo)
        if after != before:
            findings.append(
                f"{left_repo!r} state changed across a DENIED cross-repo write — the denial is not "
                "side-effect free"
            )

        own_read, _ = store.read(left_actor, left_repo, "claim:issue-1")
        steps.append(_step("own-repo state read", left_actor, left_repo, "read", own_read))
        if not own_read.ok:
            findings.append(f"fleet {left.id!r} cannot read its OWN repo state ({own_read.reason})")

        foreign_read, value = store.read(right_actor, left_repo, "claim:issue-1")
        steps.append(_step("cross-repo state read", right_actor, left_repo, "read", foreign_read))
        if foreign_read.ok or value is not None:
            findings.append(
                f"cross-repo read allowed: {right_actor!r} read {left_repo!r} state — repos are NOT isolated"
            )

        foreign_keys, keys = store.keys(right_actor, left_repo)
        steps.append(_step("cross-repo state listing", right_actor, left_repo, "list", foreign_keys))
        if foreign_keys.ok and keys:
            findings.append(f"cross-repo key listing allowed for {right_actor!r} on {left_repo!r}")

        own_keys_decision, own_keys = store.keys(left_actor, left_repo)
        steps.append(_step("own-repo state listing", left_actor, left_repo, "list", own_keys_decision))
        if own_keys_decision.ok and set(own_keys) - {"claim:issue-1"}:
            findings.append(f"{left_actor!r} sees foreign keys in its own repo: {sorted(own_keys)}")

        rollup = model.can_act(matrix, right_actor, right_repo, "rollup")
        steps.append(_step("fleet roll-up attempt", right_actor, right_repo, "rollup", rollup))
        if rollup.ok:
            findings.append(
                f"fleet actor {right_actor!r} was allowed the cross-repo roll-up right — the enterprise "
                "controller must be the only cross-repo actor"
            )

    # --- the cross-repo sweep: every principal, every repo, every action -----
    allowed_cross_repo: List[str] = []
    for principal in matrix.principals:
        actor_ids = [actor.id for actor in principal.actors] or [principal.id]
        for actor_id in actor_ids:
            repos_allowed = set()
            for repo in matrix.repos:
                for action in model.ACTIONS:
                    decision = model.can_act(matrix, actor_id, repo.id, action)
                    if decision.ok:
                        repos_allowed.add(repo.id)
                        if repo.id not in principal.scope:
                            findings.append(
                                f"authority leak: {actor_id!r} was ALLOWed {action!r} on {repo.id!r} outside "
                                f"its scope {list(principal.scope)}"
                            )
            if len(repos_allowed) > 1:
                allowed_cross_repo.append(f"{actor_id} -> {sorted(repos_allowed)}")
                if not principal.cross_repo:
                    findings.append(
                        f"cross-repo actor without the cross_repo flag: {actor_id!r} acted on "
                        f"{sorted(repos_allowed)}"
                    )

    enterprise = [one for one in matrix.principals if one.cross_repo]
    for one in enterprise:
        for repo in matrix.repos:
            decision = model.can_act(matrix, one.id, repo.id, "files")
            steps.append(_step("enterprise roll-up reach", one.id, repo.id, "files", decision))
            if not decision.ok:
                findings.append(
                    f"the enterprise controller {one.id!r} cannot reach {repo.id!r} ({decision.reason}) — the "
                    "only cross-repo actor must be able to roll up across every repo"
                )

    return IsolationReport(
        repos=tuple(one.id for one in matrix.repos),
        cross_repo_principals=cross_repo,
        steps=tuple(steps),
        findings=tuple(findings),
        allowed_cross_repo_actions=tuple(sorted(allowed_cross_repo)),
    )
