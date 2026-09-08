"""Guard attestation (issue #28, acceptance criterion 4).

A guard verdict is recorded as an attestation -- evidence, not vibes.  Each
attestation captures the guard identity, the tri-state verdict, the raw exit
code, the actual output that produced the verdict, the UTC timestamp, the git
identity of the checkout when available, provenance of the guard, and the
negative controls that prove the guard can actually fail.

Attestations are attached to every merge/verdict: a merge on a guard whose
verdict is CANNOT-ASSESS, or whose evidence is empty, is a merge without
evidence and is rejected by policy (no-false-green, AO-GR-3/AO-GR-4).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .tristate import TriState, from_exit_code, serialize


def _git(cmd: List[str]) -> str:
    """Best-effort git identity lookup; empty string when unavailable."""
    try:
        out = subprocess.run(
            ["git"] + cmd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


@dataclass
class GuardAttestation:
    """An immutable evidence record for one guard verdict."""

    guard_id: str
    verdict: TriState
    exit_code: int
    evidence: str
    timestamp: str
    git_sha: str = ""
    branch: str = ""
    provenance: str = ""
    controls: List[str] = field(default_factory=list)

    # -- construction ----------------------------------------------------
    @classmethod
    def record(
        cls,
        guard_id: str,
        exit_code: int,
        evidence: str,
        *,
        provenance: str = "",
        controls: Optional[List[str]] = None,
        timestamp: Optional[str] = None,
        git_sha: Optional[str] = None,
        branch: Optional[str] = None,
    ) -> "GuardAttestation":
        """Build an attestation from a raw guard run.

        The verdict is derived from the exit code through the tri-state
        contract (:func:`honesty.tristate.from_exit_code`) -- an attestation
        never declares a verdict by assertion alone.
        """
        ts = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return cls(
            guard_id=guard_id,
            verdict=from_exit_code(exit_code),
            exit_code=exit_code,
            evidence=evidence,
            timestamp=ts,
            git_sha=git_sha if git_sha is not None else _git(["rev-parse", "HEAD"]),
            branch=branch if branch is not None else _git(["branch", "--show-current"]),
            provenance=provenance,
            controls=list(controls or []),
        )

    # -- serialization ---------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "guard_id": self.guard_id,
            "verdict": serialize(self.verdict),
            "exit_code": self.exit_code,
            "evidence": self.evidence,
            "timestamp": self.timestamp,
            "git_sha": self.git_sha,
            "branch": self.branch,
            "provenance": self.provenance,
            "controls": list(self.controls),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "GuardAttestation":
        verdict = TriState(payload["verdict"]["status"])
        return cls(
            guard_id=payload["guard_id"],
            verdict=verdict,
            exit_code=int(payload["exit_code"]),
            evidence=payload["evidence"],
            timestamp=payload["timestamp"],
            git_sha=payload.get("git_sha", ""),
            branch=payload.get("branch", ""),
            provenance=payload.get("provenance", ""),
            controls=list(payload.get("controls", [])),
        )

    @classmethod
    def from_json(cls, document: str) -> "GuardAttestation":
        return cls.from_dict(json.loads(document))

    # -- policy helpers ---------------------------------------------------
    @property
    def attested(self) -> bool:
        """An attestation attests only when it can produce a real verdict.

        CANNOT-ASSESS with an empty or non-productive run is not attestation;
        a merge on such a record is a merge without evidence.
        """
        if self.verdict is TriState.CANNOT_ASSESS:
            return False
        return bool(self.evidence.strip())

    def summary(self) -> str:
        controls = ",".join(self.controls) if self.controls else "-"
        return (
            f"guard={self.guard_id} verdict={self.verdict.value} "
            f"exit={self.exit_code} controls=[{controls}] evidence={len(self.evidence)}B"
        )
