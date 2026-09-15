#!/usr/bin/env python3
"""The pre-existing-red attribution, proved by measurement (issue #764).

The point of this module is that a red is attributable **only** by comparing the
lane's failing set against the same sweep on clean master. So the controls here
are arranged to kill the two ways that could be faked:

* *a claim instead of a measurement* — the same lane sweep is attributed against
  two different baselines and MUST reach opposite verdicts. A comparison replaced
  by a constant (always grant, always refuse) passes one of those and fails the
  other, so the pair cannot be satisfied vacuously.
* *a blanket waiver* — the conservative refusals are provoked one by one: a red
  signal other than ``tests`` (``verify`` included), a suite with no verdict, a
  sweep record naming another commit, and a missing baseline.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance.landing import attribution as att  # noqa: E402

HEAD = "a" * 40
OTHER = "b" * 40


def sweep_json(rows, *, sha: str = HEAD) -> dict:
    """A sweep record in the exact shape ``scripts/run-pytest-suites.sh`` writes."""
    return {
        "gate": "pytest-suites",
        "sha": sha,
        "declared": len(rows),
        "auto_registered": 0,
        "passed": sum(1 for _, status, _ in rows if status == att.STATUS_OK),
        "failed": sum(1 for _, status, _ in rows if status == att.STATUS_FAIL),
        "no_verdict": sum(1 for _, status, _ in rows if status == att.STATUS_NO_VERDICT),
        "suites": [{"suite": name, "status": status, "rc": rc, "detail": ""} for name, status, rc in rows],
    }


def write_sweep(path: Path, rows, *, sha: str = HEAD) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sweep_json(rows, sha=sha)), encoding="utf-8")
    return path


def write_contract(path: Path, signals, *, commit: str = HEAD, rc: int = 1) -> Path:
    """A merge attestation carrying the contract's OWN per-signal record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "gate": "merge-gate",
                "result": "NOT-OK" if rc else "PASS",
                "exit_code": rc,
                "commit": commit,
                "branch": "issue-764",
                "timestamp": "fixture",
                "checks": [
                    {"name": name, "rc": code, "status": "OK" if code == 0 else "NOT-OK"}
                    for name, code in signals
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


#: The contract's five signals with only `tests` red — the shape this issue is
#: about, as `scripts/merge-gate.sh` writes it.
TESTS_ONLY_RED = (
    ("verify", 0),
    ("drift", 0),
    ("tests", 1),
    ("negative-controls", 0),
    ("policy-schema", 0),
)

#: A stand-in for ``scripts/run-pytest-suites.sh`` that records a fixed sweep of
#: whatever commit the worktree is on. Stubbing the *sweep* (not the measurement)
#: keeps the measurement honest: the worktree, the environment and the record read
#: are all the real ones.
STUB_SWEEP = '''#!/usr/bin/env bash
set -eu
mkdir -p .verify
python3 - <<'PY'
import json
import pathlib
import subprocess

sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
pathlib.Path(".verify/test-results.json").write_text(json.dumps({"gate": "pytest-suites", "sha": sha, "suites": [
    {"suite": "governance/modules", "status": "FAIL", "rc": 1, "detail": ""},
    {"suite": "portal", "status": "OK", "rc": 0, "detail": ""},
]}), encoding="utf-8")
PY
'''


@pytest.fixture
def lane(tmp_path):
    """A lane root with a sweep record, a contract record and a sibling baseline."""
    root = tmp_path / "lane"
    root.mkdir()
    write_sweep(
        root / att.SWEEP_REL,
        [("governance/modules", att.STATUS_FAIL, 1), ("portal", att.STATUS_OK, 0)],
    )
    write_contract(root / att.ATTESTATION_REL, TESTS_ONLY_RED)
    write_sweep(
        tmp_path / "master.json",
        [("governance/modules", att.STATUS_FAIL, 1), ("portal", att.STATUS_OK, 0)],
    )
    return root


# --- the record readers ------------------------------------------------------


def test_a_missing_sweep_record_is_data_not_an_exception(tmp_path):
    read = att.read_sweep(tmp_path / "nope.json")
    assert read.state == att.STATE_ABSENT
    assert not read.readable
    assert "no such file" in read.detail


def test_a_malformed_sweep_record_is_not_readable(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    assert att.read_sweep(bad).state == att.STATE_MALFORMED


def test_a_sweep_record_with_no_suites_is_not_a_pass(tmp_path):
    empty = write_sweep(tmp_path / "empty.json", [])
    read = att.read_sweep(empty)
    assert read.state == att.STATE_EMPTY
    assert not read.readable


def test_a_record_whose_provenance_disagrees_with_its_content_is_malformed(tmp_path):
    """A hand-edited baseline cannot stand in for a measured one."""
    path = tmp_path / "b.json"
    path.write_text(
        json.dumps({"sha": HEAD, "measured_sha": OTHER, "suites": [{"suite": "x", "status": "OK", "rc": 0}]}),
        encoding="utf-8",
    )
    read = att.read_sweep(path)
    assert read.state == att.STATE_MALFORMED
    assert "provenance mismatch" in read.detail


# --- the measurement ---------------------------------------------------------


def test_attribution_is_a_measurement_not_a_claim(tmp_path):
    """The SAME lane against two baselines MUST flip — a constant cannot do both.

    This is the control that a comparison replaced by ``return grant`` or
    ``return refuse`` cannot pass, so it proves the verdict is read from the
    baseline rather than asserted.

    """
    lane_sweep = att.read_sweep(
        write_sweep(tmp_path / "lane.json", [("governance/modules", att.STATUS_FAIL, 1)])
    )
    passes_it = att.read_sweep(
        write_sweep(tmp_path / "base-passes.json", [("governance/modules", att.STATUS_OK, 0)])
    )
    fails_it = att.read_sweep(
        write_sweep(tmp_path / "base-fails.json", [("governance/modules", att.STATUS_FAIL, 1)])
    )

    granted = att.attribute_suites(lane_sweep, fails_it)
    refused = att.attribute_suites(lane_sweep, passes_it)

    assert granted.grant is True and granted.pre_existing == ("governance/modules",)
    assert refused.grant is False and refused.lane_caused == ("governance/modules",)
    assert granted.rc == att.EXIT_OK and refused.rc == att.EXIT_NOT_OK


def test_a_lane_caused_failure_is_refused_by_name(tmp_path):
    lane_sweep = att.read_sweep(
        write_sweep(
            tmp_path / "lane.json",
            [("governance/modules", att.STATUS_FAIL, 1), ("portal", att.STATUS_FAIL, 1)],
        )
    )
    baseline = att.read_sweep(
        write_sweep(tmp_path / "base.json", [("governance/modules", att.STATUS_FAIL, 1), ("portal", att.STATUS_OK, 0)])
    )
    result = att.attribute_suites(lane_sweep, baseline)
    assert result.code == att.CODE_LANE_CAUSED
    assert result.lane_caused == ("portal",)
    assert result.pre_existing == ("governance/modules",)
    assert "portal" in result.summary


def test_a_newly_failing_suite_absent_from_the_baseline_is_refused(tmp_path):
    """No grandfathering: a suite the baseline does not report as failing refuses."""
    lane_sweep = att.read_sweep(
        write_sweep(tmp_path / "lane.json", [("governance/landing", att.STATUS_FAIL, 1)])
    )
    baseline = att.read_sweep(write_sweep(tmp_path / "base.json", [("governance/modules", att.STATUS_OK, 0)]))
    result = att.attribute_suites(lane_sweep, baseline)
    assert result.code == att.CODE_LANE_CAUSED
    assert result.lane_caused == ("governance/landing",)


def test_a_suite_with_no_verdict_is_never_attributable(tmp_path):
    lane_sweep = att.read_sweep(
        write_sweep(tmp_path / "lane.json", [("portal", att.STATUS_NO_VERDICT, 124)])
    )
    baseline = att.read_sweep(write_sweep(tmp_path / "base.json", [("portal", att.STATUS_OK, 0)]))
    result = att.attribute_suites(lane_sweep, baseline)
    assert result.grant is False
    assert result.code == att.CODE_SUITE_NO_VERDICT
    assert result.rc == att.EXIT_NOT_OK


def test_a_missing_baseline_is_cannot_assess_never_a_grant(tmp_path):
    lane_sweep = att.read_sweep(
        write_sweep(tmp_path / "lane.json", [("governance/modules", att.STATUS_FAIL, 1)])
    )
    result = att.attribute_suites(lane_sweep, att.read_sweep(tmp_path / "missing.json"))
    assert result.grant is False
    assert result.cannot_assess is True
    assert result.rc == att.EXIT_CANNOT_ASSESS


def test_a_missing_lane_record_is_cannot_assess(tmp_path):
    baseline = att.read_sweep(write_sweep(tmp_path / "base.json", [("governance/modules", att.STATUS_FAIL, 1)]))
    result = att.attribute_suites(att.read_sweep(tmp_path / "missing.json"), baseline)
    assert result.code == att.CODE_NO_LANE_RECORD
    assert result.rc == att.EXIT_CANNOT_ASSESS


# --- the contract record: which red may be attributed at all -----------------


def test_the_tests_signal_only_may_be_attributed(lane, tmp_path):
    result = att.attribute_contract(lane, commit=HEAD, baseline_file=tmp_path / "master.json")
    assert result.grant is True
    assert result.pre_existing == ("governance/modules",)
    assert result.regraded_rc == att.EXIT_OK
    assert ("verify", 0) in result.signals


def test_a_red_verify_is_never_attributed(lane, tmp_path):
    """The gate of record stays strict, however pre-existing the suite reds are."""
    write_contract(
        lane / att.ATTESTATION_REL,
        (("verify", 1), ("drift", 0), ("tests", 1), ("negative-controls", 0), ("policy-schema", 0)),
    )
    result = att.attribute_contract(lane, commit=HEAD, baseline_file=tmp_path / "master.json")
    assert result.grant is False
    assert result.code == att.CODE_SIGNAL_RED
    assert result.rc == att.EXIT_NOT_OK
    assert "verify" in result.summary
    # ...and it says so WITHOUT consulting a sweep: the signal record is read first.
    assert result.lane is None


def test_a_red_signal_other_than_tests_is_never_attributed(lane, tmp_path):
    write_contract(
        lane / att.ATTESTATION_REL,
        (("verify", 0), ("drift", 1), ("tests", 1), ("negative-controls", 0), ("policy-schema", 0)),
    )
    result = att.attribute_contract(lane, commit=HEAD, baseline_file=tmp_path / "master.json")
    assert result.code == att.CODE_SIGNAL_RED
    assert "drift" in result.summary


def test_a_tests_signal_with_no_verdict_is_not_attributed(lane, tmp_path):
    write_contract(
        lane / att.ATTESTATION_REL,
        (("verify", 0), ("drift", 0), ("tests", 2), ("negative-controls", 0), ("policy-schema", 0)),
    )
    result = att.attribute_contract(lane, commit=HEAD, baseline_file=tmp_path / "master.json")
    assert result.code == att.CODE_TESTS_NO_VERDICT
    assert result.rc == att.EXIT_CANNOT_ASSESS


def test_an_attestation_without_a_signal_record_is_not_attributable(lane, tmp_path):
    (lane / att.ATTESTATION_REL).write_text(
        json.dumps({"gate": "merge-gate", "result": "NOT-OK", "exit_code": 1, "commit": HEAD}),
        encoding="utf-8",
    )
    result = att.attribute_contract(lane, commit=HEAD, baseline_file=tmp_path / "master.json")
    assert result.code == att.CODE_NO_CONTRACT_RECORD
    assert result.rc == att.EXIT_CANNOT_ASSESS


def test_a_sweep_of_another_commit_is_not_evidence_for_this_one(lane, tmp_path):
    write_sweep(
        lane / att.SWEEP_REL,
        [("governance/modules", att.STATUS_FAIL, 1)],
        sha=OTHER,
    )
    result = att.attribute_contract(lane, commit=HEAD, baseline_file=tmp_path / "master.json")
    assert result.code == att.CODE_STALE_LANE_RECORD
    assert result.rc == att.EXIT_CANNOT_ASSESS
    assert OTHER in result.summary and HEAD in result.summary


def test_an_already_green_contract_needs_no_attribution(lane, tmp_path):
    write_contract(
        lane / att.ATTESTATION_REL,
        (("verify", 0), ("drift", 0), ("tests", 0), ("negative-controls", 0), ("policy-schema", 0)),
        rc=0,
    )
    result = att.attribute_contract(lane, commit=HEAD, baseline_file=tmp_path / "master.json")
    assert result.code == att.CODE_ALREADY_GREEN
    assert result.grant is True and result.regraded_rc == att.EXIT_OK


# --- the live measurement ----------------------------------------------------


def _stub_repo(tmp_path: Path, *, rev: str = "origin/master") -> Path:
    """A tiny real repository whose sweep script writes a record — no pytest inside.

    The measurement path is a shell-out to ``scripts/run-pytest-suites.sh`` in a
    scratch worktree. Stubbing that script (not the measurement) keeps the
    control real: git worktree creation, the clean environment, the record read
    and the cache are all exercised.
    """
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)

    git("init", "-q", "-b", "master")
    git("config", "user.name", "fixture")
    git("config", "user.email", "fixture@example.invalid")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "run-pytest-suites.sh").write_text(STUB_SWEEP, encoding="utf-8")
    (repo / "BASE.txt").write_text("base\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "base")
    git("update-ref", f"refs/remotes/{rev}", "HEAD")
    return repo


def test_measure_baseline_runs_the_real_sweep_in_a_clean_worktree(tmp_path):
    repo = _stub_repo(tmp_path)
    measured = att.measure_baseline(repo, rev="origin/master")
    assert measured.readable, measured.detail
    assert measured.failures() == frozenset({"governance/modules"})
    assert measured.sha == subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "origin/master"], capture_output=True, text=True, check=True
    ).stdout.strip()
    # the record is cached WITH its provenance, beside the sweep it came from
    cache = att.baseline_cache_path(repo, measured.sha)
    assert cache.is_file()
    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert payload["measured_sha"] == payload["sha"] == measured.sha
    assert payload["rev"] == "origin/master"
    assert payload["command"] == att.SWEEP_COMMAND_TEXT
    # ...and no worktree is left behind by the measurement
    listing = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list", "--porcelain"], capture_output=True, text=True, check=True
    )
    assert listing.stdout.count("worktree ") == 1, listing.stdout


def test_measure_baseline_of_an_unresolvable_rev_is_cannot_assess(tmp_path):
    repo = _stub_repo(tmp_path)
    measured = att.measure_baseline(repo, rev="origin/no-such-branch")
    assert not measured.readable
    assert measured.state == att.STATE_ABSENT
    assert "cannot resolve" in measured.detail


def test_the_cached_baseline_is_reused_rather_than_re_measured(tmp_path):
    repo = _stub_repo(tmp_path)
    first = att.measure_baseline(repo, rev="origin/master")
    assert first.readable
    (repo / "scripts" / "run-pytest-suites.sh").write_text("#!/usr/bin/env bash\nexit 9\n", encoding="utf-8")
    second = att.measure_baseline(repo, rev="origin/master")
    assert second.readable, "the cache must be used for the same commit"
    assert "cached at" in second.source
