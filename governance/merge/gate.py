#!/usr/bin/env python3
"""Verify-gate model for the merge-governance surface (EPIC-00 issue #43).

Models the repository's pre-merge verification contract (issue #29,
``scripts/merge-gate.sh``) as an injectable, offline tri-state check consumed
by the merge-governance engine. This is a *model* of the gate — the live gate
stays ``scripts/merge-gate.sh``; this module only mirrors its semantics so the
governance policy can be exercised offline (no network, no shell).

Semantics mirrored (additive reference to issue #29, never a redefinition):

* **Honesty tri-state (issue #28):** a gate exit code maps to
  OK (0) / NOT-OK (1) / CANNOT-ASSESS (anything else, e.g. 2 or a 124
  timeout). CANNOT-ASSESS is never a pass (AO-GR-4 no-false-green).
* **Attestation names a COMMIT:** ``scripts/merge-gate.sh`` refuses a dirty
  working tree because "an attestation names a COMMIT". A
  :class:`VerifyOutcome` only counts as green when it carries the commit it
  attested — a green check with no commit cannot make a PR mergeable.
* **Composite aggregation:** ``scripts/merge-gate.sh run`` aggregates its five
  signals (verify / drift / tests / negative-controls / policy-schema): any
  NOT-OK fails, any CANNOT-ASSESS keeps the gate from green, only an all-OK
  set reads PASS. :func:`aggregate_exit_codes` mirrors exactly that.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional, Sequence


class GateStatus(str, Enum):
    """The honesty tri-state (issue #28): OK / NOT-OK / CANNOT-ASSESS."""

    OK = "OK"
    NOT_OK = "NOT-OK"
    CANNOT_ASSESS = "CANNOT-ASSESS"


def status_from_exit_code(rc: int) -> GateStatus:
    """Map a gate exit code onto the honesty tri-state (0 / 1 / else)."""
    if rc == 0:
        return GateStatus.OK
    if rc == 1:
        return GateStatus.NOT_OK
    return GateStatus.CANNOT_ASSESS


@dataclass(frozen=True)
class VerifyOutcome:
    """The recorded result of one injected verify-gate run."""

    rc: int
    status: GateStatus
    evidence: str = ""
    commit: Optional[str] = None

    @property
    def is_green(self) -> bool:
        """Green requires an OK verdict AND a commit-named attestation."""
        return self.status is GateStatus.OK and self.commit is not None

    def as_dict(self) -> dict:
        return {
            "rc": self.rc,
            "status": self.status.value,
            "evidence": self.evidence,
            "commit": self.commit,
        }


def outcome_from_exit_code(
    rc: int, commit: Optional[str], evidence: str = ""
) -> VerifyOutcome:
    """Build a :class:`VerifyOutcome` from a raw exit code (injected gate)."""
    return VerifyOutcome(
        rc=rc,
        status=status_from_exit_code(rc),
        evidence=evidence,
        commit=commit,
    )


# A gate check is injectable: it takes the commit under test and returns a
# VerifyOutcome. The engine never runs a real shell gate — callers inject one.
GateCheck = Callable[["object", Optional[str]], VerifyOutcome]


def aggregate_exit_codes(codes: Sequence[int]) -> int:
    """Mirror ``scripts/merge-gate.sh`` run aggregation over per-signal codes.

    Any NOT-OK (1) fails the gate; otherwise any CANNOT-ASSESS (anything not
    0/1) keeps the gate from green; only an all-OK (0) set passes.
    """
    if any(c == 1 for c in codes):
        return 1
    if any(c not in (0, 1) for c in codes):
        return 2
    return 0


def green_gate(commit: Optional[str] = "HEAD") -> GateCheck:
    """A default injected gate that always passes (used by the demo)."""

    def _check(_pr: object, _commit: Optional[str] = None) -> VerifyOutcome:
        return outcome_from_exit_code(
            0, _commit or commit, evidence="make verify + merge-gate green (offline demo)"
        )

    return _check


def red_gate(reason: str = "a check failed") -> GateCheck:
    """An injected gate that always fails — negative-control helper."""

    def _check(_pr: object, _commit: Optional[str] = None) -> VerifyOutcome:
        return outcome_from_exit_code(1, _commit, evidence=reason)

    return _check


def cannot_assess_gate(reason: str = "no verdict reached") -> GateCheck:
    """An injected gate that can never reach a verdict (never a pass)."""

    def _check(_pr: object, _commit: Optional[str] = None) -> VerifyOutcome:
        return outcome_from_exit_code(2, _commit, evidence=reason)

    return _check
