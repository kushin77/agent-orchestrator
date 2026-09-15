"""Pytest bootstrap + fixtures for the governance/lifecycle suite (issue #269).

``governance/`` is a PEP-420 namespace package, so the repository root goes on
``sys.path`` and the modules are imported qualified (``governance.lifecycle.*``).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from governance.lifecycle import gate  # noqa: E402

#: A fixed 40-hex commit used where a real sha would sit.
HEAD_COMMIT = "a" * 40
MERGE_COMMIT = "b" * 40

#: What a parked gate prints: the admission control's refusal (``fleet/gatelock.py``)
#: beside ``scripts/verify.sh``'s own sentence. Built from the code and the cap rather
#: than paraphrased, so a fixture cannot drift from the words the consumer must name
#: when it reports that nothing was measured (#840).
def parked_output(exit_code: int = 11, cap: int = 4) -> str:
    """The refusal a parked gate prints, for the code and cap that parked it."""
    reason = (
        "another gate already holds this worktree"
        if exit_code == 10
        else "the box-wide gate cap is reached"
    )
    return (
        f"gate-lock: PARKED — the box-wide gate cap ({cap}) is reached; holders: pid 4242\n"
        f"verify: PARKED (rc {exit_code}, not a pass and not a failure) — {reason}; "
        "nothing was run and no attestation was touched\n"
        f"make: *** [Makefile:146: verify] Error {exit_code}\n"
    )


#: Measured on this box (2026-09-15): GNU make exits **2** for *any* failing recipe
#: and merely prints the recipe's own code, so a parked or failed gate reaches a
#: ``make verify`` caller as rc 2 and the outcome has to be read from the transcript.
MAKE_FAILURE_EXIT = 2


PARKED_OUTPUT = parked_output()


def transcript(kind: str, *, code: int = 11, cap: int = 4) -> str:
    """What the gate really prints, for each outcome, including make's error line."""
    if kind == "parked":
        return parked_output(code, cap)
    if kind == "failed":
        return (
            "== 1. shell-syntax ==\n"
            "  FAIL  scripts/thing.sh (a real check that really failed)\n"
            "verify: FAIL (1 of 120 checks failed)\n"
            "make: *** [Makefile:146: verify] Error 1\n"
        )
    if kind == "passed":
        return "verify: PASS (120 of 120 checks)\n"
    raise AssertionError(f"unknown transcript kind {kind!r}")


def make_run(kind: str, *, code: int = 11, cap: int = 4, retries: int = 0, wait: float = 0.0):
    """A real ``GateRun`` for one outcome *as make reports it*: code AND transcript."""
    exit_code = 0 if kind == "passed" else MAKE_FAILURE_EXIT
    text = transcript(kind, code=code, cap=cap)
    return gate.run_gate(
        lambda: gate.GateAttempt(exit_code=exit_code, output=text),
        retries=retries,
        wait=wait,
        sleep=lambda _seconds: None,
    )


def gate_run(exit_codes, *, output: str = "", retries: int = 0, wait: float = 0.0):
    """A real ``GateRun`` over exit codes, with no verdict line unless one is given.

    The fallback path: a caller that propagates the code but says nothing, or a run
    that died before printing a verdict. Built through ``gate.run_gate`` itself, so a
    test asserts the module's own retry policy and detail text instead of a paraphrase
    of them — and no test waits for a real permit.
    """
    codes = list(exit_codes)
    return gate.run_gate(
        lambda: gate.GateAttempt(exit_code=codes.pop(0), output=output),
        retries=retries,
        wait=wait,
        sleep=lambda _seconds: None,
    )


def parked_verification(exit_code: int = 11, *, retries: int = 0, wait: float = 0.0):
    """The exception the real port raises for a park, as make really reports it."""
    run = make_run("parked", code=exit_code, retries=retries, wait=wait)
    return gate.CannotAssess(run.verdict, run.detail(), run.remediation())


def clean_item(**overrides) -> dict:
    """A work item that closed hygienically — the baseline every test mutates."""
    item = {
        "issue": 269,
        "title": "End-to-end GitHub lifecycle",
        "state": "closed",
        "milestone": "M26 - Session Fleet Operating Model",
        "labels": ["class:elite", "pillar:autonomous-ops"],
        "pr": {
            "number": 271,
            "state": "merged",
            "branch": "issue-269",
            "head_commit": HEAD_COMMIT,
            "merge_commit": MERGE_COMMIT,
        },
        "branch_deleted": True,
        "claim": {"agent": None, "live": False},
        "directive": {"id": "d-269", "state": "done"},
        "lane": {},
        "verify": {"ok": True, "commit": HEAD_COMMIT},
        "closing_evidence": True,
    }
    item.update(overrides)
    return item


def record(*items: dict, tracking: dict | None = None, scope: str = "test fixture") -> dict:
    """A lifecycle record around the given items."""
    return {
        "scope": scope,
        "items": list(items),
        "tracking": tracking or {},
    }


class FakeOps:
    """A close-out operations port that mutates the item, so re-audit sees truth.

    Failures are injectable per action, because the interesting behaviour is what
    close-out does when a step *cannot* be completed; an action can also be made
    unassessable, which is a different outcome and must produce a different verdict
    (``closeout.py``, #840).
    """

    def __init__(
        self,
        item: dict,
        fail: tuple[str, ...] = (),
        cannot_assess: dict[str, gate.CannotAssess] | None = None,
    ) -> None:
        self.item = item
        self.fail = set(fail)
        self.cannot_assess = dict(cannot_assess or {})
        self.calls: list[str] = []

    def _record(self, action: str) -> None:
        self.calls.append(action)
        if action in self.cannot_assess:
            raise self.cannot_assess[action]
        if action in self.fail:
            raise RuntimeError(f"{action} refused by the fixture")

    def merge_pull_request(self, number: int) -> str:
        self._record("merge-pull-request")
        self.item["pr"]["state"] = "merged"
        return MERGE_COMMIT

    def record_verification(self, issue: int, commit: str) -> str:
        self._record("record-verification")
        self.item["verify"] = {"ok": True, "commit": commit}
        return f"verify green at {commit[:12]}"

    def delete_branch(self, branch: str) -> str:
        self._record("delete-branch")
        self.item["branch_deleted"] = True
        return f"deleted {branch}"

    def consume_directive(self, directive_id: str) -> str:
        self._record("consume-directive")
        self.item["directive"]["state"] = "done"
        return f"consumed {directive_id}"

    def release_claim(self, issue: int, agent: str) -> str:
        self._record("release-claim")
        self.item["claim"] = {"agent": None, "live": False}
        return "released"

    def record_closing_evidence(self, issue: int, evidence: str) -> str:
        self._record("record-closing-evidence")
        self.item["closing_evidence"] = True
        return "evidence journalled"

    def close_issue(self, issue: int, evidence: str) -> str:
        self._record("close-issue")
        self.item["state"] = "closed"
        return "closed"

    def reclaim_lane(self, session_id: str) -> str:
        self._record("reclaim-lane")
        self.item["lane"] = {}
        return f"reclaimed {session_id}"

    def refresh(self, item: dict) -> dict:
        """The mutated item is the post-close state, so it is what gets re-audited."""
        return self.item
