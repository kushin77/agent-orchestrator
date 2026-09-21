#!/usr/bin/env python3
"""The evidence a landing demands: a green, commit-named merge attestation (#764).

---knowledge---
module_id: governance.landing.evidence
system: governance
app: landing
solution_class: enterprise
patterns: [honesty-tri-state]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [Attestation, read_attestation, write_master_attestation, same_commit, Gap, evidence_gap]
invariants: ""
gotchas: ""
related: ["#764", "#1114"]
do_not_duplicate: null
---knowledge---

``scripts/merge-gate.sh run`` is the executable pre-merge contract (issue #29);
on green it writes ``.verify/merge-attestation.json`` naming the commit it
attested. This module *reads* that artifact and answers one question for the
landing driver:

    is this attestation green evidence for the commit being landed?

The answer is a **named gap** or nothing — a closed vocabulary, so the refusal
can say what it checked instead of "not mergeable". The vocabulary mirrors the
honesty tri-state the rest of the repo consumes (issue #28: OK / NOT-OK /
CANNOT-ASSESS, where CANNOT-ASSESS is never a pass) and the merge gate's own
refusal reason ("an attestation names a COMMIT"):

* absent / unreadable / no verdict  -> CANNOT-ASSESS (never a pass);
* present with a nonzero verdict    -> NOT-OK (a red lane must not merge);
* green but naming no commit        -> NOT-OK (an attestation names a COMMIT);
* green, naming a different commit  -> NOT-OK (the attestation must name the
  commit actually being merged, not merely some commit);
* green and commit-named but naming NO verifier -> NOT-OK: the attestation must
  say WHO verified it (AO-GR-13 — "never by the author alone", which is
  checkable only if the artifact records the verifying party).

The commit comparison tolerates a short SHA either side (GitHub prints short
SHAs; the gate writes the full one) but never a *different* commit.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

#: Where ``scripts/merge-gate.sh run`` writes the attestation (repo-relative).
ATTESTATION_REL = Path(".verify") / "merge-attestation.json"

#: Where the landing engine publishes master's own health after a successful
#: squash-merge (RCA 2026-09-17 fix #5, #1114) — `fleet/brain.py`'s dispatch
#: pre-check reads this same path/schema before opening a new lane's PR, so a
#: red (or stale, head-mismatched) master is never inherited by new work.
MASTER_ATTESTATION_REL = Path(".fleet") / "master-attestation.json"

STATE_READ = "read"
STATE_ABSENT = "absent"
STATE_UNREADABLE = "unreadable"

GAP_ABSENT = "attestation-absent"
GAP_UNREADABLE = "attestation-unreadable"
GAP_NO_VERDICT = "attestation-has-no-verdict"
GAP_NOT_GREEN = "attestation-not-green"
GAP_UNNAMED_COMMIT = "attestation-does-not-name-a-commit"
GAP_OTHER_COMMIT = "attestation-names-another-commit"
GAP_UNATTRIBUTED = "attestation-does-not-name-a-verifier"

#: The result string the merge gate writes -> the exit code it means.
RESULTS = {"PASS": 0, "NOT-OK": 1, "CANNOT-ASSESS": 2}

#: Gaps that mean "no verdict could be reached", i.e. the honesty tri-state's
#: CANNOT-ASSESS. Everything else is a NOT-OK refusal with real evidence behind
#: it. Kept explicit so the exit code is a fact about the gap, not a guess.
CANNOT_ASSESS_GAPS = (GAP_ABSENT, GAP_UNREADABLE, GAP_NO_VERDICT)


@dataclass(frozen=True)
class Attestation:
    """One read of a merge attestation, whether or not it was readable."""

    path: Path
    state: str
    result: str = ""
    rc: Optional[int] = None
    commit: Optional[str] = None
    branch: str = ""
    timestamp: str = ""
    verified_by: str = ""
    detail: str = ""

    @property
    def readable(self) -> bool:
        return self.state == STATE_READ

    @property
    def green(self) -> bool:
        return self.rc == 0

    def as_dict(self) -> dict:
        return {
            "path": str(self.path),
            "state": self.state,
            "result": self.result,
            "exit_code": self.rc,
            "commit": self.commit,
            "branch": self.branch,
            "timestamp": self.timestamp,
            "verified_by": self.verified_by,
            "detail": self.detail,
        }


def _rc_from_payload(payload: dict) -> Optional[int]:
    """The exit code the attestation attests, or None when it carries no verdict.

    ``exit_code`` wins when it is a real integer; otherwise the ``result``
    string is mapped through the closed :data:`RESULTS` table. An attestation
    whose result is neither (a truncated write, a hand-edited file) has no
    verdict — which is CANNOT-ASSESS, never a pass.
    """
    exit_code = payload.get("exit_code")
    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
        return exit_code
    return RESULTS.get(str(payload.get("result") or "").strip().upper())


def _commit_from_payload(payload: dict) -> tuple[Optional[str], str]:
    """The attested commit (None when unnamed) and the value that was read."""
    raw = payload.get("commit")
    if not isinstance(raw, str):
        return None, "" if raw is None else repr(raw)
    text = raw.strip()
    if not text or text.lower() in ("null", "none"):
        return None, text
    return text, text


def read_attestation(path: Path) -> Attestation:
    """Read the attestation at ``path``; never raises on a bad or missing file.

    A missing or unreadable attestation is *data* here — the caller reports it
    by name rather than treating it as an exception, because "no attestation"
    is exactly the state a fresh lane is in before the contract has run.
    """
    if not path.is_file():
        return Attestation(path=path, state=STATE_ABSENT, detail="no such file")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return Attestation(path=path, state=STATE_UNREADABLE, detail=f"{type(exc).__name__}: {exc}")
    try:
        payload = json.loads(text)
    except ValueError as exc:
        return Attestation(path=path, state=STATE_UNREADABLE, detail=f"not valid JSON ({exc})")
    if not isinstance(payload, dict):
        return Attestation(
            path=path,
            state=STATE_UNREADABLE,
            detail=f"expected a JSON object, found {type(payload).__name__}",
        )
    commit, commit_note = _commit_from_payload(payload)
    raw_verifier = payload.get("verified_by")
    verified_by = raw_verifier.strip() if isinstance(raw_verifier, str) else ""
    if verified_by.lower() == "unknown":
        verified_by = ""
    detail = "" if commit is not None else f"commit field read as {commit_note or 'absent'}"
    return Attestation(
        path=path,
        state=STATE_READ,
        result=str(payload.get("result") or ""),
        rc=_rc_from_payload(payload),
        commit=commit,
        branch=str(payload.get("branch") or ""),
        timestamp=str(payload.get("timestamp") or ""),
        verified_by=verified_by,
        detail=detail,
    )


def write_master_attestation(path: Path, attestation: Attestation, *, commit: str) -> Path:
    """Publish master's own health after a successful squash-merge (fix #5).

    Reuses the SAME shape :func:`read_attestation` reads — no second schema —
    with ``commit`` overridden to the sha that is actually landing on
    ``origin/master`` (the squash merge commit ``merge_pr`` returns; the
    caller falls back to the pre-flight attestation's own commit when the
    port cannot name one). The write is atomic (tmp file + ``os.replace``) so
    a reader can never observe a half-written file — the classic split-write
    hazard for a file another process polls on a timer.
    """
    payload = attestation.as_dict()
    payload["commit"] = commit
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def same_commit(left: Optional[str], right: Optional[str]) -> bool:
    """True when both names resolve to the same commit (short SHA either side).

    A short SHA is only accepted from 7 characters up; below that the name is
    too weak to be evidence of identity, so it is a mismatch.
    """
    if not left or not right:
        return False
    a = left.strip().lower()
    b = right.strip().lower()
    if a == b:
        return True
    if len(a) < 7 or len(b) < 7:
        return False
    return a.startswith(b) or b.startswith(a)


@dataclass(frozen=True)
class Gap:
    """A named reason the attestation is not evidence for this commit."""

    code: str
    detail: str

    @property
    def cannot_assess(self) -> bool:
        return self.code in CANNOT_ASSESS_GAPS

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


def evidence_gap(attestation: Attestation, commit: str) -> Optional[Gap]:
    """Why this attestation is not green evidence for ``commit``, or None.

    The order is the order of the contract itself: the verdict first (a red
    gate is refused for being red, not for a missing commit line), then the
    commit-naming rule, then identity.
    """
    if attestation.state == STATE_ABSENT:
        return Gap(GAP_ABSENT, f"no attestation at {attestation.path} — the pre-merge contract has not attested this tree")
    if attestation.state == STATE_UNREADABLE:
        return Gap(GAP_UNREADABLE, f"{attestation.path} could not be read ({attestation.detail})")
    if attestation.rc is None:
        return Gap(GAP_NO_VERDICT, f"{attestation.path} carries no verdict (result={attestation.result!r})")
    if attestation.rc != 0:
        return Gap(
            GAP_NOT_GREEN,
            f"the attestation attests {attestation.result or 'a failure'} "
            f"(exit_code={attestation.rc}, commit={attestation.commit or 'unnamed'})",
        )
    if attestation.commit is None:
        return Gap(
            GAP_UNNAMED_COMMIT,
            f"the attestation is green but names no commit — an attestation names a COMMIT ({attestation.detail})",
        )
    if not same_commit(attestation.commit, commit):
        return Gap(
            GAP_OTHER_COMMIT,
            f"the attestation names {attestation.commit} but the lane is landing {commit}",
        )
    if not attestation.verified_by:
        return Gap(
            GAP_UNATTRIBUTED,
            "the attestation is green and names the commit but names NO verifier — "
            "an attestation must say who verified it (AO-GR-13: never by the author alone)",
        )
    return None
