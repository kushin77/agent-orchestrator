"""The real verification port, driven end to end with a stub gate (issue #840).

`closeout`'s tests inject the *outcome*; these drive the port that produces it —
``GhOps.record_verification`` — against a real lane worktree and a stub ``make`` on
``PATH`` that returns the exit code the admission control really returns. That is
the difference between proving the consumer's vocabulary and proving the consumer:
the stub is the only fiction here, and the tree, the git head, the journal write and
the exception all come from the code that ships.

Two properties matter beyond the verdict itself:

* an admitted run records the attestation and *only* then writes the journal;
* a run that did not happen writes **no journal at all**. ``.fleet/lifecycle``'s
  presence is another module's landing record (``governance/reconcile`` reads a
  journal file as "this issue's work landed"), so a parked attempt written there
  would let capacity be read as a landing.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from governance.lifecycle import gate
from governance.lifecycle.cli import GhOps, journal_path

LANE_ISSUE = 840

_PARKED_STDERR = (
    "gate-lock: PARKED — the box-wide gate cap (4) is reached; holders: pid 4242\n"
    "verify: PARKED (rc 11, not a pass and not a failure) — the box-wide gate cap is "
    "reached; nothing was run and no attestation was touched\n"
    "make: *** [Makefile:146: verify] Error 11\n"
)

#: What the stub prints for each outcome, and the code it exits with — **as make
#: really reports it**: GNU make exits 2 for any failing recipe, printing the recipe's
#: own code on its own line. A stub that exited 11 directly would not reproduce the
#: measurement, and would let a consumer that reads only the exit code pass.
_STUB_SNIPPETS = {
    "parked": (
        'printf "%s" "$STUB_GATE_PARKED" >&2\n'
        'printf "make: *** [Makefile:146: verify] Error 11\\n" >&2\n'
        "exit 2\n"
    ),
    "failed": (
        "printf '== 1. shell-syntax ==\\n  FAIL  scripts/thing.sh (a real check)\\n'\n"
        "printf 'verify: FAIL (1 of 120 checks failed)\\n' >&2\n"
        "printf 'make: *** [Makefile:146: verify] Error 1\\n' >&2\n"
        "exit 2\n"
    ),
    "passed": "printf 'verify: PASS (120 of 120 checks)\\n'\nexit 0\n",
    "interrupted": (
        "printf 'make: *** [Makefile:146: verify] Interrupt\\n' >&2\nexit 2\n"
    ),
    "decoy": (
        "printf 'verify: PASS (1 of 1 checks)\\n'\n"
        "printf 'verify: PARKED (rc 11, not a pass and not a failure) — a provocation\\n' >&2\n"
        "printf 'verify: FAIL (1 of 120 checks failed)\\n' >&2\n"
        "printf 'make: *** [Makefile:146: verify] Error 1\\n' >&2\n"
        "exit 2\n"
    ),
}


def _git(worktree: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "lifecycle-test",
        "GIT_AUTHOR_EMAIL": "lifecycle-test@agents.invalid",
        "GIT_COMMITTER_NAME": "lifecycle-test",
        "GIT_COMMITTER_EMAIL": "lifecycle-test@agents.invalid",
    }
    result = subprocess.run(
        ["git", "-C", str(worktree), *args], capture_output=True, text=True, env=env, check=True
    )
    return result.stdout.strip()


def _scratch_root(root: Path) -> tuple[Path, str]:
    """A scratch repository root holding one provisioned lane on a real worktree."""
    worktree = root / "lane"
    worktree.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "issue-840"], cwd=worktree, check=True)
    (worktree / "README.md").write_text("lane\n", encoding="utf-8")
    _git(worktree, "add", "README.md")
    _git(worktree, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "lane head")

    lanes = root / ".fleet" / "lanes"
    lanes.mkdir(parents=True)
    (lanes / "s-840.json").write_text(
        '{"session_id": "s-840", "issue": 840, "worktree": "%s"}\n' % worktree,
        encoding="utf-8",
    )
    return worktree, _git(worktree, "rev-parse", "HEAD")


def _stub_gate(root: Path, monkeypatch, outcome: str, *, retries: int = 0) -> Path:
    """A ``make`` on ``PATH`` reporting one outcome the way the real gate reports it."""
    binary_dir = root / "bin"
    binary_dir.mkdir(exist_ok=True)
    counter = root / "gate-calls"
    stub = binary_dir / "make"
    stub.write_text(
        "#!/usr/bin/env bash\nprintf 'call\\n' >> \"$STUB_GATE_COUNT\"\n" + _STUB_SNIPPETS[outcome],
        encoding="utf-8",
    )
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binary_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("STUB_GATE_COUNT", str(counter))
    monkeypatch.setenv("STUB_GATE_PARKED", _PARKED_STDERR)
    # Bounded on purpose: the default budget would wait for a real permit.
    monkeypatch.setenv("AO_LIFECYCLE_GATE_RETRIES", str(retries))
    monkeypatch.setenv("AO_LIFECYCLE_GATE_RETRY_WAIT", "0")
    return counter


def test_the_real_port_reads_a_parked_gate_as_unassessed(tmp_path, monkeypatch):
    """Demanded case 1, end to end: a real park ⇒ CANNOT-ASSESS naming PARKED.

    The park reaches the consumer as **make's** rc 2 with the gate's own sentence on
    the transcript — the measured shape — so this also proves the verdict comes from
    the transcript and not from the exit code.
    """
    _worktree, head = _scratch_root(tmp_path)
    _stub_gate(tmp_path, monkeypatch, "parked")

    with pytest.raises(gate.CannotAssess) as raised:
        GhOps(root=tmp_path).record_verification(LANE_ISSUE, head)

    assert raised.value.verdict == gate.VERDICT_PARKED
    assert "PARKED" in raised.value.detail
    assert "the gate declared rc 11, the wrapper exited 2" in raised.value.detail
    assert "AO_GATE_MAX_CONCURRENT=4" in raised.value.detail
    assert "retry when capacity is free" in raised.value.remediation
    assert not journal_path(LANE_ISSUE, tmp_path).exists()


def test_a_park_writes_no_journal_because_presence_is_a_landing_record(tmp_path, monkeypatch):
    """A run that did not happen must not create the file reconcile reads as landed."""
    _scratch_root(tmp_path)
    _stub_gate(tmp_path, monkeypatch, "parked")

    with pytest.raises(gate.CannotAssess):
        GhOps(root=tmp_path).record_verification(LANE_ISSUE, "")

    directory = tmp_path / ".fleet" / "lifecycle"
    created = sorted(path.name for path in directory.glob("*.json")) if directory.exists() else []
    assert created == []


def test_the_real_port_still_reports_a_failure_as_a_failure(tmp_path, monkeypatch):
    """Demanded case 2, end to end: a check that ran and failed is not a park.

    Its process code is make's 2, exactly like a park's, so this fails if the
    consumer ever goes back to reading the exit code alone.
    """
    _worktree, head = _scratch_root(tmp_path)
    _stub_gate(tmp_path, monkeypatch, "failed")

    with pytest.raises(RuntimeError) as raised:
        GhOps(root=tmp_path).record_verification(LANE_ISSUE, head)

    assert not isinstance(raised.value, gate.CannotAssess)
    assert "a check failed" in str(raised.value)
    assert "reported a failure" in str(raised.value)
    assert not journal_path(LANE_ISSUE, tmp_path).exists()


def test_the_real_port_reads_the_gates_own_banner_past_a_decoy(tmp_path, monkeypatch):
    """Checks share the transcript, so stream order decides which verdict is real.

    The stub prints a decoy PASS to stdout before the gate's FAIL banner on stderr. A
    consumer that appends stderr to stdout *afterwards* reads the decoy last and calls
    this run unassessed; the gate's own banner is last in real time, and that is the
    verdict.
    """
    _worktree, head = _scratch_root(tmp_path)
    _stub_gate(tmp_path, monkeypatch, "decoy")

    with pytest.raises(RuntimeError) as raised:
        GhOps(root=tmp_path).record_verification(LANE_ISSUE, head)

    assert not isinstance(raised.value, gate.CannotAssess)
    assert "a check failed" in str(raised.value)


def test_the_real_port_reads_a_silent_death_as_unassessed(tmp_path, monkeypatch):
    """A run that printed no verdict measured nothing — CANNOT-ASSESS, not a failure."""
    _worktree, head = _scratch_root(tmp_path)
    _stub_gate(tmp_path, monkeypatch, "interrupted")

    with pytest.raises(gate.CannotAssess) as raised:
        GhOps(root=tmp_path).record_verification(LANE_ISSUE, head)

    assert raised.value.verdict == gate.VERDICT_UNASSESSED
    assert "without reporting one of its own outcomes" in raised.value.detail


def test_the_real_port_records_the_attestation_when_the_gate_admits(tmp_path, monkeypatch):
    """Demanded case 3, end to end: rc 0 records the green attestation for the head.

    The record also carries **where** it was measured (``source``). That is not
    decoration: since #786 a green record can be produced from the lane *or*
    re-measured from the commit once the lane is gone, and the two are only
    distinguishable if the record says which one it was. The invariant is unchanged —
    it is still the verified head that must be named, which is the line below.
    """
    _worktree, head = _scratch_root(tmp_path)
    _stub_gate(tmp_path, monkeypatch, "passed")

    detail = GhOps(root=tmp_path).record_verification(LANE_ISSUE, head)

    assert detail == f"verify green at {head[:12]}"
    journal = json.loads(journal_path(LANE_ISSUE, tmp_path).read_text(encoding="utf-8"))
    assert journal["verify"] == {"ok": True, "commit": head, "source": "lane"}


def test_the_real_port_retries_a_park_and_records_every_attempt(tmp_path, monkeypatch):
    """Demanded case 4, end to end: the retry is real, bounded, and reported."""
    _scratch_root(tmp_path)
    counter = _stub_gate(tmp_path, monkeypatch, "parked", retries=1)

    with pytest.raises(gate.CannotAssess) as raised:
        GhOps(root=tmp_path).record_verification(LANE_ISSUE, "")

    assert counter.read_text(encoding="utf-8").count("call") == 2
    assert "2 attempt(s)" in raised.value.detail
    assert raised.value.verdict == gate.VERDICT_PARKED
    assert not journal_path(LANE_ISSUE, tmp_path).exists()
