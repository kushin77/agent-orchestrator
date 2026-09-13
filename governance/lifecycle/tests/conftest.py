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

#: A fixed 40-hex commit used where a real sha would sit.
HEAD_COMMIT = "a" * 40
MERGE_COMMIT = "b" * 40


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
    close-out does when a step *cannot* be completed.
    """

    def __init__(self, item: dict, fail: tuple[str, ...] = ()) -> None:
        self.item = item
        self.fail = set(fail)
        self.calls: list[str] = []

    def _record(self, action: str) -> None:
        self.calls.append(action)
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
