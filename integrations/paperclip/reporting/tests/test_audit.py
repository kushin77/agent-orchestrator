"""The append-only audit trail of claim resolution (issue #592).

The trail is the surface's durable answer to "what did this run resolve": one
record per composed brief run, carrying the resolved / unresolved counts and the
finding lines. Three properties are asserted here, and each of them is
provoked rather than assumed: exactly one record per run, a non-resolving claim
named by its line in that record, and a trail that only ever grows.
"""

from __future__ import annotations

from conftest import require_real_hub

import json
import subprocess
import sys
from pathlib import Path

from governance.modules.model import TARGET_PENDING

from integrations.paperclip.reporting import audit, composer, policy as claim_policy

PACKAGE = "integrations/paperclip/reporting"
HUB = "vendor/CMR"


def run_cli(tree: Path, *args: str):
    return subprocess.run(
        [
            sys.executable,
            str(tree / PACKAGE / "cli.py"),
            *args,
            "--repo",
            str(tree),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_clean_run_appends_exactly_one_record(repo_root: Path, tmp_path: Path):
    require_real_hub()
    trail = tmp_path / "trail.jsonl"
    composition = composer.compose(
        _document(repo_root),
        repo_root,
        HUB,
        policy=claim_policy.load(repo_root / PACKAGE),
        audit_trail=audit.Trail(trail),
    )
    records = audit.read(trail)
    assert len(records) == 1, records
    record = records[0]
    assert record["schema"] == audit.TRAIL_SCHEMA
    assert record["claims"] == len(composition.claims)
    assert record["unresolved"] == 0
    assert record["resolved"] == record["claims"]
    assert record["findings"] == []
    assert record["revision"]


def test_an_unresolved_claim_appends_exactly_one_record_naming_its_line(tree: Path):
    trail = tree / "audit" / "trail.jsonl"
    proc = run_cli(tree, "compose", "--registry", str(_unresolvable(tree)), "--audit", str(trail))
    assert proc.returncode == 1, proc.stderr

    records = audit.read(trail)
    assert len(records) == 1, "one composed run must append exactly one record"
    record = records[0]
    assert record["unresolved"] >= 1
    assert record["resolved"] == record["claims"] - record["unresolved"]
    line = [f for f in record["findings"] if "BRIEF-CLAIM-UNRESOLVED" in f]
    assert line, record["findings"]
    assert "docs/MODULE-BRIEF.md:" in line[0], line[0]
    assert "templates/module/not-here.json" in line[0], line[0]


def test_the_trail_is_append_only(tree: Path):
    trail = tree / "audit" / "append-only.jsonl"
    document = _unresolvable(tree)

    first = run_cli(tree, "compose", "--registry", str(document), "--audit", str(trail))
    assert first.returncode == 1, first.stderr
    after_first = trail.read_bytes()
    assert len(audit.read(trail)) == 1

    second = run_cli(tree, "compose", "--registry", str(document), "--audit", str(trail))
    assert second.returncode == 1, second.stderr
    after_second = trail.read_bytes()

    records = audit.read(trail)
    assert len(records) == 2, records
    # Append-only means the first run's record is still there, unchanged, and the
    # file only ever grew — nothing rewrote, reordered or truncated it.
    assert after_second.startswith(after_first), "the trail was rewritten, not appended"
    assert len(after_second) > len(after_first)
    assert after_first.endswith(b"\n")


def test_two_runs_over_one_revision_record_the_same_thing(tree: Path):
    """Deterministic and offline: no clock, no network fact, no run identity."""
    trail = tree / "audit" / "deterministic.jsonl"
    document = _unresolvable(tree)
    first = run_cli(tree, "compose", "--registry", str(document), "--audit", str(trail))
    assert first.returncode == 1, first.stderr
    second = run_cli(tree, "compose", "--registry", str(document), "--audit", str(trail))
    assert second.returncode == 1, second.stderr
    records = audit.read(trail)
    assert len(records) == 2
    assert records[0] == records[1]
    assert set(records[0]) == {
        "schema",
        "revision",
        "claims",
        "resolved",
        "unresolved",
        "findings",
    }, "a field outside the declared record shape (a clock, say) appeared"


def test_a_clean_run_is_recorded_by_the_default_trail_inside_the_gitignored_dir(tree: Path):
    proc = run_cli(tree, "compose")
    assert proc.returncode == 0, proc.stderr
    default = tree / audit.DEFAULT_TRAIL
    assert default.is_file(), "the run was not recorded"
    records = audit.read(default)
    assert len(records) == 1
    assert records[0]["unresolved"] == 0


def test_the_audit_command_reports_the_trail(tree: Path):
    assert run_cli(tree, "compose").returncode == 0
    proc = run_cli(tree, "audit")
    assert proc.returncode == 0, proc.stderr
    assert "1 record(s)" in proc.stdout
    assert "unresolved" in proc.stdout


def test_an_absent_trail_is_cannot_assess_never_a_pass(repo_root: Path, tmp_path: Path):
    proc = run_cli(repo_root, "audit", "--audit", str(tmp_path / "nothing.jsonl"))
    assert proc.returncode == 2
    assert "CANNOT-ASSESS" in proc.stderr


def test_a_pending_module_run_records_no_finding(composition, tmp_path: Path):
    trail = tmp_path / "pending.jsonl"
    policy = claim_policy.load()
    record = audit.record_for(composition, policy)
    assert record["unresolved"] == 0
    assert any(
        entry["state"] == TARGET_PENDING and entry["shipped"] is False
        for entry in composition.document["modules"]
    )
    audit.append(trail, record)
    assert json.loads(trail.read_text(encoding="utf-8").strip()) == record


def _document(root: Path) -> dict:
    from governance.modules import registry

    return registry.build(root, root / HUB)


def _unresolvable(tree: Path) -> Path:
    """A registry document whose mandatory module claims a seed that is not there."""
    document = _document(tree)
    for entry in document["modules"]:
        if entry["state"] == "registered-mandatory":
            entry["assets"][0]["seed"] = "templates/module/not-here.json"
            entry["assets"][0]["seed_present"] = True
            break
    else:  # pragma: no cover - the registry always carries a mandatory module
        raise AssertionError("no registered-mandatory module to doctor")
    path = tree / "audit-unresolvable.json"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path
