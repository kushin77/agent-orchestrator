"""`scripts/lib/validate-attestation.py` against `.verify/attestation.schema.json`
(issue #882, lane L3 of #878).

Every `scripts/verify.sh` run writes `.verify/attestation.json` — on FAIL as
well as PASS — and this validator is what proves the file it wrote actually
conforms to the schema, catching both a shape defect (a missing/mistyped
field) and the semantic defect the shape alone cannot see: a FAILING check
whose verdict was fabricated as OK. The negative control below is exactly
that fabrication — the class of bug that makes a gate a false green.

This suite is placed under `governance/isolation/tests/` per this lane's file
ownership even though the subject is `.verify/` attestation, not isolation;
it is added, not a rename of an existing file.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
VALIDATOR = REPO_ROOT / "scripts" / "lib" / "validate-attestation.py"
SCHEMA = REPO_ROOT / ".verify" / "attestation.schema.json"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", str(VALIDATOR), *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def _write(tmp_path: Path, doc: dict) -> Path:
    path = tmp_path / "attestation.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _good_doc() -> dict:
    return {
        "run_id": "run-1",
        "git_sha": "abc1234",
        "overall_verdict": "FAIL",
        "checks": [
            {"name": "a", "verdict": "OK", "rc": 0, "duration": 1.0, "evidence_tail": ""},
            {"name": "b", "verdict": "FAIL", "rc": 1, "duration": 2.0, "evidence_tail": "boom"},
        ],
    }


def test_schema_and_validator_exist() -> None:
    assert SCHEMA.is_file(), f"{SCHEMA} is missing"
    assert VALIDATOR.is_file(), f"{VALIDATOR} is missing"


def test_well_formed_attestation_is_ok(tmp_path: Path) -> None:
    doc = _good_doc()
    path = _write(tmp_path, doc)
    result = _run(str(path), str(SCHEMA))
    assert result.returncode == 0, result.stdout + result.stderr


def test_missing_required_field_is_refused(tmp_path: Path) -> None:
    doc = _good_doc()
    del doc["run_id"]
    path = _write(tmp_path, doc)
    result = _run(str(path), str(SCHEMA))
    assert result.returncode == 1, result.stdout + result.stderr


def test_non_tri_state_verdict_is_refused(tmp_path: Path) -> None:
    doc = _good_doc()
    doc["checks"][0]["verdict"] = "GREEN"
    path = _write(tmp_path, doc)
    result = _run(str(path), str(SCHEMA))
    assert result.returncode == 1, result.stdout + result.stderr


def test_negative_control_a_red_reported_ok_is_refused(tmp_path: Path) -> None:
    """A fabricated attestation marking a FAILING check OK must never pass.

    This is the exact false-green shape #882 exists to refuse: schema-valid
    on its own (every required field is present and well typed), but the
    verdict lies about the rc. The validator's semantic cross-check, not the
    shape check, is what must catch it.
    """
    doc = {
        "run_id": "run-lie",
        "git_sha": "deadbee",
        "overall_verdict": "OK",
        "checks": [
            {"name": "a", "verdict": "OK", "rc": 1, "duration": 1.0, "evidence_tail": "actually failed"},
        ],
    }
    path = _write(tmp_path, doc)
    result = _run(str(path), str(SCHEMA))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "red-reported-ok" in (result.stdout + result.stderr)


def test_overall_better_than_worst_check_is_refused(tmp_path: Path) -> None:
    doc = _good_doc()
    doc["overall_verdict"] = "OK"  # a FAIL check is present -> overall must be FAIL
    path = _write(tmp_path, doc)
    result = _run(str(path), str(SCHEMA))
    assert result.returncode == 1, result.stdout + result.stderr


def test_missing_attestation_file_is_cannot_assess(tmp_path: Path) -> None:
    result = _run(str(tmp_path / "nope.json"), str(SCHEMA))
    assert result.returncode == 2, result.stdout + result.stderr
