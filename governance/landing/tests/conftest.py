"""Pytest bootstrap + offline fixtures for the governance/landing suite (issue #764).

The suite is fully offline: the effects a landing performs are injected, so the
tests never run `git`, `gh` or the closure CLI. Everything here is a recording
double — a landed lane is simulated by *configuring* the port, and the control
asserts what the engine asked it to do, in order.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance.landing import evidence as evidence_mod  # noqa: E402
from governance.landing.engine import LandingEngine, LandingRequest  # noqa: E402
from governance.landing.ports import CommandResult, PullRequest  # noqa: E402

HEAD = "a" * 40
PARENT = "b" * 40


def write_attestation(path: Path, *, result: str = "PASS", rc: int = 0, commit: str = HEAD, verified_by: str | None = "the-verifying-agent") -> Path:
    """Write a merge attestation in the shape ``scripts/merge-gate.sh`` writes.

    ``verified_by=None`` omits the field, which is the negative-control shape:
    an attestation that names no verifier must be refused (AO-GR-13).
    """
    payload = {
        "gate": "merge-gate",
        "result": result,
        "exit_code": rc,
        "commit": commit,
        "branch": "issue-764",
        "timestamp": "2026-09-14T00:00:00Z",
    }
    if verified_by is not None:
        payload["verified_by"] = verified_by
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload)
        + "\n",
        encoding="utf-8",
    )
    return path


class FakeOps:
    """A recording landing port: no subprocess, no network, no repository."""

    def __init__(
        self,
        *,
        root: Path,
        head: str = HEAD,
        pr: PullRequest | None = None,
        contract_rc: int = 0,
        contract_commit: str | None = None,
        contract_output: str = "MERGE-GATE: PASS",
        landed_contract_rc: int = 0,
        landed_contract_output: str = "check-pr-contract: LANDED OK — every merged commit since the enforcement gate carries the ticket trailer",
        closure_rc: int = 0,
        subjects: tuple = ("feat(landing): the lane's own commit subject",),
        publish_status_rc: int = 0,
        publish_status_raises: Exception | None = None,
    ) -> None:
        self.root = Path(root)
        self.attestation_path = self.root / evidence_mod.ATTESTATION_REL
        self._head = head
        self._pr = pr
        self._contract_rc = contract_rc
        self._contract_commit = contract_commit
        self._contract_output = contract_output
        self._landed_contract_rc = landed_contract_rc
        self._landed_contract_output = landed_contract_output
        self._closure_rc = closure_rc
        self._subjects = tuple(subjects)
        self._publish_status_rc = publish_status_rc
        self._publish_status_raises = publish_status_raises
        self.calls: list = []
        self.pushed = False
        self.body = ""
        self.squash_body = ""
        self.published_statuses: list = []

    # -- reads ---------------------------------------------------------------
    def head_commit(self) -> str:
        return self._head

    def latest_subject(self, rev: str) -> str:
        return self._subjects[0] if self._subjects else rev

    def commit_subjects(self, base: str, rev: str) -> tuple:
        return self._subjects

    def remote_branch_head(self, branch: str):
        return self._head if self.pushed else None

    def pull_request_for(self, branch: str):
        return self._pr

    # -- effects -------------------------------------------------------------
    def push(self, branch: str) -> str:
        self.calls.append(("push", branch))
        self.pushed = True
        return f"git push -u origin {branch}"

    def open_pr(self, *, branch: str, base: str, title: str, body_file: Path) -> PullRequest:
        self.calls.append(("open-pr", branch))
        self.body = Path(body_file).read_text(encoding="utf-8")
        self._pr = PullRequest(number=11, state="OPEN", head=self._head, base=base, title=title)
        return self._pr

    def run_contract(self, *, pr_number) -> CommandResult:
        self.calls.append(("contract", str(pr_number)))
        commit = self._contract_commit or self._head
        if self._contract_rc == 0:
            write_attestation(self.attestation_path, result="PASS", rc=0, commit=commit)
        else:
            write_attestation(self.attestation_path, result="NOT-OK", rc=self._contract_rc, commit=commit)
        return CommandResult(
            argv=("bash", "scripts/merge-gate.sh", "run"), rc=self._contract_rc, stdout=self._contract_output
        )

    def publish_status(self, *, sha: str, rc: int) -> CommandResult:
        self.calls.append(("gate-status", f"{sha}:{rc}"))
        self.published_statuses.append((sha, rc))
        if self._publish_status_raises is not None:
            raise self._publish_status_raises
        return CommandResult(
            argv=("bash", "scripts/gate-status.sh", "post"),
            rc=self._publish_status_rc,
            stdout=f"gate-status: posted ao/gate-of-record for {sha[:12]} (rc={rc})",
        )

    def check_landed_contract(self, *, base: str, head: str) -> CommandResult:
        self.calls.append(("landed-contract", f"{base}..{head}"))
        return CommandResult(
            argv=("bash", "scripts/check-pr-contract.sh", "--landed"),
            rc=self._landed_contract_rc,
            stdout=self._landed_contract_output,
        )

    def merge_pr(self, number: int, *, subject: str, body_file: Path) -> str:
        self.calls.append(("merge", str(number)))
        self.squash_body = Path(body_file).read_text(encoding="utf-8")
        return "c" * 40

    def delete_branch(self, branch: str) -> str:
        self.calls.append(("delete-branch", branch))
        return f"git push origin --delete {branch}"

    def close_lifecycle(self, issue: int) -> CommandResult:
        self.calls.append(("lifecycle-close", str(issue)))
        return CommandResult(
            argv=("python3", "governance/lifecycle/cli.py", "close"), rc=self._closure_rc, stdout="closure (fixture)"
        )


@pytest.fixture
def request_factory(tmp_path):
    """Build a LandingRequest rooted in a scratch dir."""

    def _make(**overrides) -> LandingRequest:
        params = {
            "issue": 764,
            "root": tmp_path,
            "author": "copilot-lane",
            "title": "feat(landing): the driver",
            "ai_assistance": "Copilot (Relentless, flash/LOW)",
            "apply": False,
        }
        params.update(overrides)
        return LandingRequest(**params)

    return _make


@pytest.fixture
def run_landing():
    """Land with a configured port + request, returning (result, ops)."""

    def _run(ops: FakeOps, request: LandingRequest, *, recording=None):
        engine = LandingEngine(recording or ops, request)
        return engine.land()

    return _run
