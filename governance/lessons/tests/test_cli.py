"""CLI-level tests for governance/lessons/cli.py (issue #141).

The CLI is tested against real, throwaway git repositories: the tri-state exit
codes are only meaningful if the tracked-ness and history probes are real.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import ARTIFACT, REPO_ROOT, incident, rca, rca_body

import cli
from model import CODE_RCA_ARTIFACT_UNTRACKED

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is required to exercise the CLI"
)


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )


def make_repo(path: Path, *, commit: bool = True) -> Path:
    """A throwaway repository with a committed artifact and ledger.

    The closed lesson must carry commit evidence (that is the rule under test),
    so the artifact is committed first and its sha is written into the ledger.
    """
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    artifact = path / ARTIFACT
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(rca_body(), encoding="utf-8")
    (path / ".board").mkdir(parents=True, exist_ok=True)
    (path / ".board" / "snapshot.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-09-15T00:00:00Z",
                "source": "acme/widgets",
                "issues": [{"number": 100, "state": "OPEN", "labels": []}],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    sha = ""
    if commit:
        git(path, "add", ".")
        git(
            path,
            "-c",
            "user.name=gate",
            "-c",
            "user.email=gate@example.invalid",
            "commit",
            "-q",
            "-m",
            "seed the RCA artifact",
        )
        sha = git(path, "rev-parse", "HEAD").stdout.strip()

    evidence = [
        {"kind": "commit", "ref": sha[:7]} if sha else {"kind": "artifact", "ref": ARTIFACT},
        {"kind": "artifact", "ref": ARTIFACT},
    ]
    ledger = path / "governance" / "lessons" / "ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    records = [
        incident(1),
        rca(1),
        {
            "id": "CA-0001",
            "kind": "corrective-action",
            "date": "2026-09-03",
            "rca": "RCA-0001",
            "action": "fix the mechanism",
            "status": "closed",
            "evidence": evidence,
        },
        {
            "id": "LESSON-0001",
            "kind": "lesson",
            "title": "a durable rule",
            "rca": "RCA-0001",
            "date": "2026-09-04",
            "class": "enterprise",
            "status": "closed",
            "evidence": evidence,
        },
    ]
    ledger.write_text(
        "\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n",
        encoding="utf-8",
    )
    if commit:
        git(path, "add", ".")
        git(
            path,
            "-c",
            "user.name=gate",
            "-c",
            "user.email=gate@example.invalid",
            "commit",
            "-q",
            "-m",
            "seed the ledger",
        )
    return path


def test_check_is_green_on_a_complete_repository(tmp_path, capsys):
    repo = make_repo(tmp_path / "repo")
    assert cli.main(["--root", str(repo), "check"]) == 0
    out = capsys.readouterr().out
    assert "lessons: OK" in out
    assert "incidents: 1 (1 closed)" in out


def test_check_without_a_ledger_cannot_assess(tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cli.main(["--root", str(empty), "check"]) == 2
    assert "CANNOT-ASSESS" in capsys.readouterr().err


def test_check_without_a_snapshot_cannot_assess(tmp_path, capsys):
    repo = make_repo(tmp_path / "repo")
    (repo / ".board" / "snapshot.json").unlink()
    assert cli.main(["--root", str(repo), "check"]) == 2
    assert "no board snapshot" in capsys.readouterr().err


def test_check_outside_a_work_tree_cannot_assess(tmp_path, capsys):
    repo = make_repo(tmp_path / "repo", commit=False)
    shutil.rmtree(repo / ".git")
    assert cli.main(["--root", str(repo), "check"]) == 2
    assert "not a git work tree" in capsys.readouterr().err


def test_check_reports_an_untracked_artifact(tmp_path, capsys):
    repo = make_repo(tmp_path / "repo")
    # The artifact exists on disk but is no longer tracked: it does not exist.
    assert git(repo, "rm", "--cached", "-q", ARTIFACT).returncode == 0
    assert cli.main(["--root", str(repo), "check"]) == 1
    captured = capsys.readouterr()
    assert CODE_RCA_ARTIFACT_UNTRACKED in captured.out
    assert "lessons: FAIL" in captured.err


def test_check_writes_the_report(tmp_path):
    repo = make_repo(tmp_path / "repo")
    assert cli.main(["--root", str(repo), "check"]) == 0
    report = json.loads(
        (repo / ".verify" / "lessons-report.json").read_text(encoding="utf-8")
    )
    assert report["errors"] == []
    assert report["counts"]["incidents"] == 1


def test_strict_fails_on_deviations(tmp_path, capsys):
    repo = make_repo(tmp_path / "repo")
    ledger = repo / "governance" / "lessons" / "ledger.jsonl"
    suggestion = {
        "id": "SUGGEST-0001",
        "kind": "lesson",
        "title": "an open improvement",
        "rca": "RCA-0001",
        "date": "2026-09-04",
        "class": "elite",
        "status": "open",
        "evidence": [{"kind": "artifact", "ref": ARTIFACT}],
        "owner": "governance lane",
        "remediation": "land the change",
    }
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(suggestion, sort_keys=True) + "\n")
    assert cli.main(["--root", str(repo), "check"]) == 0
    assert cli.main(["--root", str(repo), "check", "--strict"]) == 1
    assert "SUGGEST-0001 is open" in capsys.readouterr().out


def test_a_malformed_policy_fails_the_gate(tmp_path, capsys):
    repo = make_repo(tmp_path / "repo")
    (repo / "governance" / "lessons" / "policy.yaml").write_text(
        "schema: [unclosed\n", encoding="utf-8"
    )
    assert cli.main(["--root", str(repo), "check"]) == 1
    assert "NOT-OK" in capsys.readouterr().err


def test_status_summarizes_the_ledger(tmp_path, capsys):
    repo = make_repo(tmp_path / "repo")
    assert cli.main(["--root", str(repo), "status"]) == 0
    assert "incidents: 1" in capsys.readouterr().out


def test_record_appends_a_valid_record(tmp_path, capsys):
    repo = make_repo(tmp_path / "repo")
    payload = tmp_path / "record.json"
    payload.write_text(
        json.dumps(
            {
                "id": "SUGGEST-0002",
                "kind": "lesson",
                "title": "another improvement",
                "rca": "RCA-0001",
                "date": "2026-09-05",
                "class": "elite",
                "status": "open",
                "evidence": [{"kind": "artifact", "ref": ARTIFACT}],
                "owner": "gate lane",
                "remediation": "land the change",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    assert cli.main(["--root", str(repo), "record", "--file", str(payload)]) == 0
    assert "appended SUGGEST-0002" in capsys.readouterr().out
    assert cli.main(["--root", str(repo), "check"]) == 0


def test_record_refuses_an_invalid_record(tmp_path, capsys):
    repo = make_repo(tmp_path / "repo")
    payload = tmp_path / "bad.json"
    payload.write_text(json.dumps({"id": "SUGGEST-0003", "kind": "lesson"}),
                       encoding="utf-8")
    assert cli.main(["--root", str(repo), "record", "--file", str(payload)]) == 1
    err = capsys.readouterr().err
    assert "schema error(s)" in err
    assert "SUGGEST-0003" not in (
        repo / "governance" / "lessons" / "ledger.jsonl"
    ).read_text(encoding="utf-8")


def test_record_refuses_a_duplicate_id(tmp_path, capsys):
    repo = make_repo(tmp_path / "repo")
    payload = tmp_path / "dupe.json"
    payload.write_text(json.dumps(incident(1), sort_keys=True), encoding="utf-8")
    assert cli.main(["--root", str(repo), "record", "--file", str(payload)]) == 1
    assert "already in the ledger" in capsys.readouterr().err


def test_record_rejects_unparseable_input(tmp_path, capsys):
    repo = make_repo(tmp_path / "repo")
    payload = tmp_path / "broken.json"
    payload.write_text("{not json", encoding="utf-8")
    assert cli.main(["--root", str(repo), "record", "--file", str(payload)]) == 1
    assert "does not parse as JSON" in capsys.readouterr().err


def test_record_from_a_missing_file_cannot_assess(tmp_path, capsys):
    repo = make_repo(tmp_path / "repo")
    assert cli.main(["--root", str(repo), "record", "--file", "nope.json"]) == 2
    assert "CANNOT-ASSESS" in capsys.readouterr().err


def test_template_prints_the_canonical_rca_template(capsys):
    assert cli.main(["--root", str(REPO_ROOT), "template"]) == 0
    assert "## Root cause" in capsys.readouterr().out


def test_status_without_a_ledger_cannot_assess(tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cli.main(["--root", str(empty), "status"]) == 2
    assert "CANNOT-ASSESS" in capsys.readouterr().err
